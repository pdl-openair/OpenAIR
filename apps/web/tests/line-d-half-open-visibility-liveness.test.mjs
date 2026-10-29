import assert from "node:assert/strict";
import test from "node:test";
import {
  getWorkbenchTaskRequestCoordinator,
  resetWorkbenchTaskRequestCoordinators,
} from "../src/lib/workbenchTaskRequestCoordinator.mjs";

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

function createManagedProbeScenario(taskId = "task-a") {
  const clock = createClock();
  let calls = 0;
  const controls = new Map();
  const coordinator = getWorkbenchTaskRequestCoordinator("workbench-page", "canonical", taskId, {
    now: clock.now,
    random: () => 0.5,
    setTimeout: clock.setTimeout,
    clearTimeout: clock.clearTimeout,
    visibilityState: "visible",
  });
  const unsubscribe = coordinator.subscribeManagedStream(
    "task-event-stream",
    ({ signal, onEvent }) => {
      calls += 1;
      if (calls === 1) return Promise.reject(rateLimitError());
      return new Promise((resolve, reject) => {
        const control = { resolve, reject, onEvent, signal };
        controls.set(calls, control);
        signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
      });
    },
    () => {},
  );
  return { clock, coordinator, unsubscribe, controls, calls: () => calls };
}

async function reachPendingHalfOpen(scenario) {
  await flush();
  await flush();
  assert.equal(scenario.calls(), 1, "initial SSE attempt returns 429");
  const retryAt = scenario.coordinator.backoffUntil;
  await scenario.clock.advanceTo(retryAt - 1);
  assert.equal(scenario.calls(), 1, "Retry-After prevents an early probe");
  await scenario.clock.advanceTo(retryAt);
  assert.equal(scenario.calls(), 2, "Retry-After expiry creates one half-open SSE probe");
  assert.equal(scenario.coordinator.circuitState, "half-open");
  assert.equal(scenario.coordinator.backoff.probeInFlight, true);
}

test.afterEach(() => resetWorkbenchTaskRequestCoordinators());

test("hidden abort releases half-open ownership and visible starts exactly one replacement probe", async () => {
  const scenario = createManagedProbeScenario();
  await reachPendingHalfOpen(scenario);

  scenario.coordinator.setVisibility("hidden");
  await flush();
  assert.equal(scenario.calls(), 2, "hidden state does not create transport calls");
  assert.equal(scenario.coordinator.circuitState, "open");
  assert.equal(scenario.coordinator.backoff.probeInFlight, false);
  assert.equal(scenario.coordinator._probeOwner, null);

  scenario.coordinator.setVisibility("visible");
  await flush();
  assert.equal(scenario.calls(), 3, "visible creates one replacement half-open probe");
  assert.equal(scenario.coordinator.backoff.probeInFlight, true);

  scenario.controls.get(3).onEvent({ id: "evt-3", data: { status: "running" } });
  await flush();
  assert.equal(scenario.coordinator.circuitState, "closed");
  assert.equal(scenario.coordinator.backoff.probeInFlight, false);
  assert.equal(scenario.coordinator._probeOwner, null);

  scenario.controls.get(3).reject(new Error("stream closed after recovery"));
  await flush();
  const reconnectAt = scenario.clock.activeTimers().at(-1)?.dueAt;
  assert.ok(Number.isFinite(reconnectAt));
  await scenario.clock.advanceTo(reconnectAt);
  assert.equal(scenario.calls(), 4, "ordinary reconnect remains live after probe recovery");
  scenario.unsubscribe();
});

test("half-open lifecycle cleanup removes ownership for final unsubscribe, generation rebind and terminal close", async (t) => {
  await t.test("last subscriber", async () => {
    const scenario = createManagedProbeScenario("task-unmount");
    await reachPendingHalfOpen(scenario);
    scenario.unsubscribe();
    await flush();
    assert.equal(scenario.coordinator.backoff.probeInFlight, false);
    assert.equal(scenario.coordinator._probeOwner, null);
    assert.equal(scenario.coordinator.managedStreams.size, 0);
    assert.equal(scenario.clock.activeTimers().length, 0);
    scenario.coordinator.setVisibility("hidden");
    scenario.coordinator.setVisibility("visible");
    await flush();
    assert.equal(scenario.calls(), 2, "unsubscribed entry cannot revive");
  });

  await t.test("generation rebind", async () => {
    const scenario = createManagedProbeScenario("task-a");
    await reachPendingHalfOpen(scenario);
    const oldControl = scenario.controls.get(2);
    scenario.coordinator.rebind("task-b");
    await flush();
    assert.equal(scenario.coordinator.backoff.probeInFlight, false);
    assert.equal(scenario.coordinator._probeOwner, null);
    let nextGenerationCalls = 0;
    const unsubscribeNext = scenario.coordinator.subscribeManagedStream(
      "task-event-stream",
      ({ signal }) => {
        nextGenerationCalls += 1;
        return new Promise((_, reject) => {
          signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
        });
      },
      () => {},
    );
    assert.equal(nextGenerationCalls, 1, "new generation owns one connection");
    const callsBeforeOldCallback = scenario.calls();
    oldControl.reject(new Error("late old generation failure"));
    await flush();
    assert.equal(scenario.calls(), callsBeforeOldCallback);
    assert.equal(nextGenerationCalls, 1, "old callback cannot replace the new generation transport");
    assert.equal(scenario.coordinator.taskId, "task-b");
    unsubscribeNext();
    scenario.unsubscribe();
  });

  await t.test("terminal close", async () => {
    const scenario = createManagedProbeScenario("task-terminal");
    await reachPendingHalfOpen(scenario);
    scenario.coordinator.markManagedStreamTerminal("task-event-stream");
    await flush();
    assert.equal(scenario.coordinator.backoff.probeInFlight, false);
    assert.equal(scenario.coordinator._probeOwner, null);
    assert.equal(scenario.clock.activeTimers().length, 0);
    scenario.coordinator.setVisibility("hidden");
    scenario.coordinator.setVisibility("visible");
    await flush();
    assert.equal(scenario.calls(), 2, "terminal stream cannot revive");
    scenario.unsubscribe();
  });
});

test("SSE and GET share one half-open owner and GET recovers after replacement probe succeeds", async () => {
  const scenario = createManagedProbeScenario();
  await reachPendingHalfOpen(scenario);
  scenario.coordinator.setVisibility("hidden");
  await flush();
  scenario.coordinator.setVisibility("visible");
  await flush();
  assert.equal(scenario.calls(), 3);

  let getCalls = 0;
  await assert.rejects(
    scenario.coordinator.request("task-model", () => {
      getCalls += 1;
      return Promise.resolve({ ok: true });
    }),
    (error) => error?.code === "rate_limited",
  );
  assert.equal(getCalls, 0, "GET cannot steal an active SSE half-open probe");
  assert.equal(scenario.coordinator.backoff.probeInFlight, true);

  scenario.controls.get(3).onEvent({ id: "evt-success", data: { status: "running" } });
  await flush();
  const result = await scenario.coordinator.request("task-model", () => {
    getCalls += 1;
    return Promise.resolve({ ok: true, value: "fresh" });
  });
  assert.equal(result.value, "fresh");
  assert.equal(getCalls, 1);
  assert.equal(scenario.coordinator.circuitState, "closed");
  assert.equal(scenario.coordinator.backoff.probeInFlight, false);
  scenario.unsubscribe();
});
