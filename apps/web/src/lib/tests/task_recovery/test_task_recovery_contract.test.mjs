import assert from "node:assert/strict";
import { test } from "vitest";

import * as recovery from "../../taskRecovery";
import * as idempotency from "../../taskMessageIdempotency";

function fence(overrides = {}) {
  return {
    taskId: "task-a",
    expectedTaskRevision: 4,
    sourceRunId: "run-a",
    ...overrides,
  };
}

function response(overrides = {}) {
  return {
    recoveryRequestId: "request-a",
    ...fence(),
    recoveryKind: "retry",
    state: "confirmed",
    taskRevision: 5,
    successorRunId: "server-created-run",
    replayed: false,
    allowedActions: [],
    preservedFacts: {
      sourceRunState: "failed",
      sourceRunNumber: 1,
      preservedArtifactCount: 2,
    },
    ...overrides,
  };
}

function projection(overrides = {}) {
  return {
    fence: fence(),
    response: response(),
    responseUnknown: false,
    phase: "idle",
    eligibleForCreate: true,
    hasCurrentMessageId: true,
    hasPendingCache: false,
    failure: {
      failedStep: "资料核对",
      reason: "外部执行未完成",
      retainedWork: "已保留可核对的资料。",
    },
    error: null,
    ...overrides,
  };
}

test("retry only becomes confirmed after a matching authoritative response, with server successor fact", () => {
  const model = recovery.projectRecoveryUi(projection());
  assert.equal(model.state, "confirmed");
  assert.equal(model.display.title, "恢复已确认");
  assert.equal(model.display.successorFact, "已进入后续执行。");
  assert.ok(
    model.display.retainedFacts.some((fact) => fact.includes("2 项研究产出")),
  );
});

test("historical replan remains read-only even when its stored allowedActions once granted confirm", () => {
  const model = recovery.projectRecoveryUi(
    projection({
      response: response({
        recoveryKind: "replan",
        state: "requested",
        successorRunId: null,
        allowedActions: ["confirm"],
      }),
    }),
  );
  assert.equal(model.state, "awaiting_user_confirmation");
  assert.equal(model.display.title, "历史恢复记录");
  assert.equal(
    model.actionSlots.find((slot) => slot.kind === "confirm")?.available,
    false,
  );
  const denied = recovery.projectRecoveryUi(
    projection({
      response: response({
        recoveryKind: "replan",
        state: "requested",
        successorRunId: null,
        allowedActions: [],
      }),
    }),
  );
  assert.notEqual(
    denied.actionSlots.find((slot) => slot.kind === "confirm")?.available,
    true,
  );
});

test("timeout, disconnect, and missing response remain unknown and require authoritative refresh", () => {
  for (const input of [
    projection({ responseUnknown: true }),
    projection({ response: null, responseUnknown: true }),
    projection({
      response: null,
      error: { responseUnknown: true, code: null },
    }),
  ]) {
    const model = recovery.projectRecoveryUi(input);
    assert.equal(model.state, "external_result_unknown");
    assert.equal(model.authoritativeRefreshRequired, true);
    assert.notEqual(model.display.title, "恢复已确认");
  }
});

test("retry or replan timeout closes both create actions and leaves only reconciliation", () => {
  for (const recoveryKind of ["retry", "replan"]) {
    const model = recovery.projectRecoveryUi(
      projection({
        response: null,
        responseUnknown: true,
        hasPendingCache: true,
      }),
    );
    assert.equal(model.state, "external_result_unknown", recoveryKind);
    assert.equal(
      model.actionSlots.find((slot) => slot.kind === "retry")?.available,
      false,
    );
    assert.equal(
      model.actionSlots.find((slot) => slot.kind === "replan")?.available,
      false,
    );
    assert.equal(model.authoritativeRefreshRequired, true);
  }
});

test("confirm timeout never opens a create action and requires GET reconciliation for the same request", () => {
  const model = recovery.projectRecoveryUi(
    projection({
      response: null,
      responseUnknown: true,
      hasPendingCache: true,
    }),
  );
  assert.equal(
    model.actionSlots.find((slot) => slot.kind === "retry")?.available,
    false,
  );
  assert.equal(
    model.actionSlots.find((slot) => slot.kind === "replan")?.available,
    false,
  );
  assert.equal(
    model.actionSlots.find((slot) => slot.kind === "confirm")?.available,
    false,
  );
  assert.equal(model.authoritativeRefreshRequired, true);
});

test("task, expected revision, and source run fence rejects stale response", () => {
  for (const stale of [
    fence({ taskId: "task-b" }),
    fence({ expectedTaskRevision: 5 }),
    fence({ sourceRunId: "run-b" }),
  ]) {
    assert.equal(
      recovery.projectRecoveryUi(projection({ fence: stale })).state,
      "unavailable",
    );
  }
});

test("all accepted Recovery error codes produce user-facing failures without a success state", () => {
  const codes = [
    "recovery_not_found",
    "recovery_stale",
    "recovery_mutex_conflict",
    "recovery_terminal",
    "recovery_preflight_failed",
    "recovery_successor_exists",
    "idempotency_payload_conflict",
    "recovery_requires_postgres",
    "legacy_recovery_read_only",
  ];
  for (const code of codes) {
    const model = recovery.projectRecoveryUi(
      projection({ response: null, error: { code, responseUnknown: false } }),
    );
    assert.equal(model.state, "failed");
    assert.notEqual(model.display.title, "恢复已确认");
  }
});

test("refresh cache is fenced by Task A/B/A, revision, and source run", () => {
  const values = new Map();
  const originalWindow = globalThis.window;
  globalThis.window = {
    localStorage: {
      getItem: (key) => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, value),
    },
  };
  try {
    const entry = {
      taskId: "task-a",
      expectedTaskRevision: 4,
      sourceRunId: "run-a",
      recoveryRequestId: "request-a",
      request: {
        sourceRunId: "run-a",
        recoveryKind: "retry",
        expectedTaskRevision: 4,
        messageIds: ["command-a"],
        idempotencyKey: "opaque-key-a",
      },
    };
    idempotency.writeRecoveryRequestCache(entry);
    assert.deepEqual(
      idempotency.readRecoveryRequestCache("task-a", 4, "run-a"),
      entry,
    );
    assert.equal(
      idempotency.readRecoveryRequestCache("task-b", 4, "run-a"),
      null,
    );
    assert.equal(
      idempotency.readRecoveryRequestCache("task-a", 5, "run-a"),
      null,
    );
    assert.equal(
      idempotency.readRecoveryRequestCache("task-a", 4, "run-b"),
      null,
    );
    assert.deepEqual(
      idempotency.readRecoveryRequestCache("task-a", 4, "run-a"),
      entry,
    );
  } finally {
    if (originalWindow === undefined) delete globalThis.window;
    else globalThis.window = originalWindow;
  }
});

test("a changed Task A/B/A, revision, or run fence resets the pending-cache action gate", () => {
  const blocked = recovery.projectRecoveryUi(
    projection({
      response: null,
      responseUnknown: true,
      hasPendingCache: true,
    }),
  );
  assert.equal(
    blocked.actionSlots.find((slot) => slot.kind === "retry")?.available,
    false,
  );
  const freshFence = recovery.projectRecoveryUi(
    projection({
      response: null,
      responseUnknown: false,
      hasPendingCache: false,
      fence: fence({
        taskId: "task-b",
        expectedTaskRevision: 5,
        sourceRunId: "run-b",
      }),
    }),
  );
  assert.equal(
    freshFence.actionSlots.find((slot) => slot.kind === "retry")?.available,
    false,
  );
  assert.equal(
    freshFence.actionSlots.find((slot) => slot.kind === "replan")?.available,
    false,
  );
});
