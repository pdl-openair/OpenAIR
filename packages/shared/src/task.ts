/**
 * 通用科研任务语义类型（不写死具体学科或工艺场景）。
 * 后端契约以 docs/03_工程/待专项更新/architecture.md 第 7 节为准；本文件为 TS 侧对齐实现。
 */

/** Core SDR research stage keys. Workflows may use all four stages or a defined subset with user-facing labels and guards. */
export type TaskStage =
  | "problem_modeling"
  | "solution_design"
  | "execution_validation"
  | "insight_generation";

export type TaskStatus =
  | "draft"
  | "not_started"
  | "running"
  | "awaiting_confirmation"
  | "completed"
  | "failed"
  | "cancelled";

/** 普通用户看到的结果来源；执行层细节仍保留在 capabilityCalls。 */
export type ResultSource = "live" | "replay" | "demo" | "none";

export type CreationStatus =
  | "clarifying"
  | "planning"
  | "awaiting_confirmation"
  | "revision_requested"
  | "confirmed"
  | "cancelled";

export type TaskExecutionMode =
  | "automatic_research"
  | "stage_confirmation"
  | "manual_step";

/** 阶段级状态（一级 / 二级阶段共用；含可恢复材料缺口状态） */
export type StageStatus =
  | "not_started"
  | "running"
  | "needs_input"
  | "blocked_recoverable"
  | "insufficient_evidence"
  | "completed"
  | "failed";

export type SkillRiskLevel = "low" | "medium" | "high";

export type ArtifactState =
  | "usable"
  | "needs_review"
  | "needs_input"
  | "insufficient_materials"
  | "failed"
  | "partial_success"
  | "rejected_or_needs_revision";

/** 产物版本来源：每次编辑/退回/重生成/恢复都产生一条版本记录。 */
export type ArtifactVersionSource =
  | "initial"
  | "user_edit"
  | "agent_regenerate"
  | "restore"
  | "revision_requested";

/** 产物版本内容快照（`revision_requested` 为纯元数据事件时全部为空）。 */
export interface ArtifactVersion {
  versionId: string;
  versionNumber: number;
  createdAt: string;
  createdBy: string;
  source: ArtifactVersionSource;
  feedback?: string | null;
  /** 直接前驱版本（lineage）。 */
  parentVersionId?: string | null;
  /** 仅 source="restore" 非空：被恢复的源版本。 */
  restoredFromVersionId?: string | null;
  storageType?: "local" | "inline" | "external" | null;
  inlineContent?: string | null;
  path?: string | null;
  externalKey?: string | null;
  contentSha256?: string | null;
  contentSize?: number | null;
}

/** 版本历史列表项（不含正文，避免列表 payload 过大）。 */
export type ArtifactVersionSummary = Omit<ArtifactVersion, "inlineContent">;

export type UserVisibleEventSourceType =
  | "agent_message"
  | "capability_call"
  | "artifact"
  | "confirmation"
  | "stage_summary"
  | "tool_result"
  | "guidance"
  | "raw_event";

export type UserVisibleEventVisibility =
  | "visible"
  | "summary_only"
  | "advanced_only"
  | "developer_only"
  | "hidden";

export type UserVisibleAudience =
  | "ordinary_user"
  | "advanced_user"
  | "developer"
  | "audit";

export type UserFacingNarrativeSeverity =
  | "info"
  | "success"
  | "warning"
  | "blocked"
  | "error";

export interface UserFacingSuggestedAction {
  label: string;
  intent: string;
  primary?: boolean;
}

/**
 * 用户可见叙事：把内部执行事实转成普通科研用户优先阅读的短摘要。
 *
 * 后端 / Agent 应优先填充 `summary`；长 Markdown、工具原文、trace 或技术字段放在
 * `detailMarkdown` / `detailText`，前端默认折叠。该结构只负责呈现，不替代核查 Agent 的事实判断。
 */
export interface UserFacingNarrative {
  summary: string;
  detailMarkdown?: string | null;
  detailText?: string | null;
  detailCollapsedByDefault?: boolean;
  sourceType?: UserVisibleEventSourceType;
  sourceId?: string | null;
  severity?: UserFacingNarrativeSeverity;
  suggestedActions?: UserFacingSuggestedAction[];
}

/** 内部事件经过可见性闸门后的用户可见事件。 */
export interface UserVisibleEvent {
  id: string;
  taskId?: string;
  sourceType: UserVisibleEventSourceType;
  sourceId?: string | null;
  visibility: UserVisibleEventVisibility;
  audience?: UserVisibleAudience;
  narrative: UserFacingNarrative;
  createdAt?: string;
  relatedArtifactRefs?: string[];
  relatedCallId?: string | null;
  redactedFields?: string[];
  confidence?: number;
}

/** 阶段内允许挂载的通用二级节点目录项 */
export interface SubStageCatalogEntry {
  key: string;
  stageKey: TaskStage;
  label: string;
  description?: string;
  action?: string;
  expectedArtifact?: string;
  enabled?: boolean;
}

/** 一级阶段允许的二级节点目录 */
export interface StageCatalog {
  stageKey: TaskStage;
  allowedSubStages: SubStageCatalogEntry[];
}

/**
 * 二级阶段运行时实例：一个一级阶段（TaskStage）下被实际选择并执行的细分步骤。
 * `subStage` 指向目录 key；额外实例字段用于表达同类节点多次执行、分支或重试。
 */
export interface SubStage {
  subStage: string;
  status: StageStatus;
  artifactRefs: string[];
  subStageInstanceId?: string;
  parentStageKey?: TaskStage;
  label?: string;
  action?: string;
  expectedArtifact?: string;
  capabilityCallRefs?: string[];
  summary?: string | null;
  sequence?: number;
  startedAt?: string;
  finishedAt?: string;
  /** Server-derived downstream impact; does not change execution status. */
  affected?: boolean;
  l3Plan?: {
    status: string;
    steps: Array<{
      skill_id: string;
      display_name: string;
      purpose: string;
      status: string;
      result?: string | null;
    }>;
  } | null;
}

/**
 * 一级阶段详情：用于任务详情页「阶段信息」卡片与主工作台左侧二级阶段进度。
 * 与 fixture `stages.json` 单项一一对应（snake_case ↔ camelCase）。
 */
export interface StageDetail {
  stageKey: TaskStage;
  label?: string | null;
  status: StageStatus;
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
  subStages: SubStage[];
  userFacingNarrative?: UserFacingNarrative | null;
}

/** 产物中的结构化证据回溯项：结论 → 外部来源 → 原文片段。 */
export interface ArtifactEvidenceRef {
  id: string;
  claim: string;
  sourceTitle: string;
  sourceUrl: string;
  excerpt: string;
  sourceIdentifier?: string;
  locator?: string;
}

export type MaterialSourceType =
  | "user_upload"
  | "extracted"
  | "retrieved_source"
  | "evidence_row"
  | "source_fragment"
  | "artifact"
  | "external_reference";

export interface MaterialSource {
  type: MaterialSourceType;
  locator?: string | null;
  uri?: string | null;
  artifactId?: string | null;
  sourceTaskId?: string | null;
}

export interface MaterialQuality {
  readable: boolean;
  sourceBounded: boolean;
  missingFields: string[];
  warnings?: string[];
}

export interface MaterialProvenance {
  createdBy: string;
  createdAt: string;
  ingestionMode?: string | null;
}

export interface MaterialManifest {
  materialId: string;
  taskId: string;
  kind: string;
  source: MaterialSource;
  contentPreview?: string | null;
  structuredContent?: Record<string, unknown>;
  quality: MaterialQuality;
  provenance: MaterialProvenance;
  linkedArtifactIds?: string[];
}

export type MemoryScope = "durable" | "project" | "task" | "run" | "scratchpad";

export type MemoryKind =
  | "user_fact"
  | "agent_inference"
  | "decision"
  | "failed_attempt"
  | "open_question"
  | "artifact_summary";

export type MemoryConfidence = "low" | "medium" | "high" | "unknown";

export interface MemoryRecord {
  memoryId: string;
  taskId: string;
  scope: MemoryScope;
  kind: MemoryKind;
  content: string;
  linkedMaterials: string[];
  linkedArtifacts: string[];
  confidence: MemoryConfidence;
  createdBy: string;
  createdAt: string;
  validUntil?: string | null;
}

export type ToolResourceExecutionStatus =
  | "invoked_success"
  | "invoked_failed"
  | "waiting_authorization"
  | "manual_required"
  | "input_recorded"
  | "not_connected"
  | "reserved_internal";

export type ToolResourceTrustContribution =
  | "evidence"
  | "lead"
  | "needs_confirmation"
  | "not_citable"
  | "not_used";

export type ToolResourceCategory =
  | "literature"
  | "data"
  | "code"
  | "computation"
  | "experiment"
  | "human_review"
  | "external_system"
  | "user_input";

export interface ToolResourceTrustRecord {
  id: string;
  name: string;
  category: ToolResourceCategory;
  executionStatus: ToolResourceExecutionStatus;
  trustContribution: ToolResourceTrustContribution;
  usedFor: string;
  limitation: string;
  nextAction: string;
  timestamp?: string;
  relatedArtifactIds?: string[];
  sourceLabel: string;
}

export interface ArtifactValidatorIssue {
  code: string;
  severity: string;
  message: string;
}

export interface ArtifactValidatorResult {
  contractVersion: "artifact-evaluator.v1" | string;
  evaluator: string;
  artifactId?: string | null;
  sourceCapabilityCallId?: string | null;
  artifactState: ArtifactState;
  usable: boolean;
  missingInputs: string[];
  issues: ArtifactValidatorIssue[];
  nextAction: string;
}

export interface ResultProtocol {
  version: "result-protocol.v1";
  status: "ok" | "degraded" | "fatal" | "no_usable_result";
  accepted_items: Array<Record<string, unknown>>;
  quarantined_items: Array<Record<string, unknown>>;
  repaired_items: Array<Record<string, unknown>>;
  warnings: string[];
  coverage: Record<string, unknown>;
}

export interface ArtifactHumanGate {
  canConfirmPass: boolean;
  canUseDownstream: boolean;
  requiresHumanReview: boolean;
  requiresScopeExpansionConfirmation: boolean;
  allowedActions: string[];
  primaryAction: string;
  blockedReason?: string | null;
}

export interface ResearchStartConfirmationStatus {
  status: "unconfirmed" | "confirmed_with_limitations" | "blocked_for_supplement" | "revision_requested" | "paused" | string;
  confirmed_items: string[];
  missing_materials_decision?: "supplement" | "accept_limitations" | "modify_goal" | "pause" | null | string;
  limitations_accepted: boolean;
  confirmed_at?: string | null;
  accepted_limitations?: string[];
}

export interface StructuredResearchStart {
  research_goal: string;
  comparison_targets: string[];
  existing_materials: string[];
  missing_materials: string[];
  non_citable_boundaries: string[];
  recommended_next_actions: string[];
  risks: string[];
  user_confirmation_status: ResearchStartConfirmationStatus;
  accepted_limitations: string[];
  source_or_run_refs: Array<Record<string, unknown>>;
}

/** 过程产物（泛化） */
export interface Artifact {
  id: string;
  taskId?: string;
  sourceTaskId?: string;
  ownerId: string;
  workspaceId: string;
  createdBy: string;
  kind: string;
  title: string;
  summary?: string;
  createdAt: string;
  /** fixture `path`（Markdown 等相对路径） */
  path?: string;
  format?: string;
  relatedStage?: TaskStage;
  stage?: TaskStage;
  relatedSkillId?: string;
  subStageInstanceId?: string;
  substage?: string;
  sourceCapabilityCallId?: string;
  artifactState?: ArtifactState | null;
  artifactType?: string | null;
  /** Phase-1: "local" | "inline" | "external" (stub) */
  storageType?: "local" | "inline" | "external";
  /** 当 storageType="inline" 时，内容直接内联在 metadata 中 */
  inlineContent?: string;
  /** 当 storageType="external" 时，MinIO 对象 key */
  externalKey?: string;
  evidenceRefs?: ArtifactEvidenceRef[];
  linkedMaterialIds?: string[];
  materialManifest?: MaterialManifest[];
  memoryRefs?: string[];
  trustRecords?: ToolResourceTrustRecord[];
  validatorResult?: ArtifactValidatorResult | null;
  resultProtocol?: ResultProtocol | null;
  artifactQuality?: "standard" | "degraded" | null;
  warnings?: string[];
  humanGate?: ArtifactHumanGate | null;
  structuredResearchStart?: StructuredResearchStart | null;
  researchStartConfirmation?: ResearchStartConfirmationStatus | null;
  missingInputs?: string[];
  nonCitableBoundaries?: string[];
  downstreamInput?: Record<string, unknown> | null;
  /** 产物来源；缺省视为 "generated"（向后兼容） */
  origin?: "generated" | "imported" | "external" | "user_upload" | string;
  /** reference 模式：外部存储提供方（minio/s3/huggingface/url/...） */
  externalProvider?: string;
  /** reference 模式：外部产物指针 URI */
  externalUri?: string;
  /** 内容 sha256（完整性/去重） */
  contentSha256?: string;
  /** 内容字节大小 */
  contentSize?: number;
  /** 软删除/归档时间 */
  archivedAt?: string;
  /** 来源链关系（产物详情视图用；对齐 Artifact-03） */
  relations?: ArtifactRelation[];
  /** 当前生效版本 id（始终指向某个 content 版本；缺省视为仅有初始版本）。 */
  currentVersionId?: string;
  /** 版本历史（append-only；首次编辑/恢复/重生成后落库，既有产物懒加载为 v1 initial）。 */
  versions?: ArtifactVersion[];
  /** 退回修改时间（regenerate/request-revision 写入；重生成完成后清除）。 */
  revisionRequestedAt?: string | null;
  /** 最近一次退回反馈。 */
  revisionFeedback?: string | null;
  userFacingNarrative?: UserFacingNarrative | null;
}

/** 产物来源链关系（Artifact-03）。 */
export interface ArtifactRelation {
  relationType: "derived_from" | "evidence_for" | "supersedes" | "reuses" | "part_of" | "cites" | string;
  /** 相对当前产物：incoming=被指向（别人 derived_from 我），outgoing=指向他（我 derived_from 别人） */
  direction: "incoming" | "outgoing";
  /** 对端产物 id */
  artifactId: string;
  title?: string;
  kind?: string;
  capabilityCallId?: string;
  createdAt?: string;
}

/** 人机确认记录 */
export interface Confirmation {
  id: string;
  stage: TaskStage;
  decision: "approved" | "rejected" | "needs_revision";
  note?: string;
  createdAt: string;
}

/** 能力调用：台内 Agent、仿真、外部协同等统一形状 */
export type CapabilityKind = "in_app_agent" | "simulation" | "external_platform" | "other";

export type CapabilityCallStatus =
  | "not_started"
  | "running"
  | "needs_input"
  | "blocked_recoverable"
  | "insufficient_evidence"
  | "completed"
  | "failed";

export interface CapabilityCall {
  id: string;
  ownerId: string;
  workspaceId: string;
  createdBy: string;
  kind: CapabilityKind;
  name: string;
  status: CapabilityCallStatus;
  requestedAt: string;
  finishedAt?: string;
  resultSummary?: string;
  runtime?: string;
  artifactRefs?: string[];
  stageKey?: TaskStage;
  subStageInstanceId?: string;
  inputArtifactRefs?: string[];
  outputArtifactRefs?: string[];
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
  callVisibility?: string | null;
  audience?: string | null;
  partialFailureImpact?: string | null;
  artifactValidatorResults?: ArtifactValidatorResult[];
  targetAgent?: string | null;
  userFacingNarrative?: UserFacingNarrative | null;
}

export interface WorkflowSubStage {
  instanceId?: string | null;
  substage: string;
  label?: string;
  action?: string;
  expectedArtifact?: string;
}

export interface TaskPlanStage {
  stageKey: TaskStage;
  title?: string;
  description?: string;
  subStages: string[];
  substages?: WorkflowSubStage[];
}

export interface TaskPlan {
  goalSummary: string;
  domain: string;
  skillPack?: string;
  researchType?: string;
  planningSource?: string;
  understanding?: {
    taskType?: string;
    userIntent?: string;
    materialSummary?: string;
    primaryResearchQuestions?: string[];
    auxiliaryResearchDimensions?: string[];
    targetOutputs?: string[];
    evidenceBoundaries?: string[];
    missingInputs?: string[];
    riskNotes?: string[];
    planningRationale?: string;
  } | null;
  clarifications: string[];
  stages: TaskPlanStage[];
  readyToConfirm: boolean;
}

export interface TaskPlanUpdateSubstage {
  instanceId?: string | null;
  substage: string;
  label: string;
  action: string;
  expectedArtifact: string;
}

export interface TaskPlanUpdate {
  expectedRevision: number;
  goalSummary: string;
  understanding: { evidenceBoundaries: string[] };
  clarifications: string[];
  stages: Array<{
    stageKey: TaskStage;
    title: string;
    description: string;
    substages: TaskPlanUpdateSubstage[];
  }>;
}

export interface FinalResultSourceArtifactSnapshot {
  artifactId: string;
  artifactType: string;
  version: number;
  contentSha256?: string | null;
}

export interface FinalResultCurrentArtifact {
  artifactId: string;
  artifactType: string;
  version: number;
  contentSha256?: string | null;
  artifactState: ArtifactState;
}

export interface FinalResultAuthority {
  mode: "assembled_artifact" | "artifact_set";
  researchDesignVersion: number;
  resultArtifactRefs: string[];
  sourceArtifacts: FinalResultSourceArtifactSnapshot[];
  assembledAt: string;
  status: "current" | "needs_review" | "stale";
  currentResultArtifacts: FinalResultCurrentArtifact[];
  finalResultId?: string | null;
  integrityContractVersion?:
    | "legacy_v0"
    | "scientific_completion_integrity_v1"
    | "scientific_completion_integrity_v2"
    | "scientific_completion_integrity_v3";
  integrityStatus?: "validated" | "legacy_unvalidated";
}

export interface FinalResultValidationOutcome {
  contractVersion:
    | "scientific_completion_integrity_v1"
    | "scientific_completion_integrity_v2"
    | "scientific_completion_integrity_v3";
  validatorRef:
    | "core:scientific_completion_integrity:v1"
    | "core:scientific_completion_integrity:v2"
    | "core:scientific_completion_integrity:v3";
  status: "passed" | "failed" | "unknown";
  checkIds: string[];
  validatedAt: string;
}

export interface FinalResult {
  finalResultId: string;
  completionDecisionId: string;
  scientificStateVersion: number;
  disposition: "full" | "partial" | "degraded" | "evidence_gap";
  /** Historical v1/v2 presentation field. Canonical v3 intentionally leaves it null. */
  directAnswer: string | null;
  /** Historical v1/v2 presentation field. Canonical v3 intentionally leaves it empty. */
  keyFindings: string[];
  /** Logical Evidence refs from historical contracts; never aliases Artifact provenance. */
  evidenceRefs: string[];
  /** Canonical v3: exact accepted ScientificState.scientificFacts snapshot. */
  scientificResult: Record<string, unknown>;
  /** Canonical v3: provenance refs copied from the accepted ScientificState. */
  scientificSourceRefs: string[];
  uncertainty: string[];
  gaps: string[];
  limitations: string[];
  /** Historical v2 read-compatibility snapshot. Empty for canonical v3. */
  acceptedScientificFacts: Record<string, unknown>;
  validatedAt: string;
  validationOutcome?: FinalResultValidationOutcome | null;
}

/** Bounded public WorkUnit snapshot returned by Task GET. */
export interface CanonicalTaskWorkUnit {
  workUnitId: string;
  executionDecisionId: string;
  subStageInstanceId: string;
  status:
    | "queued"
    | "running"
    | "reviewing"
    | "completed"
    | "failed"
    | "blocked"
    | "cancelled"
    | "external_result_unknown";
  dispatchedAt: string;
  finishedAt?: string | null;
  outputArtifactRefs?: string[];
}

export interface PublicCompletionDecision {
  completionDecisionId: string;
  scientificStateVersion: number;
  goalAchieved: boolean | null;
  roundEndReason: "goal_achieved" | "confirmed_scope_settled" | null;
  disposition: "full" | "partial" | "degraded" | "evidence_gap";
  unmetRequirementIds: string[];
  unresolvedRequirementIds: string[];
}
export interface Task {
  taskId: string;
  ownerId: string;
  workspaceId: string;
  createdBy: string;
  agentSessionId?: string;
  title: string;
  revision?: number;
  finalResultAuthority?: FinalResultAuthority | null;
  finalResult?: FinalResult | null;
  completionDecision?: PublicCompletionDecision | null;
  goalSummary?: string;
  domain?: string;
  skillPack?: string;
  currentStage: TaskStage;
  status: TaskStatus;
  resultSource?: ResultSource;
  executionMode?: TaskExecutionMode;
  stageGateResumeApproved?: boolean;
  creationStatus?: CreationStatus;
  creationPlan?: TaskPlan | null;
  activeResearchDesignRef?: import("./researchOrchestration").ResearchDesignRef | null;
  pendingResearchDesignRef?: import("./researchOrchestration").ResearchDesignRef | null;
  researchDesignVersions?: import("./researchOrchestration").ResearchDesignVersion[];
  createdAt?: string;
  updatedAt?: string;
  startedAt?: string | null;
  finishedAt?: string | null;
  stages: TaskStage[];
  stageCatalog?: StageCatalog[];
  stageDetails?: StageDetail[];
  artifacts: Artifact[];
  /** Catalog-sourced visible artifact summary (artifact library = source of
   * truth). Populated on list views so cloned/reused tasks display correctly
   * even when the payload artifact mirror is empty. Null when not provided. */
  catalogArtifacts?: {
    count: number;
    items: Array<{ artifactId: string; title?: string | null }>;
  } | null;
  materials?: MaterialManifest[];
  memoryRecords?: MemoryRecord[];
  confirmations: Confirmation[];
  capabilityCalls: CapabilityCall[];
  workUnits?: CanonicalTaskWorkUnit[];
  trustRecords?: ToolResourceTrustRecord[];
  userVisibleEvents?: UserVisibleEvent[];
  researchType?: string;
}
