import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  getApiBaseUrl,
  getStoredAuthToken,
  registerUnauthorizedHandler,
  unregisterUnauthorizedHandler,
  setAuthToken,
  parseJson,
} from "../api/http";

/* ---------- types ---------- */

export interface AuthUser {
  userId: string;
  email: string;
  username: string;
  displayName: string | null;
  ownerId: string;
  workspaceId: string;
  createdBy: string;
  /** "user" | "admin" (Sprint-03 quota). Older caches without role default to "user". */
  role: "user" | "admin";
}

export interface AuthState {
  /** Has the initial auth check completed? */
  initialized: boolean;
  /** Is the user currently authenticated? */
  authenticated: boolean;
  /** Current user identity (null when not authenticated). */
  user: AuthUser | null;
}

export interface AuthContextValue extends AuthState {
  /** Login with email + password. Throws on failure. */
  login: (email: string, password: string) => Promise<AuthUser>;
  /** Register a new account. Does not log in; the account stays pending activation. */
  register: (
    email: string,
    username: string,
    password: string,
    extras?: { displayName?: string; captchaId?: string | null; captchaCode?: string | null }
  ) => Promise<{
    pendingActivation: boolean;
    email: string;
    message: string;
  }>;
  /** Login with a raw Bearer token (legacy / dev mode). Throws on failure. */
  loginWithToken: (token: string) => Promise<AuthUser>;
  /** Clear auth state and redirect to login. */
  logout: () => void;
  /** True when the current user has role=admin (Sprint-03 quota). */
  isAdmin: boolean;
}

/* ---------- server identity resolution ---------- */

interface AuthMeResponse {
  userId: string;
  email: string;
  username: string;
  displayName: string | null;
  ownerId: string;
  defaultWorkspaceId: string;
  role?: string;
}

interface AuthTokenResponse {
  access_token: string;
  token_type: string;
  user: AuthMeResponse;
}

function toAuthUser(serverUser: AuthMeResponse): AuthUser {
  return {
    userId: serverUser.userId,
    email: serverUser.email,
    username: serverUser.username,
    displayName: serverUser.displayName,
    ownerId: serverUser.ownerId,
    workspaceId: serverUser.defaultWorkspaceId,
    createdBy: serverUser.ownerId,
    role: serverUser.role === "admin" ? "admin" : "user",
  };
}

/**
 * Validate the current token and retrieve the user profile via GET /api/auth/me.
 */
async function fetchCurrentUser(token: string): Promise<AuthUser> {
  const headers = new Headers();
  headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(`${getApiBaseUrl()}/api/auth/me`, { headers });

  if (res.status === 401) {
    throw new Error("认证失败：无效的凭证");
  }
  if (res.status === 403) {
    throw new Error("认证失败：无权限访问");
  }
  if (!res.ok) {
    throw new Error(`认证失败：HTTP ${res.status}`);
  }

  const data = await parseJson<AuthMeResponse>(res);
  return toAuthUser(data);
}

/* ---------- storage ---------- */

const AUTH_USER_KEY = "openair.auth.user.v3";

function persistUser(user: AuthUser | null): void {
  if (typeof window === "undefined") return;
  if (user) {
    window.localStorage.setItem(AUTH_USER_KEY, JSON.stringify(user));
  } else {
    window.localStorage.removeItem(AUTH_USER_KEY);
  }
}

function restoreUser(): AuthUser | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(AUTH_USER_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<AuthUser>;
    if (parsed.ownerId && parsed.workspaceId && parsed.userId) {
      return {
        userId: parsed.userId,
        email: parsed.email ?? "",
        username: parsed.username ?? "",
        displayName: parsed.displayName ?? null,
        ownerId: parsed.ownerId,
        workspaceId: parsed.workspaceId,
        createdBy: parsed.createdBy ?? parsed.ownerId,
        role: parsed.role === "admin" ? "admin" : "user",
      };
    }
  } catch {
    // Ignore corrupt data
  }
  return null;
}

/* ---------- context ---------- */

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({
    initialized: false,
    authenticated: false,
    user: null,
  });

  // Register global 401 handler — any API 401 triggers logout
  useEffect(() => {
    const handle401 = () => {
      setAuthToken("");
      persistUser(null);
      setState({ initialized: true, authenticated: false, user: null });
    };
    registerUnauthorizedHandler(handle401);
    return () => unregisterUnauthorizedHandler();
  }, []);

  // On mount, check if there's already a valid token
  useEffect(() => {
    if (import.meta.env.DEV && window.location.search.includes("b2shot=1")) {
      const user: AuthUser = {
        userId: "researcher_user",
        email: "researcher@openair.local",
        username: "researcher",
        displayName: "Researcher",
        ownerId: "researcher_owner",
        workspaceId: "researcher_workspace",
        createdBy: "researcher_owner",
        role: "user",
      };
      persistUser(user);
      setState({ initialized: true, authenticated: true, user });
      return;
    }

    const token = getStoredAuthToken();
    const savedUser = restoreUser();

    if (!token) {
      setState({ initialized: true, authenticated: false, user: null });
      return;
    }

    // Optimistically restore from cache for instant UI
    if (savedUser) {
      setState({ initialized: true, authenticated: true, user: savedUser });
    }

    // Validate token in background
    fetchCurrentUser(token)
      .then((user) => {
        persistUser(user);
        setState({ initialized: true, authenticated: true, user });
      })
      .catch(() => {
        setAuthToken("");
        persistUser(null);
        setState({ initialized: true, authenticated: false, user: null });
      });
  }, []);

  const login = useCallback(async (email: string, password: string): Promise<AuthUser> => {
    const res = await fetch(`${getApiBaseUrl()}/api/auth/login`, {
      method: "POST",
      headers: new Headers([["Content-Type", "application/json"]]),
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      if (res.status === 401) throw new Error("邮箱或密码错误");
      if (res.status === 403) throw new Error("账号尚未激活或已被禁用，请联系管理员");
      throw new Error(text || `登录失败：HTTP ${res.status}`);
    }
    const data = await parseJson<AuthTokenResponse>(res);
    const user = toAuthUser(data.user);
    setAuthToken(data.access_token);
    persistUser(user);
    setState({ initialized: true, authenticated: true, user });
    return user;
  }, []);

  const register = useCallback(
    async (
      email: string,
      username: string,
      password: string,
      extras?: { displayName?: string; captchaId?: string | null; captchaCode?: string | null }
    ): Promise<{ pendingActivation: boolean; email: string; message: string }> => {
      const res = await fetch(`${getApiBaseUrl()}/api/auth/register`, {
        method: "POST",
        headers: new Headers([["Content-Type", "application/json"]]),
        body: JSON.stringify({
          email,
          username,
          password,
          display_name: extras?.displayName || null,
          captchaId: extras?.captchaId || null,
          captchaCode: extras?.captchaCode || null,
        }),
      });
      if (!res.ok) {
        const text = await res.text().catch(() => "");
        if (res.status === 409) throw new Error("邮箱或用户名已被注册");
        if (res.status === 400 && text.includes("captcha_required")) {
          throw new Error("请填写验证码");
        }
        if (res.status === 400 && text.includes("captcha_failed")) {
          throw new Error("验证码错误或已过期，请刷新后重试");
        }
        if (res.status === 503 && text.includes("captcha_unavailable")) {
          throw new Error("验证码服务暂不可用，请稍后重试");
        }
        throw new Error(text || `注册失败：HTTP ${res.status}`);
      }
      const data = await parseJson<{ pendingActivation?: boolean; message?: string; user?: { email?: string } }>(res);
      return {
        pendingActivation: data.pendingActivation !== false,
        email: data.user?.email || email,
        message: data.message || "账号已创建，待管理员激活后方可登录",
      };
    },
    []
  );

  const loginWithToken = useCallback(async (token: string): Promise<AuthUser> => {
    setAuthToken(token);
    try {
      const user = await fetchCurrentUser(token);
      persistUser(user);
      setState({ initialized: true, authenticated: true, user });
      return user;
    } catch (err) {
      setAuthToken("");
      throw err;
    }
  }, []);

  const logout = useCallback(() => {
    // Best-effort server audit; JWT is discarded client-side regardless.
    void import("../api/governance")
      .then(({ logoutOnServer }) => logoutOnServer())
      .catch(() => undefined)
      .finally(() => {
        setAuthToken("");
        persistUser(null);
        setState({ initialized: true, authenticated: false, user: null });
      });
    setAuthToken("");
    persistUser(null);
    setState({ initialized: true, authenticated: false, user: null });
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      ...state,
      login,
      register,
      loginWithToken,
      logout,
      isAdmin: state.user?.role === "admin",
    }),
    [state, login, register, loginWithToken, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}
