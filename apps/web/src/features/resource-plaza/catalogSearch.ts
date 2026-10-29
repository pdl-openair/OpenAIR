import { RESOURCE_AVAILABILITIES, RESOURCE_DOMAINS, RESOURCE_KINDS, RESOURCE_SCENARIOS } from "./types";
import type { ResourceAvailability, ResourceDomain, ResourceKind, ResourceScenario } from "./types";

export const RESOURCE_CATALOG_SORTS = ["relevance", "name_asc", "name_desc"] as const;
export type ResourceCatalogSort = (typeof RESOURCE_CATALOG_SORTS)[number];

export interface ResourceCatalogSearchState {
  q: string;
  resourceType?: ResourceKind;
  availability?: ResourceAvailability;
  domain?: ResourceDomain;
  scenario?: ResourceScenario;
  /** When true, only tryable CatalogRelease.visibleOnHome rows are returned (featured shelf). */
  visibleOnHome?: boolean;
  sort: ResourceCatalogSort;
  page: number;
  pageSize: number;
  more: boolean;
}

function positiveInteger(value: string | null, fallback: number, maximum: number): number {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? Math.min(parsed, maximum) : fallback;
}

export function parseResourceCatalogSearch(params: URLSearchParams): ResourceCatalogSearchState {
  const rawType = params.get("resourceType");
  const rawAvailability = params.get("availability");
  const rawDomain = params.get("domain");
  const rawScenario = params.get("scenario");
  const rawSort = params.get("sort");
  return {
    q: (params.get("q") ?? "").trim(),
    resourceType: RESOURCE_KINDS.includes(rawType as ResourceKind) ? rawType as ResourceKind : undefined,
    availability: RESOURCE_AVAILABILITIES.includes(rawAvailability as ResourceAvailability)
      ? rawAvailability as ResourceAvailability
      : undefined,
    domain: RESOURCE_DOMAINS.includes(rawDomain as ResourceDomain) ? rawDomain as ResourceDomain : undefined,
    scenario: RESOURCE_SCENARIOS.includes(rawScenario as ResourceScenario)
      ? rawScenario as ResourceScenario
      : undefined,
    ...(params.get("visibleOnHome") === "1" ? { visibleOnHome: true as const } : {}),
    sort: RESOURCE_CATALOG_SORTS.includes(rawSort as ResourceCatalogSort) ? rawSort as ResourceCatalogSort : "relevance",
    page: positiveInteger(params.get("page"), 1, 100000),
    pageSize: positiveInteger(params.get("pageSize"), 20, 100),
    more: params.get("more") === "1",
  };
}

export function serializeResourceCatalogSearch(state: ResourceCatalogSearchState): URLSearchParams {
  const params = new URLSearchParams();
  if (state.q) params.set("q", state.q);
  if (state.resourceType) params.set("resourceType", state.resourceType);
  if (state.availability) params.set("availability", state.availability);
  if (state.domain) params.set("domain", state.domain);
  if (state.scenario) params.set("scenario", state.scenario);
  if (state.visibleOnHome) params.set("visibleOnHome", "1");
  if (state.sort !== "relevance") params.set("sort", state.sort);
  if (state.page !== 1) params.set("page", String(state.page));
  if (state.pageSize !== 20) params.set("pageSize", String(state.pageSize));
  if (state.more) params.set("more", "1");
  return params;
}

export function buildResourceCatalogApiSearch(state: ResourceCatalogSearchState): URLSearchParams {
  const params = new URLSearchParams();
  if (state.q) params.set("q", state.q);
  if (state.resourceType) params.set("resourceType", state.resourceType);
  if (state.availability) params.set("availability", state.availability);
  if (state.domain) params.set("domain", state.domain);
  if (state.scenario) params.set("scenario", state.scenario);
  if (state.visibleOnHome) params.set("visibleOnHome", "true");
  params.set("sort", state.sort);
  params.set("page", String(state.page));
  params.set("pageSize", String(state.pageSize));
  return params;
}

export function hasResourceCatalogFilters(state: ResourceCatalogSearchState): boolean {
  return Boolean(state.q || state.resourceType || state.availability || state.domain || state.scenario);
}
