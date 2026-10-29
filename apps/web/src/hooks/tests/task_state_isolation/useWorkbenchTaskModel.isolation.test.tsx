import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { useLayoutEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Task } from "@openair/shared";
import type { TaskEvent } from "../../../api/client";

const mocks = vi.hoisted(() => ({ fetchTask: vi.fn(), stream: vi.fn() }));
vi.mock("../../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../../api/client")>()),
  fetchTask: mocks.fetchTask,
  subscribeTaskEvents: mocks.stream,
}));
import { useWorkbenchTaskModel } from "../../useWorkbenchTaskModel";
import { resetWorkbenchTaskRequestCoordinators } from "../../../lib/workbenchTaskRequestCoordinator";
import { readCurrentTask } from "../../../lib/userRouteState";
import { CS_RESEARCH_OPE_FIXTURE_TASK_ID } from "../../../data/csResearchOpeFixture";

function task(taskId: string, status: Task["status"] = "completed"): Task {
  return {
    taskId,
    ownerId: "owner-1",
    workspaceId: "workspace-1",
    createdBy: "user-1",
    revision: 9,
    title: taskId,
    currentStage: "problem_modeling",
    status,
    resultSource: "live",
    stages: ["problem_modeling"],
    artifacts: [],
    confirmations: [],
    capabilityCalls: [],
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
const streams: Array<{ taskId: string; emit: (event: TaskEvent) => void }> = [];
beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  streams.length = 0;
  mocks.fetchTask.mockImplementation(async (id: string) => task(id));
  mocks.stream.mockImplementation(
    (id: string, emit: (event: TaskEvent) => void, signal: AbortSignal) => {
      streams.push({ taskId: id, emit });
      return new Promise<void>((resolve) =>
        signal.addEventListener("abort", () => resolve(), { once: true }),
      );
    },
  );
});
afterEach(() => {
  cleanup();
  resetWorkbenchTaskRequestCoordinators();
});

function lifecycle(
  taskId = "task-a",
  runId = "run-1",
  type: TaskEvent["type"] = "agent_lifecycle",
): TaskEvent {
  return {
    id: `lifecycle:${taskId}:run:${runId}:${type}:unit:started`,
    type,
    taskId,
    runId,
    revision: 9,
    scope: { taskId, runId, ownerId: "owner-1", workspaceId: "workspace-1" },
    payload: { revision: 9, runId },
  };
}

describe("Workbench task loading and event isolation", () => {
  it.each(["success", "failure"] as const)(
    "ignores Task A's late %s after Task B loads",
    async (outcome) => {
      const pending = deferred<Task>();
      mocks.fetchTask.mockImplementation((id: string) =>
        id === "task-a" ? pending.promise : Promise.resolve(task(id)),
      );
      const { result, rerender } = renderHook(
        ({ id }) => useWorkbenchTaskModel(id),
        { initialProps: { id: "task-a" } },
      );
      await waitFor(() =>
        expect(mocks.fetchTask).toHaveBeenCalledWith("task-a"),
      );
      rerender({ id: "task-b" });
      await waitFor(() => expect(result.current.status).toBe("ready"));
      await act(async () => {
        if (outcome === "success") pending.resolve(task("task-a"));
        else pending.reject(new Error("late failure"));
      });
      expect(result.current).toMatchObject({
        status: "ready",
        source: "api",
        model: { taskId: "task-b" },
      });
      expect(readCurrentTask()).toBe("task-b");
    },
  );

  it("commits no Task A data while Task B is still loading", async () => {
    const pending = deferred<Task>();
    mocks.fetchTask.mockImplementation((id: string) =>
      id === "task-b" ? pending.promise : Promise.resolve(task(id)),
    );
    const committed: Array<{ status: string; taskId?: string }> = [];
    const { result, rerender } = renderHook(
      ({ id }) => {
        const state = useWorkbenchTaskModel(id);
        useLayoutEffect(() => {
          committed.push({
            status: state.status,
            taskId: state.status === "ready" ? state.model.taskId : undefined,
          });
        });
        return state;
      },
      { initialProps: { id: "task-a" } },
    );
    await waitFor(() => expect(result.current.status).toBe("ready"));
    committed.length = 0;
    rerender({ id: "task-b" });
    expect(result.current.status).toBe("loading");
    expect(committed.filter((state) => state.taskId === "task-a")).toEqual([]);
    await act(async () => pending.resolve(task("task-b")));
    expect(result.current).toMatchObject({
      status: "ready",
      model: { taskId: "task-b" },
    });
  });

  it("rejects a different Task returned for the requested route", async () => {
    mocks.fetchTask.mockResolvedValue(task("task-b"));
    const { result } = renderHook(() => useWorkbenchTaskModel("task-a"));
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(readCurrentTask()).toBeUndefined();
    expect(streams).toEqual([]);
  });

  it("never substitutes a known demo fixture for authoritative API absence", async () => {
    mocks.fetchTask.mockResolvedValue(null);
    const { result } = renderHook(() =>
      useWorkbenchTaskModel(CS_RESEARCH_OPE_FIXTURE_TASK_ID),
    );
    await waitFor(() =>
      expect(result.current).toMatchObject({
        status: "error",
        errorKind: "notFound",
      }),
    );
    expect(readCurrentTask()).toBeUndefined();
    expect(streams).toEqual([]);
  });

  it("retains only current-owner, current-workspace, current-revision and current-run events", async () => {
    mocks.fetchTask.mockResolvedValue(task("task-a", "running"));
    const { result } = renderHook(() => useWorkbenchTaskModel("task-a"));
    await waitFor(() => expect(streams).toHaveLength(1));
    const valid = lifecycle();
    await act(async () => {
      streams[0].emit(lifecycle("task-a", "run-1", "snapshot"));
      streams[0].emit({
        ...valid,
        id: `${valid.id}:owner-mismatch`,
        scope: { ...valid.scope!, ownerId: "owner-2" },
      });
      streams[0].emit({
        ...valid,
        id: `${valid.id}:workspace-mismatch`,
        scope: { ...valid.scope!, workspaceId: "workspace-2" },
      });
      streams[0].emit({
        ...valid,
        revision: 8,
        payload: { revision: 8, runId: "run-1" },
      });
      streams[0].emit(lifecycle("task-b"));
      streams[0].emit(lifecycle("task-a", "other-run"));
      streams[0].emit({
        ...valid,
        id: "lifecycle:task-a:run:forged:agent_lifecycle:unit:started",
      });
      streams[0].emit(valid);
    });
    expect(result.current).toMatchObject({
      status: "ready",
      model: { lifecycleEvents: [valid] },
    });
  });

  it("does not resurrect old stream events on Task A to B to A navigation", async () => {
    mocks.fetchTask.mockImplementation(async (id: string) =>
      task(id, "running"),
    );
    const { result, rerender } = renderHook(
      ({ id }) => useWorkbenchTaskModel(id),
      { initialProps: { id: "task-a" } },
    );
    await waitFor(() => expect(streams).toHaveLength(1));
    await act(async () => {
      streams[0].emit(lifecycle("task-a", "old-run", "snapshot"));
      streams[0].emit(lifecycle("task-a", "old-run"));
    });
    expect(result.current).toMatchObject({
      model: { lifecycleEvents: [lifecycle("task-a", "old-run")] },
    });
    rerender({ id: "task-b" });
    await waitFor(() => expect(streams).toHaveLength(2));
    rerender({ id: "task-a" });
    await waitFor(() => expect(streams).toHaveLength(3));
    await act(async () => {
      streams[0].emit(lifecycle("task-a", "old-run"));
      streams[1].emit(lifecycle("task-b"));
      streams[2].emit(lifecycle("task-a", "new-run", "snapshot"));
      streams[2].emit(lifecycle("task-a", "new-run"));
    });
    expect(result.current).toMatchObject({
      status: "ready",
      model: {
        taskId: "task-a",
        lifecycleEvents: [lifecycle("task-a", "new-run")],
      },
    });
  });
});
