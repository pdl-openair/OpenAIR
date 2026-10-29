import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { loadLocalTypeScript } from "./helpers/loadLocalTypeScript.mjs";

const ts = loadLocalTypeScript();
const sourcePath = fileURLToPath(new URL("../src/lib/workbenchUi.ts", import.meta.url));
const output = ts.transpileModule(readFileSync(sourcePath, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
  fileName: sourcePath,
}).outputText;
const module = { exports: {} };
new Function("require", "module", "exports", output)(() => ({}), module, module.exports);
const {
  projectWorkbenchArtifactState,
  workbenchArtifactStateCounts,
  resolveCanonicalArtifactState,
} = module.exports;

function artifact(overrides = {}) {
  return {
    id: "artifact-a",
    type: "report",
    title: "Report",
    format: "markdown",
    relatedStage: "insight_generation",
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
function assertProjection(actual, expected) {
  assert.equal(actual.badge.state, expected.state, "badge state");
  assert.equal(actual.countBucket, expected.bucket, "count bucket");
  assert.equal(actual.gate.isReviewable, expected.isReviewable, "isReviewable");
  assert.equal(actual.gate.canConfirmPass, expected.canConfirmPass, "canConfirmPass");
  assert.equal(actual.gate.canRequestRevision, expected.canRequestRevision, "canRequestRevision");
  assert.equal(actual.gate.canUseDownstream, expected.canUseDownstream, "canUseDownstream");
  assert.deepEqual(actual.visibleAllowedActions, expected.actions, "visible allowedActions");
}

test("validator usable drives state and counts but requires explicit downstream capability", () => {
  const projected = projectWorkbenchArtifactState(artifact({
    artifactState: null,
    validatorResult: { artifactState: "usable" },
  }));
  assertProjection(projected, {
    state: "usable", bucket: "usable", isReviewable: false,
    canConfirmPass: false, canRequestRevision: false, canUseDownstream: false,
    actions: [],
  });
});

test("validator needs_review is reviewable but exposes actions only from the server capability", () => {
  const denied = projectWorkbenchArtifactState(artifact({
    artifactState: null,
    validatorResult: { artifactState: "needs_review" },
  }));
  assertProjection(denied, {
    state: "needs_review", bucket: "needs_review", isReviewable: true,
    canConfirmPass: false, canRequestRevision: false, canUseDownstream: false,
    actions: [],
  });

  const allowed = projectWorkbenchArtifactState(artifact({
    artifactState: null,
    validatorResult: { artifactState: "needs_review" },
    humanGate: humanGate({
      canConfirmPass: true,
      requiresHumanReview: true,
      allowedActions: ["confirm_pass", "request_revision", "view_trace"],
      primaryAction: "confirm_pass",
    }),
  }));
  assertProjection(allowed, {
    state: "needs_review", bucket: "needs_review", isReviewable: true,
    canConfirmPass: true, canRequestRevision: true, canUseDownstream: false,
    actions: ["confirm_pass", "request_revision"],
  });
  assert.equal(allowed.gate.primaryAction, "confirm_pass");
  assert.equal(allowed.visibleAllowedActions.includes("view_trace"), false);
});

test("scope confirmation is executable only in partial_success and ghost primary actions fail closed", () => {
  const scope = projectWorkbenchArtifactState(artifact({
    artifactState: "partial_success",
    humanGate: humanGate({
      requiresHumanReview: true,
      requiresScopeExpansionConfirmation: true,
      allowedActions: ["confirm_scope_expansion", "view_trace", "retry"],
      primaryAction: "confirm_scope_expansion",
    }),
  }));
  assert.deepEqual(scope.visibleAllowedActions, ["confirm_scope_expansion"]);
  assert.equal(scope.gate.primaryAction, "confirm_scope_expansion");

  const ghost = projectWorkbenchArtifactState(artifact({
    artifactState: "needs_review",
    humanGate: humanGate({
      canConfirmPass: true,
      requiresHumanReview: true,
      allowedActions: ["confirm_pass", "view_trace"],
      primaryAction: "view_trace",
    }),
  }));
  assert.deepEqual(ghost.visibleAllowedActions, ["confirm_pass"]);
  assert.equal(ghost.gate.primaryAction, "");
});

test("unknown canonical state fails closed and is not counted as needs_review", () => {
  const projected = projectWorkbenchArtifactState(artifact({
    artifactState: null,
    validatorResult: null,
    humanGate: null,
  }));
  assertProjection(projected, {
    state: "unknown", bucket: "unknown", isReviewable: false,
    canConfirmPass: false, canRequestRevision: false, canUseDownstream: false,
    actions: [],
  });
});

test("artifact state wins over conflicting validator state and blocks fabricated review actions", () => {
  const projected = projectWorkbenchArtifactState(artifact({
    artifactState: "failed",
    validatorResult: { artifactState: "usable" },
    humanGate: humanGate({
      canConfirmPass: true,
      canUseDownstream: true,
      requiresHumanReview: true,
      allowedActions: ["confirm_pass", "request_revision", "use_downstream", "retry"],
      primaryAction: "confirm_pass",
    }),
  }));
  assert.equal(resolveCanonicalArtifactState(artifact({ artifactState: "failed", validatorResult: { artifactState: "usable" } })), "failed");
  assertProjection(projected, {
    state: "failed", bucket: "failed", isReviewable: false,
    canConfirmPass: false, canRequestRevision: false, canUseDownstream: false,
    actions: [],
  });
});

test("artifact counts preserve canonical buckets and keep unknown/usable out of needs_review", () => {
  const rows = [
    artifact({ id: "usable", validatorResult: { artifactState: "usable" } }),
    artifact({ id: "review", validatorResult: { artifactState: "needs_review" } }),
    artifact({ id: "unknown", validatorResult: null }),
    artifact({ id: "failed", artifactState: "failed", validatorResult: { artifactState: "usable" } }),
  ];
  assert.deepEqual(workbenchArtifactStateCounts(rows), {
    raw: 1,
    pending: 1,
    verified: 1,
    rejected: 0,
    unknown: 1,
    total: 4,
  });
});
