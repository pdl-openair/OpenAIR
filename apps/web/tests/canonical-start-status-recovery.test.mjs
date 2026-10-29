import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(testDirectory, "../../..");
const recovery = await import(
  pathToFileURL(path.join(repoRoot, "apps/web/src/api/taskStartSemantics.ts")).href
);
function memoryStorage() {
  const values = new Map();
  return {
    getItem(key) { return values.get(key) ?? null; },
    setItem(key, value) { values.set(key, value); },
    removeItem(key) { values.delete(key); },
  };
}

test("automatic-start observation suppresses a second explicit start for both started and blocked truth", () => {
  assert.deepEqual(
    recovery.observeAutomaticResearchStart("running", []),
    { state: "started", blockingReasonCode: null },
  );
  assert.deepEqual(
    recovery.observeAutomaticResearchStart("awaiting_confirmation", [
      { status: "needs_input", statusReasonCode: "required_input_missing" },
    ]),
    { state: "blocked", blockingReasonCode: "required_input_missing" },
  );
  assert.deepEqual(
    recovery.observeAutomaticResearchStart("awaiting_confirmation", []),
    { state: "blocked", blockingReasonCode: null },
  );
  assert.deepEqual(
    recovery.observeAutomaticResearchStart("not_started", []),
    { state: "explicit_start_required", blockingReasonCode: null },
  );
});

test("only the matching task request is cleared; unknown remains for refresh recovery", () => {
  const storage = memoryStorage();
  recovery.rememberDurableStartIntent("task-a", "request-a", storage);
  recovery.rememberDurableStartIntent("task-b", "request-b", storage);

  recovery.settleDurableStartIntent("task-a", "stale-request", "committed", storage);
  assert.equal(recovery.readDurableStartIntent("task-a", storage), "request-a");

  recovery.settleDurableStartIntent("task-a", "request-a", "unknown", storage);
  assert.equal(recovery.readDurableStartIntent("task-a", storage), "request-a");

  recovery.settleDurableStartIntent("task-a", "request-a", "blocked", storage);
  assert.equal(recovery.readDurableStartIntent("task-a", storage), null);

  recovery.rememberDurableStartIntent("task-a", "request-a", storage);

  recovery.settleDurableStartIntent("task-a", "request-a", "not_committed", storage);
  assert.equal(recovery.readDurableStartIntent("task-a", storage), null);
  assert.equal(recovery.readDurableStartIntent("task-b", storage), "request-b");

  recovery.settleDurableStartIntent("task-b", "request-b", "committed", storage);
  assert.equal(recovery.readDurableStartIntent("task-b", storage), null);
});

test("Task A to B drops A response and A to B to A accepts only the new A generation", () => {
  const slowA = { taskId: "task-a", selectionRequestId: "request-a", taskRevision: 4, generation: 1 };
  assert.equal(recovery.isTaskStartStatusFenceCurrent(slowA, {
    taskId: "task-b",
    selectionRequestId: "request-b",
    taskRevision: 4,
    generation: 2,
  }), false);
  assert.equal(recovery.isTaskStartStatusFenceCurrent(slowA, {
    taskId: "task-a",
    selectionRequestId: "request-a",
    taskRevision: 5,
    generation: 3,
  }), false);

  const resumedA = { taskId: "task-a", selectionRequestId: "request-a", taskRevision: 5, generation: 4 };
  assert.equal(recovery.isTaskStartStatusFenceCurrent(resumedA, {
    taskId: "task-a",
    selectionRequestId: "request-a",
    taskRevision: 5,
    generation: 4,
  }), true);
  assert.equal(recovery.isTaskStartStatusFenceCurrent(resumedA, {
    taskId: "task-a",
    selectionRequestId: "request-b",
    taskRevision: 5,
    generation: 4,
  }), false);
});
