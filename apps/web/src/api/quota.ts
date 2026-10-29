/**
 * User-side quota API client (S03-14-05).
 * Mirrors services/api/app/routes/quota.py. Uses http.ts buildAuthHeaders + parseJson
 * (NOT skills.ts's local parseJson — that one bypasses the global 401 handler).
 */
import type { QuotaSummary, TokenUsagePage } from "@openair/shared";
import { buildAuthHeaders, getApiBaseUrl, parseJson } from "./http";

export async function fetchMyQuota(): Promise<QuotaSummary> {
  const res = await fetch(`${getApiBaseUrl()}/api/quota/me`, {
    headers: buildAuthHeaders(),
  });
  return parseJson<QuotaSummary>(res);
}

export async function fetchMyUsage(opts?: {
  from?: string;
  to?: string;
  cursor?: string;
  limit?: number;
}): Promise<TokenUsagePage> {
  const params = new URLSearchParams();
  if (opts?.from) params.set("from", opts.from);
  if (opts?.to) params.set("to", opts.to);
  if (opts?.cursor) params.set("cursor", opts.cursor);
  if (opts?.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  const res = await fetch(`${getApiBaseUrl()}/api/quota/me/usage${qs ? `?${qs}` : ""}`, {
    headers: buildAuthHeaders(),
  });
  return parseJson<TokenUsagePage>(res);
}
