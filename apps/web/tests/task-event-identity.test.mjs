import assert from "node:assert/strict";
import test from "node:test";

import {
  classifyTaskEvent,
  lifecycleBucketKey,
} from "../src/lib/taskEventIdentityCore.mjs";

function scope(taskId, ownerId, workspaceId, revision) {
  return { taskId, ownerId, workspaceId, revision };
}

function event(expected, runId, id, type = "agent_lifecycle") {
  return {
    id,
    type,
    taskId: expected.taskId,
    runId,
    revision: expected.revision,
    scope: {
      taskId: expected.taskId,
      runId,
      ownerId: expected.ownerId,
      workspaceId: expected.workspaceId,
    },
    payload: { revision: expected.revision, runId },
  };
}

test("Task A -> B -> A keeps run, agent, artifact, and recovery events in separate scope buckets", () => {
  const taskA = scope("task-a", "user-1", "ws-1", 7);
  const taskB = scope("task-b", "user-1", "ws-1", 3);
  const aSnapshot = event(taskA, "run-a", "snapshot:task-a:run:run-a:r7", "snapshot");
  const bSnapshot = event(taskB, "run-b", "snapshot:task-b:run:run-b:r3", "snapshot");

  assert.equal(classifyTaskEvent(aSnapshot, taskA, null).accepted, true);
  assert.equal(classifyTaskEvent(bSnapshot, taskB, null).accepted, true);
  assert.equal(classifyTaskEvent(bSnapshot, taskA, "run-a").reason, "scope_mismatch");
  assert.notEqual(
    lifecycleBucketKey(taskA.taskId, taskA.revision, "run-a"),
    lifecycleBucketKey(taskB.taskId, taskB.revision, "run-b"),
  );

  const staleAgent = event(taskB, "run-b", "lifecycle:task-b:run:run-b:agent_lifecycle:wu-b:started");
  const staleArtifact = event(taskB, "run-b", "lifecycle:task-b:run:run-b:artifact_persisted:artifact-b:persisted", "artifact_persisted");
  const staleRecovery = event(taskB, "run-b", "lifecycle:task-b:run:run-b:execution_terminal:run-b:failed", "execution_terminal");
  for (const stale of [staleAgent, staleArtifact, staleRecovery]) {
    assert.equal(classifyTaskEvent(stale, taskA, "run-a").accepted, false);
  }
});

test("successor run fences source events even at the same Task revision", () => {
  const expected = scope("task-a", "user-1", "ws-1", 8);
  const successor = event(expected, "run-successor", "snapshot:task-a:run:run-successor:r8", "snapshot");
  assert.equal(classifyTaskEvent(successor, expected, null).runId, "run-successor");

  const sourceEvent = event(
    expected,
    "run-source",
    "lifecycle:task-a:run:run-source:execution_terminal:run-source:completed",
    "execution_terminal",
  );
  assert.equal(classifyTaskEvent(sourceEvent, expected, "run-successor").reason, "run_mismatch");
});

test("cross-user/workspace, stale revision, and forged event identity are rejected", () => {
  const expected = scope("task-a", "user-1", "ws-1", 9);
  const valid = event(expected, "run-a", "lifecycle:task-a:run:run-a:agent_lifecycle:wu-a:started");
  assert.equal(classifyTaskEvent(valid, expected, "run-a").accepted, true);

  assert.equal(
    classifyTaskEvent({ ...valid, scope: { ...valid.scope, ownerId: "user-2" } }, expected, "run-a").reason,
    "scope_mismatch",
  );
  assert.equal(
    classifyTaskEvent({ ...valid, scope: { ...valid.scope, workspaceId: "ws-2" } }, expected, "run-a").reason,
    "scope_mismatch",
  );
  assert.equal(
    classifyTaskEvent({ ...valid, revision: 8, payload: { revision: 8, runId: "run-a" } }, expected, "run-a").reason,
    "stale_revision",
  );
  assert.equal(
    classifyTaskEvent({ ...valid, id: "lifecycle:task-a:run:forged:agent_lifecycle:wu-a:started" }, expected, "run-a").reason,
    "event_id_run_mismatch",
  );
});

test("future realtime revision requests GET refresh without advancing accepted state", () => {
  const expected = scope("task-a", "user-1", "ws-1", 9);
  const future = event({ ...expected, revision: 10 }, "run-successor", "snapshot:task-a:run:run-successor:r10", "snapshot");
  assert.deepEqual(
    classifyTaskEvent(future, expected, "run-source"),
    { accepted: false, reason: "future_revision", refresh: true },
  );
});
