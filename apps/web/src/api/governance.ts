/**
 * S04-01 platform governance API client.
 */
import type {
  AdminAccountRow,
  AuditLogEntry,
  Contribution,
  UserPrefs,
} from "@openair/shared";
import { buildAuthHeaders, getApiBaseUrl, parseJson } from "./http";

export async function logoutOnServer(): Promise<void> {
  const res = await fetch(`${getApiBaseUrl()}/api/auth/logout`, {
    method: "POST",
    headers: buildAuthHeaders(),
  });
  if (!res.ok && res.status !== 401) {
    await parseJson(res);
  }
}

export async function forgotPassword(email: string): Promise<{ message: string; resetToken?: string | null }> {
  const res = await fetch(`${getApiBaseUrl()}/api/auth/password/forgot`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  return parseJson(res);
}

export async function resetPassword(token: string, newPassword: string): Promise<void> {
  const res = await fetch(`${getApiBaseUrl()}/api/auth/password/reset`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, newPassword }),
  });
  await parseJson(res);
}

export async function fetchMyPrefs(): Promise<UserPrefs> {
  const res = await fetch(`${getApiBaseUrl()}/api/auth/me/prefs`, {
    headers: buildAuthHeaders(),
  });
  return parseJson<UserPrefs>(res);
}

export async function updateMyPrefs(body: {
  quotaAlertThresholdPct?: number;
  displayName?: string;
}): Promise<UserPrefs> {
  const res = await fetch(`${getApiBaseUrl()}/api/auth/me/prefs`, {
    method: "PATCH",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  return parseJson<UserPrefs>(res);
}

export async function listMyContributions(): Promise<Contribution[]> {
  const res = await fetch(`${getApiBaseUrl()}/api/contributions/me`, {
    headers: buildAuthHeaders(),
  });
  return parseJson<Contribution[]>(res);
}

export async function createContribution(body: {
  resourceType?: "skill" | "resource";
  resourceRef: string;
  title: string;
  description?: string;
  domain: "cs_research" | "materials" | "general";
  discoveryScene: "research_materials" | "data_results" | "experiment_computation" | "research_quality";
  requestPublic?: boolean;
}): Promise<Contribution> {
  const res = await fetch(`${getApiBaseUrl()}/api/contributions`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  return parseJson<Contribution>(res);
}

export async function submitContributionPublic(contributionId: string): Promise<Contribution> {
  const res = await fetch(`${getApiBaseUrl()}/api/contributions/${contributionId}/submit-public`, {
    method: "POST",
    headers: buildAuthHeaders(),
  });
  return parseJson<Contribution>(res);
}

export async function adminListAccounts(opts?: {
  query?: string;
  limit?: number;
}): Promise<AdminAccountRow[]> {
  const params = new URLSearchParams();
  if (opts?.query) params.set("query", opts.query);
  if (opts?.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  const res = await fetch(`${getApiBaseUrl()}/api/admin/accounts${qs ? `?${qs}` : ""}`, {
    headers: buildAuthHeaders(),
  });
  return parseJson<AdminAccountRow[]>(res);
}

export async function adminSetAccountActive(
  userId: string,
  isActive: boolean,
  reason: string
): Promise<AdminAccountRow> {
  const res = await fetch(`${getApiBaseUrl()}/api/admin/accounts/${userId}/active`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ isActive, reason }),
  });
  return parseJson<AdminAccountRow>(res);
}

export async function adminListPendingContributions(): Promise<Contribution[]> {
  const res = await fetch(`${getApiBaseUrl()}/api/admin/contributions/pending`, {
    headers: buildAuthHeaders(),
  });
  return parseJson<Contribution[]>(res);
}

export async function adminReviewContribution(
  contributionId: string,
  decision: "approve" | "reject",
  reviewNotes?: string
): Promise<Contribution> {
  const res = await fetch(`${getApiBaseUrl()}/api/admin/contributions/${contributionId}/review`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ decision, reviewNotes: reviewNotes || "" }),
  });
  return parseJson<Contribution>(res);
}

export async function adminListAuditLogs(opts?: {
  action?: string;
  limit?: number;
}): Promise<AuditLogEntry[]> {
  const params = new URLSearchParams();
  if (opts?.action) params.set("action", opts.action);
  if (opts?.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  const res = await fetch(`${getApiBaseUrl()}/api/admin/audit-logs${qs ? `?${qs}` : ""}`, {
    headers: buildAuthHeaders(),
  });
  return parseJson<AuditLogEntry[]>(res);
}
