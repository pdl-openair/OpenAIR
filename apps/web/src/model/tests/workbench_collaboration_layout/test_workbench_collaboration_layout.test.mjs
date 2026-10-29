import assert from "node:assert/strict";
import { test } from "vitest";
import { hasMaterializedCanonicalCollaboration, shouldRenderCanonicalCollaboration, workbenchNextStepPresentation } from "../../workbenchCollaborationLayoutCore.mjs";

test("canonical collaboration renders only after real materialized collaboration exists", () => {
  const empty = { currentWork: [], waitingAndDependencies: [], reviewAndHandoffs: [] };
  const materialized = { currentWork: [{ items: [{ role: "执行工作" }] }], waitingAndDependencies: [], reviewAndHandoffs: [] };
  assert.equal(hasMaterializedCanonicalCollaboration(empty), false);
  assert.equal(hasMaterializedCanonicalCollaboration(materialized), true);
  assert.equal(shouldRenderCanonicalCollaboration({ apiConnected: true, progressTrustKind: "canonical", loadStatus: "ready", view: materialized }), true);
  assert.equal(shouldRenderCanonicalCollaboration({ apiConnected: false, progressTrustKind: "canonical", loadStatus: "ready", view: materialized }), false);
});

test("non-canonical or not-ready progress cannot render canonical collaboration", () => {
  const view = { currentWork: [{ items: [{}] }], waitingAndDependencies: [], reviewAndHandoffs: [] };
  assert.equal(shouldRenderCanonicalCollaboration({ apiConnected: true, progressTrustKind: "legacy", loadStatus: "ready", view }), false);
  assert.equal(shouldRenderCanonicalCollaboration({ apiConnected: true, progressTrustKind: "canonical", loadStatus: "loading", view }), false);
});

test("next-step copy distinguishes plan confirmation, draft preparation and user action", () => {
  assert.deepEqual(workbenchNextStepPresentation({ isPlanConfirmationPending: true, needsUserAction: false }), { label: "等待你确认研究方案", tone: "warning" });
  assert.deepEqual(workbenchNextStepPresentation({ isPlanConfirmationPending: false, needsUserAction: false, isDraftUnconfirmed: true }), { label: "正在准备研究方案", tone: "warning" });
  assert.deepEqual(workbenchNextStepPresentation({ isPlanConfirmationPending: false, needsUserAction: true }), { label: "待你确认", tone: "warning" });
  assert.deepEqual(workbenchNextStepPresentation({ isPlanConfirmationPending: false, needsUserAction: false }), { label: "可继续", tone: "success" });
});
