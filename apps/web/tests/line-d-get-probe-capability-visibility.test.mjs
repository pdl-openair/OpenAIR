import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { loadLocalTypeScript } from "./helpers/loadLocalTypeScript.mjs";
import {
  getWorkbenchTaskRequestCoordinator,
  resetWorkbenchTaskRequestCoordinators,
} from "../src/lib/workbenchTaskRequestCoordinator.mjs";
import { projectRightPanelArtifacts } from "../src/components/workbench/workbenchRightPanelCore.mjs";

const ts = loadLocalTypeScript();
const uiSourcePath = fileURLToPath(new URL("../src/lib/workbenchUi.ts", import.meta.url));
const uiOutput = ts.transpileModule(readFileSync(uiSourcePath, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
  fileName: uiSourcePath,
}).outputText;
const uiModule = { exports: {} };
new Function("require", "module", "exports", uiOutput)(() => ({}), uiModule, uiModule.exports);
const {
  projectWorkbenchArtifactState,
  userVisibleWorkbenchArtifacts,
  workbenchArtifactStateCounts,
} = uiModule.exports;

const flush = () => new Promise((resolve) => setImmediate(resolve));

function createClock() {
  let now = 0;
  let nextId = 1;
  const timers = new Map();
  const setTimeout = (callback, delay = 0) => {
    const id = nextId++;
    timers.set(id, { callback, dueAt: now + Number(delay || 0), cleared: false });
    return id;
  };
  const clearTimeout = (id) => {
    const timer = timers.get(id);
    if (timer) timer.cleared = true;
  };
  const advanceTo = async (target) => {
    now = target;
    let progressed = true;
    while (progressed) {
      progressed = false;
      const due = [...timers.entries()]
        .filter(([, timer]) => !timer.cleared && timer.dueAt <= now)
        .sort((left, right) => left[1].dueAt - right[1].dueAt || left[0] - right[0]);
      for (const [, timer] of due) {
        if (timer.cleared) continue;
        timer.cleared = true;
        progressed = true;
        timer.callback();
        await flush();
      }
    }
  };
  return {
    now: () => now,
    setNow: (value) => { now = value; },
    setTimeout,
    clearTimeout,
    advanceTo,
    activeTimers: () => [...timers.values()].filter((timer) => !timer.cleared),
  };
}

function rateLimitError(retryAfterMs = 1_000) {
  return Object.assign(new Error("rate limited"), {
    status: 429,
    code: "rate_limited",
    retryAfterMs,
  });
}

async function createPendingGetProbe(taskId = "task-a") {
  const clock = createClock();
  const coordinator = getWorkbenchTaskRequestCoordinator("workbench-page", "canonical", taskId, {
    now: clock.now,
    random: () => 0.5,
    setTimeout: clock.setTimeout,
    clearTimeout: clock.clearTimeout,
    visibilityState: "visible",
  });
  await assert.rejects(
    coordinator.request("seed-rate-limit", () => Promise.reject(rateLimitError())),
    (error) => error?.status === 429,
  );

  let streamCalls = 0;
  const streamControls = new Map();
  const unsubscribe = coordinator.subscribeManagedStream(
    "task-event-stream",
    ({ signal, onEvent }) => {
      streamCalls += 1;
      return new Promise((resolve, reject) => {
        streamControls.set(streamCalls, { signal, onEvent, resolve, reject });
        signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
      });
    },
    () => {},
  );
  assert.equal(streamCalls, 0, "open circuit blocks the managed SSE before Retry-After");

  clock.setNow(coordinator.backoffUntil);
  let getSignal;
  let resolveGet;
  const pendingGet = coordinator.request("task-model", (signal) => {
    getSignal = signal;
    return new Promise((resolve) => { resolveGet = resolve; });
  });
  const getOutcome = pendingGet.then(
    (value) => ({ status: "resolved", value }),
    (error) => ({ status: "rejected", error }),
  );
  assert.equal(coordinator._probeOwner?.kind, "request");
  assert.equal(coordinator._probeOwner?.requestEpoch, coordinator.inFlightRequests.get("task-model")?.requestEpoch);
  return {
    clock,
    coordinator,
    unsubscribe,
    streamControls,
    streamCalls: () => streamCalls,
    getSignal: () => getSignal,
    resolveGet,
    getOutcome,
  };
}

test.afterEach(() => resetWorkbenchTaskRequestCoordinators());

test("GET half-open hidden abort releases ownership and visible creates one SSE replacement probe", async () => {
  const scenario = await createPendingGetProbe();
  const oldToken = scenario.coordinator._probeOwner;

  scenario.coordinator.setVisibility("hidden");
  assert.equal(scenario.getSignal().aborted, true);
  assert.equal(scenario.coordinator._probeOwner, null);
  assert.equal(scenario.coordinator.backoff.probeInFlight, false);
  assert.equal(scenario.coordinator.circuitState, "open");
  assert.equal(scenario.streamCalls(), 0, "hidden state cannot issue a replacement transport");

  scenario.coordinator.setVisibility("visible");
  scenario.coordinator.setVisibility("visible");
  await flush();
  assert.equal(scenario.streamCalls(), 1, "visible resumes with exactly one SSE half-open probe");
  const replacementToken = scenario.coordinator._probeOwner;
  assert.equal(replacementToken?.kind, "managed-stream");
  assert.notEqual(replacementToken, oldToken);

  scenario.resolveGet({ ok: true, stale: true });
  const oldOutcome = await scenario.getOutcome;
  assert.equal(oldOutcome.status, "rejected");
  assert.equal(oldOutcome.error?.name, "AbortError");
  assert.equal(scenario.coordinator._probeOwner, replacementToken, "old GET completion cannot release the replacement token");

  scenario.streamControls.get(1).onEvent({ id: "evt-recovered", data: { status: "running" } });
  await flush();
  assert.equal(scenario.coordinator.circuitState, "closed");
  assert.equal(scenario.coordinator.backoff.probeInFlight, false);
  assert.equal(scenario.coordinator._probeOwner, null);
  scenario.unsubscribe();
});

test("GET half-open lifecycle cleanup aborts request for last subscriber, rebind, terminal and coordinator close", async (t) => {
  await t.test("last subscriber", async () => {
    const scenario = await createPendingGetProbe("task-last-subscriber");
    scenario.unsubscribe();
    assert.equal(scenario.getSignal().aborted, true);
    assert.equal(scenario.coordinator._probeOwner, null);
    assert.equal(scenario.coordinator.backoff.probeInFlight, false);
    assert.equal(scenario.clock.activeTimers().length, 0);
    scenario.resolveGet({ ok: true });
    await scenario.getOutcome;
  });

  await t.test("generation rebind", async () => {
    const scenario = await createPendingGetProbe("task-old");
    scenario.coordinator.rebind("task-new");
    assert.equal(scenario.getSignal().aborted, true);
    assert.equal(scenario.coordinator._probeOwner, null);
    assert.equal(scenario.coordinator.backoff.probeInFlight, false);
    let nextCalls = 0;
    const unsubscribeNext = scenario.coordinator.subscribeManagedStream(
      "task-event-stream",
      ({ signal }) => {
        nextCalls += 1;
        return new Promise((_, reject) => signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true }));
      },
      () => {},
    );
    assert.equal(nextCalls, 1);
    scenario.resolveGet({ ok: true, stale: true });
    const oldOutcome = await scenario.getOutcome;
    assert.equal(oldOutcome.status, "resolved");
    assert.equal(oldOutcome.value, undefined);
    assert.equal(nextCalls, 1, "old GET callback cannot create a new-generation transport");
    unsubscribeNext();
    scenario.unsubscribe();
  });

  await t.test("terminal close", async () => {
    const scenario = await createPendingGetProbe("task-terminal");
    scenario.coordinator.markTerminal("completed");
    assert.equal(scenario.getSignal().aborted, true);
    assert.equal(scenario.coordinator._probeOwner, null);
    assert.equal(scenario.coordinator.backoff.probeInFlight, false);
    scenario.coordinator.setVisibility("hidden");
    scenario.coordinator.setVisibility("visible");
    assert.equal(scenario.streamCalls(), 0);
    scenario.resolveGet({ ok: true });
    await scenario.getOutcome;
  });

  await t.test("coordinator close", async () => {
    const scenario = await createPendingGetProbe("task-close");
    scenario.coordinator.close();
    assert.equal(scenario.getSignal().aborted, true);
    assert.equal(scenario.coordinator._probeOwner, null);
    assert.equal(scenario.coordinator.backoff.probeInFlight, false);
    assert.equal(scenario.clock.activeTimers().length, 0);
    scenario.resolveGet({ ok: true });
    await scenario.getOutcome;
  });
});

test("GET and SSE compete for one half-open token and recovery remains live", async () => {
  const scenario = await createPendingGetProbe();
  await scenario.clock.advanceTo(scenario.coordinator.backoffUntil);
  assert.equal(scenario.streamCalls(), 0, "managed SSE cannot start while GET owns the half-open token");
  assert.equal(scenario.coordinator._probeOwner?.kind, "request");

  scenario.coordinator.setVisibility("hidden");
  assert.equal(scenario.getSignal().aborted, true);
  scenario.coordinator.setVisibility("visible");
  await flush();
  assert.equal(scenario.streamCalls(), 1);
  assert.equal(scenario.coordinator._probeOwner?.kind, "managed-stream");

  await assert.rejects(
    scenario.coordinator.request("collaboration", () => Promise.resolve({ ok: true })),
    (error) => error?.code === "rate_limited",
  );
  assert.equal(scenario.streamCalls(), 1, "GET cannot create a second half-open probe");
  scenario.streamControls.get(1).onEvent({ id: "evt-ok", data: { status: "running" } });
  await flush();
  assert.equal(scenario.coordinator.circuitState, "closed");
  assert.equal(scenario.coordinator.backoff.probeInFlight, false);
  scenario.resolveGet({ ok: true });
  await scenario.getOutcome;
  scenario.unsubscribe();
});

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

function productionArtifactProjection(rawArtifacts) {
  const visibleArtifacts = userVisibleWorkbenchArtifacts(rawArtifacts);
  const projectedArtifacts = visibleArtifacts.map((row) => ({
    artifact: row,
    projection: projectWorkbenchArtifactState(row),
  }));
  return {
    visibleArtifacts,
    middle: projectedArtifacts.map((row) => row.projection),
    right: projectRightPanelArtifacts("task-a", projectedArtifacts, "live", true),
    counts: workbenchArtifactStateCounts(visibleArtifacts),
  };
}

function assertActionProjection(rawArtifact, expected) {
  const result = productionArtifactProjection([rawArtifact]);
  assert.equal(result.middle.length, 1);
  assert.equal(result.right.length, 1);
  const middle = result.middle[0];
  const right = result.right[0];
  assert.equal(middle.state, expected.state);
  assert.equal(right.state, expected.state);
  assert.equal(right.stateLabel, middle.badge.label);
  assert.equal(right.countBucket, middle.countBucket);
  assert.equal(middle.gate.canConfirmPass, expected.canConfirmPass);
  assert.equal(right.canConfirmPass, expected.canConfirmPass);
  assert.equal(middle.gate.canRequestRevision, expected.canRequestRevision);
  assert.equal(right.canRequestRevision, expected.canRequestRevision);
  assert.equal(middle.gate.canUseDownstream, expected.canUseDownstream);
  assert.equal(right.canUseDownstream, expected.canUseDownstream);
  assert.deepEqual(middle.visibleAllowedActions, expected.actions);
  assert.deepEqual(right.visibleAllowedActions, expected.actions);
}

test("usable state without server capability is fail closed in middle and right panel", () => {
  assertActionProjection(artifact({
    validatorResult: { artifactState: "usable" },
    humanGate: null,
  }), {
    state: "usable",
    canConfirmPass: false,
    canRequestRevision: false,
    canUseDownstream: false,
    actions: [],
  });
});

test("Artifact state and explicit server capability remain separate across the capability matrix", () => {
  assertActionProjection(artifact({
    id: "usable-allowed",
    validatorResult: { artifactState: "usable" },
    humanGate: humanGate({ canUseDownstream: true, allowedActions: ["use_downstream"], primaryAction: "use_downstream" }),
  }), { state: "usable", canConfirmPass: false, canRequestRevision: false, canUseDownstream: true, actions: ["use_downstream"] });

  assertActionProjection(artifact({
    id: "usable-denied",
    validatorResult: { artifactState: "usable" },
    humanGate: humanGate({ canUseDownstream: false, allowedActions: ["use_downstream"] }),
  }), { state: "usable", canConfirmPass: false, canRequestRevision: false, canUseDownstream: false, actions: [] });

  assertActionProjection(artifact({
    id: "review-allowed",
    validatorResult: { artifactState: "needs_review" },
    humanGate: humanGate({ canConfirmPass: true, requiresHumanReview: true, allowedActions: ["confirm_pass", "request_revision"] }),
  }), { state: "needs_review", canConfirmPass: true, canRequestRevision: true, canUseDownstream: false, actions: ["confirm_pass", "request_revision"] });

  assertActionProjection(artifact({
    id: "review-denied",
    validatorResult: { artifactState: "needs_review" },
    humanGate: humanGate({ requiresHumanReview: true, allowedActions: [] }),
  }), { state: "needs_review", canConfirmPass: false, canRequestRevision: false, canUseDownstream: false, actions: [] });

  assertActionProjection(artifact({
    id: "failed-unscoped-legacy-retry",
    artifactState: "failed",
    humanGate: humanGate({ allowedActions: ["retry"] }),
  }), { state: "failed", canConfirmPass: false, canRequestRevision: false, canUseDownstream: false, actions: [] });

  assertActionProjection(artifact({
    id: "unknown",
    humanGate: humanGate({ canConfirmPass: true, canUseDownstream: true, allowedActions: ["confirm_pass", "request_revision", "use_downstream", "retry"] }),
  }), {
    state: "unknown", canConfirmPass: false, canRequestRevision: false, canUseDownstream: false, actions: [],
  });
});

test("middle and right panel consume one user-visible Artifact set before counts and actions", () => {
  const rows = [artifact({
    id: "public",
    artifactVisibility: "user",
    validatorResult: { artifactState: "usable" },
    humanGate: humanGate({ canUseDownstream: true, allowedActions: ["use_downstream"] }),
  })];
  const hiddenValues = ["internal", "diagnostic", "audit", "developer", "hidden"];
  const visibilityFields = ["artifactVisibility", "visibility", "audience"];
  for (const field of visibilityFields) {
    for (const value of hiddenValues) {
      rows.push(artifact({
        id: `${field}-${value}`,
        [field]: value,
        validatorResult: { artifactState: "usable" },
        humanGate: humanGate({ canUseDownstream: true, allowedActions: ["use_downstream"] }),
      }));
    }
  }

  const result = productionArtifactProjection(rows);
  const middleIds = result.visibleArtifacts.map((row) => row.id);
  const rightIds = result.right.map((row) => row.id);
  assert.deepEqual(middleIds, ["public"]);
  assert.deepEqual(rightIds, middleIds);
  assert.deepEqual(result.counts, {
    raw: 0,
    pending: 0,
    verified: 1,
    rejected: 0,
    unknown: 0,
    total: 1,
  });
  assert.deepEqual(result.right[0].visibleAllowedActions, ["use_downstream"]);

});
