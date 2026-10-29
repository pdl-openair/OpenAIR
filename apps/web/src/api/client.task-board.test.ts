import { afterEach, describe, expect, it, vi } from "vitest";
import { deleteTask, fetchTasks } from "./client";
import {
  ApiError,
  registerUnauthorizedHandler,
  unregisterUnauthorizedHandler
} from "./http";

afterEach(() => {
  vi.unstubAllGlobals();
  unregisterUnauthorizedHandler();
});

describe("task API board contracts", () => {
  it("rejects malformed 200 task-list payload instead of treating it as empty", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({}), {
          status: 200,
          headers: { "Content-Type": "application/json" }
        })
      )
    );

    await expect(fetchTasks()).rejects.toMatchObject({
      status: 200,
      code: "response_contract_invalid"
    });
  });

  it("uses shared 401 parsing and triggers the global session handler for delete", async () => {
    const unauthorized = vi.fn();
    registerUnauthorizedHandler(unauthorized);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "internal owner data" }), {
          status: 401,
          headers: { "Content-Type": "application/json" }
        })
      )
    );

    await expect(deleteTask("task-1")).rejects.toMatchObject({
      status: 401
    });
    expect(unauthorized).toHaveBeenCalledTimes(1);
  });
});
