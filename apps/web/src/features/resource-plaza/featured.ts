import type { ResourceCatalogSearchState } from "./catalogSearch";
import { fetchResourceCatalog } from "./api";
import type { ResourcePlazaResource } from "./types";

export {
  FEATURED_KIND_LABELS,
  FEATURED_PER_KIND,
  featuredKindOrder,
  flattenFeaturedByKind,
  isFeaturedTryable,
  pickFeaturedByKind,
} from "./featuredPick";

/**
 * Fetch featured candidates: visibleOnHome ∩ tryable (server + client).
 * Paginates until the filtered total is exhausted (no silent row cap).
 */
export async function fetchFeaturedCatalogResources(
  signal?: AbortSignal,
): Promise<ResourcePlazaResource[]> {
  const pageSize = 100;
  let page = 1;
  let total = Number.POSITIVE_INFINITY;
  const collected: ResourcePlazaResource[] = [];

  while (collected.length < total) {
    const state: ResourceCatalogSearchState = {
      q: "",
      visibleOnHome: true,
      availability: "tryable",
      sort: "relevance",
      page,
      pageSize,
      more: false,
    };
    const result = await fetchResourceCatalog(state, signal);
    collected.push(...result.resources);
    total = result.total;
    if (result.resources.length === 0 || collected.length >= total) break;
    page += 1;
  }

  return collected;
}
