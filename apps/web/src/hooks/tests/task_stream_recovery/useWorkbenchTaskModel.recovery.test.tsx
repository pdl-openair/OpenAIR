import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Task } from "@openair/shared";
import type { TaskEvent } from "../../../api/client";
const mocks = vi.hoisted(() => ({ stream: vi.fn() }));
vi.mock("../../../api/client", async (original) => ({
  ...(await original<typeof import("../../../api/client")>()),
  subscribeTaskEvents: mocks.stream,
}));
import { useWorkbenchTaskModel } from "../../useWorkbenchTaskModel";
import {
  getWorkbenchTaskRequestCoordinator,
  resetWorkbenchTaskRequestCoordinators,
} from "../../../lib/workbenchTaskRequestCoordinator";
import { ApiError } from "../../../api/http";
import { loadTaskForPages } from "../../../lib/loadTask";
const task: Task = {
  taskId: "task-a",
  ownerId: "owner-1",
  workspaceId: "workspace-1",
  createdBy: "user-1",
  revision: 9,
  title: "research",
  currentStage: "problem_modeling",
  status: "running",
  resultSource: "live",
  stages: ["problem_modeling"],
  artifacts: [],
  confirmations: [],
  capabilityCalls: [],
};
const fetchMock = vi.fn();
function pending(signal: AbortSignal) {
  return new Promise<void>((resolve) =>
    signal.addEventListener("abort", () => resolve(), { once: true }),
  );
}
async function settle() {
  await act(async () => {
    for (let i = 0; i < 15; i++) await Promise.resolve();
  });
}
async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}
beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockImplementation(
    async () =>
      new Response(JSON.stringify(task), {
        headers: { "Content-Type": "application/json" },
      }),
  );
  mocks.stream.mockImplementation(
    (_id: string, _emit: unknown, signal: AbortSignal) => pending(signal),
  );
});
afterEach(() => {
  cleanup();
  resetWorkbenchTaskRequestCoordinators();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
function mount() {
  return renderHook(() => useWorkbenchTaskModel("task-a", "zh-CN"));
}
describe("production hook and coordinator reconnect", () => {
  it("two hooks share EOF reconnect, resume cursor, and retain running truth", async () => {
    const snapshot: TaskEvent = {
      id: "snapshot:task-a:run:run-1:r9",
      type: "snapshot",
      taskId: "task-a",
      runId: "run-1",
      revision: 9,
      scope: {
        taskId: "task-a",
        runId: "run-1",
        ownerId: "owner-1",
        workspaceId: "workspace-1",
      },
      payload: {},
    };
    mocks.stream.mockImplementationOnce(
      async (_id: string, emit: (e: TaskEvent) => void) => {
        emit(snapshot);
      },
    );
    const first = mount();
    const second = mount();
    await settle();
    expect(first.result.current.status).toBe("ready");
    expect(mocks.stream).toHaveBeenCalledTimes(1);
    await advance(1999);
    expect(mocks.stream).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(mocks.stream).toHaveBeenCalledTimes(2);
    expect(mocks.stream.mock.calls[1][3]).toBe(snapshot.id);
    for (const hook of [first, second]) {
      const state = hook.result.current;
      expect(state.status).toBe("ready");
      if (state.status === "ready")
        expect(state.model.taskStatus).toBe("running");
    }
    const signal: AbortSignal = mocks.stream.mock.calls[1][2];
    first.unmount();
    expect(signal.aborted).toBe(false);
    second.unmount();
    expect(signal.aborted).toBe(true);
    await advance(30000);
    expect(mocks.stream).toHaveBeenCalledTimes(2);
  });
  it("shared Retry-After prevents early reconnect and permits one probe", async () => {
    mocks.stream.mockRejectedValueOnce(
      new ApiError({
        status: 429,
        userMessage: "rate limited",
        code: "rate_limited",
        retryAfterMs: 12000,
      }),
    );
    mount();
    mount();
    await settle();
    expect(mocks.stream).toHaveBeenCalledTimes(1);
    await advance(11999);
    expect(mocks.stream).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(mocks.stream).toHaveBeenCalledTimes(2);
  });
  it("visibility resume uses one connection and final unmount prevents future reconnect", async () => {
    const first = mount();
    const second = mount();
    await settle();
    const coordinator = getWorkbenchTaskRequestCoordinator(
      "workbench-page",
      "canonical",
      "task-a",
    );
    await act(async () => coordinator.setVisibility("hidden"));
    expect(mocks.stream.mock.calls[0][2].aborted).toBe(true);
    await act(async () => coordinator.setVisibility("visible"));
    await settle();
    expect(mocks.stream).toHaveBeenCalledTimes(2);
    first.unmount();
    expect(mocks.stream.mock.calls[1][2].aborted).toBe(false);
    second.unmount();
    await advance(30000);
    await act(async () => {
      coordinator.setVisibility("hidden");
      coordinator.setVisibility("visible");
    });
    expect(mocks.stream).toHaveBeenCalledTimes(2);
  });
  it("real primary GET preserves typed 429 and enters the shared coordinator backoff", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: "rate limited" }), {
        status: 429,
        headers: { "Retry-After": "12", "Content-Type": "application/json" },
      }),
    );
    const coordinator = getWorkbenchTaskRequestCoordinator(
      "owner",
      "workspace",
      "task-a",
    );
    const now = Date.now();
    await expect(
      coordinator.request("task-model", () =>
        loadTaskForPages("task-a", "zh-CN", { allowFixtureFallback: false }),
      ),
    ).rejects.toMatchObject({ status: 429, retryAfterMs: 12000 });
    expect(coordinator.backoffUntil).toBe(now + 12000);
    expect(coordinator.circuitState).toBe("open");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
