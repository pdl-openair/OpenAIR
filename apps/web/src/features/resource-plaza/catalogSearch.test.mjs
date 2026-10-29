import assert from "node:assert/strict";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { createServer } from "vite";

const featureRoot = new URL("./", import.meta.url);

async function loadSearchModule() {
  const server = await createServer({
    root: fileURLToPath(new URL("../../../", featureRoot)),
    configFile: false,
    cacheDir: path.join(tmpdir(), "openair-resource-plaza-search"),
    server: { middlewareMode: true },
    appType: "custom",
  });
  try {
    return await server.ssrLoadModule("/src/features/resource-plaza/catalogSearch.ts");
  } finally {
    await server.close();
  }
}

test("catalog URL state round-trips search, filters, sort, page and expansion", async () => {
  const { parseResourceCatalogSearch, serializeResourceCatalogSearch } = await loadSearchModule();
  const state = {
    q: "证据抽取",
    resourceType: "agent",
    availability: "view_only",
    domain: "cs_research",
    scenario: "problem_modeling",
    sort: "name_desc",
    page: 3,
    pageSize: 10,
    more: true,
  };
  assert.deepEqual(parseResourceCatalogSearch(serializeResourceCatalogSearch(state)), state);
});

test("only BE-01 query parameters are sent to GET /api/resources", async () => {
  const { buildResourceCatalogApiSearch } = await loadSearchModule();
  const params = buildResourceCatalogApiSearch({
    q: "ablation",
    resourceType: "skill",
    availability: "tryable",
    domain: "materials",
    scenario: "solution_design",
    sort: "name_asc",
    page: 2,
    pageSize: 20,
    more: true,
  });
  assert.equal(
    params.toString(),
    "q=ablation&resourceType=skill&availability=tryable&domain=materials&scenario=solution_design&sort=name_asc&page=2&pageSize=20",
  );
  assert.equal(params.get("sort"), "name_asc");
  assert.equal(params.has("more"), false);
});

test("visibleOnHome is forwarded on API search and round-trips in catalog URL state", async () => {
  const { buildResourceCatalogApiSearch, parseResourceCatalogSearch, serializeResourceCatalogSearch } =
    await loadSearchModule();
  const parsed = parseResourceCatalogSearch(
    serializeResourceCatalogSearch({
      q: "",
      sort: "relevance",
      page: 1,
      pageSize: 100,
      more: false,
      visibleOnHome: true,
    }),
  );
  assert.equal(parsed.visibleOnHome, true);
  assert.equal(parsed.pageSize, 100);
  const params = buildResourceCatalogApiSearch({
    q: "",
    sort: "relevance",
    page: 1,
    pageSize: 100,
    more: false,
    visibleOnHome: true,
  });
  assert.equal(params.get("visibleOnHome"), "true");
  assert.equal(params.get("pageSize"), "100");
});
test("unknown URL enum values are discarded instead of guessed", async () => {
  const { parseResourceCatalogSearch } = await loadSearchModule();
  const state = parseResourceCatalogSearch(new URLSearchParams("resourceType=tool&availability=ready_enough&page=-2&pageSize=999"));
  assert.equal(state.resourceType, undefined);
  assert.equal(state.availability, undefined);
  assert.equal(state.page, 1);
  assert.equal(state.pageSize, 100);
});
