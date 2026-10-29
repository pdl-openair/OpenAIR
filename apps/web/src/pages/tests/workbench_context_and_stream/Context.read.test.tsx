import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useResearchStartDisplayContext } from "../../../hooks/useResearchStartDisplayContext";
const deps = vi.hoisted(() => ({ fetch: vi.fn() }));
vi.mock("../../../api/researchStartDisplayContext", () => ({
  fetchResearchStartDisplayContext: deps.fetch,
}));
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  deps.fetch.mockReset();
});
it("recovers transient context reads within a bounded window without discarding known input", async () => {
  vi.useFakeTimers();
  const value = {
    taskId: "task",
    sessionId: "session",
    originalInput: "原始输入",
    displayTitle: "新研究",
    titlePending: true,
  };
  deps.fetch
    .mockResolvedValueOnce(value)
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValue({
      ...value,
      displayTitle: "研究短名",
      titlePending: false,
    });
  const reload = vi.fn();
  const view = renderHook(() =>
    useResearchStartDisplayContext("task", "session", reload),
  );
  await act(async () => {});
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1500);
  });
  expect(view.result.current?.status).toBe("unavailable");
  expect(view.result.current?.value?.originalInput).toBe("原始输入");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1500);
  });
  expect(view.result.current?.value?.displayTitle).toBe("研究短名");
  expect(reload).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60000);
  });
  expect(deps.fetch).toHaveBeenCalledTimes(3);
});
it("stops pending reads after 30 seconds and cancels polling after navigation", async () => {
  vi.useFakeTimers();
  deps.fetch.mockResolvedValue({
    taskId: "task",
    sessionId: "session",
    originalInput: "原文",
    displayTitle: null,
    titlePending: true,
  });
  const view = renderHook(() =>
    useResearchStartDisplayContext("task", "session"),
  );
  await act(async () => {});
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30000);
  });
  const calls = deps.fetch.mock.calls.length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60000);
  });
  expect(deps.fetch).toHaveBeenCalledTimes(calls);
  expect(calls).toBeLessThanOrEqual(21);
  view.unmount();
  expect(vi.getTimerCount()).toBe(0);
});
