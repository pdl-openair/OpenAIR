import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { projectRightPanelArtifacts } from "../src/components/workbench/workbenchRightPanelCore.mjs";
import { loadLocalTypeScript } from "./helpers/loadLocalTypeScript.mjs";

const ts = loadLocalTypeScript();
const sourcePath = fileURLToPath(new URL("../src/lib/workbenchUi.ts", import.meta.url));
const output = ts.transpileModule(readFileSync(sourcePath, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
  fileName: sourcePath,
}).outputText;
const module = { exports: {} };
new Function("require", "module", "exports", output)(() => ({}), module, module.exports);
const { projectWorkbenchArtifactState } = module.exports;

function artifact(overrides = {}) {
  return {
    id: "artifact-a",
    taskId: "task-a",
    title: "研究产物",
    type: "report",
    format: "markdown",
    sourceMode: "live",
    ...overrides,
  };
}

function humanGate(overrides = {}) {
  return {
    canConfirmPass: false,
    canUseDownstream: false,
    requiresHumanReview: false,
    requiresScopeExpansionConfirmation: false,
    allowedActions: [],
    primaryAction: "",
    blockedReason: null,
    ...overrides,
  };
}

function assertMiddleRight(rawArtifact, expected) {
  const middle = projectWorkbenchArtifactState(rawArtifact);
  const [right] = projectRightPanelArtifacts(
    "task-a",
    [{ artifact: rawArtifact, projection: middle }],
    "live",
    true,
  );
  assert.ok(right, "right-panel row must remain visible");
  assert.equal(middle.state, expected.state, "middle state");
  assert.equal(right.state, middle.state, "right state");
  assert.equal(right.stateLabel, middle.badge.label, "badge label");
  assert.equal(right.stateTone, middle.badge.color, "badge tone");
  assert.equal(right.countBucket, middle.countBucket, "count bucket");
  assert.equal(right.isReviewable, middle.gate.isReviewable, "isReviewable");
  assert.equal(right.canConfirmPass, middle.gate.canConfirmPass, "canConfirmPass");
  assert.equal(right.canRequestRevision, middle.gate.canRequestRevision, "canRequestRevision");
  assert.equal(right.canUseDownstream, middle.gate.canUseDownstream, "canUseDownstream");
  assert.deepEqual(right.visibleAllowedActions, middle.visibleAllowedActions, "visible allowedActions");
  assert.equal(middle.countBucket, expected.bucket);
  assert.equal(middle.gate.canConfirmPass, expected.canConfirmPass);
  assert.equal(middle.gate.canRequestRevision, expected.canRequestRevision);
  assert.equal(middle.gate.canUseDownstream, expected.canUseDownstream);
  assert.deepEqual(middle.visibleAllowedActions, expected.actions);
}

test("middle and right-panel consume one canonical Artifact projection matrix", () => {
  assertMiddleRight(artifact({
    validatorResult: { artifactState: "usable" },
    humanGate: humanGate({ canUseDownstream: true, allowedActions: ["use_downstream"], primaryAction: "use_downstream" }),
  }), {
    state: "usable", bucket: "usable", canConfirmPass: false,
    canRequestRevision: false, canUseDownstream: true, actions: ["use_downstream"],
  });

  assertMiddleRight(artifact({
    id: "artifact-review",
    validatorResult: { artifactState: "needs_review" },
    humanGate: humanGate({
      canConfirmPass: true,
      requiresHumanReview: true,
      allowedActions: ["confirm_pass", "request_revision", "view_trace"],
      primaryAction: "confirm_pass",
    }),
  }), {
    state: "needs_review", bucket: "needs_review", canConfirmPass: true,
    canRequestRevision: true, canUseDownstream: false,
    actions: ["confirm_pass", "request_revision"],
  });

  assertMiddleRight(artifact({ id: "artifact-unknown", validatorResult: null, humanGate: null }), {
    state: "unknown", bucket: "unknown", canConfirmPass: false,
    canRequestRevision: false, canUseDownstream: false, actions: [],
  });

  assertMiddleRight(artifact({
    id: "artifact-conflict",
    artifactState: "failed",
    validatorResult: { artifactState: "usable" },
    humanGate: humanGate({
      canConfirmPass: true,
      canUseDownstream: true,
      requiresHumanReview: true,
      allowedActions: ["confirm_pass", "request_revision", "use_downstream", "retry"],
      primaryAction: "confirm_pass",
    }),
  }), {
    state: "failed", bucket: "failed", canConfirmPass: false,
    canRequestRevision: false, canUseDownstream: false, actions: [],
  });

  assertMiddleRight(artifact({
    id: "artifact-review-no-capability",
    validatorResult: { artifactState: "needs_review" },
    humanGate: null,
  }), {
    state: "needs_review", bucket: "needs_review", canConfirmPass: false,
    canRequestRevision: false, canUseDownstream: false, actions: [],
  });
});

test("right-panel core cannot reinterpret raw Artifact state without the Workbench projection", () => {
  assert.deepEqual(projectRightPanelArtifacts("task-a", [artifact({ validatorResult: { artifactState: "usable" } })], "live", true), []);

});
