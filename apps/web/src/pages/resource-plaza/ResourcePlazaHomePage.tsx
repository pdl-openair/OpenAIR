import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Search } from "lucide-react";
import { Button, Input } from "antd";
import { FeaturedBentoCard } from "../../components/resource-plaza/FeaturedBentoCard";
import { ResourcePlazaResultState } from "../../components/resource-plaza";
import {
  FEATURED_KIND_LABELS,
  FEATURED_PER_KIND,
  featuredKindOrder,
  fetchFeaturedCatalogResources,
  flattenFeaturedByKind,
  isFeaturedTryable,
  pickFeaturedByKind,
  serializeResourceCatalogSearch
} from "../../features/resource-plaza";
import type { ResourceKind, ResourcePlazaResource } from "../../features/resource-plaza/types";

function matchesFeaturedKeyword(resource: ResourcePlazaResource, needle: string): boolean {
  if (!needle) return true;
  return resource.name.toLowerCase().includes(needle);
}

function bucketByKind(resources: readonly ResourcePlazaResource[]): Record<ResourceKind, ResourcePlazaResource[]> {
  const buckets: Record<ResourceKind, ResourcePlazaResource[]> = {
    skill: [],
    agent: [],
    workflow: []
  };
  for (const resource of resources) {
    if (!isFeaturedTryable(resource)) continue;
    buckets[resource.kind].push(resource);
  }
  return buckets;
}

export default function ResourcePlazaHomePage() {
  const navigate = useNavigate();
  const [keyword, setKeyword] = useState("");
  const [appliedKeyword, setAppliedKeyword] = useState("");
  const [resources, setResources] = useState<ResourcePlazaResource[]>([]);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setFailed(false);
    fetchFeaturedCatalogResources(controller.signal)
      .then(setResources)
      .catch(error => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setFailed(true);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [retryKey]);

  const needle = appliedKeyword.trim().toLowerCase();
  const isSearching = Boolean(needle);

  const featuredBuckets = useMemo(() => {
    const matched = resources.filter(resource => matchesFeaturedKeyword(resource, needle));
    // 默认精选：每类最多 FEATURED_PER_KIND；搜索时在精选候选内展示全部命中，不跳转公共资源。
    if (isSearching) return bucketByKind(matched);
    return pickFeaturedByKind(matched, FEATURED_PER_KIND);
  }, [resources, needle, isSearching]);

  const featuredFlat = useMemo(() => flattenFeaturedByKind(featuredBuckets), [featuredBuckets]);
  const hasFeatured = featuredFlat.length > 0;
  const hasAnyHomeResources = resources.length > 0;

  const openCatalog = () => {
    const params = serializeResourceCatalogSearch({
      q: "",
      sort: "relevance",
      page: 1,
      pageSize: 20,
      more: false
    });
    navigate(`/resources/catalog${params.size ? `?${params.toString()}` : ""}`);
  };

  const applySearch = (value?: string) => {
    setAppliedKeyword((value ?? keyword).trim());
  };

  const clearSearch = () => {
    setKeyword("");
    setAppliedKeyword("");
  };

  let toneCursor = 0;

  return (
    <div className="h-full w-full" data-testid="resource-plaza-home-page">
      <div className="flex h-full w-full flex-col gap-10 rounded-xl bg-surface p-6">
        <section
          className="flex items-end justify-between gap-4 max-[720px]:flex-col max-[720px]:items-stretch"
          data-testid="resource-home-search"
        >
          <div className="min-w-0">
            <h2 className="m-0 text-xl font-semibold text-text">精选资源</h2>
            <p className="m-0 mt-1 text-sm text-text-secondary">从精选智能体、工作流与技能中快速发现可用能力</p>
          </div>
          <div className="w-full max-w-[320px] shrink-0 max-[720px]:max-w-none">
            <Input
              allowClear
              value={keyword}
              onChange={event => setKeyword(event.target.value)}
              onPressEnter={() => applySearch(keyword)}
              onClear={clearSearch}
              prefix={<Search size={16} aria-hidden className="text-text-muted" />}
              placeholder="搜索精选资源"
              aria-label="在精选资源中搜索"
            />
          </div>
        </section>

        <section
          className="flex flex-col items-center gap-20 flex-1 min-h-0 overflow-scroll"
          data-testid="resource-home-featured"
        >
          {loading ? <ResourcePlazaResultState state="loading" /> : null}
          {!loading && failed ? (
            <ResourcePlazaResultState
              state="safety_error"
              onRetry={() => setRetryKey(value => value + 1)}
              onHome={() => navigate("/")}
            />
          ) : null}
          {!loading && !failed && !hasAnyHomeResources ? (
            <div className="w-full px-6 py-8 my-auto text-center">
              <h3 className="m-0 text-base font-semibold text-text">当前暂无精选资源</h3>
              <p className="mb-4 mt-2 text-sm text-text-secondary">可进入公共资源查看全部已发布资源。</p>
              <Button type="primary" onClick={openCatalog}>
                进入公共资源
              </Button>
            </div>
          ) : null}
          {!loading && !failed && hasAnyHomeResources && isSearching && !hasFeatured ? (
            <div className="w-full px-6 py-8 my-auto text-center" data-testid="resource-home-search-empty">
              <h3 className="m-0 text-base font-semibold text-text">精选中未找到匹配资源</h3>
              <p className="mb-4 mt-2 text-sm text-text-secondary">
                当前仅在精选范围内过滤。可清空关键词，或前往公共资源查看全部。
              </p>
              <div className="flex flex-wrap items-center justify-center gap-2">
                <Button onClick={clearSearch}>清空搜索</Button>
                <Button type="primary" onClick={openCatalog}>
                  进入公共资源
                </Button>
              </div>
            </div>
          ) : null}

          {!loading && !failed && hasFeatured
            ? featuredKindOrder().map(kind => {
                const items = featuredBuckets[kind];
                if (items.length === 0) return null;
                return (
                  <div
                    key={kind}
                    className="flex w-full flex-col gap-10"
                    data-testid={`resource-home-featured-${kind}`}
                  >
                    <h3 className="m-0 text-center text-xl font-semibold text-text">{FEATURED_KIND_LABELS[kind]}</h3>
                    <div className="grid w-full grid-cols-3 2xl:grid-cols-4 items-start gap-x-6 gap-y-8 max-[960px]:grid-cols-2 max-[640px]:grid-cols-1">
                      {items.map(resource => {
                        const toneIndex = toneCursor;
                        toneCursor += 1;
                        return (
                          <FeaturedBentoCard
                            key={resource.id}
                            resource={resource}
                            toneIndex={toneIndex}
                            onOpen={() => navigate(`/resources/catalog/${encodeURIComponent(resource.id)}?origin=home`)}
                          />
                        );
                      })}
                    </div>
                  </div>
                );
              })
            : null}
        </section>
      </div>
    </div>
  );
}
