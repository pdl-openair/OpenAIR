import { buildAuthHeaders, getApiBaseUrl } from "../../api/http";
import { adaptResourceCatalogDto } from "./adapter";
import { buildResourceCatalogApiSearch } from "./catalogSearch";
import type { ResourceCatalogSearchState } from "./catalogSearch";
import type {
  ResearchStartIntentResult,
  ResearchStartIntentPromoteResult,
  ResourceCatalogItemDto,
  ResourceCatalogListDto,
  ResourceCatalogPage,
  ResourcePlazaResource,
  ResourceSaveMutationResult,
  ResourceSaveStatusResult,
  ResourceSavesPage,
} from "./types";

export class ResourceCatalogRequestError extends Error {
  constructor(public readonly status: number, message?: string) {
    super(message ?? (status === 503 ? "resource_catalog_unavailable" : "resource_catalog_request_failed"));
    this.name = "ResourceCatalogRequestError";
  }
}

function isCatalogItem(value: unknown): value is ResourceCatalogItemDto {
  if (!value || typeof value !== "object") return false;
  const item = value as ResourceCatalogItemDto;
  return typeof item.id === "string" && Boolean(item.id.trim()) && typeof item.name === "string" && Boolean(item.name.trim());
}

function isCatalogListItem(value: unknown): value is ResourceCatalogItemDto {
  return isCatalogItem(value) && value.visibleInCatalog === true;
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

async function readJsonResponse(response: Response): Promise<unknown> {
  if (!response.ok) {
    await response.text().catch(() => "");
    throw new ResourceCatalogRequestError(response.status);
  }
  try {
    return await response.json();
  } catch {
    throw new ResourceCatalogRequestError(502);
  }
}

export async function fetchResourceCatalog(
  search: ResourceCatalogSearchState,
  signal?: AbortSignal,
): Promise<ResourceCatalogPage> {
  const params = buildResourceCatalogApiSearch(search);
  const response = await fetch(`${getApiBaseUrl()}/api/resources?${params.toString()}`, {
    headers: buildAuthHeaders(),
    signal,
  });
  const payload = await readJsonResponse(response) as ResourceCatalogListDto;
  if (
    !payload ||
    typeof payload !== "object" ||
    !Array.isArray(payload.resources) ||
    !payload.resources.every(isCatalogListItem) ||
    !isNonNegativeInteger(payload.total) ||
    !isNonNegativeInteger(payload.page) ||
    !isNonNegativeInteger(payload.pageSize)
  ) {
    throw new ResourceCatalogRequestError(502);
  }
  return {
    resources: payload.resources.map(adaptResourceCatalogDto),
    total: payload.total,
    page: payload.page,
    pageSize: payload.pageSize,
  };
}

export async function fetchResourceCatalogItem(resourceId: string, signal?: AbortSignal): Promise<ResourcePlazaResource | null> {
  const response = await fetch(`${getApiBaseUrl()}/api/resources/${encodeURIComponent(resourceId)}`, {
    headers: buildAuthHeaders(),
    signal,
  });
  if (response.status === 404) {
    await response.text().catch(() => "");
    return null;
  }
  const payload = await readJsonResponse(response);
  if (!isCatalogItem(payload) || payload.visibleInDetail !== true) throw new ResourceCatalogRequestError(502);
  return adaptResourceCatalogDto(payload);
}

function isSaveMutationResult(value: unknown): value is ResourceSaveMutationResult {
  if (!value || typeof value !== "object") return false;
  const item = value as ResourceSaveMutationResult;
  return typeof item.resourceId === "string" && Boolean(item.resourceId.trim()) && typeof item.saved === "boolean";
}

function isSaveStatusResult(value: unknown): value is ResourceSaveStatusResult {
  if (!value || typeof value !== "object") return false;
  const item = value as ResourceSaveStatusResult;
  return Array.isArray(item.savedResourceIds) && item.savedResourceIds.every(id => typeof id === "string" && Boolean(id.trim()));
}

export async function fetchResourceSaves(
  options: { page?: number; pageSize?: number; signal?: AbortSignal } | AbortSignal = {},
): Promise<ResourceSavesPage> {
  // Back-compat: callers historically passed AbortSignal as the sole argument.
  const normalized =
    options instanceof AbortSignal ? { signal: options } : options ?? {};
  const page = Math.max(1, normalized.page ?? 1);
  const pageSize = Math.min(100, Math.max(1, normalized.pageSize ?? 24));
  const params = new URLSearchParams({
    page: String(page),
    pageSize: String(pageSize),
  });
  const response = await fetch(`${getApiBaseUrl()}/api/resource-saves?${params.toString()}`, {
    headers: buildAuthHeaders(),
    signal: normalized.signal,
  });
  const payload = await readJsonResponse(response);
  if (!payload || typeof payload !== "object") throw new ResourceCatalogRequestError(502);
  const body = payload as { items?: unknown; page?: unknown; pageSize?: unknown; total?: unknown };
  if (
    !Array.isArray(body.items) ||
    !body.items.every(isCatalogItem) ||
    !isNonNegativeInteger(body.page) ||
    !isNonNegativeInteger(body.pageSize) ||
    !isNonNegativeInteger(body.total)
  ) {
    throw new ResourceCatalogRequestError(502);
  }
  return {
    items: body.items.map(adaptResourceCatalogDto),
    page: body.page,
    pageSize: body.pageSize,
    total: body.total,
  };
}

/** Walk server pages until every saved resource is loaded (API max pageSize = 100). */
export async function fetchAllResourceSaves(signal?: AbortSignal): Promise<ResourcePlazaResource[]> {
  const pageSize = 100;
  let page = 1;
  const items: ResourcePlazaResource[] = [];
  let total = Number.POSITIVE_INFINITY;
  while (items.length < total) {
    const batch = await fetchResourceSaves({ page, pageSize, signal });
    items.push(...batch.items);
    total = batch.total;
    if (batch.items.length === 0) break;
    if (batch.items.length < pageSize) break;
    page += 1;
  }
  return items;
}

export async function fetchResourceSaveStatus(resourceIds: string[], signal?: AbortSignal): Promise<ResourceSaveStatusResult> {
  const uniqueIds = [...new Set(resourceIds.map(id => id.trim()).filter(Boolean))];
  if (uniqueIds.length === 0) return { savedResourceIds: [] };
  const params = new URLSearchParams();
  for (const id of uniqueIds) params.append("resourceId", id);
  const response = await fetch(`${getApiBaseUrl()}/api/resource-saves/status?${params.toString()}`, {
    headers: buildAuthHeaders(),
    signal,
  });
  const payload = await readJsonResponse(response);
  if (!isSaveStatusResult(payload)) throw new ResourceCatalogRequestError(502);
  return payload;
}

export async function saveResource(resourceId: string, signal?: AbortSignal): Promise<ResourceSaveMutationResult> {
  const response = await fetch(`${getApiBaseUrl()}/api/resource-saves/${encodeURIComponent(resourceId)}`, {
    method: "PUT",
    headers: buildAuthHeaders(),
    signal,
  });
  const payload = await readJsonResponse(response);
  if (!isSaveMutationResult(payload) || payload.resourceId !== resourceId || payload.saved !== true) {
    throw new ResourceCatalogRequestError(502);
  }
  return payload;
}

export async function unsaveResource(resourceId: string, signal?: AbortSignal): Promise<ResourceSaveMutationResult> {
  const response = await fetch(`${getApiBaseUrl()}/api/resource-saves/${encodeURIComponent(resourceId)}`, {
    method: "DELETE",
    headers: buildAuthHeaders(),
    signal,
  });
  const payload = await readJsonResponse(response);
  if (!isSaveMutationResult(payload) || payload.resourceId !== resourceId || payload.saved !== false) {
    throw new ResourceCatalogRequestError(502);
  }
  return payload;
}

function isResearchStartIntentResult(value: unknown): value is ResearchStartIntentResult {
  if (!value || typeof value !== "object") return false;
  const item = value as ResearchStartIntentResult;
  return (
    typeof item.intentId === "string" &&
    Boolean(item.intentId.trim()) &&
    typeof item.researchStartUrl === "string" &&
    Boolean(item.researchStartUrl.trim())
  );
}

export async function createResearchStartIntent(resourceId: string, signal?: AbortSignal): Promise<ResearchStartIntentResult> {
  const response = await fetch(`${getApiBaseUrl()}/api/resources/${encodeURIComponent(resourceId)}/research-start-intent`, {
    method: "POST",
    headers: buildAuthHeaders(),
    signal,
  });
  const payload = await readJsonResponse(response);
  if (!isResearchStartIntentResult(payload)) throw new ResourceCatalogRequestError(502, "research_start_intent_invalid");
  return payload;
}

function isResearchStartIntentPromoteResult(value: unknown): value is ResearchStartIntentPromoteResult {
  if (!value || typeof value !== "object") return false;
  const item = value as ResearchStartIntentPromoteResult;
  return (
    /^intent_[0-9a-f]{12}$/.test(item.sessionId) &&
    typeof item.selectedResourceId === "string" &&
    Boolean(item.selectedResourceId.trim()) &&
    typeof item.selectedResourceName === "string" &&
    Boolean(item.selectedResourceName.trim()) &&
    (item.selectedResourceKind === "skill" ||
      item.selectedResourceKind === "agent" ||
      item.selectedResourceKind === "workflow")
  );
}

export async function promoteResearchStartIntent(
  catalogIntentId: string,
  signal?: AbortSignal,
): Promise<ResearchStartIntentPromoteResult> {
  const response = await fetch(
    `${getApiBaseUrl()}/api/resources/research-start-intents/${encodeURIComponent(catalogIntentId)}/promote`,
    {
      method: "POST",
      headers: buildAuthHeaders(),
      signal,
    },
  );
  const payload = await readJsonResponse(response);
  if (!isResearchStartIntentPromoteResult(payload)) {
    throw new ResourceCatalogRequestError(502, "research_start_intent_promote_invalid");
  }
  return payload;
}
