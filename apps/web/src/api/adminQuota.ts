/**
 * Admin quota API client (S03-14-05).
 * Mirrors services/api/app/routes/admin_quota.py (all endpoints require role=admin).
 */
import type {
  AdminUserQuotaDetail,
  AdminUserQuotaRow,
  QuotaAdjustmentRecord,
  QuotaAdjustmentRequest,
  QuotaTierCatalogEntry,
  TokenUsagePage,
} from "@openair/shared";
import { buildAuthHeaders, getApiBaseUrl, parseJson } from "./http";

export async function adminListQuotaTiers(): Promise<QuotaTierCatalogEntry[]> {
  const res = await fetch(`${getApiBaseUrl()}/api/admin/quota/tiers`, {
    headers: buildAuthHeaders(),
  });
  return parseJson<QuotaTierCatalogEntry[]>(res);
}

export async function adminListUsers(opts?: { query?: string; limit?: number; offset?: number }): Promise<AdminUserQuotaRow[]> {
  const params = new URLSearchParams();
  if (opts?.query) params.set("query", opts.query);
  if (opts?.limit) params.set("limit", String(opts.limit));
  if (opts?.offset) params.set("offset", String(opts.offset));
  const qs = params.toString();
  const res = await fetch(`${getApiBaseUrl()}/api/admin/users${qs ? `?${qs}` : ""}`, {
    headers: buildAuthHeaders(),
  });
  return parseJson<AdminUserQuotaRow[]>(res);
}

export async function adminGetUserQuota(userId: string): Promise<AdminUserQuotaDetail> {
  const res = await fetch(`${getApiBaseUrl()}/api/admin/users/${userId}/quota`, {
    headers: buildAuthHeaders(),
  });
  return parseJson<AdminUserQuotaDetail>(res);
}

export async function adminGetUserUsage(
  userId: string,
  opts?: { from?: string; to?: string; cursor?: string; limit?: number }
): Promise<TokenUsagePage> {
  const params = new URLSearchParams();
  if (opts?.from) params.set("from", opts.from);
  if (opts?.to) params.set("to", opts.to);
  if (opts?.cursor) params.set("cursor", opts.cursor);
  if (opts?.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  const res = await fetch(`${getApiBaseUrl()}/api/admin/users/${userId}/quota/usage${qs ? `?${qs}` : ""}`, {
    headers: buildAuthHeaders(),
  });
  return parseJson<TokenUsagePage>(res);
}

export async function adminListAdjustments(userId: string, limit?: number): Promise<QuotaAdjustmentRecord[]> {
  const qs = limit ? `?limit=${limit}` : "";
  const res = await fetch(`${getApiBaseUrl()}/api/admin/users/${userId}/quota/adjustments${qs}`, {
    headers: buildAuthHeaders(),
  });
  return parseJson<QuotaAdjustmentRecord[]>(res);
}

export async function adminCreateAdjustment(userId: string, body: QuotaAdjustmentRequest): Promise<QuotaAdjustmentRecord> {
  const res = await fetch(`${getApiBaseUrl()}/api/admin/users/${userId}/quota/adjustments`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  return parseJson<QuotaAdjustmentRecord>(res);
}
