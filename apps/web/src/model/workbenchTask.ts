import { projectWorkbenchDelivery } from "./workbenchDelivery";
import { createFormingWorkbenchTaskModel } from "./formingWorkbenchTask";
import { projectResearchDesigns } from "../lib/confirmedResearchDesign";
import type {
  Artifact,
  ArtifactEvidenceRef,
  ArtifactHumanGate,
  ArtifactValidatorResult,
  CanonicalTaskWorkUnit,
  CapabilityCall,
  CreationStatus,
  MaterialManifest,
  StageStatus,
  Task,
  TaskExecutionMode,
  TaskPlan,
  TaskStage,
  ToolResourceTrustRecord,
  UserFacingNarrative,
  UserVisibleEvent,
  ResultSource,
  TaskStatus,
  UiLanguage,
} from "@openair/shared";
import { STAGE_LABELS, STATUS_LABELS } from "@openair/shared";
import {
  durableExecutionDisplayStatus,
  parseDurableProgressProjection,
  coerceStartedStageStatus,
  type ArtifactDisplayProjection,
  type DurableResearchActivity,
  type DurableProgressProjection,
  type DurableStageProgress,
  type DurableSubstageProgress,
  type TaskEvent,
  type TaskWithProgressProjection,
} from "../api/client";
import type { CanonicalResearchCurrentState } from "./researchActivityStreamCore.mjs";
import {
  resolveSubstageStageKey,
  rollupDurableStageStatus,
  CANONICAL_STAGE_ORDER,
  canonicalStageLabel,
  durableSubstageBaseLabel,
  durableSubstageDisplayLabel,
  projectVisibleDurableResearch,
} from "./workbenchScreenCore.mjs";
import {
  PAGE_PHASE_ORDER,
  UNDERLYING_STAGE_ORDER,
  aggregatePhaseStatus,
  mapStageStatusToNav,
  pagePhaseForUnderlying,
  underlyingStageForPagePhase,
  type PagePhaseId,
  type PhaseNavStatus,
} from "../lib/pagePhases";
import type { FixtureArtifactJson, FixtureCapabilityCallJson, FixtureStageJson, FixtureTaskJson } from "../data/fixtureTypes";
import { fixtureContentForPath } from "../data/csResearchOpeArtifactContent";
import { isUnderlyingStage, labelForSubStage, skillTechnicalId, subStageRowsFromFixture } from "../data/recordedRunAdapter";
import { normalizeResultSource } from "../lib/resultSource";

import { parseResearchInterruption, type ResearchInterruption } from "../lib/workbenchInterruption.mjs";

export type ProgressProjectionTrust =
  | { kind: "canonical"; projection: DurableProgressProjection }
  | { kind: "legacy" }
  | { kind: "invalid"; reason: "revision_mismatch" | "malformed" };

export interface WorkbenchSubStageRow {
  id: string;
  subStage: string;
  label: string;
  /** Stable position within the canonical parent stage. */
  instanceSequence?: number;
  /** Server-authored instance objective. Never synthesized from creationPlan. */
  objective?: string | null;
  status: PhaseNavStatus;
  /** Raw durable projection status when present (needs_input / failed / …). */
  projectionStatus?: string;
  statusReasonCode?: string | null;
  artifactRefs: string[];
  /** Durable work units backing this substage; used for live progress polling. */
  workUnitIds?: string[];
  affected?: boolean;
  completedCount?: number;
  totalCount?: number;
  l3Plan?: import("../lib/workbenchInteraction").L3PlanState | null;
}

export interface WorkbenchStageRow {
  stage: TaskStage;
  label: string;
  pagePhase: PagePhaseId;
  pagePhaseLabel: string;
  status: PhaseNavStatus;
  /** Raw durable projection status when present. */
  projectionStatus?: string;
  statusReasonCode?: string | null;
  summary: string | null;
  risks: string | null;
  nextSteps: string | null;
  entryCondition?: string | null;
  completionCondition?: string | null;
  lockedReason?: string | null;
  primaryAction?: string | null;
  canSkip?: boolean | null;
  canReview?: boolean | null;
  canLock?: boolean | null;
  artifactRefs: string[];
  completedCount?: number;
  totalCount?: number;
  /** Live projection only: zero instances means this stage is outside the current plan. */
  planState?: "planned" | "unplanned";
  subStages: WorkbenchSubStageRow[];
}

export interface WorkbenchArtifact {
  displayProjection?: ArtifactDisplayProjection | null;
  id: string;
  taskId?: string;
  sourceTaskId?: string;
  type: string;
  title: string;
  summary?: string | null;
  format: string;
  relatedStage: TaskStage;
  createdAt?: string;
  /** fixture artifacts.json 中的相对路径（演示环境用于载入正文） */
  path?: string;
  relatedSkillId?: string;
  substage?: string | null;
  subStageInstanceId?: string | null;
  sourceCapabilityCallId?: string | null;
  artifactState?: string | null;
  validatorResult?: ArtifactValidatorResult | null;
  resultProtocol?: Artifact["resultProtocol"] | null;
  artifactQuality?: Artifact["artifactQuality"] | null;
  warnings?: string[];
  humanGate?: ArtifactHumanGate | null;
  structuredResearchStart?: Artifact["structuredResearchStart"] | null;
  researchStartConfirmation?: Artifact["researchStartConfirmation"] | null;
  missingInputs?: string[];
  nonCitableBoundaries?: string[];
  downstreamInput?: Record<string, unknown> | null;
  artifactType?: string | null;
  artifactVisibility?: "user" | "audit" | "internal" | "hidden" | string | null;
  audience?: string | null;
  /** 已解析的正文（markdown / 代码等），API 对接后由后端返回 */
  content?: string | null;
  sourceMode?: "live" | "replay" | "demo" | "none" | "fixture" | "inline" | "external";
  /** 存储类型：inline（JSONB）、local（文件系统）、external（MinIO） */
  storageType?: "inline" | "local" | "external";
  /** MinIO 对象 key（storageType=external 时有值） */
  externalKey?: string;
  evidenceRefs?: ArtifactEvidenceRef[];
  linkedMaterialIds?: string[];
  materialManifest?: MaterialManifest[];
  memoryRefs?: string[];
  trustRecords?: ToolResourceTrustRecord[];
  sourceLabel?: string | null;
  trustStatus?: "citable" | "review_needed" | "low_confidence" | "failed" | "sample_placeholder";
  processingStatus?: string | null;
  handoffActions?: string[];
  pageCount?: number | null;
  /** 当前生效版本 id（issue #302 产物版本管理） */
  currentVersionId?: string | null;
  /** 版本数量（含初始/编辑/重生成/恢复/退回事件） */
  versionCount?: number;
  /** 退回修改时间（待修改态） */
  revisionRequestedAt?: string | null;
  /** 最近一次退回反馈 */
  revisionFeedback?: string | null;
  userFacingNarrative?: UserFacingNarrative | null;
}

export interface WorkbenchCapabilityCall {
  id: string;
  name: string;
  status: string;
  runtime?: string;
  startedAt?: string;
  endedAt?: string | null;
  artifactRefs?: string[];
  stageKey?: TaskStage;
  subStageInstanceId?: string;
  inputArtifactRefs?: string[];
  outputArtifactRefs?: string[];
  resultSummary?: string | null;
  prevCallId?: string | null;
  inputCallRefs?: string[];
  runId?: string | null;
  runMode?: string | null;
  sourceMode?: string | null;
  provider?: string | null;
  adapter?: string | null;
  model?: string | null;
  sourceAccess?: string | null;
  sourceMetadata?: Record<string, unknown>;
  callVisibility?: "user" | "audit" | "internal" | "hidden" | string | null;
  audience?: string | null;
  partialFailureImpact?: string | null;
  artifactValidatorResults?: ArtifactValidatorResult[];
  targetAgent?: string | null;
  userFacingNarrative?: UserFacingNarrative | null;
}

export interface WorkbenchTaskModel {
  taskId: string;
  /** Authoritative GET scope used to fence realtime events; not presentation data. */
  ownerId?: string;
  workspaceId?: string;
  revision: number;
  title: string;
  goalSummary: string;
  domain: string;
  skillPack?: string;
  currentUnderlyingStage: TaskStage;
  taskStatus: TaskStatus;
  /** Explicit user-visible execution status from durable projection (or legacy task status). */
  executionDisplayStatus: string;
  /** Read-only, same-revision interruption facts from canonical progress; never an action Authority. */
  interruption?: ResearchInterruption | null;
  progressTrust: ProgressProjectionTrust;
  progressRevision: number | null;
  stageCounts: { completed: number; total: number } | null;
  /** Live collaborators only from durable workbenchProjection; never from plan lists. */
  collaborationNodes: DurableProgressProjection["workbenchProjection"]["collaborationNodes"];
  collaborationEvents: DurableProgressProjection["workbenchProjection"]["collaborationEvents"];
  /** Revision-bound lifecycle facts received from the durable task event stream. */
  lifecycleEvents: TaskEvent[];
  /** Canonical public WorkUnit facts; used for round grouping of repeatable-substage artifacts. */
  workUnits: CanonicalTaskWorkUnit[];
  /** Task-GET history rebuilt from durable canonical research facts. */
  researchProcessHistory: DurableResearchActivity[];
  /** Same Task-GET snapshot; SSE/history can never replace this current state. */
  researchCurrentState: CanonicalResearchCurrentState | null;
  resultSource: ResultSource;
  /** Canonical user-facing research delivery from the task public projection. */
  finalResult: NonNullable<Task["finalResult"]> | null;
  completionDecision?: Task["completionDecision"];
  executionMode: TaskExecutionMode;
  stageGateResumeApproved: boolean;
  creationStatus?: CreationStatus;
  /** Planning context only — never treated as live stage completion. */
  creationPlan?: TaskPlan | null;
  confirmedResearchDesign?: import("@openair/shared").ResearchDesignVersion | null;
  researchDesign: {
    designId: string;
    version: number;
    status: "draft" | "confirmed" | "superseded" | "cancelled";
  } | null;
  researchDesignHistory: Array<{
    version: number;
    status: "draft" | "confirmed" | "superseded" | "cancelled";
    createdAt: string;
    goal: string;
    instances: Array<{
      instanceId: string;
      stageKey: TaskStage;
      objective: string;
      sequenceHint: number | null;
    }>;
  }>;
  createdAt: string;
  updatedAt: string;
  stages: WorkbenchStageRow[];
  artifacts: WorkbenchArtifact[];
  capabilityCalls: WorkbenchCapabilityCall[];
  trustRecords: ToolResourceTrustRecord[];
  userVisibleEvents: UserVisibleEvent[];
}

export function stageRowForPagePhase(model: WorkbenchTaskModel, phase: PagePhaseId): WorkbenchStageRow | undefined {
  const u = underlyingStageForPagePhase(phase);
  return model.stages.find((r) => r.stage === u);
}

export function phaseSummaryMap(model: WorkbenchTaskModel): Record<PagePhaseId, string | null> {
  const out: Record<PagePhaseId, string | null> = {
    problem: null,
    method: null,
    experiment: null,
    report: null,
  };
  for (const p of PAGE_PHASE_ORDER) {
    out[p] = stageRowForPagePhase(model, p)?.summary ?? null;
  }
  return out;
}

export function pagePhaseAggregateStatus(model: WorkbenchTaskModel): Record<PagePhaseId, PhaseNavStatus> {
  const out: Record<PagePhaseId, PhaseNavStatus> = {
    problem: "not_started",
    method: "not_started",
    experiment: "not_started",
    report: "not_started",
  };
  for (const p of PAGE_PHASE_ORDER) {
    const row = stageRowForPagePhase(model, p);
    out[p] = row ? row.status : "not_started";
  }
  return out;
}

/** 聚合多行状态（二级步骤列表） */
export function aggregateSubStageStatus(statuses: PhaseNavStatus[]): PhaseNavStatus {
  return aggregatePhaseStatus(statuses);
}

function mapFixtureTaskStatus(raw: string): TaskStatus {
  const s = raw.toLowerCase();
  if (s === "completed") return "completed";
  if (s === "failed" || s === "blocked") return "failed";
  if (s === "awaiting_confirmation" || s === "waiting_user" || s === "pending") return "awaiting_confirmation";
  return "running";
}

function mapProjectionStatusToNav(status: string): PhaseNavStatus {
  if (status === "external_result_unknown") return "pending";
  if (status === "needs_input") return "pending";
  if (status === "cancelled" || status === "stopped") return "failed";
  return mapStageStatusToNav(status);
}

function mapTaskProgressToTaskStatus(status: string): TaskStatus {
  if (status === "draft") return "draft";
  if (status === "not_started") return "not_started";
  if (status === "running") return "running";
  if (status === "awaiting_confirmation") return "awaiting_confirmation";
  if (status === "completed") return "completed";
  if (status === "failed") return "failed";
  if (status === "cancelled" || status === "stopped") return "cancelled";
  return "failed";
}

function resolveProgressTrust(task: TaskWithProgressProjection): ProgressProjectionTrust {
  const raw = (task as TaskWithProgressProjection).progressProjection;
  if (raw == null) return { kind: "legacy" };
  const parsed = parseDurableProgressProjection(raw);
  if (!parsed) return { kind: "invalid", reason: "malformed" };
  const taskRevision = task.revision ?? 0;
  if (parsed.revision !== taskRevision) return { kind: "invalid", reason: "revision_mismatch" };
  return { kind: "canonical", projection: parsed };
}

/** Legacy only: never invent completed from artifactRefs; calls may refine not_started. */
function resolveSubstageNavStatus(
  sub: WorkbenchSubStageRow,
  calls: WorkbenchCapabilityCall[],
): PhaseNavStatus {
  if (sub.status !== "not_started") return sub.status;
  const related = calls.filter((c) => c.subStageInstanceId === sub.id);
  if (related.some((c) => c.status === "blocked_recoverable")) {
    return "blocked";
  }
  if (related.some((c) => c.status === "needs_input" || c.status === "insufficient_evidence" || c.status === "external_result_unknown")) {
    return "pending";
  }
  if (related.some((c) => c.status === "failed" || c.status === "cancelled")) return "failed";
  if (related.some((c) => c.status === "running")) return "running";
  // Intentionally do NOT treat artifactRefs as completed — that was T08 root-cause guessing.
  return sub.status;
}

function resolveStageNavStatus(
  task: Task,
  stage: TaskStage,
  storedStatus: string,
): PhaseNavStatus {
  const mapped = mapStageStatusToNav(storedStatus);
  if (stage === task.currentStage && task.status === "awaiting_confirmation") {
    return "pending";
  }
  // Do not promote prior stages to completed solely from currentStage index.
  return mapped;
}

/** Reserved-task shell contains display metadata, no generated goal. */
export function formingWorkbenchTaskModel(input: { taskId: string; title: string }): WorkbenchTaskModel {
  return createFormingWorkbenchTaskModel(input, [...UNDERLYING_STAGE_ORDER].map(stage => buildStageRow(stage)));
}

/** Stage order for live rows: durable stageDetails / task.stages only — never creationPlan. */
function workflowStageOrder(task: Task): TaskStage[] {
  const fromTask = (task.stages ?? []).filter(isUnderlyingStage);
  const fromDetails = (task.stageDetails ?? [])
    .map((stage) => stage.stageKey)
    .filter(isUnderlyingStage);

  for (const ordered of [fromDetails, fromTask]) {
    const deduped = ordered.filter((stage, idx) => ordered.indexOf(stage) === idx);
    if (deduped.length > 0) return deduped;
  }
  return [...UNDERLYING_STAGE_ORDER];
}

const ORDINARY_USER_COPY_REPLACEMENTS: Record<string, string> = {
  insight_generation: STAGE_LABELS.insight_generation,
  code_implementation: labelForSubStage("code_implementation"),
  experiment_execution: labelForSubStage("experiment_execution"),
  result_monitoring: labelForSubStage("result_monitoring"),
};

function ordinaryUserText(value?: string | null): string | null {
  if (!value) return value ?? null;
  return Object.entries(ORDINARY_USER_COPY_REPLACEMENTS).reduce(
    (text, [internalName, displayName]) => text.replaceAll(internalName, displayName),
    value,
  );
}

function buildStageRow(stage: TaskStage, hit?: Partial<WorkbenchStageRow>): WorkbenchStageRow {
  const pagePhase = pagePhaseForUnderlying(stage);
  const fixedStageLabel = STAGE_LABELS[stage];
  return {
    stage,
    label: fixedStageLabel,
    pagePhase,
    pagePhaseLabel: fixedStageLabel,
    status: hit?.status ?? "not_started",
    projectionStatus: hit?.projectionStatus,
    statusReasonCode: hit?.statusReasonCode ?? null,
    summary: ordinaryUserText(hit?.summary),
    risks: ordinaryUserText(hit?.risks),
    nextSteps: ordinaryUserText(hit?.nextSteps),
    entryCondition: ordinaryUserText(hit?.entryCondition),
    completionCondition: ordinaryUserText(hit?.completionCondition),
    lockedReason: ordinaryUserText(hit?.lockedReason),
    primaryAction: ordinaryUserText(hit?.primaryAction),
    canSkip: hit?.canSkip ?? null,
    canReview: hit?.canReview ?? null,
    canLock: hit?.canLock ?? null,
    artifactRefs: hit?.artifactRefs ?? [],
    completedCount: hit?.completedCount,
    totalCount: hit?.totalCount,
    planState: hit?.planState,
    subStages: hit?.subStages ?? [],
  };
}

function buildLiteratureSubStagesFromArtifacts(artifacts: WorkbenchArtifact[]): WorkbenchSubStageRow[] {
  const byType = new Map(artifacts.map((artifact) => [artifact.type, artifact.id]));
  const specs: Array<{ subStage: string; label: string; types: string[] }> = [
    { subStage: "research_plan", label: "检索策略", types: ["literature_search_strategy"] },
    { subStage: "source_table", label: "来源表", types: ["source_table"] },
    { subStage: "recommended_readings", label: "推荐阅读", types: ["top_k_source_set"] },
    { subStage: "literature_synthesis", label: "短综述", types: ["short_literature_review_draft"] },
    { subStage: "gap_next_steps", label: "缺口与下一步", types: ["literature_gaps_next_steps"] },
  ];
  return specs
    .map((spec): WorkbenchSubStageRow | null => {
      const refs = spec.types.map((type) => byType.get(type)).filter((id): id is string => Boolean(id));
      if (refs.length === 0) return null;
      return {
        id: spec.subStage,
        subStage: spec.subStage,
        label: spec.label,
        status: "not_started" as const,
        artifactRefs: refs,
        l3Plan: undefined,
      };
    })
    .filter((row): row is WorkbenchSubStageRow => row !== null);
}

function withLiteratureSubStages(stages: WorkbenchStageRow[], artifacts: WorkbenchArtifact[]): WorkbenchStageRow[] {
  const literatureArtifacts = artifacts.filter((artifact) =>
    [
      "literature_search_strategy",
      "source_table",
      "top_k_source_set",
      "short_literature_review_draft",
      "literature_gaps_next_steps",
    ].includes(artifact.type),
  );
  if (literatureArtifacts.length === 0) return stages;
  return stages.map((stage) => {
    if (stage.stage !== "problem_modeling") return stage;
    const existingBySubStage = new Map(stage.subStages.map((sub) => [sub.subStage, sub]));
    // Planning/context nodes only — never mark completed from artifact presence.
    const derived = buildLiteratureSubStagesFromArtifacts(literatureArtifacts)
      .map((sub) => ({ ...sub, status: "not_started" as const }))
      .filter((sub) => !existingBySubStage.has(sub.subStage));
    if (derived.length === 0) {
      return stage;
    }
    return {
      ...stage,
      subStages: [...stage.subStages, ...derived],
    };
  });
}

function mapApiCapabilityStatus(c: CapabilityCall): string {
  return c.status === "not_started" ? "pending" : c.status;
}

function resolveCapabilityCallSkillName(call: CapabilityCall, task: Task): string {
  const name = (call.name || "").trim();
  if (name && name !== "workbench_chat" && name !== "workbench-chat") {
    return skillTechnicalId(name);
  }
  const instanceId = call.subStageInstanceId;
  if (instanceId) {
    for (const detail of task.stageDetails ?? []) {
      for (const ss of detail.subStages ?? []) {
        if (ss.subStageInstanceId === instanceId) {
          return skillTechnicalId(ss.subStage);
        }
      }
    }
  }
  return name || "workbench_chat";
}

function inferArtifactFormat(path?: string, format?: string): string {
  if (format) return format;
  if (path?.endsWith(".md")) return "md";
  if (path?.endsWith(".json")) return "json";
  return "text";
}

function mapApiArtifact(task: Task, a: Artifact): WorkbenchArtifact {
  const path = a.path ?? (a.summary?.includes("/") ? a.summary : undefined);
  const isInline = a.storageType === "inline";
  return {
    id: a.id,
    taskId: a.taskId,
    sourceTaskId: a.sourceTaskId,
    type: a.kind,
    title: a.title,
    summary: a.summary ?? null,
    format: inferArtifactFormat(path, a.format),
    relatedStage: a.relatedStage ?? a.stage ?? task.currentStage,
    createdAt: a.createdAt,
    path,
    relatedSkillId: a.relatedSkillId,
    substage: a.substage ?? null,
    subStageInstanceId: a.subStageInstanceId ?? null,
    // inline 存储的产物，正文已随任务接口下发，无需再走 hydrate
    content: isInline ? (a.inlineContent ?? null) : null,
    sourceMode: undefined,
    sourceCapabilityCallId: a.sourceCapabilityCallId ?? null,
    artifactState: a.artifactState ?? null,
    validatorResult: a.validatorResult ?? null,
    resultProtocol: a.resultProtocol ?? null,
    artifactQuality: a.artifactQuality ?? null,
    warnings: a.warnings ?? [],
    humanGate: a.humanGate ?? null,
    structuredResearchStart: a.structuredResearchStart ?? null,
    researchStartConfirmation: a.researchStartConfirmation ?? null,
    missingInputs: a.missingInputs ?? [],
    nonCitableBoundaries: a.nonCitableBoundaries ?? [],
    downstreamInput: a.downstreamInput ?? null,
    artifactType: a.artifactType ?? a.kind,
    artifactVisibility: (a as { artifactVisibility?: string | null }).artifactVisibility ?? null,
    audience: (a as { audience?: string | null }).audience ?? null,
    storageType: a.storageType,
    externalKey: a.externalKey,
    evidenceRefs: a.evidenceRefs ?? [],
    linkedMaterialIds: a.linkedMaterialIds ?? [],
    materialManifest: a.materialManifest ?? [],
    memoryRefs: a.memoryRefs ?? [],
    trustRecords: a.trustRecords ?? [],
    currentVersionId: a.currentVersionId ?? null,
    versionCount: a.versions?.length ?? 0,
    revisionRequestedAt: a.revisionRequestedAt ?? null,
    revisionFeedback: a.revisionFeedback ?? null,
    userFacingNarrative: a.userFacingNarrative ?? null,
  };
}

function artifactSourceModeFromCallOrTask(
  task: Task,
  artifact: WorkbenchArtifact,
  calls: WorkbenchCapabilityCall[],
): WorkbenchArtifact["sourceMode"] | undefined {
  const call = artifact.sourceCapabilityCallId
    ? calls.find((item) => item.id === artifact.sourceCapabilityCallId)
    : calls.find((item) => item.outputArtifactRefs?.includes(artifact.id) || item.artifactRefs?.includes(artifact.id));
  const raw = (call?.sourceMode ?? call?.runMode ?? task.resultSource)?.toLowerCase();
  if (raw === "live") return "live";
  if (raw === "replay" || raw === "replay_fallback") return "replay";
  if (raw === "fixture") return "fixture";
  if (raw === "demo") return "demo";
  if (raw === "none") return "none";
  if (!call && task.resultSource === "live") return "live";
  if (!call && task.resultSource === "replay") return "replay";
  if (!call && task.resultSource === "demo") return "demo";
  return undefined;
}

export function hydrateArtifactContent(
  model: WorkbenchTaskModel,
  artifactId: string,
  content: string,
  sourceMode: "live" | "replay" | "inline" | "external",
  evidenceRefs: ArtifactEvidenceRef[] = [],
  format?: string | null,
  displayProjection?: ArtifactDisplayProjection | null,
): WorkbenchTaskModel {
  const hydratedFormat = format?.trim() || null;
  return {
    ...model,
    artifacts: model.artifacts.map((artifact) =>
      artifact.id === artifactId
        ? { ...artifact, content, displayProjection, sourceMode, evidenceRefs, format: hydratedFormat ?? artifact.format }
        : artifact,
    ),
  };
}

type DesignInstanceDisplay = {
  substageDefinitionId: string;
  stageKey: string;
  objective: string;
  sequenceHint: number | null;
};

function activeDesignInstanceDisplays(task: Task): {
  hasActiveDesign: boolean;
  instances: Map<string, DesignInstanceDisplay>;
} {
  const versions = task.researchDesignVersions ?? [];
  const activeRef = task.activeResearchDesignRef;
  const active = activeRef
    ? versions.find((design) => design.researchDesignId === activeRef.researchDesignId && design.version === activeRef.version)
    : [...versions].filter((design) => design.status === "confirmed").sort((a, b) => b.version - a.version)[0];
  return {
    hasActiveDesign: Boolean(active),
    instances: new Map((active?.instances ?? []).map((instance) => [instance.substageInstanceId, {
      substageDefinitionId: instance.substageDefinitionId,
      stageKey: instance.stageKey,
      objective: instance.objective,
      sequenceHint: instance.sequenceHint,
    }])),
  };
}

function stagesFromDurableProjection(
  projection: DurableProgressProjection,
): WorkbenchStageRow[] {
  const substagesByStage = new Map<string, Array<DurableSubstageProgress & { sourceSequence: number }>>();
  for (const [sourceIndex, sub] of projection.substageProgress.entries()) {
    const substageDefinitionId = sub.substageDefinitionId;
    const substageKey = substageDefinitionId ?? sub.substageInstanceId;
    const stageKey = resolveSubstageStageKey(substageKey, sub.stageKey);
    if (!stageKey) continue;
    const list = substagesByStage.get(stageKey) ?? [];
    list.push({
      ...sub,
      substageDefinitionId,
      stageKey,
      objective: sub.objective ?? null,
      sequence: sub.sequence ?? null,
      sourceSequence: sourceIndex + 1,
    });
    substagesByStage.set(stageKey, list);
  }

  const byKey = new Map(projection.stageProgress.map((s) => [s.stageKey, s]));
  const visibleStageKeys = (CANONICAL_STAGE_ORDER as string[])
    .filter((stageKey: string) => substagesByStage.has(stageKey));

  return visibleStageKeys.map((stageKey: string) => {
    const stageHit: DurableStageProgress | undefined = byKey.get(stageKey);
    const subs = [...(substagesByStage.get(stageKey) ?? [])].sort((left, right) => {
      const leftSequence = typeof left.sequence === "number" ? left.sequence : left.sourceSequence;
      const rightSequence = typeof right.sequence === "number" ? right.sequence : right.sourceSequence;
      return leftSequence - rightSequence || left.substageInstanceId.localeCompare(right.substageInstanceId);
    });
    const baseLabels: string[] = subs.map((sub) => durableSubstageBaseLabel(sub) as string);
    const duplicateCounts = new Map<string, number>();
    for (const label of baseLabels) duplicateCounts.set(label, (duplicateCounts.get(label) ?? 0) + 1);
    const stage = stageKey as TaskStage;
    const stageReason = stageHit?.statusReasonCode
      ?? subs.find((sub) => sub.statusReasonCode)?.statusReasonCode
      ?? null;
    const projectionStatus = rollupDurableStageStatus({
      stageStatus: stageHit?.status ?? (subs[0]?.status ?? "not_started"),
      statusReasonCode: stageReason,
      completedCount: stageHit?.completedCount ?? 0,
      totalCount: stageHit?.totalCount ?? 0,
      substages: subs.map((sub) => ({
        status: sub.status,
        projectionStatus: sub.status,
        statusReasonCode: sub.statusReasonCode ?? null,
        workUnitIds: sub.workUnitIds ?? [],
      })),
    });
    return buildStageRow(stage, {
      status: mapProjectionStatusToNav(projectionStatus),
      projectionStatus,
      statusReasonCode: stageReason,
      completedCount: stageHit?.completedCount,
      totalCount: stageHit?.totalCount,
      planState: "planned",
      artifactRefs: stageHit?.artifactRefs ?? [],
      summary: canonicalStageLabel(stageKey),
      subStages: subs.map((sub, index) => {
        const subStatus = coerceStartedStageStatus({
          status: sub.status,
          projectionStatus: sub.status,
          statusReasonCode: sub.statusReasonCode ?? null,
          workUnitIds: sub.workUnitIds ?? [],
        });
        const substageKey = sub.substageDefinitionId ?? sub.substageInstanceId;
        const baseLabel = baseLabels[index];
        const instanceSequence = index + 1;
        return {
          id: sub.substageInstanceId,
          subStage: substageKey,
          label: durableSubstageDisplayLabel(sub, instanceSequence, duplicateCounts.get(baseLabel) ?? 0),
          instanceSequence,
          objective: sub.objective?.trim() || null,
          status: mapProjectionStatusToNav(subStatus),
          projectionStatus: subStatus,
          statusReasonCode: sub.statusReasonCode ?? null,
          artifactRefs: sub.artifactRefs ?? [],
          workUnitIds: sub.workUnitIds ?? [],
          affected: Boolean(sub.affected),
        };
      }),
    });
  });
}

function failVisibleStages(): WorkbenchStageRow[] {
  // An invalid canonical projection has no trustworthy stage membership.
  // Fail visibly with an empty structure instead of fabricating four stages.
  return [];
}

function legacyStagesFromTask(task: Task, capabilityCalls: WorkbenchCapabilityCall[]): WorkbenchStageRow[] {
  const stageMap = new Map(
    (task.stageDetails ?? [])
      .filter((sd): sd is NonNullable<typeof sd> => Boolean(sd))
      .map((sd) => [sd.stageKey, sd]),
  );
  return workflowStageOrder(task).map((stage) => {
    const hit = stageMap.get(stage);
    if (hit) {
      const subStages = (hit.subStages ?? [])
        .filter((s): s is NonNullable<typeof s> => Boolean(s))
        .map((s) => {
        const row: WorkbenchSubStageRow = {
          id: s.subStageInstanceId ?? s.subStage,
          subStage: s.subStage,
          label: s.label && s.label !== s.subStage ? s.label : labelForSubStage(s.subStage),
          status: mapStageStatusToNav(s.status),
          artifactRefs: s.artifactRefs ?? [],
          affected: Boolean(s.affected),
          l3Plan: s.l3Plan ?? undefined,
        };
        return { ...row, status: resolveSubstageNavStatus(row, capabilityCalls) };
        });
      return buildStageRow(stage, {
        status: resolveStageNavStatus(task, stage, hit.status),
        label: hit.label ?? undefined,
        pagePhaseLabel: hit.label ?? undefined,
        summary: hit.summary,
        risks: hit.risks,
        nextSteps: hit.nextSteps,
        entryCondition: hit.entryCondition,
        completionCondition: hit.completionCondition,
        lockedReason: hit.lockedReason,
        primaryAction: hit.primaryAction,
        canSkip: hit.canSkip,
        canReview: hit.canReview,
        canLock: hit.canLock,
        artifactRefs: hit.artifactRefs ?? [],
        subStages,
      });
    }
    // Legacy without stageDetails: not_started only — never heuristic completed/running.
    return buildStageRow(stage, { status: "not_started" });
  });
}

export function workbenchModelFromApiTask(task: Task): WorkbenchTaskModel {
  const capabilityCalls: WorkbenchCapabilityCall[] = task.capabilityCalls.map((c) => ({
    id: c.id,
    name: resolveCapabilityCallSkillName(c, task),
    status: mapApiCapabilityStatus(c),
    runtime: c.runtime,
    startedAt: c.requestedAt,
    endedAt: c.finishedAt ?? null,
    artifactRefs: c.artifactRefs ?? [],
    stageKey: c.stageKey,
    subStageInstanceId: c.subStageInstanceId,
    inputArtifactRefs: c.inputArtifactRefs ?? [],
    outputArtifactRefs: c.outputArtifactRefs ?? [],
    resultSummary: c.resultSummary ?? null,
    prevCallId: c.prevCallId ?? null,
    inputCallRefs: c.inputCallRefs ?? [],
    runId: c.runId ?? null,
    runMode: c.runMode ?? null,
    sourceMode: c.sourceMode ?? null,
    provider: c.provider ?? null,
    adapter: c.adapter ?? null,
    model: c.model ?? null,
    sourceAccess: c.sourceAccess ?? null,
    sourceMetadata: c.sourceMetadata ?? {},
    callVisibility: (c as { callVisibility?: string | null }).callVisibility ?? null,
    audience: (c as { audience?: string | null }).audience ?? null,
    partialFailureImpact: c.partialFailureImpact ?? null,
    artifactValidatorResults: c.artifactValidatorResults ?? [],
    targetAgent: c.targetAgent ?? null,
    userFacingNarrative: c.userFacingNarrative ?? null,
  }));

  const resolvedTrust = resolveProgressTrust(task as TaskWithProgressProjection);
  const activeDesign = activeDesignInstanceDisplays(task);
  const trust: ProgressProjectionTrust = resolvedTrust.kind === "canonical"
    ? {
        kind: "canonical",
        projection: projectVisibleDurableResearch(
          resolvedTrust.projection,
          activeDesign.instances,
          activeDesign.hasActiveDesign,
        ) as DurableProgressProjection,
      }
    : resolvedTrust;
  const artifacts: WorkbenchArtifact[] = task.artifacts.map((a) => {
    const artifact = mapApiArtifact(task, a);
    return {
      ...artifact,
      sourceMode: artifact.sourceMode ?? artifactSourceModeFromCallOrTask(task, artifact, capabilityCalls),
    };
  });

  let stages: WorkbenchStageRow[];
  let taskStatus: TaskStatus;
  let executionDisplayStatus: string;
  let progressRevision: number | null = null;
  let stageCounts: { completed: number; total: number } | null = null;
  let collaborationNodes: WorkbenchTaskModel["collaborationNodes"] = [];
  let collaborationEvents: WorkbenchTaskModel["collaborationEvents"] = [];
  const lifecycleEvents: WorkbenchTaskModel["lifecycleEvents"] = [];

  if (trust.kind === "canonical") {
    const projection = trust.projection;
    stages = stagesFromDurableProjection(projection);
    taskStatus = mapTaskProgressToTaskStatus(projection.taskProgress.status);
    executionDisplayStatus = durableExecutionDisplayStatus(projection);
    progressRevision = projection.revision;
    stageCounts = projection.stageProgress.reduce(
      (counts, stage) => ({
        completed: counts.completed + stage.completedCount,
        total: counts.total + stage.totalCount,
      }),
      { completed: 0, total: 0 },
    );
    // Live collaborators only from durable workbenchProjection — never plan capability lists or first-skill selection.
    collaborationNodes = projection.workbenchProjection.collaborationNodes ?? [];
    collaborationEvents = projection.workbenchProjection.collaborationEvents ?? [];
  } else if (trust.kind === "invalid") {
    stages = failVisibleStages();
    taskStatus = task.status === "completed" ? "failed" : task.status;
    executionDisplayStatus = "projection_unavailable";
    progressRevision = typeof (task as TaskWithProgressProjection).progressProjection === "object"
      && (task as TaskWithProgressProjection).progressProjection
      && typeof ((task as TaskWithProgressProjection).progressProjection as { revision?: unknown }).revision === "number"
      ? ((task as TaskWithProgressProjection).progressProjection as { revision: number }).revision
      : null;
    stageCounts = null;
    collaborationNodes = [];
    collaborationEvents = [];
  } else {
    // Legacy / flag-off: keep non-orchestration display; do not fabricate live execution.
    stages = withLiteratureSubStages(legacyStagesFromTask(task, capabilityCalls), artifacts);
    taskStatus = task.status;
    executionDisplayStatus = task.status;
    progressRevision = null;
    stageCounts = null;
    collaborationNodes = [];
    collaborationEvents = [];
  }

  const activeFromProjection = trust.kind === "canonical"
    ? trust.projection.taskProgress.activeStageKeys.find((key): key is TaskStage => isUnderlyingStage(key))
    : undefined;
  const delivery = projectWorkbenchDelivery(task);
  const researchProcessHistory = trust.kind === "canonical"
    ? (trust.projection.researchProcessHistory ?? [])
    : [];
  const researchCurrentState: CanonicalResearchCurrentState | null = trust.kind === "canonical"
    ? {
        revision: task.revision ?? trust.projection.revision,
        taskStatus: task.status,
        taskFinishedAt: task.finishedAt ?? null,
        workUnits: (task.workUnits ?? []).map((workUnit) => ({
          workUnitId: workUnit.workUnitId,
          status: workUnit.status,
          startedAt: workUnit.dispatchedAt ?? null,
          finishedAt: workUnit.finishedAt ?? null,
        })),
      }
    : null;

  return {
    taskId: task.taskId,
    ownerId: task.ownerId,
    workspaceId: task.workspaceId,
    revision: task.revision ?? 0,
    title: task.title,
    goalSummary: task.creationPlan?.planningSource === "research_design_v1_2"
      ? (task.creationPlan.goalSummary ?? task.goalSummary ?? "")
      : (task.goalSummary ?? ""),
    domain: task.domain ?? "research_task",
    skillPack: task.skillPack,
    currentUnderlyingStage: activeFromProjection ?? task.currentStage,
    taskStatus,
    executionDisplayStatus,
    interruption: trust.kind === "canonical" ? parseResearchInterruption(
      ((task as TaskWithProgressProjection).progressProjection as { interruption?: unknown } | null)?.interruption,
      task.revision ?? 0,
    ) : null,
    progressTrust: trust,
    progressRevision,
    stageCounts,
    collaborationNodes,
    collaborationEvents,
    lifecycleEvents,
    workUnits: task.workUnits ?? [],
    researchProcessHistory,
    researchCurrentState,
    resultSource: normalizeResultSource(task.resultSource),
    ...delivery,
    executionMode: task.executionMode ?? "stage_confirmation",
    stageGateResumeApproved: task.stageGateResumeApproved ?? false,
    creationStatus: task.creationStatus,
    creationPlan: task.creationPlan ?? null,
    ...projectResearchDesigns(task.researchDesignVersions ?? []),
    researchDesignHistory: [...(task.researchDesignVersions ?? [])]
      .sort((left, right) => right.version - left.version)
      .map((design) => ({
        version: design.version,
        status: design.status,
        createdAt: design.createdAt,
        goal: design.goal,
        instances: [...(design.instances ?? [])]
          .sort((left, right) => (
            (left.sequenceHint ?? Number.MAX_SAFE_INTEGER) - (right.sequenceHint ?? Number.MAX_SAFE_INTEGER)
          ))
          .map((instance) => ({
            instanceId: instance.substageInstanceId,
            stageKey: instance.stageKey,
            objective: instance.objective,
            sequenceHint: instance.sequenceHint ?? null,
          })),
      })),
    createdAt: task.createdAt ?? new Date().toISOString(),
    updatedAt: task.updatedAt ?? task.createdAt ?? new Date().toISOString(),
    stages,
    artifacts,
    capabilityCalls,
    trustRecords: task.trustRecords ?? [],
    userVisibleEvents: task.userVisibleEvents ?? [],
  };
}

export function workbenchModelFromCsResearchFixture(
  rawTask: FixtureTaskJson,
  rawStages: FixtureStageJson[],
  rawArtifacts: FixtureArtifactJson[],
  rawCalls: FixtureCapabilityCallJson[],
  language: UiLanguage = "zh-CN",
): WorkbenchTaskModel {
  const stageMap = new Map<string, FixtureStageJson>();
  for (const row of rawStages) {
    stageMap.set(row.stage, row);
  }

  const stages: WorkbenchStageRow[] = UNDERLYING_STAGE_ORDER.map((stage) => {
    const hit = stageMap.get(stage);
    if (!hit) return buildStageRow(stage);
    const subRows = subStageRowsFromFixture(hit.sub_stages ?? [], stage, language);
    return buildStageRow(stage, {
      status: mapStageStatusToNav(hit.status as StageStatus),
      summary: hit.summary,
      risks: hit.risks,
      nextSteps: hit.next_steps,
      artifactRefs: hit.artifact_refs ?? [],
      subStages: subRows.map((s) => ({
        id: s.id,
        subStage: s.subStage,
        label: s.label,
        status: mapStageStatusToNav(s.status),
        artifactRefs: s.artifactRefs,
      })),
    });
  });

  const artifacts: WorkbenchArtifact[] = rawArtifacts
    .filter((a) => isUnderlyingStage(a.related_stage))
    .map((a) => ({
      id: a.artifact_id,
      type: a.type,
      title: a.title,
      summary: null,
      format: a.format,
      relatedStage: a.related_stage as TaskStage,
      createdAt: rawTask.updated_at,
      path: a.path,
      relatedSkillId: a.related_skill_id,
      sourceCapabilityCallId: null,
      content: fixtureContentForPath(a.path),
      sourceMode: "fixture",
      evidenceRefs: (a.evidence_refs ?? []).map((ref) => ({
        id: ref.id,
        claim: ref.claim,
        sourceTitle: ref.source_title,
        sourceUrl: ref.source_url,
        excerpt: ref.excerpt,
        sourceIdentifier: ref.source_identifier,
        locator: ref.locator,
      })),
      trustRecords: [],
      userFacingNarrative: null,
    }));

  const capabilityCalls: WorkbenchCapabilityCall[] = rawCalls.map((c) => ({
    id: c.call_id,
    name: c.skill_id,
    status: c.status,
    runtime: c.runtime,
    startedAt: c.started_at,
    endedAt: c.ended_at ?? null,
    artifactRefs: c.artifact_refs ?? [],
    stageKey: undefined,
    subStageInstanceId: undefined,
    inputArtifactRefs: [],
    outputArtifactRefs: [],
    resultSummary: c.result_summary ?? null,
    runMode: "fixture",
    sourceMode: "fixture",
    targetAgent: null,
    userFacingNarrative: null,
  }));

  const current = isUnderlyingStage(rawTask.current_stage) ? rawTask.current_stage : "problem_modeling";

  return {
    taskId: rawTask.task_id,
    revision: 0,
    title: rawTask.title,
    goalSummary: rawTask.user_goal,
    domain: rawTask.domain,
    currentUnderlyingStage: current,
    taskStatus: mapFixtureTaskStatus(rawTask.status),
    executionDisplayStatus: mapFixtureTaskStatus(rawTask.status),
    progressTrust: { kind: "legacy" },
    progressRevision: null,
    stageCounts: null,
    collaborationNodes: [],
    collaborationEvents: [],
    lifecycleEvents: [],
    researchProcessHistory: [],
    researchCurrentState: null,
    resultSource: "demo",
    finalResult: null,
    executionMode: "stage_confirmation",
    stageGateResumeApproved: false,
    creationStatus: undefined,
    creationPlan: null,
    researchDesign: null,
    researchDesignHistory: [],
    createdAt: rawTask.created_at,
    updatedAt: rawTask.updated_at,
    stages,
    artifacts,
    capabilityCalls,
    trustRecords: [],
    userVisibleEvents: [],
    workUnits: [],
  };
}

export function taskStatusLabel(status: TaskStatus): string {
  return STATUS_LABELS[status];
}
