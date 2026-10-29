"""Pydantic models aligned with packages/shared/src/task.ts (API camelCase)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.research_orchestration import ResearchDesignRef, ResearchDesignVersion
from app.models.research_public_delivery import PublicResearchDeliveryFields

TaskStage = Literal[
    "problem_modeling",
    "solution_design",
    "execution_validation",
    "insight_generation",
]

TaskStatus = Literal[
    "draft",
    "not_started",
    "running",
    "awaiting_confirmation",
    "completed",
    "failed",
    "cancelled",
]

ResultSource = Literal["live", "replay", "demo", "none"]

CreationStatus = Literal[
    "clarifying",
    "planning",
    "awaiting_confirmation",
    "revision_requested",
    "confirmed",
    "cancelled",
]

TaskExecutionMode = Literal["automatic_research", "stage_confirmation", "manual_step"]

StageStatus = Literal[
    "not_started",
    "running",
    "needs_input",
    "blocked_recoverable",
    "insufficient_evidence",
    "completed",
    "failed",
]

SkillRiskLevel = Literal["low", "medium", "high"]

ArtifactState = Literal[
    "usable",
    "needs_review",
    "needs_input",
    "insufficient_materials",
    "failed",
    "partial_success",
    "rejected_or_needs_revision",
]
ArtifactReviewDecision = Literal["approved", "rejected", "needs_revision"]
MaterialSourceType = Literal[
    "user_upload",
    "extracted",
    "retrieved_source",
    "evidence_row",
    "source_fragment",
    "artifact",
    "external_reference",
]
MemoryScope = Literal["durable", "project", "task", "run", "scratchpad"]
MemoryKind = Literal[
    "user_fact",
    "agent_inference",
    "decision",
    "failed_attempt",
    "open_question",
    "artifact_summary",
]
MemoryConfidence = Literal["low", "medium", "high", "unknown"]

ArtifactVersionSource = Literal[
    "initial",
    "user_edit",
    "agent_regenerate",
    "restore",
    "revision_requested",
]

CapabilityKind = Literal["in_app_agent", "simulation", "external_platform", "other"]

CapabilityCallStatus = Literal[
    "not_started",
    "running",
    "needs_input",
    "blocked_recoverable",
    "insufficient_evidence",
    "completed",
    "failed",
]

ConfirmationDecision = Literal["approved", "rejected", "needs_revision"]

ToolResourceExecutionStatus = Literal[
    "invoked_success",
    "invoked_failed",
    "waiting_authorization",
    "manual_required",
    "input_recorded",
    "not_connected",
    "reserved_internal",
]

ToolResourceTrustContribution = Literal[
    "evidence",
    "lead",
    "needs_confirmation",
    "not_citable",
    "not_used",
]

UserVisibleEventSourceType = Literal[
    "assistant_message",
    "capability_call",
    "artifact",
    "confirmation",
    "stage",
    "guidance",
    "raw_event",
]

UserVisibleEventVisibility = Literal[
    "visible",
    "summary_only",
    "advanced_only",
    "developer_only",
    "hidden",
]

UserVisibleAudience = Literal[
    "ordinary_user",
    "advanced_user",
    "developer",
    "audit",
]

UserFacingNarrativeSeverity = Literal["info", "success", "warning", "blocked", "error"]


class UserFacingSuggestedActionOut(BaseModel):
    label: str
    intent: str
    primary: bool | None = None


class UserFacingNarrativeOut(BaseModel):
    summary: str
    detailMarkdown: str | None = None
    detailText: str | None = None
    detailCollapsedByDefault: bool | None = True
    sourceType: UserVisibleEventSourceType | None = None
    sourceId: str | None = None
    severity: UserFacingNarrativeSeverity | None = None
    suggestedActions: list[UserFacingSuggestedActionOut] = Field(default_factory=list)


class UserVisibleEventOut(BaseModel):
    id: str
    taskId: str | None = None
    sourceType: UserVisibleEventSourceType
    sourceId: str | None = None
    visibility: UserVisibleEventVisibility
    audience: UserVisibleAudience | None = None
    narrative: UserFacingNarrativeOut
    createdAt: str | None = None
    relatedArtifactRefs: list[str] = Field(default_factory=list)
    relatedCallId: str | None = None
    redactedFields: list[str] = Field(default_factory=list)
    confidence: float | None = None

ToolResourceCategory = Literal[
    "literature",
    "data",
    "code",
    "computation",
    "experiment",
    "human_review",
    "external_system",
    "user_input",
]

STAGE_ORDER: list[TaskStage] = [
    "problem_modeling",
    "solution_design",
    "execution_validation",
    "insight_generation",
]


class SubStageCatalogEntryOut(BaseModel):
    key: str
    stageKey: TaskStage
    label: str
    description: str | None = None
    action: str | None = None
    expectedArtifact: str | None = None
    enabled: bool | None = None


class StageCatalogOut(BaseModel):
    stageKey: TaskStage
    allowedSubStages: list[SubStageCatalogEntryOut] = Field(default_factory=list)


class SubStageOut(BaseModel):
    subStage: str
    status: StageStatus
    artifactRefs: list[str] = Field(default_factory=list)
    subStageInstanceId: str | None = None
    parentStageKey: TaskStage | None = None
    label: str | None = None
    action: str | None = None
    expectedArtifact: str | None = None
    capabilityCallRefs: list[str] = Field(default_factory=list)
    summary: str | None = None
    sequence: int | None = None
    startedAt: str | None = None
    finishedAt: str | None = None
    l3Plan: dict | None = None
    affected: bool = False


class StageDetailOut(BaseModel):
    stageKey: TaskStage
    label: str | None = None
    status: StageStatus
    summary: str | None = None
    risks: str | None = None
    nextSteps: str | None = None
    entryCondition: str | None = None
    completionCondition: str | None = None
    lockedReason: str | None = None
    primaryAction: str | None = None
    canSkip: bool | None = None
    canReview: bool | None = None
    canLock: bool | None = None
    artifactRefs: list[str] = Field(default_factory=list)
    subStages: list[SubStageOut] = Field(default_factory=list)
    userFacingNarrative: UserFacingNarrativeOut | None = None


class ArtifactEvidenceRefOut(BaseModel):
    id: str
    claim: str
    sourceTitle: str
    sourceUrl: str
    excerpt: str
    sourceIdentifier: str | None = None
    locator: str | None = None


class MaterialSourceOut(BaseModel):
    type: MaterialSourceType
    locator: str | None = None
    uri: str | None = None
    artifactId: str | None = None
    sourceTaskId: str | None = None


class MaterialQualityOut(BaseModel):
    readable: bool = True
    sourceBounded: bool = True
    missingFields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MaterialProvenanceOut(BaseModel):
    createdBy: str
    createdAt: str
    ingestionMode: str | None = None


class MaterialManifestOut(BaseModel):
    materialId: str
    taskId: str
    kind: str
    source: MaterialSourceOut
    contentPreview: str | None = None
    structuredContent: dict[str, Any] = Field(default_factory=dict)
    quality: MaterialQualityOut = Field(default_factory=MaterialQualityOut)
    provenance: MaterialProvenanceOut
    linkedArtifactIds: list[str] = Field(default_factory=list)


class MemoryRecordOut(BaseModel):
    memoryId: str
    taskId: str
    scope: MemoryScope = "task"
    kind: MemoryKind
    content: str
    linkedMaterials: list[str] = Field(default_factory=list)
    linkedArtifacts: list[str] = Field(default_factory=list)
    confidence: MemoryConfidence = "unknown"
    createdBy: str
    createdAt: str
    validUntil: str | None = None


class ToolResourceTrustRecordOut(BaseModel):
    id: str
    name: str
    category: ToolResourceCategory
    executionStatus: ToolResourceExecutionStatus
    trustContribution: ToolResourceTrustContribution
    usedFor: str
    limitation: str
    nextAction: str
    timestamp: str | None = None
    relatedArtifactIds: list[str] = Field(default_factory=list)
    sourceLabel: str


class ArtifactValidatorIssueOut(BaseModel):
    code: str
    severity: str
    message: str


class ArtifactValidatorResultOut(BaseModel):
    contractVersion: str = "artifact-evaluator.v1"
    evaluator: str
    artifactId: str | None = None
    sourceCapabilityCallId: str | None = None
    artifactState: ArtifactState
    usable: bool
    missingInputs: list[str] = Field(default_factory=list)
    issues: list[ArtifactValidatorIssueOut] = Field(default_factory=list)
    nextAction: str


class ResultProtocolOut(BaseModel):
    """Public, bounded projection of the code-owned ResultProtocolV1."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["result-protocol.v1"] = "result-protocol.v1"
    status: Literal["ok", "degraded", "fatal", "no_usable_result"]
    accepted_items: list[dict] = Field(default_factory=list)
    quarantined_items: list[dict] = Field(default_factory=list)
    repaired_items: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    coverage: dict = Field(default_factory=dict)


class ArtifactHumanGateOut(BaseModel):
    canConfirmPass: bool
    canUseDownstream: bool
    requiresHumanReview: bool
    requiresScopeExpansionConfirmation: bool
    allowedActions: list[str] = Field(default_factory=list)
    primaryAction: str
    blockedReason: str | None = None


class ResearchStartConfirmationStatusOut(BaseModel):
    status: str
    confirmed_items: list[str] = Field(default_factory=list)
    missing_materials_decision: str | None = None
    limitations_accepted: bool = False
    confirmed_at: str | None = None
    accepted_limitations: list[str] = Field(default_factory=list)


class StructuredResearchStartOut(BaseModel):
    research_goal: str
    comparison_targets: list[str] = Field(default_factory=list)
    existing_materials: list[str] = Field(default_factory=list)
    missing_materials: list[str] = Field(default_factory=list)
    non_citable_boundaries: list[str] = Field(default_factory=list)
    recommended_next_actions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    user_confirmation_status: ResearchStartConfirmationStatusOut
    accepted_limitations: list[str] = Field(default_factory=list)
    source_or_run_refs: list[dict] = Field(default_factory=list)


class ArtifactVersionOut(BaseModel):
    """Artifact version metadata. Content is only exposed by the canonical content route."""

    versionId: str
    versionNumber: int
    createdAt: str
    createdBy: str
    source: ArtifactVersionSource
    feedback: str | None = None
    parentVersionId: str | None = None
    restoredFromVersionId: str | None = None
    storageType: str | None = None
    path: str | None = None
    externalKey: str | None = None
    contentSha256: str | None = None
    contentSize: int | None = None


class ArtifactVersionSummaryOut(BaseModel):
    """版本历史列表项（不含 inlineContent，减小 payload）。"""

    versionId: str
    versionNumber: int
    createdAt: str
    createdBy: str
    source: ArtifactVersionSource
    feedback: str | None = None
    parentVersionId: str | None = None
    restoredFromVersionId: str | None = None
    storageType: str | None = None
    path: str | None = None
    externalKey: str | None = None
    contentSha256: str | None = None
    contentSize: int | None = None


class ArtifactVersionsOut(BaseModel):
    artifactId: str
    revision: int | None = None
    currentVersionId: str | None = None
    versions: list[ArtifactVersionSummaryOut] = Field(default_factory=list)


class CanonicalArtifactValidationOut(BaseModel):
    """Safe, field-bounded diagnostics from a canonical v1.2 Artifact."""

    model_config = ConfigDict(extra="forbid")

    status: str | None = None
    artifactContract: str | None = None
    artifactSchemaDigest: str | None = None
    contentSha256: str | None = None
    errorCode: str | None = None
    fieldPaths: list[str] = Field(default_factory=list)


class ArtifactOut(BaseModel):
    id: str
    taskId: str | None = None
    sourceTaskId: str | None = None
    ownerId: str
    workspaceId: str
    createdBy: str
    kind: str
    title: str
    summary: str | None = None
    createdAt: str
    path: str | None = None
    format: str | None = None
    relatedStage: TaskStage | None = None
    stage: TaskStage | None = None
    relatedSkillId: str | None = None
    subStageInstanceId: str | None = None
    substage: str | None = None
    sourceCapabilityCallId: str | None = None
    workUnitId: str | None = None
    sourceRefs: list[str] = Field(default_factory=list)
    lineageKind: Literal["direct_work_unit_output", "capability_call_output"] | None = None
    canonicalVersion: int | None = Field(default=None, ge=1)
    artifactState: ArtifactState | None = None
    artifactType: str | None = None
    artifactVisibility: str | None = None
    audience: str | None = None
    storageType: str | None = "local"
    evidenceRefs: list[ArtifactEvidenceRefOut] = Field(default_factory=list)
    linkedMaterialIds: list[str] = Field(default_factory=list)
    materialManifest: list[MaterialManifestOut] = Field(default_factory=list)
    memoryRefs: list[str] = Field(default_factory=list)
    trustRecords: list[ToolResourceTrustRecordOut] = Field(default_factory=list)
    validatorResult: ArtifactValidatorResultOut | None = None
    resultProtocol: ResultProtocolOut | None = None
    artifactQuality: Literal["standard", "degraded"] | None = None
    warnings: list[str] = Field(default_factory=list)
    canonicalValidation: CanonicalArtifactValidationOut | None = None
    humanGate: ArtifactHumanGateOut | None = None
    structuredResearchStart: StructuredResearchStartOut | None = None
    researchStartConfirmation: ResearchStartConfirmationStatusOut | None = None
    missingInputs: list[str] = Field(default_factory=list)
    nonCitableBoundaries: list[str] = Field(default_factory=list)
    downstreamInput: dict | None = None
    origin: str | None = None
    externalProvider: str | None = None
    externalUri: str | None = None
    contentSha256: str | None = None
    contentSize: int | None = None
    archivedAt: str | None = None
    currentVersionId: str | None = None
    versions: list[ArtifactVersionSummaryOut] = Field(default_factory=list)
    revisionRequestedAt: str | None = None
    revisionFeedback: str | None = None
    reusedFrom: dict[str, str] | None = None
    userFacingNarrative: UserFacingNarrativeOut | None = None


class EditArtifactContentIn(BaseModel):
    content: str
    changeReason: str | None = None
    # NOTE: no `editor` field — the version author is always derived from the
    # authenticated identity in the route, never trusted from the request body
    # (otherwise a client could forge the modifier).


class RegenerateArtifactIn(BaseModel):
    feedback: str | None = None
    skillId: str | None = None


class ArtifactContentOut(BaseModel):
    artifactId: str
    path: str
    format: str | None = None
    sourceMode: Literal["live", "replay", "external", "inline", "local", "object"]
    content: str
    displayProjection: dict[str, Any] | None = None
    evidenceRefs: list[ArtifactEvidenceRefOut] = Field(default_factory=list)
    linkedMaterialIds: list[str] = Field(default_factory=list)


class CatalogArtifactOut(BaseModel):
    """产物库目录行：sourceTaskId / sourceTaskTitle 为读取时由所属 task 注入的计算字段。"""

    id: str
    taskId: str | None = None
    ownerId: str
    workspaceId: str
    createdBy: str
    kind: str
    title: str
    summary: str | None = None
    createdAt: str
    format: str | None = None
    relatedStage: TaskStage | None = None
    stage: TaskStage | None = None
    relatedSkillId: str | None = None
    subStageInstanceId: str | None = None
    substage: str | None = None
    sourceCapabilityCallId: str | None = None
    artifactState: ArtifactState | None = None
    artifactType: str | None = None
    artifactVisibility: str | None = None
    audience: str | None = None
    storageType: str | None = "local"
    origin: str | None = None
    externalProvider: str | None = None
    externalUri: str | None = None
    contentSize: int | None = None
    reusedFrom: dict[str, str] | None = None
    sourceTaskId: str | None = None
    sourceTaskTitle: str | None = None
    linkedMaterialIds: list[str] = Field(default_factory=list)
    memoryRefs: list[str] = Field(default_factory=list)
    validatorResult: ArtifactValidatorResultOut | None = None
    resultProtocol: ResultProtocolOut | None = None
    artifactQuality: Literal["standard", "degraded"] | None = None
    warnings: list[str] = Field(default_factory=list)
    humanGate: ArtifactHumanGateOut | None = None
    userFacingNarrative: UserFacingNarrativeOut | None = None


class ArtifactListOut(BaseModel):
    artifacts: list[CatalogArtifactOut]
    total: int
    page: int
    pageSize: int


class ArtifactDetailOut(CatalogArtifactOut):
    path: str | None = None
    externalKey: str | None = None
    contentSha256: str | None = None
    archivedAt: str | None = None
    evidenceRefs: list[ArtifactEvidenceRefOut] = Field(default_factory=list)
    materialManifest: list[MaterialManifestOut] = Field(default_factory=list)
    validatorResult: ArtifactValidatorResultOut | None = None
    humanGate: ArtifactHumanGateOut | None = None
    currentVersionId: str | None = None
    versions: list[ArtifactVersionSummaryOut] = Field(default_factory=list)
    revisionRequestedAt: str | None = None
    revisionFeedback: str | None = None


class ReuseArtifactIn(BaseModel):
    targetTaskId: str
    title: str | None = None
    summary: str | None = None


class ReuseArtifactOut(BaseModel):
    artifact: ArtifactOut
    sourceArtifactId: str
    sourceTaskId: str


class ReassignArtifactIn(BaseModel):
    targetTaskId: str
    currentTaskId: str | None = None
    ownershipConfirmed: bool = False
    note: str | None = None


class UpdateArtifactMaterialsIn(BaseModel):
    linkedMaterialIds: list[str] = Field(default_factory=list)
    note: str | None = None


class ArtifactOwnershipConfirmationOut(BaseModel):
    code: Literal["ownership_confirmation_required"] = "ownership_confirmation_required"
    reason: str
    sourceTaskId: str | None = None
    sourceTaskTitle: str | None = None
    sourceDomain: str | None = None
    targetTaskId: str
    targetTaskTitle: str | None = None
    targetDomain: str | None = None


class ReviewArtifactIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ArtifactReviewDecision
    note: str | None = None


class ConfirmScopeExpansionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = None


class ArtifactRelationOut(BaseModel):
    """来源链关系：相对查询产物，direction 标示对方是 incoming(被指向) 还是 outgoing(指向他)。"""
    relationType: str
    direction: Literal["incoming", "outgoing"]
    artifactId: str  # 对端产物 id
    title: str | None = None
    kind: str | None = None
    capabilityCallId: str | None = None
    createdAt: str | None = None


class ArtifactRelationsOut(BaseModel):
    relations: list[ArtifactRelationOut]


class IngestCallbackIn(BaseModel):
    """通道3：外部异步训练完成回调。external_uri（引用）或 object_key（已 presigned 上传）。"""

    task_id: str
    capability_call_id: str
    signature: str
    kind: str = "model"
    title: str | None = None
    summary: str | None = None
    external_uri: str | None = None
    external_provider: str | None = None
    object_key: str | None = None
    sha256: str | None = None
    size: int | None = None


class ConfirmationOut(BaseModel):
    id: str
    stage: TaskStage
    decision: ConfirmationDecision
    note: str | None = None
    createdAt: str


class CapabilityCallOut(BaseModel):
    id: str
    ownerId: str
    workspaceId: str
    createdBy: str
    kind: CapabilityKind = "in_app_agent"
    name: str
    status: CapabilityCallStatus
    requestedAt: str
    finishedAt: str | None = None
    resultSummary: str | None = None
    runtime: str | None = None
    artifactRefs: list[str] = Field(default_factory=list)
    stageKey: TaskStage | None = None
    subStageInstanceId: str | None = None
    inputArtifactRefs: list[str] = Field(default_factory=list)
    outputArtifactRefs: list[str] = Field(default_factory=list)
    workUnitId: str | None = None
    capabilityType: Literal["skill", "tool", "agent_runtime"] | None = None
    capabilityId: str | None = None
    attemptNumber: int | None = Field(default=None, ge=1)
    canonicalStatus: Literal[
        "queued",
        "running",
        "succeeded",
        "failed",
        "external_result_unknown",
    ] | None = None
    prevCallId: str | None = None
    inputCallRefs: list[str] = Field(default_factory=list)
    runId: str | None = None
    skillHash: str | None = None
    runMode: str | None = None
    sourceMode: str | None = None
    provider: str | None = None
    adapter: str | None = None
    model: str | None = None
    sourceAccess: str | None = None
    sourceMetadata: dict[str, Any] = Field(default_factory=dict)
    callVisibility: str | None = None
    audience: str | None = None
    partialFailureImpact: str | None = None
    artifactValidatorResults: list[ArtifactValidatorResultOut] = Field(default_factory=list)
    targetAgent: str | None = None
    executionMode: str | None = None
    contextPackId: str | None = None
    runKey: str | None = None
    userInputPolicy: str | None = None
    runtimeGateway: str | None = None
    gatewayReason: str | None = None
    budget: dict[str, Any] | None = None
    materialReadinessReport: dict[str, Any] | None = None
    userFacingNarrative: UserFacingNarrativeOut | None = None


class WorkflowSubStageOut(BaseModel):
    instanceId: str | None = Field(default=None, min_length=1)
    substage: str
    label: str | None = None
    action: str | None = None
    expectedArtifactTypes: list[str] = Field(default_factory=list)
    expectedArtifact: str | None = None


class TaskPlanStageOut(BaseModel):
    stageKey: TaskStage
    title: str | None = None
    description: str | None = None
    subStages: list[str] = Field(default_factory=list)
    substages: list[WorkflowSubStageOut] = Field(default_factory=list)


class TaskPlanUnderstandingOut(BaseModel):
    taskType: str | None = None
    userIntent: str | None = None
    materialSummary: str | None = None
    primaryResearchQuestions: list[str] = Field(default_factory=list)
    auxiliaryResearchDimensions: list[str] = Field(default_factory=list)
    targetOutputs: list[str] = Field(default_factory=list)
    evidenceBoundaries: list[str] = Field(default_factory=list)
    missingInputs: list[str] = Field(default_factory=list)
    riskNotes: list[str] = Field(default_factory=list)


class TaskPlanOut(BaseModel):
    goalSummary: str
    domain: str
    skillPack: str | None = None
    researchType: str | None = None
    planningSource: str | None = None
    understanding: TaskPlanUnderstandingOut | None = None
    clarifications: list[str] = Field(default_factory=list)
    stages: list[TaskPlanStageOut] = Field(default_factory=list)
    readyToConfirm: bool


class EditableWorkflowSubStageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instanceId: str | None = Field(default=None, min_length=1)
    substage: str
    label: str
    action: str
    expectedArtifact: str

    @field_validator("substage", "label", "action", "expectedArtifact")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class EditableTaskPlanStageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stageKey: TaskStage
    title: str
    description: str
    substages: list[EditableWorkflowSubStageIn] = Field(default_factory=list)

    @field_validator("title", "description")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class EditableTaskPlanUnderstandingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidenceBoundaries: list[str] = Field(default_factory=list)


class UpdateTaskPlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=0)
    goalSummary: str
    understanding: EditableTaskPlanUnderstandingIn
    clarifications: list[str] = Field(default_factory=list)
    stages: list[EditableTaskPlanStageIn]

    @field_validator("goalSummary")
    @classmethod
    def validate_goal_summary(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @model_validator(mode="after")
    def validate_fixed_stage_order(self) -> "UpdateTaskPlanIn":
        if [stage.stageKey for stage in self.stages] != STAGE_ORDER:
            raise ValueError("stages must contain the fixed four stages in order")
        if not any(stage.substages for stage in self.stages):
            raise ValueError("creation plan must include at least one substage")
        for stage in self.stages:
            keys = [substage.instanceId or substage.substage for substage in stage.substages]
            if len(keys) != len(set(keys)):
                raise ValueError(f"duplicate research step in stage: {stage.stageKey}")
        return self


class AgentResearchDesignRevisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedRevision: int = Field(ge=0)
    instruction: str = Field(min_length=1, max_length=8000)

    @field_validator("instruction")
    @classmethod
    def validate_instruction(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class CanonicalWorkUnitOut(BaseModel):
    """Stable public identity and lineage for one canonical v1.2 WorkUnit."""

    model_config = ConfigDict(extra="forbid")

    workUnitId: str
    executionDecisionId: str
    assignmentId: str
    subStageInstanceId: str
    responsibleActorId: str
    executorType: Literal["agent", "skill", "legacy_unknown"]
    executorId: str
    status: Literal[
        "queued",
        "running",
        "reviewing",
        "completed",
        "failed",
        "blocked",
        "cancelled",
        "external_result_unknown",
    ]
    terminalReasonCode: str | None = None
    dependencyWorkUnitIds: list[str] = Field(default_factory=list)
    inputArtifactRefs: list[str] = Field(default_factory=list)
    inputMemoryRefs: list[str] = Field(default_factory=list)
    outputArtifactRefs: list[str] = Field(default_factory=list)
    dispatchedAt: str
    finishedAt: str | None = None


class CatalogArtifactItem(BaseModel):
    artifactId: str
    title: str | None = None


class CatalogArtifactsSummary(BaseModel):
    """Catalog-sourced visible artifact summary for list views (count + top-N)."""
    count: int = 0
    items: list[CatalogArtifactItem] = Field(default_factory=list)


class TaskOut(PublicResearchDeliveryFields):
    taskId: str
    ownerId: str
    workspaceId: str
    createdBy: str
    agentSessionId: str | None = None
    title: str
    revision: int = Field(default=0, ge=0)
    progressProjection: dict[str, Any] | None = None
    goalSummary: str | None = None
    domain: str | None = None
    skillPack: str | None = None
    currentStage: TaskStage
    status: TaskStatus
    resultSource: ResultSource = "none"
    executionMode: TaskExecutionMode = "stage_confirmation"
    stageGateResumeApproved: bool = False
    creationStatus: CreationStatus | None = None
    creationPlan: TaskPlanOut | None = None
    activeResearchDesignRef: ResearchDesignRef | None = None
    pendingResearchDesignRef: ResearchDesignRef | None = None
    researchDesignVersions: list[ResearchDesignVersion] = Field(default_factory=list)
    createdAt: str | None = None
    updatedAt: str | None = None
    startedAt: str | None = None
    finishedAt: str | None = None
    stages: list[TaskStage]
    stageCatalog: list[StageCatalogOut] = Field(default_factory=list)
    stageDetails: list[StageDetailOut] = Field(default_factory=list)
    artifacts: list[ArtifactOut] = Field(default_factory=list)
    # Catalog-sourced visible artifact summary (artifact library = source of
    # truth). Used by list views (task board) so cloned/reused tasks — whose
    # artifacts live in the catalog, not the payload mirror — display correctly.
    catalogArtifacts: CatalogArtifactsSummary | None = None
    materials: list[MaterialManifestOut] = Field(default_factory=list)
    memoryRecords: list[MemoryRecordOut] = Field(default_factory=list)
    confirmations: list[ConfirmationOut] = Field(default_factory=list)
    capabilityCalls: list[CapabilityCallOut] = Field(default_factory=list)
    workUnits: list[CanonicalWorkUnitOut] = Field(default_factory=list)
    trustRecords: list[ToolResourceTrustRecordOut] = Field(default_factory=list)
    userVisibleEvents: list[UserVisibleEventOut] = Field(default_factory=list)
    researchType: str | None = None
    draftClientRequestId: str | None = None

class TaskListOut(BaseModel):
    tasks: list[TaskOut]
    # List pagination. total = in-scope task count (independent of the page).
    # When the client omits limit, the API applies DEFAULT_TASK_LIST_LIMIT (20).
    # Max page size is 200. List items use a slim projection (no design/catalog
    # blobs); GET /api/tasks/{id} remains the full TaskOut.
    total: int = 0
    limit: int | None = None
    offset: int = 0
    statusTotals: dict[str, int] = Field(default_factory=dict)


class TaskTitleOptionOut(BaseModel):
    taskId: str
    title: str


class TaskTitleOptionsOut(BaseModel):
    """Picker rows for artifact library / reuse — title columns only."""

    tasks: list[TaskTitleOptionOut]
    total: int = 0
    limit: int = 50
    offset: int = 0


class ReleaseEvidenceOut(BaseModel):
    hasTaskObject: bool
    hasArtifactOrGapState: bool
    hasCapabilityCallOrRunRecord: bool
    hasFailureBoundary: bool
    hasExampleIsolation: bool
    notes: str | None = None


class StarterUsageGuideOut(BaseModel):
    suitableFor: str
    input: str
    output: str
    boundary: str


class StarterVerifiedSampleOut(BaseModel):
    sampleId: str
    taskTitle: str
    realInput: str
    taskGoal: str
    progress: str
    output: str
    boundary: str
    nextAction: str
    evidencePaths: list[str] = Field(default_factory=list)


class StarterFailureStateOut(BaseModel):
    state: Literal["workflow_draft", "gap_state", "requires_existing_inputs", "prep_check"]
    reason: str
    impact: str
    nextAction: str
    retryPolicy: str
    partialOutputStatus: str


class ReleaseGatedStarterOut(BaseModel):
    starterId: str
    title: str
    description: str
    category: Literal["Project", "Workflow", "Agent", "SkillCategory"]
    productFamily: Literal[
        "literature_sources",
        "evidence_mechanisms",
        "hypotheses_leads",
        "writing_reports",
        "experiments_training",
        "planning_project",
        "audit_trust",
        "presentation_reporting",
    ]
    visibility: Literal["public_invited_trial", "hidden", "internal", "example_only"]
    releaseDecision: Literal[
        "visible_now",
        "visible_with_guardrail",
        "hidden",
        "internal_only",
        "requires_external_verification",
        "prep_check_only",
        "requires_existing_inputs",
    ]
    creationMode: Literal[
        "create_real_task",
        "create_task_draft",
        "create_workflow_draft_then_confirm",
        "requires_existing_inputs",
        "prep_check_only",
    ]
    readyLevel: Literal[
        "ready",
        "with_guardrail",
        "requires_existing_inputs",
        "prep_check_only",
        "later",
    ]
    artifactExpectation: list[str] = Field(default_factory=list)
    requiredInputs: list[str] = Field(default_factory=list)
    artifactOutputs: list[str] = Field(default_factory=list)
    outputList: list[str] = Field(default_factory=list)
    boundaryCopy: str
    failureStates: list[StarterFailureStateOut] = Field(default_factory=list)
    sourceMode: Literal[
        "local_deterministic",
        "metadata_or_abstract_bounded",
        "requires_source_packet",
        "training_prep_only",
        "none_until_verified",
    ]
    trustBoundary: str
    guardrail: list[str] = Field(default_factory=list)
    fallback: list[str] = Field(default_factory=list)
    releaseEvidence: ReleaseEvidenceOut
    primaryAction: Literal["使用此起点"]
    secondaryAction: Literal["查看示例", "查看样例"] | None = None
    usageGuide: StarterUsageGuideOut | None = None
    verifiedSample: StarterVerifiedSampleOut | None = None
    sampleStatus: Literal["verified", "pending"] | None = None
    sampleDisabledReason: str | None = None


class StarterListOut(BaseModel):
    starters: list[ReleaseGatedStarterOut]


class StarterUseIn(BaseModel):
    goalSummary: str | None = None
    title: str | None = None
    inputArtifactIds: list[str] = Field(default_factory=list)


class StarterUseOut(BaseModel):
    starter: ReleaseGatedStarterOut
    outcome: Literal[
        "workflow_draft",
        "prep_check",
        "gap_state",
        "requires_existing_inputs",
        "literature_artifacts",
        "real_task",
    ]
    task: TaskOut | None = None
    artifact: ArtifactOut | None = None
    state: dict[str, Any] | None = None
    nextAction: str


class CreateTaskIn(BaseModel):
    title: str | None = None
    goalSummary: str | None = None
    domain: str = "research_task"
    skillPack: str | None = None
    currentStage: TaskStage = "problem_modeling"
    status: TaskStatus = "running"
    resultSource: ResultSource = "none"
    executionMode: TaskExecutionMode = "stage_confirmation"
    creationStatus: CreationStatus | None = None
    creationPlan: TaskPlanOut | dict[str, Any] | None = None
    stages: list[TaskStage] | None = None
    ownerId: str | None = None
    workspaceId: str | None = None
    createdBy: str | None = None
    clientRequestId: str | None = Field(default=None, min_length=1, max_length=160)


class CloneTaskIn(BaseModel):
    """Body for cloning a task into a fresh project seeded from the source spec.

    ``includeArtifacts=False`` yields a template (spec only, no carried-over
    artifacts); the default reuses every source artifact (content-addressed,
    zero-copy) into the new task.
    """
    title: str | None = None
    includeArtifacts: bool = True


class TaskProfileDraftOut(BaseModel):
    taskId: str
    ownerId: str
    workspaceId: str
    createdBy: str
    title: str
    goalSummary: str | None = None
    domain: str | None = None
    currentStage: TaskStage
    status: TaskStatus


class TaskProfileStateOut(BaseModel):
    needsConfirmation: bool
    confirmedAt: str | None = None
    draft: TaskProfileDraftOut


class TaskProfileConfirmIn(BaseModel):
    taskId: str
    title: str
    goalSummary: str | None = None
    domain: str | None = None


class PatchTaskStatusIn(BaseModel):
    status: TaskStatus


class PatchTaskExecutionModeIn(BaseModel):
    executionMode: TaskExecutionMode


class TaskExecutionPolicyOut(BaseModel):
    taskId: str
    currentMode: TaskExecutionMode
    pendingMode: TaskExecutionMode | None = None
    policyVersion: int
    pendingPolicyVersion: int | None = None
    changedBy: str
    changedAt: str
    appliedAt: str


class ExecutionGateDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["continue_run", "start_revision_run", "terminate_run"]
    note: str | None = Field(default=None, max_length=2_000)


class ExecutionGateDecisionOut(BaseModel):
    gateId: str
    taskId: str
    runId: str
    state: Literal["resolved", "cancelled"]
    action: Literal["continue_run", "start_revision_run", "terminate_run"]
    successorRunId: str | None = None
    taskStatus: TaskStatus
    userMessage: str


class RenameTaskIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        title = value.strip()
        if not title:
            raise ValueError("title must not be blank")
        return title


class StageResultIn(BaseModel):
    label: str | None = None
    summary: str | None = None
    risks: str | None = None
    nextSteps: str | None = None
    entryCondition: str | None = None
    completionCondition: str | None = None
    lockedReason: str | None = None
    primaryAction: str | None = None
    canSkip: bool | None = None
    canReview: bool | None = None
    canLock: bool | None = None
    status: StageStatus | None = None
    artifactRefs: list[str] | None = None
    subStages: list[SubStageOut] | None = None
