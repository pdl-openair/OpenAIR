import { act, renderHook, waitFor } from "@testing-library/react";
import { StrictMode, useCallback, type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { useWorkbenchAuthority } from "../../useWorkbenchAuthority";
import { useWorkbenchSessionState } from "../../useWorkbenchSessionState";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
const empty = { draft: "", busy: false };

describe("task-scoped Workbench authority", () => {
  it.each(["success", "failure"] as const)(
    "ignores an obsolete A read after A -> B -> A (%s)",
    async (outcome) => {
      const old = deferred<string>();
      const read = vi
        .fn()
        .mockReturnValueOnce(old.promise)
        .mockResolvedValueOnce("B-current")
        .mockResolvedValueOnce("A-current");
      const { result, rerender } = renderHook(
        ({ id }) =>
          useWorkbenchAuthority({
            read: useCallback(() => read(id), [id]),
            enabled: true,
          }),
        { initialProps: { id: "A" } },
      );
      rerender({ id: "B" });
      await waitFor(() => expect(result.current.data).toBe("B-current"));
      rerender({ id: "A" });
      await waitFor(() => expect(result.current.data).toBe("A-current"));
      await act(async () =>
        outcome === "success"
          ? old.resolve("A-obsolete")
          : old.reject(new Error("old failure")),
      );
      expect(result.current.data).toBe("A-current");
      expect(result.current.unavailable).toBe(false);
    },
  );
  it("newer refresh wins over an earlier read", async () => {
    const old = deferred<string>();
    const read = vi
      .fn()
      .mockReturnValueOnce(old.promise)
      .mockResolvedValue("current");
    const { result } = renderHook(() =>
      useWorkbenchAuthority({ read, enabled: true }),
    );
    await act(async () => {
      await result.current.refresh();
    });
    await act(async () => old.resolve("obsolete"));
    expect(result.current.data).toBe("current");
  });
  it("retains policy evidence on failure but marks it unavailable", async () => {
    const read = vi
      .fn()
      .mockResolvedValueOnce("confirmed")
      .mockRejectedValueOnce(new Error("unavailable"));
    const { result } = renderHook(() =>
      useWorkbenchAuthority({ read, enabled: true, retainOnError: true }),
    );
    await waitFor(() => expect(result.current.data).toBe("confirmed"));
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.data).toBe("confirmed");
    expect(result.current.unavailable).toBe(true);
  });
  it("disabling and re-enabling authority does not resurrect an old snapshot", async () => {
    const pending = deferred<string>();
    const read = vi
      .fn()
      .mockResolvedValueOnce("old")
      .mockReturnValueOnce(pending.promise);
    const { result, rerender } = renderHook(
      ({ enabled }) => useWorkbenchAuthority({ read, enabled }),
      { initialProps: { enabled: true } },
    );
    await waitFor(() => expect(result.current.data).toBe("old"));
    rerender({ enabled: false });
    expect(result.current.data).toBeNull();
    rerender({ enabled: true });
    expect(result.current.data).toBeNull();
    await act(async () => pending.resolve("new"));
    expect(result.current.data).toBe("new");
  });
  it("StrictMode cleanup cannot publish the first lifetime's late read", async () => {
    const old = deferred<string>();
    const read = vi
      .fn()
      .mockReturnValueOnce(old.promise)
      .mockResolvedValue("current");
    const { result } = renderHook(
      () => useWorkbenchAuthority({ read, enabled: true }),
      {
        wrapper: ({ children }: { children: ReactNode }) => (
          <StrictMode>{children}</StrictMode>
        ),
      },
    );
    await waitFor(() => expect(result.current.data).toBe("current"));
    await act(async () => old.resolve("obsolete"));
    expect(result.current.data).toBe("current");
  });
  it("session revisits start empty and old callbacks cannot erase the new draft", () => {
    const { result, rerender } = renderHook(
      ({ key }) => useWorkbenchSessionState(key, empty),
      { initialProps: { key: "A" } },
    );
    const oldUpdate = result.current[1];
    act(() => oldUpdate({ draft: "old", busy: true }));
    rerender({ key: "B" });
    expect(result.current[0]).toEqual(empty);
    rerender({ key: "A" });
    expect(result.current[0]).toEqual(empty);
    act(() => result.current[1]({ draft: "new", busy: false }));
    act(() => oldUpdate({ draft: "obsolete", busy: true }));
    expect(result.current[0]).toEqual({ draft: "new", busy: false });
  });
});
