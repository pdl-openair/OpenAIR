import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import test from "node:test";
import {
  authoritativeReloadPlan,
  projectRightPanelArtifacts,
  projectRightPanelResources,
  safeRightPanelText,
} from "../src/components/workbench/workbenchRightPanelCore.mjs";
import { loadLocalTypeScript } from "./helpers/loadLocalTypeScript.mjs";

const ts = loadLocalTypeScript();
const workbenchUiPath = fileURLToPath(new URL("../src/lib/workbenchUi.ts", import.meta.url));
const workbenchUiOutput = ts.transpileModule(readFileSync(workbenchUiPath, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
  fileName: workbenchUiPath,
}).outputText;
const workbenchUiModule = { exports: {} };
new Function("require", "module", "exports", workbenchUiOutput)(() => ({}), workbenchUiModule, workbenchUiModule.exports);
const { projectWorkbenchArtifactState, userVisibleWorkbenchArtifacts } = workbenchUiModule.exports;

function artifact(overrides = {}) {
  return {
    id: "artifact-a",
    taskId: "task-a",
    title: "研究结论",
    sourceMode: "live",
    artifactVisibility: "user",
    artifactState: "usable",
    humanGate: {
      canConfirmPass: false,
      canUseDownstream: true,
      requiresHumanReview: false,
      requiresScopeExpansionConfirmation: false,
      allowedActions: ["use_downstream"],
      primaryAction: "use_downstream",
    },
    ...overrides,
  };
}

function projectedArtifact(overrides = {}) {
  const value = artifact(overrides);
  return { artifact: value, projection: projectWorkbenchArtifactState(value) };
}

test("Task A/B/A artifact projection keeps canonical replay/demo and consumes the parent-filtered user-visible set", () => {
  const rawArtifacts = [
    artifact(),
    artifact({ id: "artifact-b", taskId: "task-b" }),
    artifact({ id: "artifact-replay", sourceMode: "replay" }),
    artifact({ id: "artifact-demo", sourceMode: "demo" }),
    artifact({ id: "artifact-none", sourceMode: "none" }),
    artifact({ id: "artifact-fixture", sourceMode: "fixture" }),
    artifact({ id: "artifact-hidden", artifactVisibility: "internal" }),
  ];
  const projected = projectRightPanelArtifacts("task-a", userVisibleWorkbenchArtifacts(rawArtifacts).map((value) => ({
    artifact: value,
    projection: projectWorkbenchArtifactState(value),
  })));
  assert.deepEqual(projected.map((row) => row.id).sort(), ["artifact-a", "artifact-demo", "artifact-none", "artifact-replay"].sort());
  assert.equal(projected.find((row) => row.id === "artifact-replay")?.sourceMode, "replay");
  assert.equal(projected.find((row) => row.id === "artifact-demo")?.sourceMode, "demo");
  assert.deepEqual(projectRightPanelArtifacts("task-b", [projectedArtifact()]), []);

  const canonicalDemo = projectRightPanelArtifacts(
    "task-a",
    [projectedArtifact({ id: "artifact-api-demo", sourceMode: "fixture" })],
    "demo",
    true,
  );
  assert.equal(canonicalDemo[0]?.sourceMode, "demo");
  assert.deepEqual(projectRightPanelArtifacts(
    "task-a",
    [projectedArtifact({ id: "artifact-local-fixture", sourceMode: "fixture" })],
    "demo",
    false,
  ), []);
});

test("usable artifact is visible and never exposes confirmation or revision actions", () => {
  const [usable] = projectRightPanelArtifacts("task-a", [projectedArtifact()]);
  assert.equal(usable.state, "usable");
  assert.equal(usable.stateLabel, "可使用");
  assert.equal(usable.canUseDownstream, true);
  assert.equal(usable.canConfirmPass, false);
  assert.equal(usable.canRequestRevision, false);
});

test("needs_review actions appear only when the authoritative humanGate allows them", () => {
  const allowed = artifact({
    artifactState: "needs_review",
    humanGate: {
      canConfirmPass: true,
      canUseDownstream: false,
      requiresHumanReview: true,
      requiresScopeExpansionConfirmation: false,
      allowedActions: ["confirm_pass", "request_revision"],
      primaryAction: "confirm_pass",
    },
  });
  const denied = artifact({ id: "artifact-denied", artifactState: "needs_review", humanGate: null });
  const rows = projectRightPanelArtifacts("task-a", [
    { artifact: allowed, projection: projectWorkbenchArtifactState(allowed) },
    { artifact: denied, projection: projectWorkbenchArtifactState(denied) },
  ]);
  assert.equal(rows.find((row) => row.id === "artifact-a").canConfirmPass, true);
  assert.equal(rows.find((row) => row.id === "artifact-a").canRequestRevision, true);
  assert.equal(rows.find((row) => row.id === "artifact-denied").canConfirmPass, false);
  assert.equal(rows.find((row) => row.id === "artifact-denied").canRequestRevision, false);
});

test("all canonical artifact states remain visible with clear Chinese labels", () => {
  const states = [
    ["usable", "可使用"],
    ["needs_review", "待复核"],
    ["needs_input", "待补充材料"],
    ["insufficient_materials", "材料不足"],
    ["partial_success", "部分完成"],
    ["rejected_or_needs_revision", "待修改"],
    ["failed", "未形成可用产物"],
  ];
  const rows = projectRightPanelArtifacts("task-a", states.map(([state], index) => projectedArtifact({ id: `a-${index}`, artifactState: state })));
  assert.deepEqual(new Map(rows.map((row) => [row.state, row.stateLabel])), new Map(states));

  const [unknown] = projectRightPanelArtifacts("task-a", [projectedArtifact({ id: "unknown", artifactState: null, humanGate: null })]);
  assert.equal(unknown.state, "unknown");
  assert.equal(unknown.stateLabel, "状态待确认");
});

test("terminal invalidation plans authoritative GET refreshes and never carries final truth", () => {
  assert.deepEqual(
    authoritativeReloadPlan(["collaboration", "task", "artifact", "review", "recovery", "controller_receipt", "allowed_actions"]),
    { collaboration: true, task: true, controllerCommand: true },
  );
  assert.deepEqual(authoritativeReloadPlan([]), { collaboration: false, task: false, controllerCommand: false });
  assert.deepEqual(Object.keys(authoritativeReloadPlan(["task"])).sort(), ["collaboration", "controllerCommand", "task"]);
});

test("canonical artifact type identifiers are localized in the right collaboration stream", () => {
  const [question] = projectRightPanelArtifacts("task-a", [projectedArtifact({
    title: "research_question_refinement",
    artifactType: "research_question_refinement",
  })]);
  const [literature] = projectRightPanelArtifacts("task-a", [projectedArtifact({
    id: "literature",
    title: "key_literature_positioning",
    artifactType: "key_literature_positioning",
  })]);
  const [validation] = projectRightPanelArtifacts("task-a", [projectedArtifact({
    id: "validation",
    title: "validation_design",
    artifactType: "validation_design",
  })]);
  assert.equal(question.title, "研究问题凝练");
  assert.equal(literature.title, "关键文献定位");
  assert.equal(validation.title, "验证方案设计");
});

test("technical product text is replaced before it reaches ordinary UI", () => {
  assert.equal(safeRightPanelText("provider=openai model=gpt-4", "研究能力"), "研究能力");
  assert.equal(safeRightPanelText("Traceback (most recent call last)", "研究产物"), "研究产物");
  const [row] = projectRightPanelArtifacts("task-a", [projectedArtifact({ title: "prompt=hidden" })]);
  assert.equal(row.title, "研究产物");
});

test("tools and resources use only user-safe live Task GET capability narratives", () => {
  const rows = projectRightPanelResources([
    { id: "live", sourceMode: "live", status: "completed", callVisibility: "user", userFacingNarrative: { summary: "检索并整理了相关文献" }, provider: "secret-provider" },
    { id: "replay", sourceMode: "replay", status: "completed", userFacingNarrative: { summary: "历史结果" } },
    { id: "internal", sourceMode: "live", status: "completed", callVisibility: "internal", userFacingNarrative: { summary: "内部调用" } },
    { id: "unsafe", sourceMode: "live", status: "running", userFacingNarrative: { summary: "provider=openai" } },
  ]);
  assert.deepEqual(rows.map((row) => row.id), ["live", "unsafe"]);
  assert.equal(rows[0].summary, "检索并整理了相关文献");
  assert.doesNotMatch(JSON.stringify(rows), /secret-provider|provider=openai|内部调用|历史结果/);
  assert.equal(rows[1].statusTone, "processing");
});
