import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Beaker,
  BookOpen,
  Bot,
  CheckCircle2,
  Cpu,
  Eye,
  FlaskConical,
  GitBranch,
  Lightbulb,
  ListFilter,
  Settings2,
  Search,
  Sparkles,
  Target,
  WandSparkles
} from "lucide-react";
import { Button, Input, Pagination, Select } from "antd";
import { CatalogIconCard } from "../../components/resource-plaza/CatalogIconCard";
import { ResourcePlazaResultState } from "../../components/resource-plaza";
import {
  fetchResourceCatalog,
  parseResourceCatalogSearch,
  serializeResourceCatalogSearch
} from "../../features/resource-plaza";
import {
  RESOURCE_AVAILABILITY_PRESENTATIONS,
  RESOURCE_DOMAIN_LABELS,
  RESOURCE_KIND_LABELS,
  RESOURCE_SCENARIO_LABELS
} from "../../features/resource-plaza/state";
import type { ResourceCatalogSearchState } from "../../features/resource-plaza/catalogSearch";
import type {
  ResourceAvailability,
  ResourceCatalogPage,
  ResourceDomain,
  ResourceKind,
  ResourceScenario
} from "../../features/resource-plaza/types";
import { cn } from "../../lib/cn";
import type { LucideIcon } from "lucide-react";

type FilterChip = {
  value: string;
  label: string;
  icon: LucideIcon;
};

type FilterGroupTone = {
  idle: string;
  icon: string;
  selected: string;
};

const GROUP_TONES = {
  brand: {
    idle: "border-transparent bg-brand-soft text-text",
    icon: "text-brand",
    selected: "border-brand bg-brand-soft font-medium text-brand"
  },
  success: {
    idle: "border-transparent bg-success-soft text-text",
    icon: "text-success",
    selected: "border-success bg-success-soft font-medium text-success"
  },
  warning: {
    idle: "border-transparent bg-warning-soft text-text",
    icon: "text-warning",
    selected: "border-warning bg-warning-soft font-medium text-warning"
  },
  purple: {
    idle: "border-transparent bg-purple-soft text-text",
    icon: "text-purple",
    selected: "border-purple bg-purple-soft font-medium text-purple"
  },
  muted: {
    idle: "border-transparent bg-surface-soft text-text",
    icon: "text-text-muted",
    selected: "border-border-strong bg-surface-soft font-medium text-text"
  }
} as const satisfies Record<string, FilterGroupTone>;

/** 筛选项与正式目录一致；视觉保留芯片样式，一行最多两项。 */
const ALL_VALUE = "all";

/** 类型优先，不提供「全部混排」。 */
const TYPE_CHIPS: FilterChip[] = [
  { value: "agent", label: "智能体", icon: Bot },
  { value: "skill", label: "技能", icon: WandSparkles },
  { value: "workflow", label: "工作流模板", icon: GitBranch }
];

const AVAILABILITY_CHIPS: FilterChip[] = [
  { value: ALL_VALUE, label: "全部", icon: ListFilter },
  { value: "tryable", label: RESOURCE_AVAILABILITY_PRESENTATIONS.tryable.label, icon: CheckCircle2 },
  {
    value: "configuration_required",
    label: RESOURCE_AVAILABILITY_PRESENTATIONS.configuration_required.label,
    icon: Settings2
  },
  { value: "view_only", label: RESOURCE_AVAILABILITY_PRESENTATIONS.view_only.label, icon: Eye }
];

const DOMAIN_CHIPS: FilterChip[] = [
  { value: ALL_VALUE, label: "全部", icon: Sparkles },
  { value: "cs_research", label: RESOURCE_DOMAIN_LABELS.cs_research, icon: Cpu },
  { value: "general", label: RESOURCE_DOMAIN_LABELS.general, icon: Sparkles },
  { value: "materials", label: RESOURCE_DOMAIN_LABELS.materials, icon: Beaker },
  { value: "ope", label: RESOURCE_DOMAIN_LABELS.ope, icon: FlaskConical },
  { value: "opentrajectory", label: RESOURCE_DOMAIN_LABELS.opentrajectory, icon: Target },
  { value: "sdr", label: RESOURCE_DOMAIN_LABELS.sdr, icon: BookOpen }
];

const SCENARIO_CHIPS: FilterChip[] = [
  { value: ALL_VALUE, label: "全部", icon: ListFilter },
  { value: "problem_modeling", label: RESOURCE_SCENARIO_LABELS.problem_modeling, icon: Lightbulb },
  { value: "solution_design", label: RESOURCE_SCENARIO_LABELS.solution_design, icon: WandSparkles },
  {
    value: "execution_validation",
    label: RESOURCE_SCENARIO_LABELS.execution_validation,
    icon: Bot
  },
  {
    value: "insight_generation",
    label: RESOURCE_SCENARIO_LABELS.insight_generation,
    icon: GitBranch
  }
];

function resolveChipSelection(selectedValue: string | undefined): string {
  return selectedValue ?? ALL_VALUE;
}

function FilterChipButton({
  chip,
  selected,
  tone,
  onClick
}: {
  chip: FilterChip;
  selected: boolean;
  tone: FilterGroupTone;
  onClick: () => void;
}) {
  const Icon = chip.icon;
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      title={chip.label}
      className={cn(
        "inline-flex min-w-0 items-center gap-1.5 rounded-lg border px-2.5 py-2 text-left text-[12px] leading-4 transition-colors",
        selected ? tone.selected : tone.idle,
        !selected && "hover:opacity-90"
      )}
    >
      <Icon size={14} className={cn("shrink-0", selected ? undefined : tone.icon)} aria-hidden />
      <span className="min-w-0 truncate">{chip.label}</span>
    </button>
  );
}

function FilterChipGroup({
  title,
  chips,
  selectedValue,
  onSelect,
  tone
}: {
  title: string;
  chips: FilterChip[];
  selectedValue?: string;
  onSelect: (value: string | undefined) => void;
  tone: FilterGroupTone;
}) {
  if (chips.length === 0) return null;
  const active = resolveChipSelection(selectedValue);
  return (
    <section className="flex shrink-0 flex-col gap-2">
      <h3 className="m-0 text-[13px] font-semibold text-text">{title}</h3>
      <div className="grid grid-cols-2 gap-2">
        {chips.map(chip => (
          <FilterChipButton
            key={chip.value}
            chip={chip}
            tone={tone}
            selected={active === chip.value}
            onClick={() => {
              if (chip.value === ALL_VALUE) {
                onSelect(undefined);
                return;
              }
              if (active === chip.value) return;
              onSelect(chip.value);
            }}
          />
        ))}
      </div>
    </section>
  );
}

export default function ResourcePlazaCatalogPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const searchKey = searchParams.toString();
  const search = useMemo(() => {
    const parsed = parseResourceCatalogSearch(new URLSearchParams(searchKey));
    return {
      ...parsed,
      // 有关键词时不默认 agent，保持跨类型结果；无关键词浏览仍默认智能体
      resourceType: parsed.resourceType ?? (parsed.q ? undefined : "agent"),
      // 侧栏已去掉「暂不可用」，遗留 query 视为未选
      availability: parsed.availability === "unavailable" ? undefined : parsed.availability,
      more: true
    } satisfies ResourceCatalogSearchState;
  }, [searchKey]);

  const [draftKeyword, setDraftKeyword] = useState(search.q);
  const [result, setResult] = useState<ResourceCatalogPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [retryKey, setRetryKey] = useState(0);

  const searchPlaceholder = search.resourceType
    ? `搜索${RESOURCE_KIND_LABELS[search.resourceType]}名称或说明`
    : "搜索资源名称或说明";
  // 默认智能体不算「额外筛选」；跨类型（无 resourceType）算筛选态
  const hasFilters = Boolean(
    search.q ||
    search.availability ||
    search.domain ||
    search.scenario ||
    !search.resourceType ||
    search.resourceType !== "agent"
  );

  useEffect(() => setDraftKeyword(search.q), [search.q]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setFailed(false);
    fetchResourceCatalog(search, controller.signal)
      .then(setResult)
      .catch(error => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setFailed(true);
        setResult(null);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [
    search.q,
    search.resourceType,
    search.availability,
    search.domain,
    search.scenario,
    search.sort,
    search.page,
    search.pageSize,
    retryKey
  ]);

  const updateSearch = (patch: Partial<ResourceCatalogSearchState>, replace = true) => {
    setSearchParams(serializeResourceCatalogSearch({ ...search, ...patch }), { replace });
  };

  useEffect(() => {
    if (loading || failed || !result) return;
    const maximumPage = Math.max(1, Math.ceil(result.total / search.pageSize) || 1);
    if (search.page > maximumPage) updateSearch({ page: maximumPage });
  }, [failed, loading, result, search.page, search.pageSize]);

  const clearFilters = () =>
    updateSearch({
      q: "",
      resourceType: "agent",
      availability: undefined,
      domain: undefined,
      scenario: undefined,
      sort: "relevance",
      page: 1,
      pageSize: 20
    });

  const submitSearch = () => updateSearch({ q: draftKeyword.trim(), page: 1 }, false);
  const openDetail = (resourceId: string) => {
    const detailSearch = serializeResourceCatalogSearch(search).toString();
    navigate(`/resources/catalog/${encodeURIComponent(resourceId)}${detailSearch ? `?${detailSearch}` : ""}`);
  };

  return (
    <div className="h-full w-full" data-testid="resource-plaza-catalog-page">
      <div className="flex h-full min-h-0 w-full flex-col gap-6 overflow-hidden rounded-xl bg-surface p-6">
        <section
          className="flex shrink-0 flex-col gap-3"
          aria-label="公共资源搜索"
          data-testid="resource-catalog-search"
        >
          <div className="flex items-end justify-between gap-4 max-[900px]:flex-col max-[900px]:items-stretch">
            <div className="min-w-0">
              <h2 className="m-0 text-xl font-semibold text-text">公共资源</h2>
              <p className="m-0 mt-1 text-sm text-text-secondary">浏览并筛选已发布的智能体、技能与工作流模板</p>
            </div>
            <div className="flex items-center gap-3 max-[900px]:w-full">
              <div className="w-full max-w-[320px] max-[900px]:max-w-none max-[900px]:flex-1">
                <Input
                  allowClear
                  prefix={<Search size={16} className="text-text-muted" aria-hidden />}
                  value={draftKeyword}
                  onChange={event => setDraftKeyword(event.target.value)}
                  onPressEnter={submitSearch}
                  onClear={() => updateSearch({ q: "", page: 1 })}
                  placeholder={searchPlaceholder}
                  aria-label="搜索公共资源"
                />
              </div>
              <Select
                aria-label="资源排序"
                value={search.sort}
                onChange={value => updateSearch({ sort: value, page: 1 })}
                options={[
                  { label: "默认排序", value: "relevance" },
                  { label: "名称：升序", value: "name_asc" },
                  { label: "名称：降序", value: "name_desc" }
                ]}
                className="min-w-[120px] shrink-0"
              />
            </div>
          </div>
          {/* {!loading && !failed && result ? (
            <p className="m-0 text-sm text-text-secondary">共 {result.total} 个</p>
          ) : null} */}
        </section>

        <div className="grid min-h-0 flex-1 grid-cols-[280px_minmax(0,1fr)] gap-6 overflow-hidden max-[960px]:grid-cols-1 max-[960px]:overflow-y-auto">
          <aside
            className="flex max-h-full h-full min-h-0 w-full flex-col self-start overflow-hidden rounded-[12px] border border-border bg-surface-soft px-3 py-3 max-[960px]:max-h-[42vh]"
            aria-label="资源筛选"
            data-testid="resource-filter-sidebar"
          >
            <div className="flex max-h-full flex-col gap-6 overflow-y-auto overscroll-contain pe-1">
              <FilterChipGroup
                title="资源类型"
                chips={TYPE_CHIPS}
                selectedValue={search.resourceType}
                tone={GROUP_TONES.brand}
                onSelect={value => {
                  if (!value || value === ALL_VALUE) return;
                  updateSearch({
                    resourceType: value as ResourceKind,
                    availability: undefined,
                    domain: undefined,
                    scenario: undefined,
                    page: 1
                  });
                }}
              />
              <FilterChipGroup
                title="使用状态"
                chips={AVAILABILITY_CHIPS}
                selectedValue={search.availability}
                tone={GROUP_TONES.success}
                onSelect={value =>
                  updateSearch({
                    availability: value as ResourceAvailability | undefined,
                    page: 1
                  })
                }
              />

              <FilterChipGroup
                title="领域"
                chips={DOMAIN_CHIPS}
                selectedValue={search.domain}
                tone={GROUP_TONES.warning}
                onSelect={value =>
                  updateSearch({
                    domain: value as ResourceDomain | undefined,
                    page: 1
                  })
                }
              />

              <FilterChipGroup
                title="研究场景"
                chips={SCENARIO_CHIPS}
                selectedValue={search.scenario}
                tone={GROUP_TONES.purple}
                onSelect={value =>
                  updateSearch({
                    scenario: value as ResourceScenario | undefined,
                    page: 1
                  })
                }
              />

              <Button type="link" onClick={clearFilters} data-testid="resource-catalog-clear-all">
                清除筛选
              </Button>
            </div>
          </aside>

          <section
            className="flex min-h-0 min-w-0 flex-col overflow-hidden"
            aria-live="polite"
            data-testid="resource-catalog-results"
          >
            <div className="resource-catalog-scroll min-h-0 flex-1 overflow-y-auto overscroll-contain pe-1">
              {loading ? <ResourcePlazaResultState state="loading" /> : null}
              {!loading && failed ? (
                <ResourcePlazaResultState
                  state="safety_error"
                  onRetry={() => setRetryKey(value => value + 1)}
                  onHome={() => navigate("/resources")}
                />
              ) : null}
              {!loading && !failed && result?.total === 0 && hasFilters ? (
                <ResourcePlazaResultState state="empty" onReset={clearFilters} />
              ) : null}
              {!loading && !failed && result?.total === 0 && !hasFilters ? (
                <ResourcePlazaResultState
                  state="no_supply"
                  onReload={() => setRetryKey(value => value + 1)}
                  onHome={() => navigate("/resources")}
                />
              ) : null}
              {!loading && !failed && result && result.total > 0 ? (
                <div className="grid grid-cols-2 2xl:grid-cols-3 items-start gap-x-4 gap-y-6 max-[720px]:grid-cols-1">
                  {result.resources.map(resource => (
                    <CatalogIconCard key={resource.id} resource={resource} onOpen={() => openDetail(resource.id)} />
                  ))}
                </div>
              ) : null}
            </div>
            {!loading && !failed && result && result.total > 0 ? (
              <footer className="flex shrink-0 items-center justify-end pt-3" data-testid="resource-catalog-pagination">
                <Pagination
                  current={search.page}
                  pageSize={search.pageSize}
                  total={result.total}
                  showSizeChanger
                  pageSizeOptions={[10, 20, 50]}
                  showTotal={total => `共 ${total} 项`}
                  onChange={(page, pageSize) => updateSearch({ page, pageSize })}
                />
              </footer>
            ) : null}
          </section>
        </div>
      </div>
    </div>
  );
}
