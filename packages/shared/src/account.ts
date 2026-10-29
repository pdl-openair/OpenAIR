/**
 * Account & quota shared types (Sprint-03 unified user quota).
 *
 * Rule source: docs/task_specs/sprint03/S03-14-01_quota_rules.md
 * Backend mirror: services/api/app/models/account_schemas.py
 * Provider-neutral: quota is measured in tokens across all LLM providers.
 */

export type UserRole = "user" | "admin";

export type QuotaTier = "trial" | "free" | "pro" | "internal" | "admin";

export type QuotaPeriodType = "permanent" | "daily" | "monthly" | "unlimited";

export const QUOTA_TIERS: readonly QuotaTier[] = ["trial", "free", "pro", "internal", "admin"];

/** Chinese display labels for quota tiers. */
export const QUOTA_TIER_LABELS: Record<QuotaTier, string> = {
  trial: "试用",
  free: "免费",
  pro: "专业",
  internal: "内部",
  admin: "管理员",
};

export const QUOTA_PERIOD_TYPE_LABELS: Record<QuotaPeriodType, string> = {
  permanent: "一次性额度",
  daily: "每日重置",
  monthly: "每月重置",
  unlimited: "不限量",
};

/** GET /api/admin/quota/tiers — live limits from backend config/env. */
export interface QuotaTierCatalogEntry {
  tier: QuotaTier;
  periodType: QuotaPeriodType;
  /** null when unlimited (admin) */
  periodLimit: number | null;
}

/** GET /api/quota/me */
export interface QuotaSummary {
  tier: QuotaTier;
  periodType: QuotaPeriodType;
  unlimited: boolean;
  /** null when unlimited */
  periodLimit: number | null;
  periodUsed: number;
  bonusQuota: number;
  /** null when unlimited */
  remaining: number | null;
  /** ISO-8601; null for permanent/unlimited tiers */
  resetAt: string | null;
}

export type TokenUsageStatus = "success" | "failed" | "retried";

/** Ledger source values (services/api/app/modules/token_ledger_repository.py VALID_SOURCES). */
export const TOKEN_USAGE_SOURCES = [
  "workbench_chat",
  "execute_skill",
  "research_start",
  "durable_dispatch",
  "structured_skill",
] as const;
export type TokenUsageSource = (typeof TOKEN_USAGE_SOURCES)[number];

export const TOKEN_USAGE_SOURCE_LABELS: Record<TokenUsageSource, string> = {
  workbench_chat: "工作台对话",
  execute_skill: "技能执行",
  research_start: "研究起点规划",
  durable_dispatch: "编排调度",
  structured_skill: "结构化技能",
};

/** One ledger entry (GET /api/quota/me/usage). */
export type TokenUsageKeySource = "platform" | "user";

export interface TokenUsageEntry {
  ledgerId: string;
  userId: string;
  workspaceId: string | null;
  taskId: string | null;
  callId: string | null;
  source: string;
  /** Null for platform-key rows on user-facing APIs (enterprise privacy). */
  provider: string | null;
  /** Null for platform-key rows on user-facing APIs; full value for BYOK / admin. */
  model: string | null;
  /** platform = OpenAIR-provided keys; user = BYOK. */
  keySource: TokenUsageKeySource;
  status: TokenUsageStatus;
  attempt: number;
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  estimated: boolean;
  createdAt: string;
}

export interface TokenUsagePage {
  items: TokenUsageEntry[];
  nextCursor: string | null;
}

/** HTTP 402 body when quota is exhausted (S03-14-01 §6). */
export interface QuotaExceededError {
  code: "quota_exhausted";
  message: string;
  tier: QuotaTier;
  remaining: number;
  resetAt: string | null;
}

export const QUOTA_EXCEEDED_CODE = "quota_exhausted" as const;

// ---------------------------------------------------------------------------
// Admin (S03-14-04)
// ---------------------------------------------------------------------------

export type QuotaAdjustType = "bonus_quota" | "tier_change";

export interface QuotaAdjustmentRecord {
  adjustmentId: string;
  userId: string;
  adjustType: QuotaAdjustType;
  delta: number | null;
  oldTier: QuotaTier | null;
  newTier: QuotaTier | null;
  reason: string;
  operatorId: string;
  createdAt: string;
}

/** POST /api/admin/users/{id}/quota/adjustments */
export interface QuotaAdjustmentRequest {
  type: QuotaAdjustType;
  /** required when type === "bonus_quota"; must be a positive integer */
  delta?: number;
  /** required when type === "tier_change" */
  newTier?: QuotaTier;
  reason: string;
}

/** Admin user list row (GET /api/admin/users). */
export interface AdminUserQuotaRow {
  userId: string;
  email: string;
  username: string;
  displayName: string | null;
  role: UserRole;
  isActive: boolean;
  quota: QuotaSummary;
  createdAt: string | null;
}

/** Contribution / publish review (S04-01). */
export type ContributionStatus =
  | "private_ready"
  | "pending_public_review"
  | "approved_public"
  | "rejected"
  | "withdrawn";

export type ContributionSystemCheckStatus = "not_checked" | "passed" | "needs_attention";
export type ContributionDomain = "cs_research" | "materials" | "general";
export type ContributionDiscoveryScene =
  | "research_materials"
  | "data_results"
  | "experiment_computation"
  | "research_quality";

export interface Contribution {
  contributionId: string;
  ownerId: string;
  workspaceId: string | null;
  resourceType: string;
  resourceRef: string;
  title: string;
  description: string;
  domain: ContributionDomain;
  discoveryScene: ContributionDiscoveryScene;
  status: ContributionStatus;
  systemCheckStatus: ContributionSystemCheckStatus;
  systemCheckIssues: string[];
  reviewNotes: string | null;
  reviewerId: string | null;
  reviewedAt: string | null;
  catalogResourceId: string | null;
  releaseVersion: string | null;
  publishedAt: string | null;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface AuditLogEntry {
  auditId: string;
  action: string;
  actorId: string | null;
  targetId: string | null;
  outcome: string;
  detail: Record<string, unknown>;
  ip: string | null;
  createdAt: string | null;
}

export interface UserPrefs {
  userId: string;
  displayName: string | null;
  quotaAlertThresholdPct: number;
  role: string;
  isActive: boolean;
}

export interface AdminAccountRow {
  userId: string;
  email: string;
  username: string;
  displayName: string | null;
  role: string;
  isActive: boolean;
  quotaTier: string;
  createdAt: string | null;
}

/** Per-source consumption aggregate (admin detail view). */
export interface SourceUsageSummary {
  source: string;
  calls: number;
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
}

/** Per-model consumption aggregate (admin detail; full model names). */
export interface ModelUsageSummary {
  model: string | null;
  provider: string | null;
  keySource: TokenUsageKeySource;
  calls: number;
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
}

export interface UsageTimeseriesPoint {
  periodStart: string;
  periodLabel: string;
  calls: number;
  totalTokens: number;
}

export interface UsageTimeseriesBundle {
  granularity: "day" | "week" | "month";
  points: UsageTimeseriesPoint[];
  peakTokens: number;
  peakPeriod: string | null;
  troughTokens: number;
  troughPeriod: string | null;
  avgTokens: number;
}

/** GET /api/admin/users/{id}/quota */
export interface AdminUserQuotaDetail {
  user: AdminUserQuotaRow;
  /** recent consumption (default 30 days), grouped by source */
  usageBySource: SourceUsageSummary[];
  usageByModel: ModelUsageSummary[];
  timeseriesDaily: UsageTimeseriesBundle | null;
  timeseriesWeekly: UsageTimeseriesBundle | null;
  timeseriesMonthly: UsageTimeseriesBundle | null;
  recentUsage: TokenUsageEntry[];
  totalTokens30d: number;
}
