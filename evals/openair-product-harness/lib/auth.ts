// Harness-side dev login helper.
//
// Adds Authorization to APIRequestContext-driven calls and seeds the SPA
// AuthContext via localStorage so the dev account can pass through PR #116
// AuthGuard. Strictly local; do NOT log the token, attach it to gates, or
// surface it in page UI / screenshots.

import type { APIRequestContext, Page, PlaywrightWorkerArgs } from "@playwright/test";
import { API_BASE_URL } from "./config";

// Storage keys must mirror apps/web/src/api/http.ts (AUTH_TOKEN_KEY) and
// apps/web/src/context/AuthContext.tsx (AUTH_USER_KEY); kept here as harness
// constants so the harness does not import product code.
const AUTH_TOKEN_STORAGE_KEY = "openair.auth.token.v1";
const AUTH_USER_STORAGE_KEY = "openair.auth.user.v2";

export const HARNESS_DEV_EMAIL =
  process.env.OPENAIR_PRODUCT_HARNESS_DEV_EMAIL?.trim() || "dev@openair.dev";

const HARNESS_DEV_PASSWORD =
  process.env.OPENAIR_PRODUCT_HARNESS_DEV_PASSWORD?.trim() || "dev123456";

export const HARNESS_ADMIN_EMAIL =
  process.env.OPENAIR_PRODUCT_HARNESS_ADMIN_EMAIL?.trim() || "admin@openair.dev";

const HARNESS_ADMIN_PASSWORD =
  process.env.OPENAIR_PRODUCT_HARNESS_ADMIN_PASSWORD?.trim() || "admin123456";

interface AuthMeResponse {
  userId: string;
  email: string;
  username: string;
  displayName: string | null;
  ownerId: string;
  defaultWorkspaceId: string;
}

interface AuthTokenResponse {
  access_token: string;
  token_type: string;
  user: AuthMeResponse;
}

/**
 * Browser-side AuthUser shape (mirrors apps/web/src/context/AuthContext.tsx).
 * Field name `workspaceId` (not `defaultWorkspaceId`) is required so the
 * SPA's restoreUser() accepts the seeded payload.
 */
export interface BrowserAuthUser {
  userId: string;
  email: string;
  username: string;
  displayName: string | null;
  ownerId: string;
  workspaceId: string;
  createdBy: string;
}

export interface DevSession {
  /** Pre-authenticated APIRequestContext. Caller must dispose() in finally. */
  request: APIRequestContext;
  /** Browser-shaped user payload for localStorage seeding. */
  browserUser: BrowserAuthUser;
  /** Internal access token. NEVER log, attach to gates, or render to UI. */
  token: string;
}

type AuthProbeResponse = {
  authRequired?: boolean;
};

/** Build a plain API request context without Authorization header. */
export async function createApiRequestContext(
  playwright: PlaywrightWorkerArgs["playwright"],
  apiBaseUrl: string = API_BASE_URL,
): Promise<APIRequestContext> {
  return playwright.request.newContext({ baseURL: apiBaseUrl });
}

/**
 * Resolve whether harness should run in JWT auth mode.
 *
 * Priority:
 * 1) OPENAIR_PRODUCT_HARNESS_AUTH_REQUIRED=1|0 explicit override
 * 2) GET /health -> authRequired
 * 3) fallback false (open mode)
 */
export async function resolveHarnessAuthRequired(
  playwright: PlaywrightWorkerArgs["playwright"],
  apiBaseUrl: string = API_BASE_URL,
): Promise<boolean> {
  const forced = process.env.OPENAIR_PRODUCT_HARNESS_AUTH_REQUIRED?.trim();
  if (forced === "1") return true;
  if (forced === "0") return false;

  const probe = await createApiRequestContext(playwright, apiBaseUrl);
  try {
    const response = await probe.get("/health");
    if (!response.ok()) return false;
    const body = (await response.json().catch(() => ({}))) as AuthProbeResponse;
    return body.authRequired === true;
  } catch {
    return false;
  } finally {
    await probe.dispose();
  }
}

/**
 * Login as the dev seed account and return an Authed APIRequestContext.
 *
 * - Bootstraps a short-lived APIRequestContext to call POST /api/auth/login.
 * - Builds a long-lived APIRequestContext with `Authorization: Bearer <token>`.
 * - Token never leaves the returned object; do not console.log it.
 */
export async function loginAsDev(
  playwright: PlaywrightWorkerArgs["playwright"],
  apiBaseUrl: string = API_BASE_URL,
): Promise<DevSession> {
  const bootstrap = await playwright.request.newContext({ baseURL: apiBaseUrl });
  try {
    const response = await bootstrap.post("/api/auth/login", {
      data: { email: HARNESS_DEV_EMAIL, password: HARNESS_DEV_PASSWORD },
    });
    if (!response.ok()) {
      const detail = await response.text().catch(() => "<no body>");
      throw new Error(
        `Harness dev login failed: HTTP ${response.status()} ${detail.slice(0, 160)}`,
      );
    }
    const body = (await response.json()) as AuthTokenResponse;
    const token = body.access_token;
    const serverUser = body.user;
    if (!token || !serverUser?.userId || !serverUser?.defaultWorkspaceId) {
      throw new Error(
        "Harness dev login response missing access_token / user.userId / user.defaultWorkspaceId",
      );
    }
    const authedRequest = await playwright.request.newContext({
      baseURL: apiBaseUrl,
      extraHTTPHeaders: { Authorization: `Bearer ${token}` },
    });
    return {
      request: authedRequest,
      token,
      browserUser: {
        userId: serverUser.userId,
        email: serverUser.email,
        username: serverUser.username,
        displayName: serverUser.displayName,
        ownerId: serverUser.ownerId,
        workspaceId: serverUser.defaultWorkspaceId,
        createdBy: serverUser.ownerId,
      },
    };
  } finally {
    await bootstrap.dispose();
  }
}

/**
 * Login as the seeded admin account (needed to activate pending registrations).
 */
export async function loginAsAdmin(
  playwright: PlaywrightWorkerArgs["playwright"],
  apiBaseUrl: string = API_BASE_URL,
): Promise<DevSession> {
  const bootstrap = await playwright.request.newContext({ baseURL: apiBaseUrl });
  try {
    const response = await bootstrap.post("/api/auth/login", {
      data: { email: HARNESS_ADMIN_EMAIL, password: HARNESS_ADMIN_PASSWORD },
    });
    if (!response.ok()) {
      const detail = await response.text().catch(() => "<no body>");
      throw new Error(
        `Harness admin login failed: HTTP ${response.status()} ${detail.slice(0, 160)}`,
      );
    }
    const body = (await response.json()) as AuthTokenResponse;
    const token = body.access_token;
    const serverUser = body.user;
    if (!token || !serverUser?.userId || !serverUser?.defaultWorkspaceId) {
      throw new Error(
        "Harness admin login response missing access_token / user.userId / user.defaultWorkspaceId",
      );
    }
    const authedRequest = await playwright.request.newContext({
      baseURL: apiBaseUrl,
      extraHTTPHeaders: { Authorization: `Bearer ${token}` },
    });
    return {
      request: authedRequest,
      token,
      browserUser: {
        userId: serverUser.userId,
        email: serverUser.email,
        username: serverUser.username,
        displayName: serverUser.displayName,
        ownerId: serverUser.ownerId,
        workspaceId: serverUser.defaultWorkspaceId,
        createdBy: serverUser.ownerId,
      },
    };
  } finally {
    await bootstrap.dispose();
  }
}

export async function activateAccount(
  adminRequest: APIRequestContext,
  userId: string,
  reason: string = "harness activate",
): Promise<void> {
  const response = await adminRequest.post(`/api/admin/accounts/${encodeURIComponent(userId)}/active`, {
    data: { isActive: true, reason },
  });
  if (!response.ok()) {
    const detail = await response.text().catch(() => "<no body>");
    throw new Error(
      `Harness activate failed: HTTP ${response.status()} ${detail.slice(0, 160)}`,
    );
  }
}

/**
 * Seed browser-side AuthContext via localStorage so subsequent navigations to
 * protected routes (/tasks, /workbench) bypass the redirect to /login.
 *
 * Implementation: registers an addInitScript that writes both keys BEFORE any
 * page script runs, so AuthContext's mount useEffect reads the token on its
 * very first execution and sets `initialized: true, authenticated: true`
 * synchronously without ever flipping through an unauthenticated state.
 *
 * - Uses the exact storage keys the SPA reads (mirrors apps/web).
 * - Token is passed through addInitScript args; harness never logs it.
 * - Must be called BEFORE the first navigation to any route.
 */
export async function seedBrowserAuth(page: Page, session: DevSession): Promise<void> {
  await page.addInitScript(
    (args: { tokenKey: string; userKey: string; token: string; userJson: string }) => {
      try {
        window.localStorage.setItem(args.tokenKey, args.token);
        window.localStorage.setItem(args.userKey, args.userJson);
      } catch (e) {
        // eslint-disable-next-line no-console
        console.warn("[harness] localStorage auth seed failed:", e);
      }
    },
    {
      tokenKey: AUTH_TOKEN_STORAGE_KEY,
      userKey: AUTH_USER_STORAGE_KEY,
      token: session.token,
      userJson: JSON.stringify(session.browserUser),
    },
  );
}

/**
 * Single entry for browser + API dev auth in Product Harness.
 * Call before the first navigation to AuthGuard-protected routes.
 */
export async function prepareDevHarnessAuth(
  playwright: PlaywrightWorkerArgs["playwright"],
  page: Page,
  apiBaseUrl: string = API_BASE_URL,
): Promise<DevSession> {
  const session = await loginAsDev(playwright, apiBaseUrl);
  await seedBrowserAuth(page, session);
  return session;
}

export async function disposeDevSession(session: DevSession): Promise<void> {
  await session.request.dispose();
}
