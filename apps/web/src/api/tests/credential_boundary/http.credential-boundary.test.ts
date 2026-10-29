import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  buildAuthHeaders,
  getStoredAuthToken,
  parseJson,
  registerUnauthorizedHandler,
  setAuthToken,
  unregisterUnauthorizedHandler,
} from "../../http";

beforeEach(() => {
  localStorage.clear();
  vi.stubEnv("VITE_OPENAIR_AUTH_TOKEN", "");
});

afterEach(() => {
  unregisterUnauthorizedHandler();
  localStorage.clear();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("explicit browser credentials", () => {
  it("does not invent credentials and preserves an explicitly supplied header", () => {
    expect(buildAuthHeaders().has("Authorization")).toBe(false);
    expect(
      buildAuthHeaders({ Authorization: "Bearer explicit" }).get(
        "Authorization",
      ),
    ).toBe("Bearer explicit");
  });

  it("prefers stored login credentials over environment credentials without logging them", () => {
    const spies = ["log", "debug", "info", "warn", "error"].map((method) =>
      vi.spyOn(console, method as "log").mockImplementation(() => undefined),
    );
    vi.stubEnv("VITE_OPENAIR_AUTH_TOKEN", "environment-token");
    expect(buildAuthHeaders().get("Authorization")).toBe(
      "Bearer environment-token",
    );
    setAuthToken("  stored-token  ");
    expect(buildAuthHeaders().get("Authorization")).toBe("Bearer stored-token");
    expect(spies.every((spy) => spy.mock.calls.length === 0)).toBe(true);
  });

  it.each([401, 403])(
    "status %i preserves identity and hides protected backend details",
    async (status) => {
      setAuthToken("original-identity-token");
      const unauthorized = vi.fn();
      registerUnauthorizedHandler(unauthorized);
      const response = new Response(
        JSON.stringify({
          detail: {
            ownerId: "private-owner",
            workspaceId: "private-workspace",
            token: "private-token",
          },
        }),
        { status, headers: { "Content-Type": "application/json" } },
      );
      const error: unknown = await parseJson(response).catch(
        (failure: unknown) => failure,
      );
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).status).toBe(status);
      expect((error as Error).message).not.toMatch(
        /private-owner|private-workspace|private-token/,
      );
      expect(unauthorized).toHaveBeenCalledTimes(status === 401 ? 1 : 0);
      expect(getStoredAuthToken()).toBe("original-identity-token");
    },
  );
});
