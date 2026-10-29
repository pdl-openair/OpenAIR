import assert from "node:assert/strict";
import test from "node:test";
import {
  getWorkbenchTaskRequestCoordinator,
  resetWorkbenchTaskRequestCoordinators,
} from "../src/lib/workbenchTaskRequestCoordinator.mjs";
import { parseRetryAfter, RateLimitBackoff } from "../src/lib/rateLimitBackoff.mjs";

class FakeSource {
  constructor() { this.closed = false; this.onopen = null; this.onmessage = null; this.onerror = null; }
  close() { this.closed = true; }
}

function response(status = 200, body = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

test.afterEach(() => resetWorkbenchTaskRequestCoordinators());

test("same Task subscribers share one SSE and final unsubscribe closes it", () => {
  let connects = 0;
  let source;
  const c = getWorkbenchTaskRequestCoordinator("owner", "workspace", "task", {
    connectSse: () => { connects += 1; source = new FakeSource(); return source; },
  });
  const a = c.subscribe(() => {});
  const b = c.subscribe(() => {});
  assert.equal(connects, 1);
  a(); assert.equal(source.closed, false);
  b(); assert.equal(source.closed, true);
});

test("StrictMode remount is one active connection and terminal tasks clean up", () => {
  let connects = 0; let source;
  const c = getWorkbenchTaskRequestCoordinator("o", "w", "t", { connectSse: () => { connects += 1; source = new FakeSource(); return source; } });
  const first = c.subscribe(() => {}); first();
  const second = c.subscribe(() => {});
  assert.equal(connects, 2); // the first mount fully unmounted before remount
  c.markTerminal("completed");
  assert.equal(source.closed, true); assert.equal(c.subscribers.size, 0); second();
});

test("task switch fences old generation, aborts GET, clears SSE and timers", async () => {
  let source; let firstSource; let aborts = 0;
  const c = getWorkbenchTaskRequestCoordinator("o", "w", "a", { connectSse: () => { source = new FakeSource(); firstSource ??= source; return source; } });
  const unsubscribe = c.subscribe(() => {});
  const pending = c.fetchTask((signal) => new Promise((resolve, reject) => {
    signal.addEventListener("abort", () => { aborts += 1; reject(new DOMException("aborted", "AbortError")); });
    setTimeout(() => resolve(response()), 10);
  }));
  c.rebind("b");
  assert.equal(c.generation, 1); assert.equal(firstSource.closed, true); assert.equal(aborts, 1);
  await assert.rejects(pending, /aborted/);
  unsubscribe();
});

test("A to B to A generations isolate events and owner/workspace/task keys", () => {
  let sources = [];
  const c = getWorkbenchTaskRequestCoordinator("o", "w", "a", { connectSse: () => { const s = new FakeSource(); sources.push(s); return s; } });
  const seen = []; const unsubscribe = c.subscribe((event) => seen.push(event.value));
  sources[0].onmessage({ value: "a1", lastEventId: "1" });
  c.rebind("b"); c.rebind("a");
  sources[0].onmessage({ value: "stale", lastEventId: "old" });
  sources[1]?.onmessage?.({ value: "b", lastEventId: "2" });
  sources[2]?.onmessage?.({ value: "a2", lastEventId: "3" });
  assert.deepEqual(seen, ["a1", "a2"]); assert.equal(c.lastEventId, "3"); unsubscribe();
  assert.notEqual(getWorkbenchTaskRequestCoordinator("o", "w", "a"), getWorkbenchTaskRequestCoordinator("other", "w", "a"));
});

test("same resource concurrent GET is deduped and abort result cannot write to new generation", async () => {
  let calls = 0; let release;
  const c = getWorkbenchTaskRequestCoordinator("o", "w", "t");
  const request = () => { calls += 1; return new Promise((resolve) => { release = () => resolve(response(200, { ok: true })); }); };
  const one = c.fetchTask(request); const two = c.fetchTask(request);
  assert.equal(one, two); assert.equal(calls, 1);
  c.rebind("new"); release(); assert.equal(await one, undefined);
});

test("Retry-After header takes precedence, including integer seconds and HTTP dates", () => {
  const now = Date.parse("Wed, 21 Oct 2015 07:28:00 GMT");
  assert.equal(parseRetryAfter({ headers: new Headers({ "Retry-After": "2" }), body: { retryAfter: 99 } }, now), 2_000);
  assert.equal(parseRetryAfter({ headers: new Headers({ "Retry-After": new Date(now + 4_000).toUTCString() }), body: { retryAfter: 99 } }, now), 4_000);
  assert.equal(parseRetryAfter({ headers: new Headers(), body: { retryAfter: 3 } }, now), 3_000);
});

test("429 creates shared backoff, blocks all resources, and only one half-open probe", async () => {
  let now = 0; let calls = 0; let timers = [];
  const c = getWorkbenchTaskRequestCoordinator("o", "w", "t", {
    now: () => now, setTimeout: (fn, delay) => { timers.push({ fn, delay }); return timers.length; }, clearTimeout: () => {},
  });
  await assert.rejects(c.fetchTask(() => response(429, { retryAfter: 2 })), (error) => error.code === "rate_limited");
  assert.equal(c.backoffUntil, 2_000); assert.equal(c.circuitState, "open");
  await assert.rejects(c.fetchCollaboration(() => { calls += 1; return response(); }), /rate limited/);
  assert.equal(calls, 0);
  let probes = 0; const release = c.acquire(); c._scheduleProbe(); now = 2_000; timers.at(-1)?.fn();
  assert.equal(c.circuitState, "open");
  const probe = c.authoritativeRefresh(() => new Promise((resolve) => {
    probes += 1;
    setImmediate(() => resolve(response()));
  }));
  assert.equal(c.circuitState, "half-open");
  await assert.rejects(c.fetchCollaboration(() => { calls += 1; return response(); }), /rate limited/);
  await probe;
  assert.equal(probes, 1); assert.equal(c.circuitState, "closed");
  release();
});


test("visibility recovery respects shared Retry-After instead of bypassing the circuit", async () => {
  let now = 0;
  let refreshes = 0;
  let timers = [];
  const c = getWorkbenchTaskRequestCoordinator("o", "w", "t", {
    now: () => now,
    setTimeout: (fn, delay) => { timers.push({ fn, delay }); return timers.length; },
    clearTimeout: () => {},
    connectSse: () => new FakeSource(),
    refresh: () => { refreshes += 1; return Promise.resolve(response()); },
  });
  const unsubscribe = c.subscribe(() => {});
  await assert.rejects(c.fetchTask(() => response(429, { retryAfter: 4 })), /rate limit/i);
  c.setVisibility("hidden");
  c.setVisibility("visible");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(refreshes, 0);
  assert.equal(c.circuitState, "open");
  assert.ok(timers.some((entry) => entry.delay === 4_000));
  now = 4_000;
  const probe = c.authoritativeRefresh(() => { refreshes += 1; return Promise.resolve(response()); });
  await probe;
  assert.equal(refreshes, 1);
  assert.equal(c.circuitState, "closed");
  unsubscribe();
});

test("concurrent callers share the same half-open resource probe", async () => {
  let now = 0;
  let calls = 0;
  let resolveProbe;
  const c = getWorkbenchTaskRequestCoordinator("o", "w", "t", { now: () => now });
  await assert.rejects(c.fetchTask(() => response(429, { retryAfter: 1 })), /rate limit/i);
  now = c.backoffUntil;
  const first = c.fetchTask(() => {
    calls += 1;
    return new Promise((resolve) => { resolveProbe = () => resolve(response()); });
  });
  const second = c.fetchTask(() => {
    calls += 1;
    return response();
  });
  assert.equal(first, second);
  assert.equal(calls, 1);
  resolveProbe();
  await first;
  assert.equal(c.circuitState, "closed");
});

test("continuous 429 enters open and successful half-open resets circuit", () => {
  let now = 0; const b = new RateLimitBackoff({ now: () => now, random: () => 0.5 });
  b.retryAfter(1_000); assert.equal(b.circuitState, "open"); now = b.backoffUntil; assert.equal(b.beginProbe(), true);
  b.success(); assert.equal(b.circuitState, "closed"); assert.equal(b.consecutiveRateLimits, 0);
});

test("hidden visibility pauses polling/SSE and visible performs one controlled refresh", async () => {
  let source; let refreshes = 0;
  const c = getWorkbenchTaskRequestCoordinator("o", "w", "t", {
    connectSse: () => { source = new FakeSource(); return source; },
    refresh: () => { refreshes += 1; return Promise.resolve(response()); },
  });
  const unsubscribe = c.subscribe(() => {}); c.fallbackPollTimer = 1; c.setVisibility("hidden");
  assert.equal(source.closed, true); assert.equal(c.fallbackPollTimer, null);
  c.setVisibility("visible"); await new Promise((resolve) => setImmediate(resolve));
  assert.equal(refreshes, 1); unsubscribe();
});

test("terminal Task closes active connection and preserves Last-Event-ID across reconnect", () => {
  let source; const ids = [];
  const c = getWorkbenchTaskRequestCoordinator("o", "w", "t", { connectSse: ({ lastEventId }) => { ids.push(lastEventId); source = new FakeSource(); return source; } });
  const unsubscribe = c.subscribe(() => {}); source.onmessage({ lastEventId: "evt-1", data: JSON.stringify({ taskId: "t" }) });
  source.onerror(); assert.equal(ids[1], "evt-1"); c.markTerminal("failed"); assert.equal(source.closed, true); unsubscribe();
});

test("scheduled planning reads share visibility pause and Retry-After ownership", async () => {
  let now = 0;
  const timers = [];
  const c = getWorkbenchTaskRequestCoordinator("o", "w", "task", {
    now: () => now,
    visibilityState: "visible",
    setTimeout: (fn, delay) => { timers.push({ fn, delay, cleared: false }); return timers.length; },
    clearTimeout: (id) => { if (timers[id - 1]) timers[id - 1].cleared = true; },
  });
  await assert.rejects(
    c.request("task-model", () => Promise.reject(Object.assign(new Error("rate limited"), { status: 429, code: "rate_limited", retryAfterMs: 12_000 }))),
    /rate limited/,
  );
  let reads = 0;
  c.schedule("research-start-session:s1", () => { reads += 1; }, 2_500);
  assert.equal(timers.at(-1).delay, 12_000, "scheduled reads must inherit the shared Retry-After window");
  c.setVisibility("hidden");
  assert.equal(timers.at(-1).cleared, true);
  c.setVisibility("visible");
  assert.equal(timers.at(-1).delay, 12_000);
  now = 12_000;
  timers.at(-1).fn();
  assert.equal(reads, 1);
});

test("timer callback racing a hidden tab retains the scheduled reconciliation", () => {
  let now = 0;
  const timers = [];
  const c = getWorkbenchTaskRequestCoordinator("o", "w", "task", {
    now: () => now,
    visibilityState: "visible",
    setTimeout: (fn, delay) => { timers.push({ fn, delay, cleared: false }); return timers.length; },
    clearTimeout: (id) => { if (timers[id - 1]) timers[id - 1].cleared = true; },
  });
  let reads = 0;
  c.schedule("research-start-session:s1", () => { reads += 1; }, 0);
  const racedTimer = timers.at(-1);
  c.visibilityState = "hidden"; // simulate visibility changing as the timer callback begins
  racedTimer.fn();
  assert.equal(reads, 0);
  assert.equal(c.scheduledRequests.has("research-start-session:s1"), true);
  c.setVisibility("visible");
  const resumedTimer = timers.at(-1);
  assert.notEqual(resumedTimer, racedTimer);
  resumedTimer.fn();
  assert.equal(reads, 1);
});
