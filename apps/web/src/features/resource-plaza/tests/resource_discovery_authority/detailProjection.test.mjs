import assert from "node:assert/strict";
import { test } from "vitest";
import { projectResourceDetail } from "../../detailProjection.ts";

function resource(overrides = {}) {
  return {
    id: "skill:detail-test",
    name: "详情测试能力",
    kind: "skill",
    description: "帮助科研用户整理输入并形成可复查结果。",
    inputs: ["研究目标"],
    outputs: ["结果摘要"],
    trustSummary: "",
    usageConditions: "",
    limitations: "不替代人工判断。",
    source: "OpenAIR 官方",
    maintainer: "OpenAIR 官方",
    releaseVersion: "1.0.0",
    visibleOnHome: true,
    visibleInCatalog: true,
    visibleInDetail: true,
    usagePolicy: "仅限受控使用",
    licenseSummary: "仅限受控使用",
    evidenceStatus: "已声明验证事实",
    evidenceConflictDisposition: "",
    configurationRequirements: [],
    permissionRequirements: [],
    executionEntry: undefined,
    availability: "tryable",
    executionBlocked: false,
    primaryAction: "try",
    safetyRisk: undefined,
    capabilityId: "capability:detail-test",
    durableExecutable: true,
    launchMode: "task_context",
    dependencyGaps: [],
    unavailableReason: undefined,
    runtimeFamily: "durable",
    domains: ["general"],
    scenarios: ["problem_modeling"],
    discoveryScenes: ["research_materials"],
    ...overrides,
  };
}

test("long description uses a bounded extracted summary while preserving the complete original", async () => {
  const original = `${"This capability coordinates a technically detailed workflow with explicit inputs, remote execution constraints, intermediate checks, and handoff requirements ".repeat(3)}. A second sentence remains available.`;
  const projection = projectResourceDetail(resource({ description: original }));
  const descriptionSection = projection.progressiveSections.find(
    (section) => section.key === "description",
  );

  assert.notEqual(projection.valueSummary, original);
  assert.equal(descriptionSection?.body, original);
});

test("a startable resource suppresses the obsolete no-runtime-entry limitation without hiding real limits", async () => {
  const projection = projectResourceDetail(
    resource({
      limitations: "尚未声明可独立验证的用户运行入口。；不能替代人工复核。",
    }),
  );
  const serialized = JSON.stringify(projection);

  assert.equal(projection.canStart, true);
  assert.equal(projection.criticalLimitation, "不能替代人工复核。");
  assert.doesNotMatch(serialized, /尚未声明可独立验证的用户运行入口/);
});

test("dependency gaps and execution blocks still prevent start without exposing internal reasons", async () => {
  const missingDependency = projectResourceDetail(
    resource({ dependencyGaps: ["缺少远程执行服务"] }),
  );
  const blocked = projectResourceDetail(resource({ executionBlocked: true }));

  assert.equal(missingDependency.canStart, false);
  assert.equal(blocked.canStart, false);
});

test("view-only resources never receive a start action", async () => {
  const projection = projectResourceDetail(
    resource({
      availability: "view_only",
      primaryAction: "view",
      durableExecutable: true,
    }),
  );

  assert.equal(projection.canStart, false);
});
