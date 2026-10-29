/** R0-C: terminal status alone cannot grant recovery; the server action snapshot does. */
import assert from "node:assert/strict";

import test from "node:test";
import { TERMINAL_TASK_STATUSES, isTerminalTaskStatus } from "../src/lib/terminalTaskActionTruth.mjs";
import { canSubmitWorkbenchSupplement, currentWorkbenchActionSnapshot, hasWorkbenchTaskAction } from "../src/lib/workbenchInterruption.mjs";

const snapshot = (status, allowedActions = []) => ({ schemaVersion: "workbench_user_actions_v1", lifecycleKind: "canonical_v1_2", taskId: "t", revision: 3, taskStatus: status, allowedActions, items: [] });

test("completed, failed and cancelled stay terminal while recovery remains separately authorized", () => {
  for (const status of TERMINAL_TASK_STATUSES) {
    assert.equal(isTerminalTaskStatus(status), true);
    assert.equal(canSubmitWorkbenchSupplement(snapshot(status), "t", 3), false);
    assert.equal(hasWorkbenchTaskAction(snapshot(status), "t", 3, "continue_research"), false);
  }
});

test("running or failed cannot create supplement authority without a current server grant", () => {
  for (const status of ["draft", "not_started", "running", "awaiting_confirmation", "failed"]) {
    assert.equal(canSubmitWorkbenchSupplement(snapshot(status), "t", 3), false);
    assert.equal(canSubmitWorkbenchSupplement(null, "t", 3), false);
  }
  const allowed = snapshot("awaiting_confirmation", ["supplement_input"]);
  assert.equal(canSubmitWorkbenchSupplement(allowed, "t", 3), true);
  assert.equal(canSubmitWorkbenchSupplement(allowed, "t", 4), false);
  assert.equal(canSubmitWorkbenchSupplement(allowed, "other", 3), false);
});

test("a stopped task resumes only from the server same-task continuation action", () => {
  const denied = { ...snapshot("cancelled"), executionStopped: true };
  assert.equal(hasWorkbenchTaskAction(denied, "t", 3, "continue_research"), false);
  const allowed = { ...denied, allowedActions: ["continue_research"] };
  assert.equal(hasWorkbenchTaskAction(allowed, "t", 3, "continue_research"), true);
  assert.equal(currentWorkbenchActionSnapshot(allowed, "t", 4), null);
});
