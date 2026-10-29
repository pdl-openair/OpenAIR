import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  ArrowLeft,
  Bot,
  CircleAlert,
  GitBranch,
  Heart,
  Inbox,
  Lightbulb,
  Package,
  WandSparkles,
  type LucideIcon
} from "lucide-react";
import { Button, Typography, message } from "antd";
import { ResourcePlazaResultState } from "../../components/resource-plaza";
import { createAgentSession } from "../../features/agent-session/api";
import {
  fetchResourceCatalogItem,
  useResourceSaveState
} from "../../features/resource-plaza";
import { projectResourceDetail } from "../../features/resource-plaza/detailProjection";
import type {
  ResourceDetailFactGroup,
  ResourceDetailProgressiveSection
} from "../../features/resource-plaza/detailProjection";
import type { ResourcePlazaResource } from "../../features/resource-plaza/types";
import { cn } from "../../lib/cn";

const KIND_TONES = {
  agent: { shell: "bg-brand-soft", icon: "bg-brand text-surface" },
  workflow: { shell: "bg-success-soft", icon: "bg-success text-surface" },
  skill: { shell: "bg-warning-soft", icon: "bg-warning text-surface" }
} as const;

const KIND_ICONS = {
  agent: Bot,
  skill: WandSparkles,
  workflow: GitBranch
} as const;

function FactList({ values, hasMore = false }: { values: string[]; hasMore?: boolean }) {
  return (
    <>
      <ul className="m-0 list-disc space-y-1 pl-[18px]">
        {values.map((value, index) => (
          <li key={`${index}-${value}`}>{value}</li>
        ))}
      </ul>
      {hasMore ? <p className="mb-0 mt-1 text-xs text-text-muted">完整内容可在下方展开查看。</p> : null}
    </>
  );
}

function JudgmentItem({ label, icon: Icon, children }: { label: string; icon: LucideIcon; children: ReactNode }) {
  return (
    <div className="rounded-[14px] bg-surface-soft px-4 py-4">
      <div className="mb-2 flex items-center gap-2">
        <span className="inline-flex h-7 w-7 items-center justify-center rounded-lg bg-surface text-text-secondary">
          <Icon size={14} aria-hidden />
        </span>
        <p className="m-0 text-[13px] font-semibold text-text">{label}</p>
      </div>
      <div className="text-[13px] leading-6 text-text-secondary pl-9">{children}</div>
    </div>
  );
}

function ProgressiveFactGroup({ group }: { group: ResourceDetailFactGroup }) {
  return (
    <div className="rounded-lg bg-surface-soft px-[14px] py-3">
      <p className="m-0 text-[13px] font-semibold text-text">{group.label}</p>
      <ul className="mb-0 mt-1 list-disc space-y-1 pl-[18px] text-xs leading-5 text-text-secondary">
        {group.values.map((value, index) => (
          <li key={`${index}-${value}`}>{value}</li>
        ))}
      </ul>
    </div>
  );
}

function ProgressiveSectionContent({ section }: { section: ResourceDetailProgressiveSection }) {
  return (
    <div data-testid={`resource-detail-progressive-${section.key}`}>
      {section.body ? (
        <div className="rounded-lg bg-surface-soft px-[14px] py-3 w-[calc((100%-0.75rem)/2)]">
          <p className="m-0 mb-1 text-[13px] font-semibold text-text">{section.title}</p>
          <Typography.Paragraph className="mb-0! whitespace-pre-wrap break-words text-xs! leading-6 text-text-secondary!">
            {section.body}
          </Typography.Paragraph>
        </div>
      ) : null}
      {section.groups.length ? (
        <div className={`grid grid-cols-2 gap-3 max-[760px]:grid-cols-1 ${section.body ? "mt-3" : ""}`}>
          {section.groups.map(group => (
            <ProgressiveFactGroup key={group.label} group={group} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

export default function ResourcePlazaDetailHandoffPage() {
  const { resourceId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [resource, setResource] = useState<ResourcePlazaResource | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [retryKey, setRetryKey] = useState(0);
  const [starting, setStarting] = useState(false);
  const origin = searchParams.get("origin");
  const catalogParams = new URLSearchParams(searchParams);
  catalogParams.delete("origin");
  const catalogQuery = catalogParams.toString();
  const querySuffix = catalogQuery ? `?${catalogQuery}` : "";
  const backTarget =
    origin === "home" ? "/resources" : origin === "mine" ? "/resources/mine" : `/resources/catalog${querySuffix}`;
  const backLabel = origin === "home" ? "返回资源广场" : origin === "mine" ? "返回我的资源" : "返回搜索结果";
  const backToCatalog = () => navigate(backTarget);
  const saveIds = useMemo(() => (resource?.id ? [resource.id] : []), [resource?.id]);
  const { isSaved, isPending, toggleSave } = useResourceSaveState(saveIds);

    const startPlazaSession = async () => {
    if (starting || !resourceId.trim()) return;
    setStarting(true);
    try {
      const session = await createAgentSession(resourceId);
      navigate(`/agent-sessions/${encodeURIComponent(session.sessionId)}?from=resource`);
    } catch {
      message.error("无法开始试用，请稍后重试。");
    } finally {
      setStarting(false);
    }
  };

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setFailed(false);
    fetchResourceCatalogItem(resourceId, controller.signal)
      .then(value => {
        setResource(value);
        setFailed(value === null);
      })
      .catch(error => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setFailed(true);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [resourceId, retryKey]);

  if (loading) {
    return (
      <div className="h-full w-full">
        <div className="h-full w-full rounded-xl bg-surface p-6">
          <ResourcePlazaResultState state="loading" />
        </div>
      </div>
    );
  }
  if (failed || !resource) {
    return (
      <div className="h-full w-full">
        <div className="h-full w-full rounded-xl bg-surface p-6">
          <ResourcePlazaResultState
            state="load_error"
            description="该资源的详情暂时无法读取。"
            onRetry={() => setRetryKey(value => value + 1)}
            onHome={backToCatalog}
            homeLabel={backLabel}
          />
        </div>
      </div>
    );
  }

  const detail = projectResourceDetail(resource);
  const saved = isSaved(resource.id);
  const savePending = isPending(resource.id);
  const tone = KIND_TONES[resource.kind];
  const KindIcon = KIND_ICONS[resource.kind];

  return (
    <div className="h-full w-full flex flex-col gap-4" data-testid="resource-detail-handoff">
      <Button type="link" className="self-start px-0! flex-none" icon={<ArrowLeft size={15} />} onClick={backToCatalog}>
        {backLabel}
      </Button>
      <div className="flex flex-1 min-h-0 w-full flex-col gap-8 overflow-y-auto rounded-xl bg-surface p-6">
        <header className={cn("rounded-[20px] p-5 max-[720px]:p-4", tone.shell)}>
          <div className="flex items-start justify-between gap-5 max-[720px]:flex-col max-[720px]:items-stretch">
            <div className="flex min-w-0 flex-1 items-start gap-4">
              <span
                className={cn("inline-flex h-12 w-12 shrink-0 items-center justify-center rounded-[14px]", tone.icon)}
              >
                <KindIcon size={22} aria-hidden />
              </span>
              <div className="min-w-0 flex-1">
                <Typography.Title level={1} className="mb-2! mt-0! text-lg! leading-9! text-text!">
                  {resource.name || "未命名资源"}
                </Typography.Title>
                <Typography.Paragraph className="mb-0! text-sm! leading-7 text-text-secondary">
                  {resource.description}
                </Typography.Paragraph>
                {!detail.canStart ? (
                  <p className="mb-0 mt-2 text-[13px] leading-6 text-text-secondary">
                    当前可查看研究说明，暂不能在平台内直接使用。
                  </p>
                ) : null}
              </div>
            </div>
            <div
              className="flex shrink-0 items-center gap-3 max-[720px]:w-full"
              data-testid="resource-detail-action-panel"
            >
              <Button
                className="max-[720px]:flex-1"
                type="primary"
                loading={starting}
                disabled={!detail.canStart || starting}
                onClick={() => void startPlazaSession()}
                data-testid="resource-use-capability"
              >
                {starting ? "正在准备" : "立即使用"}
              </Button>
              <Button
                className="max-[720px]:flex-1"
                icon={saved ? <Heart size={16} className="fill-current" /> : <Heart size={16} />}
                loading={savePending}
                disabled={savePending}
                onClick={() => void toggleSave(resource.id, !saved)}
                data-testid="resource-detail-save"
              >
                {saved ? "已收藏" : "收藏"}
              </Button>
            </div>
          </div>
        </header>

        <section>
          <h2 className="m-0 text-xl font-semibold text-text">研究要点</h2>
          <div className="mt-4 grid grid-cols-2 gap-4 max-[700px]:grid-cols-1">
            <JudgmentItem label="适合什么" icon={Lightbulb}>
              <FactList values={detail.suitability.scenarios} hasMore={detail.suitability.hasMore} />
              <p className="mb-0 mt-2 text-xs text-text-muted">研究领域：{detail.suitability.domains.join("、")}</p>
            </JudgmentItem>
            <JudgmentItem label="需要提供什么" icon={Inbox}>
              <FactList values={detail.inputs.values} hasMore={detail.inputs.hasMore} />
            </JudgmentItem>
            <JudgmentItem label="将得到什么" icon={Package}>
              <FactList values={detail.outputs.values} hasMore={detail.outputs.hasMore} />
            </JudgmentItem>
            <JudgmentItem label="使用提示" icon={CircleAlert}>
              <p className="m-0">{detail.criticalLimitation}</p>
            </JudgmentItem>
          </div>
        </section>

        {detail.progressiveSections.length ? (
          <section data-testid="resource-detail-progressive-disclosure">
            <div className="mb-3">
              <h2 className="m-0 text-xl font-semibold text-text">研究说明</h2>
            </div>
            <div className="flex flex-col gap-4">
              {detail.progressiveSections.map(section => (
                <ProgressiveSectionContent section={section} />
              ))}
            </div>
          </section>
        ) : null}
      </div>
    </div>
  );
}
