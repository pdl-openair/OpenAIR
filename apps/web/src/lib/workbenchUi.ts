import type { WorkbenchArtifact, WorkbenchCapabilityCall, WorkbenchStageRow, WorkbenchTaskModel } from "../model/workbenchTask";

const STORAGE_PREFIX = "openair.workbench.v1";
const PROFILE_LEAVE_PREFIX = "openair.workbench.profile-left.v1";

import { researchInterruptionPresentation } from "./workbenchInterruption.mjs";
import { researchArtifactTitle } from "./researchArtifactTitle";

export interface WorkbenchLocalState {
  supplementNote: string;
  lastViewPhaseId: string | null;
  lastSubStageKey: string | null;
}

export function workbenchStorageKey(taskId: string): string {
  return `${STORAGE_PREFIX}:${taskId}`;
}

export function readWorkbenchLocal(taskId: string): WorkbenchLocalState {
  try {
    const raw = localStorage.getItem(workbenchStorageKey(taskId));
    if (!raw) return { supplementNote: "", lastViewPhaseId: null, lastSubStageKey: null };
    const parsed = JSON.parse(raw) as Partial<WorkbenchLocalState>;
    return {
      supplementNote: typeof parsed.supplementNote === "string" ? parsed.supplementNote : "",
      lastViewPhaseId: typeof parsed.lastViewPhaseId === "string" ? parsed.lastViewPhaseId : null,
      lastSubStageKey: typeof parsed.lastSubStageKey === "string" ? parsed.lastSubStageKey : null,
    };
  } catch {
    return { supplementNote: "", lastViewPhaseId: null, lastSubStageKey: null };
  }
}

export function writeWorkbenchLocal(taskId: string, next: WorkbenchLocalState): void {
  localStorage.setItem(workbenchStorageKey(taskId), JSON.stringify(next));
}

function profileLeftKey(taskId: string): string {
  return `${PROFILE_LEAVE_PREFIX}:${taskId}`;
}

export function hasLeftWorkbenchOnce(taskId: string): boolean {
  return localStorage.getItem(profileLeftKey(taskId)) === "1";
}

export function markWorkbenchLeft(taskId: string): void {
  localStorage.setItem(profileLeftKey(taskId), "1");
}

export interface CapabilityAggregate {
  completed: number;
  running: number;
  failed: number;
  pending: number;
  runningNames: string[];
}

export function aggregateCapabilities(calls: WorkbenchCapabilityCall[]): CapabilityAggregate {
  let completed = 0;
  let running = 0;
  let failed = 0;
  let pending = 0;
  const runningNames: string[] = [];
  for (const c of calls) {
    if (c.status === "completed") completed += 1;
    else if (c.status === "running") {
      running += 1;
      runningNames.push(c.name);
    } else if (c.status === "failed") failed += 1;
    else if (c.status === "pending") pending += 1;
  }
  return { completed, running, failed, pending, runningNames };
}

/** 产物展示用图标（短标签，避免绑定具体学科文件名） */
export function artifactGlyph(type: string, format: string): string {
  const t = `${type} ${format}`.toLowerCase();
  if (t.includes("pdf")) return "PDF";
  if (t.includes("directory") || t.includes("code_folder")) return "</>";
  if (t.includes("table") || t.includes("csv")) return "表";
  if (t.includes("code") || t.includes("script")) return "</>";
  if (t.includes("chart") || t.includes("plot")) return "图";
  if (t.includes("link") || t.includes("url")) return "链";
  if (t.includes("summary") || t.includes("note") || t.includes("markdown")) return "文";
  return "件";
}

export function artifactTypeLabel(type: string): string {
  const normalized = type.toLowerCase();
  const mapped = researchArtifactTitle({ type, title: type });
  if (mapped !== "研究成果") return mapped;
  if (normalized === "research_question_refinement") return "研究问题凝练";
  if (normalized === "key_literature_positioning") return "关键文献定位";
  if (normalized === "validation_design") return "验证方案设计";
  if (normalized === "short_literature_review_draft" || normalized.includes("literature_review")) {
    return "文献综述草稿";
  }
  if (normalized === "top_k_source_set" || normalized.includes("recommended")) return "推荐阅读";
  if (normalized === "source_table") return "来源表";
  if (normalized === "literature_search_strategy") return "检索策略";
  if (normalized === "literature_gaps_next_steps") return "缺口与下一步";
  if (normalized.includes("workflow") || normalized.includes("plan")) return "工作计划";
  if (normalized.includes("summary") || normalized.includes("note")) return "摘要";
  if (normalized.includes("table")) return "表格";
  if (normalized.includes("code")) return "代码";
  return "任务产物";
}

function artifactAudienceValue(artifact: WorkbenchArtifact, key: string): string | null {
  const value = (artifact as unknown as Record<string, unknown>)[key];
  return typeof value === "string" ? value.toLowerCase() : null;
}

function isHiddenVisibility(value?: string | null): boolean {
  return value != null && ["internal", "diagnostic", "audit", "developer", "hidden"].includes(value.toLowerCase());
}

export function isInternalWorkbenchArtifact(artifact?: WorkbenchArtifact | null): boolean {
  if (!artifact) return false;
  const visibility = artifactAudienceValue(artifact, "artifactVisibility")
    ?? artifactAudienceValue(artifact, "visibility")
    ?? artifactAudienceValue(artifact, "audience");
  if (isHiddenVisibility(visibility)) {
    return true;
  }
  return false;
}

export function userVisibleWorkbenchArtifacts(artifacts: WorkbenchArtifact[]): WorkbenchArtifact[] {
  return artifacts
    .filter((artifact) => !isInternalWorkbenchArtifact(artifact))
    .map((artifact) => ({ ...artifact, title: researchArtifactTitle(artifact) }));
}

export function isResearchStartDraftArtifact(artifact?: WorkbenchArtifact | null): boolean {
  if (!artifact) return false;
  return (
    artifact.type === "research_start_draft"
    || artifact.artifactType === "research_start_draft"
    || artifact.relatedSkillId === "research_start_draft"
  );
}

export function isInternalWorkbenchCall(call?: WorkbenchCapabilityCall | null): boolean {
  if (!call) return false;
  const visibility = (typeof call.callVisibility === "string" ? call.callVisibility.toLowerCase() : null)
    ?? (typeof call.audience === "string" ? call.audience.toLowerCase() : null);
  if (isHiddenVisibility(visibility)) {
    return true;
  }

  if (call.name === "release_gated_starter_packaging" && call.runtime === "openair_local_release_gate") {
    return true;
  }
  return false;
}

export function userVisibleWorkbenchCalls(calls: WorkbenchCapabilityCall[]): WorkbenchCapabilityCall[] {
  return calls.filter((call) => !isInternalWorkbenchCall(call));
}

export function isLiveWorkbenchExecutionCall(call?: WorkbenchCapabilityCall | null): boolean {
  return Boolean(
    call
    && !isInternalWorkbenchCall(call)
    && call.runtime !== "workbench_control"
    && call.runMode === "live"
    && call.sourceMode === "live",
  );
}

export function isLowConfidenceWorkbenchSource(model?: WorkbenchTaskModel | null, artifact?: WorkbenchArtifact | null): boolean {
  const mode = artifact?.sourceMode ?? model?.resultSource;
  return (
    mode === "fixture"
    || mode === "replay"
    || mode === "demo"
    || mode === "none"
    || artifact?.trustStatus === "low_confidence"
    || artifact?.trustStatus === "sample_placeholder"
  );
}

export type CanonicalArtifactState =
  | "usable"
  | "needs_review"
  | "needs_input"
  | "insufficient_materials"
  | "failed"
  | "partial_success"
  | "rejected_or_needs_revision";

export type ResolvedCanonicalArtifactState = CanonicalArtifactState | "unknown";

export interface CanonicalArtifactStatePresentation {
  state: ResolvedCanonicalArtifactState;
  label: string;
  color: string;
}

const CANONICAL_ARTIFACT_STATE_PRESENTATION: Record<CanonicalArtifactState, Omit<CanonicalArtifactStatePresentation, "state">> = {
  usable: { label: "可使用", color: "success" },
  needs_review: { label: "待复核", color: "gold" },
  needs_input: { label: "待补充材料", color: "warning" },
  insufficient_materials: { label: "材料不足", color: "warning" },
  failed: { label: "未形成可用产物", color: "red" },
  partial_success: { label: "部分完成", color: "processing" },
  rejected_or_needs_revision: { label: "待修改", color: "red" },
};

function normalizeCanonicalArtifactState(raw: string | null | undefined): ResolvedCanonicalArtifactState {
  if (raw === "usable" || raw === "verified_artifact" || raw === "ready") return "usable";
  if (raw === "needs_review" || raw === "pending_review" || raw === "raw_output") return "needs_review";
  if (raw === "needs_input" || raw === "blocked_recoverable") return "needs_input";
  if (raw === "insufficient_materials" || raw === "insufficient_evidence") return "insufficient_materials";
  if (raw === "failed") return "failed";
  if (raw === "partial_success" || raw === "stage_summary") return "partial_success";
  if (raw === "rejected_or_needs_revision") return "rejected_or_needs_revision";
  return "unknown";
}

export type ArtifactStatusPresentation = { label: string; color: string };

export function canonicalArtifactStateFromValue(value: unknown): CanonicalArtifactState | null {
  const state = normalizeCanonicalArtifactState(typeof value === "string" ? value : undefined);
  return state === "unknown" ? null : state;
}

/**
 * The only Artifact state resolver used by badges, counts, gates and actions.
 * Precedence is server Artifact state, validator state, then unknown. Capability facts never create state.
 */
export function resolveCanonicalArtifactState(artifact?: WorkbenchArtifact | null): ResolvedCanonicalArtifactState {
  const artifactState = normalizeCanonicalArtifactState(artifact?.artifactState);
  if (artifactState !== "unknown") return artifactState;
  const validatorState = normalizeCanonicalArtifactState(artifact?.validatorResult?.artifactState);
  if (validatorState !== "unknown") return validatorState;
  return "unknown";
}

export function canonicalArtifactStatePresentation(artifact?: WorkbenchArtifact | null): CanonicalArtifactStatePresentation {
  const state = resolveCanonicalArtifactState(artifact);
  if (state === "unknown") return { state, label: "状态待确认", color: "default" };
  return { state, ...CANONICAL_ARTIFACT_STATE_PRESENTATION[state] };
}

export interface WorkbenchArtifactGate {
  state: ResolvedCanonicalArtifactState;
  isReviewable: boolean;
  canConfirmPass: boolean;
  canRequestRevision: boolean;
  canUseDownstream: boolean;
  requiresHumanReview: boolean;
  requiresScopeExpansionConfirmation: boolean;
  allowedActions: string[];
  primaryAction: string;
  blockedReason?: string | null;
}

export function canonicalArtifactState(artifact?: WorkbenchArtifact | null): ResolvedCanonicalArtifactState {
  return resolveCanonicalArtifactState(artifact);
}

const ARTIFACT_STATUS_LABELS: Record<CanonicalArtifactState, { zh: string; en: string; color: string }> = {
  usable: { zh: "已核查入库", en: "Validated", color: "success" },
  needs_review: { zh: "待你核查", en: "Needs review", color: "gold" },
  needs_input: { zh: "待补充材料", en: "Needs input", color: "warning" },
  insufficient_materials: { zh: "材料不足", en: "Insufficient materials", color: "warning" },
  failed: { zh: "未形成可用产物", en: "No usable artifact", color: "red" },
  partial_success: { zh: "需确认范围", en: "Scope confirmation needed", color: "processing" },
  rejected_or_needs_revision: { zh: "退回修改", en: "Needs revision", color: "red" },
};

export function artifactStatusPresentation(
  artifact: WorkbenchArtifact,
  language: "en-US" | string,
): ArtifactStatusPresentation | null {
  const state = resolveCanonicalArtifactState(artifact);
  if (state !== "unknown") {
    const presentation = ARTIFACT_STATUS_LABELS[state];
    return { label: language === "en-US" ? presentation.en : presentation.zh, color: presentation.color };
  }
  return { label: language === "en-US" ? "Status pending" : "状态待确认", color: "default" };
}

export function artifactHumanGate(artifact?: WorkbenchArtifact | null): WorkbenchArtifactGate {
  const state = resolveCanonicalArtifactState(artifact);
  const serverGate = artifact?.humanGate ?? null;
  const serverActions = new Set<string>(
    Array.isArray(serverGate?.allowedActions)
      ? serverGate.allowedActions.filter((action: unknown): action is string => typeof action === "string")
      : [],
  );
  const isReviewable = state === "needs_review";
  const canConfirmPass = Boolean(
    isReviewable
    && serverGate?.canConfirmPass
    && serverActions.has("confirm_pass"),
  );
  const canRequestRevision = Boolean(isReviewable && serverActions.has("request_revision"));
  const canUseDownstream = Boolean(
    state === "usable"
    && serverGate?.canUseDownstream
    && serverActions.has("use_downstream"),
  );
  const requiresScopeExpansionConfirmation = state === "partial_success"
    && Boolean(serverGate?.requiresScopeExpansionConfirmation)
    && serverActions.has("confirm_scope_expansion");
  const requiresHumanReview = isReviewable
    || requiresScopeExpansionConfirmation
    || Boolean(state !== "unknown" && serverGate?.requiresHumanReview);

  const allowedActions = state !== "unknown" && serverGate
    ? [...serverActions].filter((action) => {
        if (action === "confirm_pass") return canConfirmPass;
        if (action === "request_revision") return canRequestRevision;
        if (action === "use_downstream") return canUseDownstream;
        if (action === "confirm_scope_expansion") return requiresScopeExpansionConfirmation;
        return false;
      })
    : [];
  const requestedPrimaryAction = serverGate?.primaryAction ?? "";
  const primaryAction = allowedActions.includes(requestedPrimaryAction)
    ? requestedPrimaryAction
    : "";

  return {
    state,
    isReviewable,
    canConfirmPass,
    canRequestRevision,
    canUseDownstream,
    requiresHumanReview,
    requiresScopeExpansionConfirmation,
    allowedActions,
    primaryAction,
    blockedReason: serverGate?.blockedReason ?? (state === "unknown" ? "artifact_state_unknown" : null),
  };
}

export interface WorkbenchArtifactStateProjection {
  state: ResolvedCanonicalArtifactState;
  badge: CanonicalArtifactStatePresentation;
  gate: WorkbenchArtifactGate;
  countBucket: "usable" | "needs_review" | "needs_input" | "failed" | "partial_success" | "rejected" | "unknown";
  visibleAllowedActions: string[];
}

/** Shared Workbench projection consumed by status counts and action surfaces. */
export function projectWorkbenchArtifactState(artifact?: WorkbenchArtifact | null): WorkbenchArtifactStateProjection {
  const gate = artifactHumanGate(artifact);
  const badge = gate.state === "unknown"
    ? { state: "unknown" as const, label: "状态待确认", color: "default" }
    : { state: gate.state, ...CANONICAL_ARTIFACT_STATE_PRESENTATION[gate.state] };
  const countBucket = gate.state === "usable"
    ? "usable"
    : gate.state === "needs_review"
      ? "needs_review"
      : gate.state === "needs_input" || gate.state === "insufficient_materials"
        ? "needs_input"
        : gate.state === "failed"
          ? "failed"
          : gate.state === "partial_success"
            ? "partial_success"
            : gate.state === "rejected_or_needs_revision"
              ? "rejected"
              : "unknown";
  return {
    state: gate.state,
    badge,
    gate,
    countBucket,
    visibleAllowedActions: [...gate.allowedActions],
  };
}

export function workbenchArtifactStateCounts(artifacts: WorkbenchArtifact[]) {
  const projections = artifacts.map(projectWorkbenchArtifactState);
  return {
    raw: projections.filter((projection) => projection.countBucket === "needs_review").length,
    pending: projections.filter((projection) => projection.gate.isReviewable).length,
    verified: projections.filter((projection) => projection.countBucket === "usable").length,
    rejected: projections.filter((projection) => projection.countBucket === "rejected").length,
    unknown: projections.filter((projection) => projection.countBucket === "unknown").length,
    total: projections.length,
  };
}

export type WorkbenchDecisionSeverity = "info" | "success" | "warning" | "error";

export interface WorkbenchDecisionState {
  statusLabel: string;
  statusColor: string;
  severity: WorkbenchDecisionSeverity;
  headline: string;
  nextAction: string;
  blockerReason: string | null;
  primaryActionLabel: string;
  canAdvance: boolean;
  advanceDisabledReason: string | null;
  focusTarget: "artifact" | "support" | "current";
  recoveryAction?: "refresh";
}

function activeRunningSubstage(stage?: WorkbenchStageRow | null): boolean {
  return Boolean(stage?.subStages?.some((sub) => sub.status === "running"));
}

function hasRecoverableSubstageContext(stage?: WorkbenchStageRow | null): boolean {
  return Boolean(stage && (stage.subStages?.length ?? 0) > 0);
}

function uniqueVisibleArtifacts(artifacts: WorkbenchArtifact[]): WorkbenchArtifact[] {
  const seen = new Set<string>();
  const out: WorkbenchArtifact[] = [];
  for (const artifact of artifacts) {
    if (seen.has(artifact.id) || isInternalWorkbenchArtifact(artifact)) continue;
    seen.add(artifact.id);
    out.push(artifact);
  }
  return out;
}

function stageDecisionArtifacts(
  model: WorkbenchTaskModel,
  stage?: WorkbenchStageRow | null,
): WorkbenchArtifact[] {
  if (!stage) return [];
  const stageArtifactIds = new Set(stage.artifactRefs ?? []);
  return uniqueVisibleArtifacts(
    model.artifacts.filter((artifact) =>
      stageArtifactIds.has(artifact.id) || artifact.relatedStage === stage.stage,
    ),
  );
}

function artifactGateStatus(gate: WorkbenchArtifactGate): Pick<WorkbenchDecisionState, "statusLabel" | "statusColor" | "severity" | "primaryActionLabel" | "nextAction" | "focusTarget"> {
  if (gate.state === "usable" && gate.canUseDownstream) {
    return {
      statusLabel: "可进入下一阶段",
      statusColor: "success",
      severity: "success",
      primaryActionLabel: "下一阶段",
      nextAction: "当前产物可下游使用，确认后进入下一阶段。",
      focusTarget: "current",
    };
  }
  if (gate.state === "needs_review" || gate.canConfirmPass) {
    return {
      statusLabel: "待你核查",
      statusColor: "gold",
      severity: "warning",
      primaryActionLabel: "复核当前产物",
      nextAction: "先核查来源、边界和内容；通过后再进入下一阶段，不确定就退回修改。",
      focusTarget: "artifact",
    };
  }
  if (gate.state === "needs_input" || gate.state === "insufficient_materials") {
    return {
      statusLabel: gate.state === "needs_input" ? "待补充材料" : "材料不足",
      statusColor: "warning",
      severity: "warning",
      primaryActionLabel: "补充材料",
      nextAction: "当前产物存在输入缺口。具体需要及可用操作请查看当前研究说明。",
      focusTarget: "support",
    };
  }
  if (gate.state === "partial_success" || gate.requiresScopeExpansionConfirmation) {
    return {
      statusLabel: "需确认范围",
      statusColor: "processing",
      severity: "warning",
      primaryActionLabel: "确认范围",
      nextAction: "先确认是否扩大范围；这不会把当前产物标记为可下游使用。",
      focusTarget: "artifact",
    };
  }
  if (gate.state === "rejected_or_needs_revision") {
    return {
      statusLabel: "退回修改",
      statusColor: "red",
      severity: "error",
      primaryActionLabel: "查看退回原因",
      nextAction: "查看退回原因；可用处理操作以当前研究说明为准。",
      focusTarget: "artifact",
    };
  }
  if (gate.state === "unknown") {
    return {
      statusLabel: "状态待确认",
      statusColor: "default",
      severity: "info",
      primaryActionLabel: "等待状态更新",
      nextAction: "当前尚未确认可执行的产物状态或后续操作；暂不会开放复核或继续使用。",
      focusTarget: "artifact",
    };
  }
  return {
    statusLabel: "未形成可用产物",
    statusColor: "red",
    severity: "error",
    primaryActionLabel: "查看当前步骤",
    nextAction: "当前产物尚不可使用；已有的其他成果仍按各自状态保留。",
    focusTarget: "support",
  };
}

export function artifactGateMessage(gate: WorkbenchArtifactGate): string {
  if (gate.state === "needs_input") {
    return "当前产物存在输入缺口，尚不能确认通过；具体输入及操作请查看研究说明。";
  }
  if (gate.state === "insufficient_materials") {
    return "当前产物材料不足，尚不能确认核查通过。";
  }
  if (gate.state === "partial_success") {
    return "当前只是部分完成，需要先确认是否扩大范围；不能直接作为可用产物推进。";
  }
  if (gate.state === "failed") {
    return "当前产物未通过核查，不能确认使用；已有的其他成果不受此状态覆盖。";
  }
  if (gate.state === "rejected_or_needs_revision") {
    return "该产物已退回修改，处理退回原因前不能进入下一阶段。";
  }
  if (gate.state === "unknown") {
    return "当前尚未确认产物状态或后续操作；暂不会开放复核或继续使用。";
  }
  return "当前产物尚不可下游使用；请查看它的核查状态和当前研究说明。";
}

export function buildWorkbenchDecisionState({
  model,
  stage,
  selectedArtifact,
  isHistoricalView = false,
  taskStatusLabel,
}: {
  model: WorkbenchTaskModel;
  stage?: WorkbenchStageRow | null;
  selectedArtifact?: WorkbenchArtifact | null;
  isHistoricalView?: boolean;
  taskStatusLabel?: string;
}): WorkbenchDecisionState {
  const interruption = researchInterruptionPresentation(model);
  if (interruption) {
    return {
      statusLabel: interruption.statusLabel,
      statusColor: interruption.tone === "error" ? "red" : interruption.tone === "info" ? "default" : "warning",
      severity: interruption.tone as WorkbenchDecisionSeverity,
      headline: interruption.summary,
      nextAction: interruption.userAction,
      blockerReason: interruption.impact,
      primaryActionLabel: interruption.presentationMode === "awaiting_user" ? "补充研究信息" : "查看研究进展",
      canAdvance: false,
      advanceDisabledReason: interruption.impact,
      focusTarget: "support",
    };
  }
  if (isHistoricalView) {
    return {
      statusLabel: "历史阶段",
      statusColor: "default",
      severity: "info",
      headline: "正在查看历史阶段。",
      nextAction: "先返回当前阶段，再继续推进任务。",
      blockerReason: "历史阶段不能直接推进。",
      primaryActionLabel: "返回当前阶段",
      canAdvance: false,
      advanceDisabledReason: "正在查看历史阶段；请先返回当前阶段。",
      focusTarget: "current",
    };
  }

  // Live research without formal interruption: never invent failure/recovery facts.
  // Ordinary-user interruption meaning comes only from progressProjection.interruption.
  if (model.progressTrust.kind === "canonical" || model.progressTrust.kind === "invalid") {
    const status = model.executionDisplayStatus;
    if (status === "research_design_incomplete") {
      return {
        statusLabel: "研究方案未形成",
        statusColor: "warning",
        severity: "warning",
        headline: "研究方案尚未形成，当前没有可确认或可核查的内容。",
        nextAction: "可在协作区说明需求；有方案后再确认或推进。",
        blockerReason: "没有研究方案，不能当作待确认或结果待核查。",
        primaryActionLabel: "查看协作区",
        canAdvance: false,
        advanceDisabledReason: "研究方案尚未形成，不能进入下一阶段。",
        focusTarget: "support",
      };
    }
    if (
      status === "failed"
      || status === "external_result_unknown"
      || status === "needs_input"
      || status === "blocked_recoverable"
      || status === "blocked"
      || status === "stopped"
      || status === "cancelled"
      || status === "projection_unavailable"
    ) {
      return {
        statusLabel: status === "projection_unavailable" ? "进度暂不可确认" : "查看当前进展",
        statusColor: "default",
        severity: "info",
        headline: "当前研究状态以正式进展说明和已有成果为准。",
        nextAction: "查看已有研究产物和协作区。",
        blockerReason: null,
        primaryActionLabel: "查看当前进展",
        canAdvance: false,
        advanceDisabledReason: "当前不能推进到下一阶段。",
        focusTarget: "support",
      };
    }
    if (status === "running") {
      return {
        statusLabel: "执行中",
        statusColor: "processing",
        severity: "info",
        headline: "研究正在推进。",
        nextAction: "等待当前步骤完成；如需停止，使用停止本轮。",
        blockerReason: null,
        primaryActionLabel: "查看进展",
        canAdvance: false,
        advanceDisabledReason: "执行中不能跳过到下一阶段。",
        focusTarget: "support",
      };
    }
    if (status === "completed") {
      return {
        statusLabel: "已完成",
        statusColor: "success",
        severity: "success",
        headline: "任务已完成，最终产物和协作记录已保留。",
        nextAction: "查看最终产物或在右侧协作流中回顾执行过程。",
        blockerReason: null,
        primaryActionLabel: "查看最终产物",
        canAdvance: false,
        advanceDisabledReason: "任务已完成，不能继续推进阶段。",
        focusTarget: "artifact",
      };
    }
    if (status === "not_started") {
      return {
        statusLabel: "未开始",
        statusColor: "default",
        severity: "info",
        headline: "任务已创建，尚未进入执行。",
        nextAction: "点击“开始任务”，进入第一阶段执行。",
        blockerReason: "需要用户明确开始。",
        primaryActionLabel: "开始任务",
        canAdvance: false,
        advanceDisabledReason: "任务尚未开始，不能进入下一阶段。",
        focusTarget: "support",
      };
    }
  }

  if (model.taskStatus === "draft") {
    return {
      statusLabel: "等待你确认",
      statusColor: "warning",
      severity: "warning",
      headline: "研究起点草案尚未确认。",
      nextAction: "确认开始，或补充已有论文、数据、代码和边界条件。",
      blockerReason: "确认前不会开始检索、生成结论或推进后续阶段。",
      primaryActionLabel: "确认开始",
      canAdvance: false,
      advanceDisabledReason: "研究起点草案尚未确认，不能进入下一阶段。",
      focusTarget: "support",
    };
  }

  if (model.taskStatus === "not_started") {
    return {
      statusLabel: "尚未开始",
      statusColor: "default",
      severity: "info",
      headline: "任务已创建，尚未进入执行。",
      nextAction: "点击“开始任务”，进入第一阶段执行。",
      blockerReason: "需要用户明确开始。",
      primaryActionLabel: "开始任务",
      canAdvance: false,
      advanceDisabledReason: "任务尚未开始，不能进入下一阶段。",
      focusTarget: "support",
    };
  }

  if (model.taskStatus === "completed") {
    return {
      statusLabel: taskStatusLabel ?? "已完成",
      statusColor: "success",
      severity: "success",
      headline: "任务已完成，最终产物和协作记录已保留。",
      nextAction: "查看最终产物或在右侧协作流中回顾执行过程。",
      blockerReason: null,
      primaryActionLabel: "查看最终产物",
      canAdvance: false,
      advanceDisabledReason: "任务已完成，不能继续推进阶段。",
      focusTarget: "artifact",
    };
  }

  const runningCalls = model.capabilityCalls.filter((call) => call.status === "running").length;
  const queuedCalls = model.capabilityCalls.filter((call) => call.status === "queued" || call.status === "pending").length;

  if (model.taskStatus === "running" && runningCalls === 0 && queuedCalls > 0) {
    return {
      statusLabel: "准备中",
      statusColor: "gold",
      severity: "info",
      headline: "研究工作已排队，正在准备开始。",
      nextAction: "等待当前工作开始；开始后这里会显示真实进展。",
      blockerReason: null,
      primaryActionLabel: "查看进展",
      canAdvance: false,
      advanceDisabledReason: "当前工作正在准备，不能跳过或重试。",
      focusTarget: "support",
    };
  }

  const completedExecutionCalls = model.capabilityCalls.filter((call) => (
    isLiveWorkbenchExecutionCall(call)
    && call.status === "completed"
  ));
  const completedExecutionCallIds = new Set(completedExecutionCalls.map((call) => call.id));
  const completedExecutionArtifactIds = new Set(completedExecutionCalls.flatMap((call) => [
    ...(call.artifactRefs ?? []),
    ...(call.outputArtifactRefs ?? []),
  ]));
  const stageArtifacts = stageDecisionArtifacts(model, stage);
  const reviewableStageArtifacts = stageArtifacts.filter((artifact) => !isResearchStartDraftArtifact(artifact));
  const hasReviewableExecutionArtifact = reviewableStageArtifacts.some((artifact) => (
    (artifact.sourceCapabilityCallId != null && completedExecutionCallIds.has(artifact.sourceCapabilityCallId))
    || completedExecutionArtifactIds.has(artifact.id)
  ));

  if (model.taskStatus === "running" && !activeRunningSubstage(stage) && model.stageGateResumeApproved && hasReviewableExecutionArtifact) {
    return {
      statusLabel: "可进入下一阶段",
      statusColor: "success",
      severity: "success",
      headline: "阶段确认已通过，可以安全恢复到下一阶段。",
      nextAction: "继续进入下一阶段；系统会完成恢复并定位到下一阶段的第一个子阶段。",
      blockerReason: null,
      primaryActionLabel: "下一阶段",
      canAdvance: true,
      advanceDisabledReason: null,
      focusTarget: "current",
    };
  }

  // Capability calls are persisted independently from substage state. During
  // that short reconciliation window, a running call is authoritative: the
  // task is progressing, not waiting for recovery.
  if (model.taskStatus === "running" && runningCalls > 0) {
    return {
      statusLabel: "运行中",
      statusColor: "processing",
      severity: "info",
      headline: "系统正在推进当前子阶段。",
      nextAction: "等待当前运行完成，再复核产物或决定是否进入下一阶段。",
      blockerReason: "运行中不能静默进入下一阶段。",
      primaryActionLabel: "查看运行进度",
      canAdvance: false,
      advanceDisabledReason: "当前还有步骤正在运行，完成后才能进入复核或推进。",
      focusTarget: "support",
    };
  }

  const terminalInputCall = model.capabilityCalls.find((call) => (
    call.status === "needs_input"
    || call.status === "insufficient_evidence"
  ));
  if (model.taskStatus === "running" && terminalInputCall && !activeRunningSubstage(stage)) {
    return {
      statusLabel: "等待你的补充",
      statusColor: "warning",
      severity: "warning",
      headline: "当前步骤已结束，需要补充信息后再继续。",
      nextAction: "在右侧协作区补充范围、材料或判断依据；系统会将输入记录到当前节点。",
      blockerReason: terminalInputCall.resultSummary ?? "当前步骤没有形成可继续使用的产物。",
      primaryActionLabel: "补充当前节点",
      canAdvance: false,
      advanceDisabledReason: "当前节点等待你的补充，不能进入下一阶段。",
      focusTarget: "support",
    };
  }

  const blockedRecoverableCall = model.capabilityCalls.find((call) => call.status === "blocked_recoverable");
  if (model.taskStatus === "running" && blockedRecoverableCall && !activeRunningSubstage(stage)) {
    return {
      statusLabel: "执行受阻",
      statusColor: "warning",
      severity: "warning",
      headline: "当前步骤暂未继续推进。",
      nextAction: "查看受阻原因。",
      blockerReason: blockedRecoverableCall.resultSummary ?? "当前步骤执行受阻。",
      primaryActionLabel: "查看受阻详情",
      canAdvance: false,
      advanceDisabledReason: "执行受阻，不能进入下一阶段。",
      focusTarget: "support",
    };
  }

  if (model.taskStatus !== "running") {
    const label = taskStatusLabel ?? model.taskStatus;
    const isFailed = model.taskStatus === "failed";
    return {
      statusLabel: isFailed ? "执行未完成" : label,
      statusColor: isFailed ? "red" : "warning",
      severity: isFailed ? "error" : "warning",
      headline: isFailed ? "当前研究步骤未能继续。" : "当前结果需要人工确认。",
      nextAction: isFailed ? "查看已有成果和中断说明；可用操作确认后会显示。" : "先确认当前结果或补充来源后再继续。",
      blockerReason: isFailed ? "当前步骤及其后续判断未完成，已有成果保持各自核查状态。" : "等待人工确认。",
      primaryActionLabel: isFailed ? "查看本次影响" : "完成确认",
      canAdvance: false,
      advanceDisabledReason: isFailed ? "本次执行未完成，不能推进阶段。" : `当前任务状态为 ${label}，不能推进阶段。`,
      focusTarget: isFailed ? "support" : "artifact",
    };
  }

  const inputBlockedSubstage = stage?.subStages.find((substage) => substage.projectionStatus === "needs_input");
  if (inputBlockedSubstage) {
    return {
      statusLabel: "待补充材料",
      statusColor: "warning",
      severity: "warning",
      headline: `当前节点「${inputBlockedSubstage.label}」需要补充材料。`,
      nextAction: "具体需要的输入和可用操作将在研究说明中显示。",
      blockerReason: "当前节点缺少可定位的输入材料，不能进入下一阶段。",
      primaryActionLabel: "补充材料",
      canAdvance: false,
      advanceDisabledReason: "当前节点需要补充材料，不能进入下一阶段。",
      focusTarget: "support",
    };
  }

  const hasExecutionRecord = model.capabilityCalls.some(isLiveWorkbenchExecutionCall);
  if (!hasExecutionRecord) {
    return {
      statusLabel: "准备中",
      statusColor: "warning",
      severity: "warning",
      headline: "任务已启动，正在准备首项研究工作。",
      nextAction: "等待当前工作开始；出现真实进展后会在这里更新。",
      blockerReason: null,
      primaryActionLabel: "查看进展",
      canAdvance: false,
      advanceDisabledReason: "当前工作正在准备，不能跳过或重试。",
      focusTarget: "support",
    };
  }

  const selectedStageArtifact = selectedArtifact && stageArtifacts.some((artifact) => artifact.id === selectedArtifact.id)
    ? selectedArtifact
    : null;
  const targetArtifact = selectedStageArtifact && !isResearchStartDraftArtifact(selectedStageArtifact)
    ? selectedStageArtifact
    : reviewableStageArtifacts[0] ?? null;
  if (!targetArtifact) {
    if (model.taskStatus === "running" && !activeRunningSubstage(stage)) {
      return {
        statusLabel: "准备中",
        statusColor: "warning",
        severity: "info",
        headline: "当前阶段正在准备下一项工作。",
        nextAction: hasRecoverableSubstageContext(stage)
          ? "等待当前工作状态更新。"
          : "等待当前工作状态确认。",
        blockerReason: null,
        primaryActionLabel: "查看进展",
        canAdvance: false,
        advanceDisabledReason: "当前工作正在准备，不能跳过或重试。",
        focusTarget: "support",
      };
    }
    return {
      statusLabel: "暂无可复核产物",
      statusColor: "warning",
      severity: "warning",
      headline: "当前阶段还没有形成可复核产物。",
      nextAction: "等待当前工作状态确认；已有的其他成果仍可查看。",
      blockerReason: "没有可供复核和下游使用的阶段产物。",
      primaryActionLabel: "补充材料",
      canAdvance: false,
      advanceDisabledReason: "当前阶段还没有可复核的产物，不能进入下一阶段。",
      focusTarget: "support",
    };
  }

  const blockingArtifact = reviewableStageArtifacts.find((artifact) => {
    const gate = artifactHumanGate(artifact);
    return !gate.canUseDownstream || gate.requiresHumanReview || gate.requiresScopeExpansionConfirmation;
  }) ?? targetArtifact;
  const gate = artifactHumanGate(blockingArtifact);
  const gateStatus = artifactGateStatus(gate);
  const canAdvance = hasReviewableExecutionArtifact
    && !reviewableStageArtifacts.some((artifact) => {
      const artifactGate = artifactHumanGate(artifact);
      return !artifactGate.canUseDownstream || artifactGate.requiresHumanReview || artifactGate.requiresScopeExpansionConfirmation;
    });
  const disabledReason = canAdvance
    ? null
    : gate.state === "needs_review" || gate.canConfirmPass
      ? "当前产物需要人工复核；通过后才能进入下一阶段。"
      : artifactGateMessage(gate);

  const resolvedDisabledReason = disabledReason ?? "当前阶段尚不能进入下一阶段。";
  return {
    ...gateStatus,
    headline: canAdvance ? "当前阶段产物可进入下一阶段。" : resolvedDisabledReason,
    blockerReason: canAdvance ? null : resolvedDisabledReason,
    canAdvance,
    advanceDisabledReason: canAdvance ? null : resolvedDisabledReason,
  };
}

export function userFacingArtifactSourceLabel(model?: WorkbenchTaskModel | null, artifact?: WorkbenchArtifact | null): string {
  if (isResearchStartDraftArtifact(artifact)) return "任务起点";
  if (isLowConfidenceWorkbenchSource(model, artifact)) return "来源待核对";
  if ((artifact?.sourceMode ?? model?.resultSource) === "external") return "外部来源";
  if ((artifact?.sourceMode ?? model?.resultSource) === "inline") return "任务产物";
  return "任务产物";
}

export function evidenceConfidenceSummary(model: WorkbenchTaskModel, artifact?: WorkbenchArtifact | null): string {
  if (isLowConfidenceWorkbenchSource(model, artifact)) {
    return "该产物需补充来源后再引用，并由科研人员复核。";
  }
  if ((artifact?.evidenceRefs?.length ?? 0) > 0) {
    return `已关联 ${artifact?.evidenceRefs?.length ?? 0} 条来源线索；关键结论仍需按原文、实验条件和上下文复核。`;
  }
  return "证据来源仍需补充；当前归纳不能直接作为最终结论或引用依据。";
}

export function reviewStateLabel(model: WorkbenchTaskModel, artifact?: WorkbenchArtifact | null): { label: string; color: string } {
  void model;
  if (!isResearchStartDraftArtifact(artifact)) {
    const presentation = artifact ? artifactStatusPresentation(artifact, "zh-CN") : null;
    if (presentation) return presentation;
  }
  if (isResearchStartDraftArtifact(artifact)) return { label: "研究起点草案", color: "processing" };
  const projection = projectWorkbenchArtifactState(artifact);
  if (projection.state === "usable") return { label: "已核查入库", color: projection.badge.color };
  if (projection.state === "needs_review") return { label: "待你核查", color: projection.badge.color };
  if (projection.state === "needs_input") return { label: "待补充材料", color: projection.badge.color };
  if (projection.state === "partial_success") return { label: "需确认范围", color: projection.badge.color };
  if (projection.state === "rejected_or_needs_revision") return { label: "退回修改", color: projection.badge.color };
  return { label: projection.badge.label, color: projection.badge.color };
}

export function domainResearchLens(model: WorkbenchTaskModel): { label: string; summary: string; gaps: string[] } {
  const text = `${model.domain} ${model.title} ${model.goalSummary}`.toLowerCase();
  if (/material|alloy|polymer|battery|catalyst|材料|合金|聚合物|电池|催化|成分|工艺|结构|性能/.test(text)) {
    return {
      label: "材料视角",
      summary: "材料体系、成分 / 工艺 / 结构 / 性能、表征与实验缺口需要单独核对。",
      gaps: ["材料体系：待补充字段", "成分 / 工艺 / 结构 / 性能：待补充字段", "表征 / 实验缺口：待补充字段"],
    };
  }
  if (/bio|biomed|cell|protein|clinical|mouse|patient|gene|生医|细胞|样本|物种|对照|批次|统计/.test(text)) {
    return {
      label: "生医视角",
      summary: "样本 / 物种 / 细胞系、实验条件、对照 / 批次 / 统计边界需要单独核对。",
      gaps: ["样本 / 物种 / 细胞系：待补充字段", "实验条件：待补充字段", "对照 / 批次 / 统计边界：待补充字段"],
    };
  }
  if (/ai|ml|model|dataset|baseline|metric|code|env|算法|数据集|基线|指标|复现/.test(text)) {
    return {
      label: "AI 视角",
      summary: "dataset、baseline、metric、code/env 与复现实验缺口需要单独核对。",
      gaps: ["dataset：待补充字段", "baseline：待补充字段", "metric：待补充字段", "code/env：待补充字段", "复现实验缺口：待补充字段"],
    };
  }
  return {
    label: "通用科研视角",
    summary: "领域字段尚未识别；请补充研究对象、材料来源、验证条件和评价指标。",
    gaps: ["研究对象：待补充字段", "证据来源：待补充字段", "验证条件 / 评价指标：待补充字段"],
  };
}

export function sanitizeWorkbenchText(value: string): string {
  const internalDetailPattern = /research-config\.ya?ml|workspace\s+(?:directory|path|scan|structure)|工作区目录|目录结构|配置文件|file\s+not\s+found|未找到.*(?:ya?ml|配置|文件)|(?:ya?ml|配置|文件).*未找到|\bconfig(?:uration)?\b|\badapter\b|\btrace\b|\bskill[_ -]?id\b/i;
  if (internalDetailPattern.test(value)) {
    return "当前步骤暂未继续，系统正在确认执行条件；已有研究产物继续保留。";
  }
  return value
    .replace(
      /Traceable literature run completed with\s+(\d+)\s+deduped sources and\s+(\d+)\s+(?:Top-K recommendations|推荐阅读)\.?/gi,
      "已完成可追溯文献调研：$1 条去重来源，$2 条推荐阅读。",
    )
    .replace(/traceable literature live path/gi, "可追溯文献调研")
    .replace(/Top-K recommendations/gi, "推荐阅读")
    .replace(/metadata\/abstract-bounded/gi, "基于题录与摘要信息（需你核对）")
    .replace(/abstract-bounded/gi, "基于摘要信息（需你核对）")
    .replace(/Seed fallback record for RAG evaluation/gi, "来源待补充记录（不能直接引用）")
    .replace(/seed fallback record/gi, "来源待补充记录");
}
