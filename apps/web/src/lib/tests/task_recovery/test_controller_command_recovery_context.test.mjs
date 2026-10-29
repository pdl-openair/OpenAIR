import assert from "node:assert/strict";
import { test } from "vitest";

import * as recovery from "../../taskRecovery";
function fence(taskId, expectedTaskRevision, sourceRunId) {
  return { taskId, expectedTaskRevision, sourceRunId };
}

test("a revision 4 command for run A binds the advanced revision 5 fence, never run B", () => {
  const store = recovery.rememberDeferredRecoveryContext({
    store: { version: 1, contexts: [] },
    taskId: "task-a",
    expectedTaskRevision: 4,
    submittedSourceRunId: "run-a",
    commandId: "command-a",
  });
  const [context] = recovery.deferredRecoveryContextsForTask(store, "task-a", 5);
  assert.deepEqual(recovery.bindDeferredRecoveryContextToFence({
    context,
    fence: fence("task-a", 5, "run-b"),
  }).commandIds, []);
  assert.deepEqual(recovery.bindDeferredRecoveryContextToFence({
    context,
    fence: fence("task-a", 5, "run-a"),
  }).commandIds, ["command-a"]);
});

test("deferred contexts stay isolated across Task A to B to A", () => {
  let store = { version: 1, contexts: [] };
  store = recovery.rememberDeferredRecoveryContext({
    store,
    taskId: "task-a",
    expectedTaskRevision: 4,
    submittedSourceRunId: null,
    commandId: "command-a",
  });
  store = recovery.rememberDeferredRecoveryContext({
    store,
    taskId: "task-b",
    expectedTaskRevision: 4,
    submittedSourceRunId: null,
    commandId: "command-b",
  });
  assert.deepEqual(recovery.deferredRecoveryContextsForTask(store, "task-b", 4).map((context) => context.commandIds), [["command-b"]]);
  assert.deepEqual(recovery.deferredRecoveryContextsForTask(store, "task-a", 4).map((context) => context.commandIds), [["command-a"]]);
});

test("a revision 2 supplement submitted before a run binds the revision 4 first run and never drifts", () => {
  const store = recovery.rememberDeferredRecoveryContext({
    store: { version: 1, contexts: [] },
    taskId: "task-a",
    expectedTaskRevision: 2,
    submittedSourceRunId: null,
    commandId: "command-a",
  });
  const [context] = recovery.deferredRecoveryContextsForTask(store, "task-a", 4);
  const firstBinding = recovery.bindDeferredRecoveryContextToFence({
    context,
    fence: fence("task-a", 4, "run-a"),
  });
  assert.equal(firstBinding.context.boundSourceRunId, "run-a");
  assert.deepEqual(firstBinding.commandIds, ["command-a"]);
  assert.deepEqual(recovery.bindDeferredRecoveryContextToFence({
    context: firstBinding.context,
    fence: fence("task-a", 4, "run-b"),
  }).commandIds, []);
});

test("a revision 4 context cannot be consumed by an older revision 3 fence", () => {
  const store = recovery.rememberDeferredRecoveryContext({
    store: { version: 1, contexts: [] },
    taskId: "task-a",
    expectedTaskRevision: 4,
    submittedSourceRunId: "run-a",
    commandId: "command-a",
  });
  const [context] = recovery.deferredRecoveryContextsForTask(store, "task-a", 4);
  assert.deepEqual(recovery.bindDeferredRecoveryContextToFence({
    context,
    fence: fence("task-a", 3, "run-a"),
  }).commandIds, []);
});

test("a same-task revision refresh settles a completed submission while a task switch does not", () => {
  assert.equal(recovery.isCurrentControllerSubmissionTask({
    submittedTaskId: "task-a",
    submissionGeneration: 7,
    currentTaskId: "task-a",
    currentSubmissionGeneration: 7,
  }), true);
  assert.equal(recovery.isCurrentControllerSubmissionTask({
    submittedTaskId: "task-a",
    submissionGeneration: 7,
    currentTaskId: "task-b",
    currentSubmissionGeneration: 8,
  }), false);
});
