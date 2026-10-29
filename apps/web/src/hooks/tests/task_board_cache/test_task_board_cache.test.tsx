import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../../api/http";
import {
  invalidateTaskBoardCache,
  readTaskBoardCache,
  writeTaskBoardCache,
} from "../../../lib/taskBoardCache";
import {
  TASK_BOARD_TIMEOUT_MS,
  useTaskBoardList,
} from "../../useTaskBoardList";
import type { TaskListPage } from "../../../api/client";

const api = vi.hoisted(() => ({ fetchTaskListPage: vi.fn() }));
const auth = vi.hoisted(() => ({
  user: { userId: "user-a", ownerId: "owner-a", workspaceId: "workspace-a" },
}));
vi.mock("../../../api/client", () => api);
vi.mock("../../../context/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("../../../api/http", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../../api/http")>()),
  getApiBaseUrl: () => "http://api",
}));

const scope = JSON.stringify([
  "http://api",
  "user-a",
  "owner-a",
  "workspace-a",
]);
const key = `${scope}:${JSON.stringify([1, 20, "all"])}`;
const cached: TaskListPage = {
  tasks: [],
  total: 7,
  limit: 20,
  offset: 0,
  statusTotals: { completed: 7 },
};

beforeEach(() => {
  sessionStorage.clear();
  api.fetchTaskListPage.mockReset();
  auth.user.workspaceId = "workspace-a";
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("cached board refresh", () => {
  it("shows cached counts immediately while refresh is pending, then adopts live data", async () => {
    writeTaskBoardCache(key, cached);
    let resolve!: (page: TaskListPage) => void;
    api.fetchTaskListPage.mockReturnValue(
      new Promise<TaskListPage>((done) => {
        resolve = done;
      }),
    );
    const { result } = renderHook(() => useTaskBoardList(1, 20, "all"));
    expect(result.current.data?.total).toBe(7);
    expect(result.current.loading).toBe(false);
    expect(result.current.cached).toBe(true);
    await act(async () => resolve({ ...cached, total: 8 }));
    expect(result.current.data?.total).toBe(8);
    expect(result.current.cached).toBe(false);
    expect(readTaskBoardCache(key)?.total).toBe(8);
  });

  it("preserves cache on network failure but removes it on forbidden refresh", async () => {
    writeTaskBoardCache(key, cached);
    api.fetchTaskListPage.mockRejectedValue(new TypeError("network"));
    const { result } = renderHook(() => useTaskBoardList(1, 20, "all"));
    await waitFor(() => expect(result.current.error).toBeTruthy());
    expect(result.current.data?.total).toBe(7);
    api.fetchTaskListPage.mockRejectedValue(new ApiError("forbidden", 403));
    act(() => result.current.reload());
    await waitFor(() => expect(result.current.data).toBeNull());
    expect(readTaskBoardCache(key)).toBeNull();
  });

  it("isolates workspace and pagination and ignores responses after switching", async () => {
    writeTaskBoardCache(key, cached);
    let resolve!: (page: TaskListPage) => void;
    api.fetchTaskListPage.mockReturnValueOnce(
      new Promise<TaskListPage>((done) => {
        resolve = done;
      }),
    );
    api.fetchTaskListPage.mockReturnValue(new Promise(() => {}));
    const { result, rerender } = renderHook(
      ({ page }) => useTaskBoardList(page, 20, "all"),
      { initialProps: { page: 1 } },
    );
    const firstSignal = api.fetchTaskListPage.mock.calls[0][0].signal;
    auth.user.workspaceId = "workspace-b";
    rerender({ page: 2 });
    expect(result.current.data).toBeNull();
    expect(firstSignal.aborted).toBe(true);
    await act(async () => resolve({ ...cached, total: 100 }));
    expect(result.current.data).toBeNull();
    expect(readTaskBoardCache(key)?.total).toBe(7);
  });

  it("ends infinite loading after 15 seconds and supports retry", async () => {
    vi.useFakeTimers();
    api.fetchTaskListPage.mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useTaskBoardList(1, 20, "all"));
    await act(async () => vi.advanceTimersByTimeAsync(TASK_BOARD_TIMEOUT_MS));
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBe("项目加载超时，请重试。");
    expect(api.fetchTaskListPage.mock.calls[0][0].signal.aborted).toBe(true);
    api.fetchTaskListPage.mockResolvedValue(cached);
    await act(async () => result.current.reload());
    expect(result.current.data?.total).toBe(7);
    expect(result.current.error).toBeNull();
  });

  it("expires cache and invalidates all filters after a mutation", () => {
    vi.useFakeTimers();
    writeTaskBoardCache(key, cached);
    vi.advanceTimersByTime(5 * 60_000 + 1);
    expect(readTaskBoardCache(key)).toBeNull();
    writeTaskBoardCache(key, cached);
    writeTaskBoardCache(`${scope}:[2,20,"failed"]`, cached);
    invalidateTaskBoardCache(scope);
    expect(sessionStorage.length).toBe(0);
  });
});
