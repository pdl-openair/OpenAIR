import assert from "node:assert/strict";
import test from "node:test";
import { getWorkbenchTaskRequestCoordinator, resetWorkbenchTaskRequestCoordinators } from "../src/lib/workbenchTaskRequestCoordinator.mjs";

class Source { constructor() { this.closed = false; } close() { this.closed = true; } }

test.afterEach(() => resetWorkbenchTaskRequestCoordinators());

test("two subscribers create one EventSource and Last-Event-ID is retained", () => {
  let source; let count = 0; let last;
  const coordinator = getWorkbenchTaskRequestCoordinator("o", "w", "t", {
    connectSse: ({ lastEventId }) => { last = lastEventId; count += 1; source = new Source(); return source; },
  });
  const first = coordinator.subscribe(() => {});
  const second = coordinator.subscribe(() => {});
  assert.equal(count, 1);
  source.onmessage?.({ lastEventId: "event-1", data: "{}" });
  source.onerror?.();
  assert.equal(last, "event-1");
  first(); second(); assert.equal(source.closed, true);
});

test("task generation fences old SSE and terminal status closes it", () => {
  const sources = [];
  const coordinator = getWorkbenchTaskRequestCoordinator("o", "w", "a", {
    connectSse: () => { const source = new Source(); source.onmessage = null; sources.push(source); return source; },
  });
  const unsubscribe = coordinator.subscribe(() => {});
  coordinator.rebind("b");
  assert.equal(sources[0].closed, true);
  coordinator.markTerminal("completed");
  assert.equal(sources[1].closed, true);
  unsubscribe();
});
