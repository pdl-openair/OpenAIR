import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { chooseFocusedArtifactId } from "../src/lib/artifactViewSelectionCore.mjs";
import { loadLocalTypeScript } from "./helpers/loadLocalTypeScript.mjs";

const ts = loadLocalTypeScript();
const uiSourcePath = new URL("../src/lib/workbenchUi.ts", import.meta.url);
const uiOutput = ts.transpileModule(readFileSync(uiSourcePath, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
}).outputText;
const uiModule = { exports: {} };
new Function("require", "module", "exports", uiOutput)(() => ({}), uiModule, uiModule.exports);
const { userVisibleWorkbenchArtifacts } = uiModule.exports;

function hiddenArtifact(overrides = {}) {
  return {
    id: "artifact-developer",
    taskId: "task-a",
    title: "内部诊断产物",
    type: "report",
    format: "markdown",
    relatedStage: "problem_modeling",
    subStageInstanceId: "sub-a",
    artifactVisibility: "developer",
    validatorResult: { artifactState: "needs_review" },
    humanGate: {
      canConfirmPass: true,
      canUseDownstream: false,
      requiresHumanReview: true,
      requiresScopeExpansionConfirmation: false,
      allowedActions: ["confirm_pass"],
      primaryAction: "confirm_pass",
      blockedReason: null,
    },
    ...overrides,
  };
}

test("hidden Artifact ID cannot win deep-link focus selection", () => {
  const hidden = hiddenArtifact();
  const visible = userVisibleWorkbenchArtifacts([hidden]);
  assert.deepEqual(visible, []);
  assert.equal(chooseFocusedArtifactId({
    focusedArtifactId: null,
    deepLinkedArtifactId: hidden.id,
    visibleArtifactIds: visible.map((artifact) => artifact.id),
    allArtifactIds: [hidden.id],
  }), null);
});
