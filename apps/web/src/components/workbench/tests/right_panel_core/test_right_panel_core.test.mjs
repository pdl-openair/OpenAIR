import assert from "node:assert/strict";
import { test } from "vitest";
import { authoritativeReloadPlan, collaborationCanonicalArtifactExists, looksLikeInternalExecutionDetail, presentCollaborationActionReason, presentCollaborationResultSummary, projectRightPanelArtifacts, projectRightPanelResources, safeRightPanelText } from "../../workbenchRightPanelCore.mjs";

function projectedArtifact(overrides = {}, projectionOverrides = {}) {
  return {
    artifact: { id: "artifact-a", taskId: "task-a", title: "研究结论", sourceMode: "live", ...overrides },
    projection: {
      state: "usable",
      badge: { label: "可使用", color: "success" },
      gate: { isReviewable: false, canConfirmPass: false, canRequestRevision: false, canUseDownstream: true },
      visibleAllowedActions: ["use_downstream"],
      ...projectionOverrides,
    },
  };
}

test("canonical visible Artifact truth overrides internal result dumps but payload refs alone do not", () => {
  assert.equal(collaborationCanonicalArtifactExists(["artifact-a"], ["artifact-a"]), true);
  assert.equal(collaborationCanonicalArtifactExists(["artifact-ghost"], ["artifact-a"]), false);
  const dump = "configuration adapter trace skill_id";
  assert.equal(looksLikeInternalExecutionDetail(dump), true);
  assert.equal(presentCollaborationResultSummary(dump, { canonicalArtifactExists: true }), "研究产物已形成，可查看相关产物。");
  assert.doesNotMatch(presentCollaborationActionReason(dump, { canonicalArtifactExists: false }), /configuration|adapter|trace|skill_id/i);
});

test("scientific copy is not hidden merely because it contains adapter or configuration vocabulary", () => {
  const scientific = "We compare adapter configuration strategies.";
  assert.equal(looksLikeInternalExecutionDetail(scientific), false);
  assert.equal(presentCollaborationResultSummary(scientific), scientific);
});

test("right-panel Artifact projection uses canonical projection and task identity", () => {
  const rows = projectRightPanelArtifacts("task-a", [
    projectedArtifact(),
    projectedArtifact({ id: "artifact-b", taskId: "task-b" }),
    projectedArtifact({ id: "artifact-replay", sourceMode: "replay" }),
    projectedArtifact({ id: "artifact-demo", sourceMode: "demo" }),
  ]);
  assert.deepEqual(rows.map((row) => row.id).sort(), ["artifact-a", "artifact-demo", "artifact-replay"].sort());
  assert.equal(rows.find((row) => row.id === "artifact-replay").sourceModeLabel, "历史结果");
  assert.equal(rows.find((row) => row.id === "artifact-demo").sourceModeLabel, "演示内容");
  assert.equal(rows.find((row) => row.id === "artifact-a").canUseDownstream, true);
});

test("review actions appear only from authoritative projection gates", () => {
  const rows = projectRightPanelArtifacts("task-a", [
    projectedArtifact({ id: "allowed" }, {
      state: "needs_review",
      badge: { label: "待复核", color: "warning" },
      gate: { isReviewable: true, canConfirmPass: true, canRequestRevision: true, canUseDownstream: false },
      visibleAllowedActions: ["confirm_pass", "request_revision"],
    }),
    projectedArtifact({ id: "denied" }, {
      state: "needs_review",
      badge: { label: "待复核", color: "warning" },
      gate: {},
      visibleAllowedActions: [],
    }),
  ]);
  assert.equal(rows.find((row) => row.id === "allowed").canConfirmPass, true);
  assert.equal(rows.find((row) => row.id === "denied").canConfirmPass, false);
});

test("terminal invalidation plans authoritative GET refreshes and carries no final truth", () => {
  assert.deepEqual(authoritativeReloadPlan(["collaboration", "task", "artifact", "controller_receipt"]), { collaboration: true, task: true, controllerCommand: true });
  assert.deepEqual(authoritativeReloadPlan([]), { collaboration: false, task: false, controllerCommand: false });
});

test("resources expose only user-visible live capability narratives", () => {
  const rows = projectRightPanelResources([
    { id: "live", sourceMode: "live", status: "completed", callVisibility: "user", userFacingNarrative: { summary: "\u68c0\u7d22\u5e76\u6574\u7406\u4e86\u76f8\u5173\u6587\u732e" } },
    { id: "replay", sourceMode: "replay", status: "completed", userFacingNarrative: { summary: "\u5386\u53f2\u7ed3\u679c" } },
    { id: "internal", sourceMode: "live", status: "completed", callVisibility: "internal", userFacingNarrative: { summary: "\u5185\u90e8\u8c03\u7528" } },
    { id: "unsafe", sourceMode: "live", status: "running", userFacingNarrative: { summary: "provider=openai" } },
  ]);
  assert.equal(rows.length, 2);
  const live = rows.find((row) => row.id === "live");
  const unsafe = rows.find((row) => row.id === "unsafe");
  assert.equal(live?.summary, "\u68c0\u7d22\u5e76\u6574\u7406\u4e86\u76f8\u5173\u6587\u732e");
  assert.equal(live?.statusTone, "success");
  assert.equal(unsafe?.statusTone, "processing");
  assert.equal(typeof unsafe?.summary, "string");
  assert.ok((unsafe?.summary ?? "").length > 0);
  assert.doesNotMatch(unsafe?.summary ?? "", /provider|openai/i);
});

test("presentation boundary replaces obvious engineering copy", () => {
  assert.equal(safeRightPanelText("provider=openai model=gpt-4", "研究能力"), "研究能力");
  assert.equal(safeRightPanelText("Traceback (most recent call last)", "研究产物"), "研究产物");
});
