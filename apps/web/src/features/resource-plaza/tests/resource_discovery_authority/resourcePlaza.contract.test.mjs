import assert from "node:assert/strict";
import { test } from "vitest";
import { adaptResourceCatalogDto } from "../../adapter.ts";

const releaseFacts = {
  releaseVersion: "1.0.0",
  visibleOnHome: true,
  visibleInCatalog: true,
  visibleInDetail: true,
  usagePolicy: "仅限受控查看",
  licenseSummary: "仅限受控查看",
  evidenceStatus: "已声明验证事实",
};

test("catalog ownership facts are preserved and undeclared release facts do not block discovery", async () => {
  const catalogItem = {
    id: "catalog-skill-1",
    name: "证据整理",
    kind: "skill",
    source: "资源目录",
    maintainer: "能力维护组",
    availability: "tryable",
    executionBlocked: false,
    primaryAction: "try",
    ...releaseFacts,
  };
  const resource = adaptResourceCatalogDto(catalogItem);
  assert.equal(resource.source, catalogItem.source);
  assert.equal(resource.maintainer, catalogItem.maintainer);
  assert.equal(resource.availability, "tryable");
  assert.equal(resource.safetyRisk, undefined);

  const undeclared = adaptResourceCatalogDto({
    ...catalogItem,
    source: undefined,
    maintainer: undefined,
    releaseVersion: undefined,
    licenseSummary: undefined,
    evidenceStatus: undefined,
  });
  assert.equal(undeclared.availability, "tryable");
  assert.equal(undeclared.safetyRisk, undefined);
  assert.equal(undeclared.releaseVersion, "");
});

test("BE-01 aliases are adapted and unknown enums fail closed", async () => {
  const backendItem = {
    id: "skill:real",
    name: "真实资源",
    resourceType: "skill",
    input: ["研究问题"],
    output: ["研究边界"],
    requirements: ["已登录", "具备权限"],
    limitations: ["不替代人工判断"],
    source: "OpenAIR 官方",
    maintainer: "OpenAIR 官方",
    availability: "tryable",
    executionBlocked: false,
    primaryAction: "try",
    ...releaseFacts,
  };
  const adapted = adaptResourceCatalogDto(backendItem);
  assert.deepEqual(adapted.inputs, backendItem.input);
  assert.deepEqual(adapted.outputs, backendItem.output);
  assert.equal(adapted.usageConditions, "已登录；具备权限");
  assert.equal(adapted.limitations, "不替代人工判断");
  assert.equal(adapted.availability, "tryable");

  for (const unsafe of [
    { ...backendItem, resourceType: "tool" },
    { ...backendItem, availability: "maybe_ready" },
    { ...backendItem, primaryAction: "view" },
    { ...backendItem, executionBlocked: true },
  ]) {
    const resource = adaptResourceCatalogDto(unsafe);
    assert.equal(resource.availability, "unavailable");
    assert.equal(resource.safetyRisk, "execution_blocked");
  }
});

test("runtime status fields are preserved without deriving execution state", async () => {
  const resource = adaptResourceCatalogDto({
    id: "skill:runtime-ready-later",
    name: "Runtime status consumer",
    resourceType: "skill",
    availability: "view_only",
    executionBlocked: true,
    primaryAction: "view",
    capabilityId: "capability:runtime-ready-later",
    durableExecutable: true,
    launchMode: "task_context",
    dependencyGaps: ["provider configuration"],
    runtimeFamily: "durable",
  });
  assert.equal(resource.capabilityId, "capability:runtime-ready-later");
  assert.equal(resource.durableExecutable, true);
  assert.equal(resource.launchMode, "task_context");
  assert.deepEqual(resource.dependencyGaps, ["provider configuration"]);
  assert.equal(resource.availability, "view_only");
});

test("DATA-01C view-only release remains view-only while execution is blocked", async () => {
  const resource = adaptResourceCatalogDto({
    id: "skill:title_abstract_screening",
    name: "标题摘要筛选",
    resourceType: "skill",
    source: "OpenAIR 官方",
    maintainer: "OpenAIR 官方",
    availability: "view_only",
    executionBlocked: true,
    primaryAction: "view",
    ...releaseFacts,
  });
  assert.equal(resource.availability, "view_only");
  assert.equal(resource.primaryAction, "view");
  assert.equal(resource.executionBlocked, true);
  assert.equal(resource.safetyRisk, undefined);
});
