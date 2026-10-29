import {
  resolveBrowserAuthToken,
  shouldAttachAuthorization,
} from "../lib/browserCredentialTruth.mjs";
import { hasInternalEngineeringLanguage } from "../lib/userFacingLanguage";
import type { QuotaExceededError } from "@openair/shared";

const DEFAULT_BASE = "http://localhost:8000";
const AUTH_TOKEN_KEY = "openair.auth.token.v1";

export function getApiBaseUrl(): string {
  const configured = import.meta.env.VITE_OPENAIR_API_BASE_URL;
  if (configured === undefined) return import.meta.env.PROD ? "" : DEFAULT_BASE;
  return configured.trim();
}

export function getAuthToken(): string {
  return resolveBrowserAuthToken(getStoredAuthToken(), import.meta.env.VITE_OPENAIR_AUTH_TOKEN);
}

export function getStoredAuthToken(): string {
  if (typeof window !== "undefined") {
    const local = window.localStorage.getItem(AUTH_TOKEN_KEY)?.trim();
    if (local) return local;
  }
  return "";
}

export function setAuthToken(token: string): void {
  if (typeof window === "undefined") return;
  const normalized = token.trim();
  if (!normalized) {
    window.localStorage.removeItem(AUTH_TOKEN_KEY);
    return;
  }
  window.localStorage.setItem(AUTH_TOKEN_KEY, normalized);
}

export function buildAuthHeaders(init?: HeadersInit): Headers {
  const headers = new Headers(init);
  const token = getAuthToken();
  if (shouldAttachAuthorization(token) && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  return headers;
}

/**
 * Append the caller's JWT to a platform artifact URL for browser-native media
 * fetches (`<img>` / `<iframe>`).  Those requests cannot carry an Authorization
 * header, so the /raw route accepts the same token via ?access_token=.
 */
export function withArtifactAuth(url: string): string {
  const token = getAuthToken();
  if (!token || !shouldAttachAuthorization(token) || !url.includes("/api/")) return url;
  return url + (url.includes("?") ? "&" : "?") + "access_token=" + encodeURIComponent(token);
}

let _onUnauthorized: (() => void) | null = null;
export function registerUnauthorizedHandler(handler: () => void): void { _onUnauthorized = handler; }
export function unregisterUnauthorizedHandler(): void { _onUnauthorized = null; }
function handleUnauthorized(): void { _onUnauthorized?.(); }

let _onQuotaExhausted: ((payload: QuotaExceededError) => void) | null = null;
export function registerQuotaExhaustedHandler(handler: (payload: QuotaExceededError) => void): void { _onQuotaExhausted = handler; }
export function unregisterQuotaExhaustedHandler(): void { _onQuotaExhausted = null; }
function handleQuotaExhausted(payload: QuotaExceededError): void { _onQuotaExhausted?.(payload); }

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | undefined;
  readonly detail: unknown;
  readonly payload: unknown;
  readonly retryAfterMs: number | undefined;
  readonly requestId: string | undefined;
  readonly correlationId: string | undefined;
  readonly userMessage: string;

  constructor(input: {
    status: number;
    userMessage: string;
    code?: string;
    detail?: unknown;
    payload?: unknown;
    retryAfterMs?: number;
    requestId?: string;
    correlationId?: string;
  });
  constructor(message: string, status: number, code?: string, payload?: unknown, retryAfterMs?: number);
  constructor(
    inputOrMessage: {
      status: number;
      userMessage: string;
      code?: string;
      detail?: unknown;
      payload?: unknown;
      retryAfterMs?: number;
      requestId?: string;
      correlationId?: string;
    } | string,
    status?: number,
    code?: string,
    payload?: unknown,
    retryAfterMs?: number,
  ) {
    const input = typeof inputOrMessage === "string"
      ? { status: status ?? 0, userMessage: inputOrMessage, code, payload, retryAfterMs }
      : inputOrMessage;
    super(input.userMessage);
    this.name = "ApiError";
    this.status = input.status;
    this.code = input.code;
    this.detail = input.detail;
    this.payload = input.payload;
    this.retryAfterMs = input.retryAfterMs;
    this.requestId = input.requestId;
    this.correlationId = input.correlationId;
    this.userMessage = input.userMessage;
  }
}

export function isApiErrorStatus(error: unknown, ...statuses: number[]): error is ApiError {
  return error instanceof ApiError && statuses.includes(error.status);
}

export type ApiFailureKind =
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "contract"
  | "server"
  | "network"
  | "other";

/** Typed classification only; never infer auth or scope from backend prose. */
export function classifyApiFailure(error: unknown): ApiFailureKind {
  if (!(error instanceof ApiError)) return "network";
  if (error.status === 401) return "unauthorized";
  if (error.status === 403) return "forbidden";
  if (error.status === 404) return "not_found";
  if (error.code === "response_contract_invalid") return "contract";
  if (error.status >= 500) return "server";
  return "other";
}

const MIN_RETRY_AFTER_MS = 1_000;
const MAX_RETRY_AFTER_MS = 5 * 60_000;

export function parseRetryAfterMs(headers: Headers, payload: unknown, now = Date.now()): number {
  const raw = headers.get("Retry-After")?.trim();
  let valueMs: number | undefined;
  if (raw && /^\d+$/.test(raw)) valueMs = Number(raw) * 1_000;
  else if (raw) {
    const date = Date.parse(raw);
    if (Number.isFinite(date)) valueMs = date - now;
  }
  if (valueMs === undefined && typeof payload === "object" && payload !== null) {
    const record = payload as { retryAfter?: unknown; retry_after?: unknown; retryAfterMs?: unknown };
    const retryAfter = record.retryAfter ?? record.retry_after;
    if (typeof record.retryAfterMs === "number" && Number.isFinite(record.retryAfterMs)) valueMs = record.retryAfterMs;
    else if (typeof retryAfter === "number" && Number.isFinite(retryAfter)) valueMs = retryAfter * 1_000;
    else if (typeof retryAfter === "string" && /^\d+(?:\.\d+)?$/.test(retryAfter)) valueMs = Number(retryAfter) * 1_000;
  }
  return Math.min(MAX_RETRY_AFTER_MS, Math.max(MIN_RETRY_AFTER_MS, valueMs ?? 5_000));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function parseStructuredBody(text: string): Record<string, unknown> | null {
  if (!text) return null;
  try {
    const parsed = JSON.parse(text);
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function nonEmptyText(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function safeBackendMessage(value: unknown): string | undefined {
  const text = nonEmptyText(value);
  if (!text || text.length > 240) return undefined;
  if (/^[a-z0-9_.:-]+$/i.test(text)) return undefined;
  if (/traceback|stack\s*trace|exception|postgres|runtime|adapter|authorization|bearer\s+/i.test(text)) return undefined;
  if (/\b(?:revision|workunit|executiondecision|capability|idempotency|provider|endpoint|request[_ -]?id|correlation[_ -]?id)\b/i.test(text)) return undefined;
  if (hasInternalEngineeringLanguage(text)) return undefined;
  if (/localhost|127\.0\.0\.1|:\d{4,5}\b/.test(text)) return undefined;
  return text;
}

function userMessageForStatus(status: number, backendMessage?: string): string {
  if (status === 401) return "未登录或登录状态已失效";
  if (status === 403) return "无权限访问";
  if (status === 404) return backendMessage ?? "未找到请求的内容。";
  if (status === 409) return backendMessage ?? "当前内容已更新，请刷新后重试。";
  if (status === 428) return backendMessage ?? "当前操作条件尚未满足，请按页面提示处理。";
  if (status === 429) return "请求较多，请稍后继续。";
  if (status >= 500) return "服务暂时不可用，请稍后继续。";
  return backendMessage ?? "请求未完成，请检查后重试。";
}

export async function apiErrorFromResponse(res: Response): Promise<ApiError> {
  const text = await res.text().catch(() => "");
  const payload = parseStructuredBody(text);
  const detail = payload?.detail;
  const detailRecord = isRecord(detail) ? detail : null;
  const code = nonEmptyText(detailRecord?.code) ?? nonEmptyText(payload?.code);
  const backendMessage = safeBackendMessage(
    detailRecord?.userMessage
      ?? detailRecord?.message
      ?? (typeof detail === "string" ? detail : undefined)
      ?? payload?.userMessage
      ?? payload?.message,
  );
  const requestId = nonEmptyText(res.headers.get("X-Request-ID"))
    ?? nonEmptyText(detailRecord?.requestId)
    ?? nonEmptyText(payload?.requestId);
  const correlationId = nonEmptyText(res.headers.get("X-Correlation-ID"))
    ?? nonEmptyText(detailRecord?.correlationId)
    ?? nonEmptyText(payload?.correlationId);
  const retryAfterMs = res.status === 429 ? parseRetryAfterMs(res.headers, detailRecord ?? payload) : undefined;
  const error = new ApiError({
    status: res.status,
    code,
    detail,
    payload: payload ?? (text || undefined),
    retryAfterMs,
    requestId,
    correlationId,
    userMessage: userMessageForStatus(res.status, backendMessage),
  });
  if (error.status === 401) handleUnauthorized();
  const quotaPayload = isRecord(detail) ? detail : isRecord(error.payload) ? error.payload : null;
  if (error.status === 402 && error.code === "quota_exhausted" && quotaPayload) {
    handleQuotaExhausted(quotaPayload as unknown as QuotaExceededError);
  }
  return error;
}

export async function parseJson<T>(res: Response): Promise<T> {
  if (!res.ok) throw await apiErrorFromResponse(res);
  return (await res.json()) as T;
}

/** Parse a successful empty response while sharing all error/auth handling with parseJson. */
export async function parseVoid(res: Response): Promise<void> {
  if (!res.ok) throw await apiErrorFromResponse(res);
  await res.text().catch(() => "");
}
