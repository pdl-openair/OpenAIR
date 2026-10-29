import type { ResourceKind, ResourcePlazaResource } from "./types";
import { RESOURCE_KINDS } from "./types";

/** 每个 resourceType 在精选页最多展示条数（与 CatalogRelease.visibleOnHome 门控对齐）。 */
export const FEATURED_PER_KIND = 8;

const FEATURED_KIND_ORDER: ResourceKind[] = ["agent", "skill", "workflow"];

export const FEATURED_KIND_LABELS: Record<ResourceKind, string> = {
  skill: "精选技能",
  agent: "精选智能体",
  workflow: "精选工作流",
};

/** 精选货架只收可试用资源（与 CatalogRelease.visibleOnHome 产品口径一致）。 */
export function isFeaturedTryable(resource: ResourcePlazaResource): boolean {
  return (
    resource.visibleOnHome === true &&
    resource.availability === "tryable" &&
    resource.executionBlocked !== true &&
    resource.primaryAction === "try"
  );
}

/**
 * 从目录页结果中按类型各取至多 FEATURED_PER_KIND 条精选可试用资源。
 * 顺序：agent → skill → workflow；同类型内保持目录返回顺序。
 * 注意：前端资源对象字段是 ``kind``（不是 API 的 resourceType）。
 */
export function pickFeaturedByKind(
  resources: readonly ResourcePlazaResource[],
  perKind: number = FEATURED_PER_KIND,
): Record<ResourceKind, ResourcePlazaResource[]> {
  const buckets: Record<ResourceKind, ResourcePlazaResource[]> = {
    skill: [],
    agent: [],
    workflow: [],
  };
  for (const resource of resources) {
    if (!isFeaturedTryable(resource)) continue;
    if (!RESOURCE_KINDS.includes(resource.kind)) continue;
    const bucket = buckets[resource.kind];
    if (bucket.length >= perKind) continue;
    bucket.push(resource);
  }
  return buckets;
}

export function flattenFeaturedByKind(
  buckets: Record<ResourceKind, ResourcePlazaResource[]>,
): ResourcePlazaResource[] {
  return FEATURED_KIND_ORDER.flatMap(kind => buckets[kind]);
}

export function featuredKindOrder(): readonly ResourceKind[] {
  return FEATURED_KIND_ORDER;
}
