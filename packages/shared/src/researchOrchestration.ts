import {
  assertExecutionAssignmentContract,
  assertWorkUnitContract,
  assertExecutionMethodRoleEvaluations,
  assertExecutionMethodReference,
  type ExecutionMethodAssignmentMetadata,
  type ExecutionMethodWorkMetadata,
  type ExecutionMethodRoleEvaluation,
} from "./researchExecutionMethodReadback";
export type { ExecutionMethodRoleEvaluation } from "./researchExecutionMethodReadback";
import {
  assertPlanningSourceUsages,
  type PlanningSourceUsage,
} from "./researchPlanningSourceUsage";
export type { PlanningSourceUsage } from "./researchPlanningSourceUsage";
import { assertCapabilityCallContract, type CapabilityCallV12 } from "./researchCapabilityCall";
export type { CapabilityCallV12, ResearchCapabilityCallStatusV12, InternalCapabilityCallInput } from "./researchCapabilityCall";
import {
  assertProjectCompletionRequirements as validateCompletionRequirements,
  assertCompletionDecisionContract,
  assertWorkflowCoverage,
  assertTargetContributions,
  type ProjectCompletionDecisionDetails,
  type WorkflowResponsibilityCoverage,
  type ProjectCompletionRequirement,
} from "./projectCompletionTargets";
export type {
  ProjectCompletionRequirement,
  TargetOutputReuseAcceptance,
  TargetOutputReuseJudgment,
  WorkflowResponsibilityCoverage,
  CompletionRequirementAssessment,
  ProjectCompletionDecisionDetails,
} from "./projectCompletionTargets";
/** Frozen research orchestration v1.2 persistence contracts. */

declare const positiveIntegerBrand: unique symbol;
declare const nonNegativeIntegerBrand: unique symbol;
declare const sha256HashBrand: unique symbol;

type UtcOffset = "Z" | `${"+" | "-"}${number}:${number}`;
export type ISODateTime = `${number}-${number}-${number}T${number}:${number}:${number}${UtcOffset}`;
export type PositiveInteger = number & { readonly [positiveIntegerBrand]: "positive-integer" };
export type NonNegativeInteger = number & {
  readonly [nonNegativeIntegerBrand]: "non-negative-integer";
};
export type Sha256Hash = string & { readonly [sha256HashBrand]: "sha256-hash" };
export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

export function positiveInteger(value: number): PositiveInteger {
  if (!Number.isInteger(value) || value <= 0) {
    throw new Error("expected a positive integer");
  }
  return value as PositiveInteger;
}

export function nonNegativeInteger(value: number): NonNegativeInteger {
  if (!Number.isInteger(value) || value < 0) {
    throw new Error("expected a non-negative integer");
  }
  return value as NonNegativeInteger;
}

export function awareDateTime(value: string): ISODateTime {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-](\d{2}):(\d{2}))$/.exec(value);
  if (match === null) {
    throw new Error("expected an aware ISO-8601 timestamp");
  }
  const [, yearText, monthText, dayText, hourText, minuteText, secondText, offsetHourText, offsetMinuteText] = match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const hour = Number(hourText);
  const minute = Number(minuteText);
  const second = Number(secondText);
  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const daysInMonth = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  const offsetHour = offsetHourText === undefined ? 0 : Number(offsetHourText);
  const offsetMinute = offsetMinuteText === undefined ? 0 : Number(offsetMinuteText);
  if (
    year === 0 || month < 1 || month > 12 || day < 1 || day > (daysInMonth[month - 1] ?? 0) ||
    hour > 23 || minute > 59 || second > 59 || offsetHour > 23 || offsetMinute > 59
  ) {
    throw new Error("expected an aware ISO-8601 timestamp");
  }
  return value as ISODateTime;
}

export function sha256Hash(value: string): Sha256Hash {
  if (!/^sha256:[0-9a-f]{64}$/.test(value)) {
    throw new Error("expected a lower-case sha256 digest");
  }
  return value as Sha256Hash;
}

export type StageKey =
  | "problem_modeling"
  | "solution_design"
  | "execution_validation"
  | "insight_generation";
export type ResearchTaskStatusV12 =
  | "draft"
  | "not_started"
  | "running"
  | "awaiting_confirmation"
  | "completed"
  | "failed"
  | "cancelled";
export type ResearchTaskExecutionModeV12 = "automatic_research" | "stage_confirmation" | "manual_step";
export type SubstageRuntimeStatus =
  | "planned"
  | "ready"
  | "queued"
  | "running"
  | "reviewing"
  | "needs_input"
  | "blocked_recoverable"
  | "insufficient_evidence"
  | "completed"
  | "failed"
  | "cancelled"
  | "skipped";
export type ResearchArtifactStateV12 =
  | "usable"
  | "needs_review"
  | "needs_input"
  | "insufficient_materials"
  | "failed"
  | "partial_success"
  | "rejected_or_needs_revision";
export type RiskClass = "low" | "medium" | "high";
export type PlanningMode = "lightweight" | "partial" | "sdr";
export type PlanningAttemptStatus = "started" | "completed" | "failed";
export type AdditionalActionType = "none" | "semantic_adequacy" | "planner_repair";
export type ResearchDesignStatus = "draft" | "confirmed" | "superseded" | "cancelled";
export type AssignmentRole = "primary" | "reviewer" | "support";
export type CompletionCoverage = "completion_capable" | "progress_capable";
export type ExecutionStrategy =
  | "direct_skill"
  | "skill_composition"
  | "execution_method"
  | "specialist_agent"
  | "general_research_agent"
  | "multi_agent";
export type ExecutionPurpose = "completion" | "progress";
export type WorkUnitSourceMode = "live" | "legacy_projection";
export type ArtifactLineageKind = "capability_call_output" | "direct_work_unit_output";
export type CompletionDisposition = "full" | "partial" | "degraded" | "evidence_gap";

export interface InstanceRef {
  substageInstanceId: string;
  instanceRevision: PositiveInteger;
}

export interface ResearchDesignRef {
  researchDesignId: string;
  version: PositiveInteger;
}

export interface WorkflowProvenanceRef {
  workflowId: string;
  workflowVersion: PositiveInteger;
  contentHash: Sha256Hash;
}

export interface RegistrySnapshotRef {
  schemaVersion: PositiveInteger;
  contentHash: Sha256Hash;
}

export interface MultiAgentRecipeRef {
  recipeId: string;
  recipeVersion: PositiveInteger;
}

export interface AgentRef {
  agentId: string;
  definitionVersion: PositiveInteger;
}

export interface SkillRef {
  skillId: string;
  definitionVersion: PositiveInteger;
}

export type CapabilityRef = AgentRef | SkillRef;
export type ExecutionCandidateRef = CapabilityRef | MultiAgentRecipeRef;

export interface BindingRef {
  bindingId: string;
  bindingVersion: PositiveInteger;
}

export interface ResearchUnderstanding {
  researchType: string | null;
  userIntent: string;
  materialSummary: string | null;
  primaryResearchQuestions: string[];
  auxiliaryResearchDimensions: string[];
  targetOutputs: string[];
  evidenceBoundaries: string[];
  missingInputs: string[];
  riskNotes: string[];
  planningRationale: string;
}

export interface PlanningComplexitySnapshot {
  problemClarity: "high" | "medium" | "low";
  expectedStageSpan: "one" | "multiple" | "unknown";
  dependencyComplexity: RiskClass;
  evidenceBurden: RiskClass;
  externalActionRisk: RiskClass;
  researchUncertainty: RiskClass;
  rationale: string;
}

export interface PlanningInputSnapshot {
  sources: Array<{
    sourceKind: "user_request" | "user_revision" | "plan_revision";
    sourceRef: string;
    content: string;
  }>;
  baseResearchDesignRef: ResearchDesignRef | null;
}

export interface PlanningAttempt {
  planningSourceUsages?: PlanningSourceUsage[];
  userInputSnapshot?: PlanningInputSnapshot;
  planningAttemptId: string;
  taskId: string;
  requestId: string;
  plannerCallIdempotencyKey: string;
  plannerProviderCallRef: string | null;
  additionalActionType: AdditionalActionType;
  additionalActionIdempotencyKey: string | null;
  additionalProviderCallRef: string | null;
  status: PlanningAttemptStatus;
  startedAt: ISODateTime;
  finishedAt: ISODateTime | null;
}

export type AcceptanceCriterionKind =
  | "target_output"
  | "artifact_quality"
  | "constraint_compliance"
  | "evidence_boundary_compliance"
  | "external_action_guard"
  | "risk_guard"
  | "professional_judgment";
export type JudgmentRequirement =
  | "deterministic"
  | "research_synthesis"
  | "domain_professional"
  | "human_authority";
export type EvaluatorType = "deterministic" | "agent" | "human_gate";

export interface AcceptanceCriterion {
  criterionId: string;
  description: string;
  criterionKind: AcceptanceCriterionKind;
  coverageTargetIds: string[];
  requiredArtifactType: string | null;
  judgmentRequirement: JudgmentRequirement;
  evaluatorType: EvaluatorType;
  evaluatorRef: string | null;
}

export interface SubstageDependency {
  dependencyId: string;
  upstreamInstanceRef: InstanceRef;
  conditionType: "instance_completed" | "artifact_usable" | "confirmation_granted";
  requiredArtifactTypes: string[];
  confirmationTargetRef: string | null;
}

export interface ScopeBoundary {
  allowedObjectiveKinds: string[];
  excludedActions: string[];
  allowedMaterialRefs: string[];
  maxSubstageInstances: PositiveInteger | null;
}

export interface BudgetBoundary {
  maxModelCalls: NonNegativeInteger | null;
  maxToolCalls: NonNegativeInteger | null;
  maxWallTimeSec: NonNegativeInteger | null;
  maxCostAmount: number | null;
  currency: string | null;
}

export interface ExternalActionBoundary {
  allowedActionKinds: string[];
  confirmationRequiredKinds: string[];
  allowedDestinations: string[];
}

export interface RiskBoundary {
  maxRiskClass: RiskClass;
  prohibitedActions: string[];
  humanGateTriggers: string[];
}

export interface AuthorizationSnapshot {
  policyVersion: string;
  allowedMemoryScopes: string[];
  allowedExternalActionKinds: string[];
  confirmationRefs: string[];
}

export interface BudgetSnapshot {
  boundary: BudgetBoundary;
  consumedModelCalls: NonNegativeInteger;
  consumedToolCalls: NonNegativeInteger;
  consumedCostAmount: number;
}

export interface RiskSnapshot {
  boundary: RiskBoundary;
  evaluatedRiskClass: RiskClass;
  triggeredHumanGates: string[];
}

export type SuccessMode = "all_required" | "any_required";
export type ReviewRequirement = "none" | "all_reviewers_approve" | "any_reviewer_approves";
export type ReviewerRejectAction = "new_revision_decision" | "human_gate";

export interface CompletionPolicy {
  requiredAssignmentIds: string[];
  successMode: SuccessMode;
  reviewerAssignmentIds: string[];
  reviewRequirement: ReviewRequirement;
  partialSuccessAllowed: boolean;
  onReviewerReject: ReviewerRejectAction;
}

export interface ProgressPolicy {
  requiredAssignmentIds: string[];
  requiredArtifactTypes: string[];
  acceptedResultCodes: "progressed" | "needs_input" | "blocked_recoverable";
}

export interface CompletionPolicyTemplate {
  requiredTemplateKeys: string[];
  successMode: SuccessMode;
  reviewerTemplateKeys: string[];
  reviewRequirement: ReviewRequirement;
  partialSuccessAllowed: boolean;
  onReviewerReject: ReviewerRejectAction;
}

export interface ArtifactRequirementTemplate {
  artifactType: string;
  minimumCount: NonNegativeInteger;
  sourceTemplateKeys: string[];
}

export interface ResultAssemblyPolicyTemplate {
  mode: "primary_output" | "designated_assembler" | "artifact_set";
  assemblerTemplateKey: string | null;
  finalArtifactRequirements: ArtifactRequirementTemplate[];
}

export interface ArtifactRequirement {
  artifactType: string;
  minimumCount: NonNegativeInteger;
  sourceAssignmentIds: string[];
}

export interface ResultAssemblyPolicy {
  mode: "primary_output" | "designated_assembler" | "artifact_set";
  assemblerAssignmentId: string | null;
  finalArtifactRequirements: ArtifactRequirement[];
}

export type CoverageSourceType =
  | "target_output"
  | "constraint"
  | "evidence_boundary"
  | "external_action_boundary"
  | "risk_boundary";

export interface CoverageTarget {
  targetId: string;
  sourceType: CoverageSourceType;
  sourcePath: string;
  description: string;
  requiredArtifactTypes: string[];
  enforcementRef: string | null;
  required: boolean;
}

export interface CoverageClaim {
  targetId: string;
  instanceRefs: InstanceRef[];
  coverageMode: "full" | "partial" | "none";
  acceptanceCriterionRefs: string[];
  derivationRuleRefs: string[];
  rationale: string;
}

export interface ResearchDesignCoverageReport {
  coverageReportId: string;
  designRef: ResearchDesignRef;
  planningAttemptId: string;
  targets: CoverageTarget[];
  claims: CoverageClaim[];
  uncoveredRequiredTargetIds: string[];
  overloadedInstanceRefs: InstanceRef[];
  missingDependencyRefs: string[];
  status: "passed_structural" | "passed_semantic" | "executable_with_gaps" | "needs_revision";
  evaluationTier: PlanningMode;
  evaluationMode: "deterministic" | "semantic_assisted";
  semanticEvaluationRef: string | null;
  automaticRepairCount: NonNegativeInteger;
  policyVersion: string;
  evaluatedAt: ISODateTime;
}

export interface RepeatableDefinitionReentryAuthority {
  policyVersion: "repeatable_definition_reentry_v1";
  triggerDefinitionIds: string[];
}

export interface SubstageInstance {
  substageInstanceId: string;
  instanceRevision: PositiveInteger;
  substageDefinitionId: string;
  substageDefinitionVersion: PositiveInteger;
  stageKey: StageKey;
  objective: string;
  inputRefs: string[];
  expectedArtifactTypes: string[];
  acceptanceCriteria: AcceptanceCriterion[];
  dependencies: SubstageDependency[];
  sequenceHint: NonNegativeInteger | null;
  repeatableReentryAuthority: RepeatableDefinitionReentryAuthority | null;
}

export interface ReentryExecutionInstance {
  researchDesignVersion: PositiveInteger;
  instance: SubstageInstance;
  templateInstanceRef: InstanceRef;
  triggeringScientificStateVersion: PositiveInteger;
  triggeringExecutionDecisionId: string;
  previousRelevantInstanceRef: InstanceRef;
  triggeringStateSourceRefs: string[];
  reentryFingerprint: string;
  createdAt: ISODateTime;
}

export interface ResearchDesignVersion {
  researchDesignId: string;
  taskId: string;
  version: PositiveInteger;
  supersedesVersion: PositiveInteger | null;
  status: ResearchDesignStatus;
  planningMode: PlanningMode;
  planningComplexity: PlanningComplexitySnapshot;
  goal: string;
  understanding: ResearchUnderstanding;
  constraints: string[];
  scopeBoundary: ScopeBoundary;
  budgetBoundary: BudgetBoundary;
  externalActionBoundary: ExternalActionBoundary;
  riskBoundary: RiskBoundary;
  workflowProvenanceRef: WorkflowProvenanceRef | null;
  projectCompletionRequirements: ProjectCompletionRequirement[];
  workflowResponsibilityCoverage?: WorkflowResponsibilityCoverage[];
  instances: SubstageInstance[];
  coverageReport: ResearchDesignCoverageReport | null;
  createdAt: ISODateTime;
  confirmedAt: ISODateTime | null;
  confirmationRef: string | null;
}

export interface SubstageRuntimeState {
  substageInstanceId: string;
  instanceRevision: PositiveInteger;
  status: SubstageRuntimeStatus;
  activeExecutionDecisionId: string | null;
  workUnitIds: string[];
  artifactRefs: string[];
  statusReasonCode: string | null;
  updatedAt: ISODateTime;
}

export interface RecipeAssignmentTemplate {
  templateKey: string;
  role: AssignmentRole;
  requiredSupportedRole: AssignmentRole;
  objectiveTemplate: string;
  dependencyTemplateKeys: string[];
}

export interface MultiAgentRecipeDefinition {
  recipeId: string;
  recipeVersion: PositiveInteger;
  substageDefinitionId: string;
  substageDefinitionVersion: PositiveInteger;
  applicabilityRuleRefs: string[];
  assignmentTemplates: RecipeAssignmentTemplate[];
  completionPolicyTemplate: CompletionPolicyTemplate;
  resultAssemblyPolicyTemplate: ResultAssemblyPolicyTemplate;
  enabled: boolean;
}

export interface ActorRef {
  actorType: "orchestrator" | "agent";
  actorId: string;
  definitionVersion: PositiveInteger | null;
}

export interface ExecutorRef {
  executorType: "agent" | "skill" | "legacy_unknown";
  executorId: string;
  definitionVersion: PositiveInteger | null;
}

export interface RuntimeReadinessSnapshot {
  capabilityRef: CapabilityRef;
  ready: boolean;
  runtimeBindingId: string | null;
  checkedAt: ISODateTime;
  expiresAt: ISODateTime;
  reason: string | null;
}

export interface SemanticFitEvidence {
  objectiveMatch: "exact" | "compatible" | "none";
  inputContractMatch: "exact" | "adapter_compatible" | "none";
  outputContractMatch: "exact" | "adapter_compatible" | "partial" | "none";
  professionalJudgmentFit: "required_supported" | "not_required" | "unsupported";
  positiveRuleRefs: string[];
  negativeRuleRefs: string[];
  adapterRefs: string[];
  policyVersion: string;
  rationale: string;
}

export interface CandidatePreference {
  semanticFit: "exact" | "compatible" | "weak";
  semanticFitEvidence: SemanticFitEvidence;
  completionCoverage: CompletionCoverage;
  executableNow: boolean;
  executionBlockerCodes: CandidateExecutionBlockerCode[];
  riskClass: RiskClass;
  estimatedCostAmount: number | null;
  permissionBreadth: "narrow" | "standard" | "broad";
  supportsProfessionalJudgment: boolean;
  activeWorkUnitCount: NonNegativeInteger;
  lastAssignedAt: ISODateTime | null;
}

export type CandidateRejectionCode =
  | "disabled"
  | "role_mismatch"
  | "input_incompatible"
  | "output_incompatible"
  | "agent_skill_not_granted"
  | "explicitly_not_for"
  | "semantic_fit_insufficient";
export type CandidateExecutionBlockerCode =
  | "required_input_missing"
  | "authorization_denied"
  | "risk_exceeded"
  | "budget_exceeded"
  | "runtime_unavailable"
  | "provider_unavailable";

export interface CandidateEvaluation {
  executionMethodRoleEvaluations?: ExecutionMethodRoleEvaluation[];
  candidateEvaluationId: string;
  candidateRef: ExecutionCandidateRef;
  sourceBindingRefs: BindingRef[];
  supportedRole: string;
  eligible: boolean;
  rejectionCodes: CandidateRejectionCode[];
  preference: CandidatePreference;
  rationale: string;
}

export interface ExecutionAssignment extends ExecutionMethodAssignmentMetadata {
  assignmentId: string;
  recipeTemplateKey: string | null;
  role: AssignmentRole;
  completionCoverage: CompletionCoverage;
  objective: string;
  sourceBindingRefs: BindingRef[];
  responsibleActorRef: ActorRef;
  executorRef: ExecutorRef;
  allowedSkillRefs: SkillRef[];
  inputRefs: string[];
  expectedArtifactTypes: string[];
  acceptanceCriteria: AcceptanceCriterion[];
  dependencyAssignmentIds: string[];
}

export type FallbackReasonCode =
  | "no_completion_coverage_preferred_candidate"
  | "preferred_not_executable_inputs"
  | "preferred_not_executable_authorization"
  | "preferred_not_executable_budget"
  | "preferred_not_executable_risk"
  | "preferred_not_executable_provider"
  | "preferred_not_executable_runtime";
export type ExecutionDecisionStatus =
  | "proposed"
  | "selected"
  | "dispatched"
  | "closed"
  | "superseded"
  | "cancelled";
export type ExecutionDecisionOutcome =
  | "succeeded"
  | "partial"
  | "progressed"
  | "needs_input"
  | "rejected"
  | "blocked"
  | "failed"
  | "cancelled";

export interface AssignmentPreflightOutcome {
  assignmentId: string;
  failureKind: "material_unavailable" | "contract_violation";
  reasonCode: string;
  fieldPaths: string[];
  inputArtifactSnapshots: CanonicalArtifactSnapshot[];
  inputScientificStateVersion: PositiveInteger | null;
  checkedAt: ISODateTime;
}

export interface ExecutionDecision {
  assignmentPreflightOutcomes?: AssignmentPreflightOutcome[];
  executionMethodRef?: { [key: string]: JsonValue } | null;
  executionDecisionId: string;
  selectionRequestId: string;
  decisionSequence: PositiveInteger;
  supersedesExecutionDecisionId: string | null;
  revisionReasonCode: string | null;
  taskId: string;
  researchDesignVersion: PositiveInteger;
  substageInstanceId: string;
  substageInstanceRevision: PositiveInteger;
  registrySnapshotRef: RegistrySnapshotRef;
  purpose: ExecutionPurpose;
  strategy: ExecutionStrategy;
  multiAgentRecipeRef: MultiAgentRecipeRef | null;
  candidateEvaluations: CandidateEvaluation[];
  fallbackInvoked: boolean;
  fallbackReasonCode: FallbackReasonCode | null;
  preferredCandidateEvaluationRefs: string[];
  assignments: ExecutionAssignment[];
  invocationContext: { [key: string]: JsonValue };
  completionPolicy: CompletionPolicy | null;
  progressPolicy: ProgressPolicy | null;
  resultAssemblyPolicy: ResultAssemblyPolicy;
  runtimeReadiness: RuntimeReadinessSnapshot[];
  budgetSnapshot: BudgetSnapshot;
  policyVersion: string;
  rationale: string;
  status: ExecutionDecisionStatus;
  outcome: ExecutionDecisionOutcome | null;
  decidedAt: ISODateTime;
  closedAt: ISODateTime | null;
}

export type WorkUnitStatus =
  | "queued"
  | "running"
  | "reviewing"
  | "completed"
  | "failed"
  | "blocked"
  | "cancelled"
  | "external_result_unknown";

export interface CanonicalArtifactSnapshot {
  artifactId: string;
  artifactType: string;
  version: PositiveInteger;
  contentSha256: string | null;
}

export interface WorkUnit extends ExecutionMethodWorkMetadata {
  workUnitId: string;
  idempotencyKey: string;
  taskId: string;
  executionDecisionId: string;
  assignmentId: string;
  researchDesignVersion: PositiveInteger;
  substageInstanceId: string;
  substageInstanceRevision: PositiveInteger;
  substageDefinitionId: string;
  substageDefinitionVersion: PositiveInteger;
  registrySnapshotRef: RegistrySnapshotRef;
  responsibleActorRef: ActorRef;
  executorRef: ExecutorRef;
  instruction: string;
  inputArtifactRefs: string[];
  inputArtifactSnapshots: CanonicalArtifactSnapshot[];
  inputMemoryRefs: string[];
  invocationContext: { [key: string]: JsonValue };
  allowedSkillRefs: SkillRef[];
  authorizationSnapshot: AuthorizationSnapshot;
  budgetSnapshot: BudgetSnapshot;
  riskSnapshot: RiskSnapshot;
  expectedArtifactTypes: string[];
  acceptanceCriteria: AcceptanceCriterion[];
  stopConditions: string[];
  dependencyWorkUnitIds: string[];
  executionGroupId: string | null;
  policyVersion: string;
  sourceMode: WorkUnitSourceMode;
  dispatchable: boolean;
  status: WorkUnitStatus;
  terminalReasonCode: string | null;
  dispatchedAt: ISODateTime;
  frozenAt: ISODateTime;
  finishedAt: ISODateTime | null;
}

export type ResultCheckStatus = "pending" | "passed" | "failed";

export interface ResultV1 {
  resultId: string;
  workUnitId: string;
  capabilityCallId: string | null;
  storageType: string;
  storageScope: string | null;
  storageRef: string;
  contentSha256: string;
  mimeType: string;
  checkStatus: ResultCheckStatus;
  checkResult: { [key: string]: JsonValue };
  sourceRefs: string[];
  artifactRefs: string[];
  scientificStateVersions: PositiveInteger[];
  createdAt: ISODateTime;
}

export interface ArtifactV12 {
  artifactId: string;
  workUnitId: string;
  substageInstanceId: string;
  sourceCapabilityCallId: string | null;
  artifactType: string;
  artifactState: ResearchArtifactStateV12;
  version: PositiveInteger;
  sourceRefs: string[];
  validatorResult: { [key: string]: JsonValue };
  lineageKind: ArtifactLineageKind | null;
  sourceResultId: string | null;
}

export interface ConfirmationV12 {
  confirmationId: string;
  targetType:
    | "research_design"
    | "stage_gate"
    | "substage_gate"
    | "artifact"
    | "scope_expansion"
    | "budget_expansion";
  targetId: string;
  targetVersion: PositiveInteger;
  decision: "approved" | "rejected" | "request_revision";
  actorId: string;
  createdAt: ISODateTime;
}

export type InteractionVerdict =
  | {
      verdictType: "handoff";
      decision: "approved" | "insufficient_evidence";
      rationaleCode: string;
    }
  | {
      verdictType: "review";
      decision: "approved" | "rejected" | "insufficient_evidence";
      rationaleCode: string;
    }
  | {
      verdictType: "acceptance";
      decision: "approved";
      rationaleCode: string;
    }
  | {
      verdictType: "rejection";
      decision: "rejected" | "insufficient_evidence";
      rationaleCode: string;
    };

type NonEmptyArtifactRefs = readonly [string, ...string[]];
type ArtifactEvidence =
  | { inputArtifactRefs: NonEmptyArtifactRefs; outputArtifactRefs: string[] }
  | { inputArtifactRefs: string[]; outputArtifactRefs: NonEmptyArtifactRefs };
type InteractionBase = {
  interactionId: string;
  idempotencyKey: string;
  taskId: string;
  executionDecisionId: string;
  sourceAssignmentId: string;
  sourceWorkUnitId: string;
  targetAssignmentId: string | null;
  targetWorkUnitId: string | null;
  summary: string;
  createdAt: ISODateTime;
};
type HandoffEvidence = ArtifactEvidence & {
  verdict: Extract<InteractionVerdict, { verdictType: "handoff" }> | null;
};
type HandoffVerdictEvidence = {
  inputArtifactRefs: string[];
  outputArtifactRefs: string[];
  verdict: Extract<InteractionVerdict, { verdictType: "handoff" }>;
};
type ReviewEvidence = ArtifactEvidence & {
  verdict: Extract<InteractionVerdict, { verdictType: "review" }> | null;
};
type ReviewVerdictEvidence = {
  inputArtifactRefs: string[];
  outputArtifactRefs: string[];
  verdict: Extract<InteractionVerdict, { verdictType: "review" }>;
};
type AcceptanceEvidence = ArtifactEvidence & {
  verdict: Extract<InteractionVerdict, { verdictType: "acceptance" }> | null;
};
type AcceptanceVerdictEvidence = {
  inputArtifactRefs: string[];
  outputArtifactRefs: string[];
  verdict: Extract<InteractionVerdict, { verdictType: "acceptance" }>;
};
type RejectionEvidence = ArtifactEvidence & {
  verdict: Extract<InteractionVerdict, { verdictType: "rejection" }> | null;
};
type RejectionVerdictEvidence = {
  inputArtifactRefs: string[];
  outputArtifactRefs: string[];
  verdict: Extract<InteractionVerdict, { verdictType: "rejection" }>;
};
type OptionalEvidenceInteraction = {
  interactionType: "challenge_raised" | "response_provided";
  inputArtifactRefs: string[];
  outputArtifactRefs: string[];
  verdict: InteractionVerdict | null;
};

export type AgentInteractionRecord = InteractionBase &
  (
    | ({ interactionType: "handoff_requested" | "handoff_completed" } &
        (HandoffEvidence | HandoffVerdictEvidence))
    | ({ interactionType: "review_submitted" } & (ReviewEvidence | ReviewVerdictEvidence))
    | ({ interactionType: "acceptance_recorded" | "target_output_reuse_accepted" } &
        (AcceptanceEvidence | AcceptanceVerdictEvidence))
    | ({ interactionType: "rejection_recorded" } & (RejectionEvidence | RejectionVerdictEvidence))
    | OptionalEvidenceInteraction
  );

export type DispatchStatus =
  | "pending"
  | "claimed"
  | "submission_authorized"
  | "submitted"
  | "suppressed"
  | "submission_unknown"
  | "failed";

export interface DispatchRecord {
  dispatchId: string;
  taskId: string;
  executionDecisionId: string;
  workUnitId: string;
  idempotencyKey: string;
  executionNamespace: string | null;
  status: DispatchStatus;
  attemptCount: NonNegativeInteger;
  leaseOwner: string | null;
  leaseExpiresAt: ISODateTime | null;
  authorizationTaskRevision: NonNegativeInteger | null;
  remoteRunId: string | null;
  nextAttemptAt: ISODateTime | null;
  lastErrorCode: string | null;
  lastFailureAt: ISODateTime | null;
  createdAt: ISODateTime;
  updatedAt: ISODateTime;
}

export interface FinalResultAuthority {
  mode: "assembled_artifact" | "artifact_set";
  researchDesignVersion: PositiveInteger;
  resultArtifactRefs: string[];
  sourceArtifacts: CanonicalArtifactSnapshot[];
  assembledAt: ISODateTime;
  finalResultId: string | null;
  integrityContractVersion:
    | "legacy_v0"
    | "scientific_completion_integrity_v1"
    | "scientific_completion_integrity_v2"
    | "scientific_completion_integrity_v3";
}

export interface ScientificStateV1 {
  scientificStateVersion: PositiveInteger;
  researchDesignVersion: PositiveInteger;
  scientificFacts: { [key: string]: JsonValue };
  executionState: { [key: string]: JsonValue };
  sourceRefs: string[];
  createdAt: ISODateTime;
}

export interface CompletionDecision extends ProjectCompletionDecisionDetails {
  completionDecisionId: string;
  scientificStateVersion: PositiveInteger;
  disposition: CompletionDisposition;
  sourceRefs: string[];
  decidedAt: ISODateTime;
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
  validatedAt: ISODateTime;
}

export interface FinalResult {
  finalResultId: string;
  completionDecisionId: string;
  scientificStateVersion: PositiveInteger;
  disposition: CompletionDisposition;
  directAnswer: string | null;
  keyFindings: string[];
  evidenceRefs: string[];
  /** v3 canonical fields; optional only so raw persisted v1/v2 records remain readable before adapter defaulting. */
  scientificResult?: { [key: string]: JsonValue };
  scientificSourceRefs?: string[];
  uncertainty: string[];
  gaps: string[];
  limitations: string[];
  /** Historical v2 snapshot; optional on raw v1 records. */
  acceptedScientificFacts?: { [key: string]: JsonValue };
  validatedAt: ISODateTime;
  validationOutcome: FinalResultValidationOutcome | null;
}

export interface ResearchTaskV12 {
  taskId: string;
  revision: NonNegativeInteger;
  status: ResearchTaskStatusV12;
  executionMode: ResearchTaskExecutionModeV12;
  pendingExecutionMode: ResearchTaskExecutionModeV12 | null;
  executionPolicyVersion: string;
  activeResearchDesignRef: ResearchDesignRef | null;
  registrySnapshotRef: RegistrySnapshotRef;
  planningAttempts: PlanningAttempt[];
  researchDesignVersions: ResearchDesignVersion[];
  reentryExecutionInstances: ReentryExecutionInstance[];
  substageRuntimeStates: SubstageRuntimeState[];
  executionDecisions: ExecutionDecision[];
  workUnits: WorkUnit[];
  capabilityCalls: CapabilityCallV12[];
  results: ResultV1[];
  artifacts: ArtifactV12[];
  confirmations: ConfirmationV12[];
  agentInteractionRecords: AgentInteractionRecord[];
  progressProjection: { [key: string]: JsonValue } | null;
  finalResultAuthority: FinalResultAuthority | null;
  scientificStates: ScientificStateV1[];
  completionDecisions: CompletionDecision[];
  finalResults: FinalResult[];
  startedAt: ISODateTime | null;
  finishedAt: ISODateTime | null;
}

export interface LegacySubstageProjection {
  substageInstanceId: string;
  stageKey: StageKey;
  substageKey: string;
  sequence: NonNegativeInteger;
}

export interface LegacyWorkUnitProjection {
  workUnitId: string;
  sourceCapabilityCallId: string | null;
  artifactId: string | null;
  substageInstanceId: string | null;
  responsibleActorRef: ActorRef;
  executorRef: ExecutorRef;
  sourceMode: "legacy_projection";
  dispatchable: false;
}

export interface LegacyTaskProjection {
  taskId: string;
  researchDesignRef: ResearchDesignRef;
  substageInstances: LegacySubstageProjection[];
  workUnits: LegacyWorkUnitProjection[];
  collaborationNodes: { [key: string]: JsonValue }[];
}

type JsonRecord = { [key: string]: unknown };

export const researchOrchestrationRuntimeValidatorNames = [
  "AcceptanceCriterion",
  "ActorRef",
  "AgentInteractionRecord",
  "AgentRef",
  "ArtifactRequirement",
  "ArtifactRequirementTemplate",
  "ArtifactV12",
  "AuthorizationSnapshot",
  "BindingRef",
  "BudgetBoundary",
  "BudgetSnapshot",
  "CandidateEvaluation",
  "CandidatePreference",
  "CapabilityCallV12",
  "CapabilityRef",
  "CompletionPolicy",
  "CompletionPolicyTemplate",
  "ConfirmationV12",
  "CoverageClaim",
  "CoverageTarget",
  "DispatchRecord",
  "ExecutionAssignment",
  "ExecutionCandidateRef",
  "ExecutionDecision",
  "ExecutorRef",
  "ExternalActionBoundary",
  "InstanceRef",
  "InteractionVerdict",
  "JsonObject",
  "MultiAgentRecipeDefinition",
  "MultiAgentRecipeRef",
  "PlanningAttempt",
  "PlanningComplexitySnapshot",
  "ProgressPolicy",
  "RecipeAssignmentTemplate",
  "RegistrySnapshotRef",
  "ResearchDesignCoverageReport",
  "ResearchDesignRef",
  "ResearchDesignVersion",
  "ReentryExecutionInstance",
  "RepeatableDefinitionReentryAuthority",
  "ResearchTaskV12",
  "ResearchUnderstanding",
  "ResultAssemblyPolicy",
  "ResultAssemblyPolicyTemplate",
  "RiskBoundary",
  "RiskSnapshot",
  "RuntimeReadinessSnapshot",
  "ScopeBoundary",
  "SemanticFitEvidence",
  "SkillRef",
  "SubstageDependency",
  "SubstageInstance",
  "SubstageRuntimeState",
  "WorkUnit",
] as const;

type RuntimeValidatorName = (typeof researchOrchestrationRuntimeValidatorNames)[number];
const observedRuntimeValidators = new Set<RuntimeValidatorName>();

function markRuntimeValidator(name: RuntimeValidatorName): void {
  observedRuntimeValidators.add(name);
}

export function resetResearchOrchestrationRuntimeValidatorCoverage(): void {
  observedRuntimeValidators.clear();
}

export function getResearchOrchestrationRuntimeValidatorCoverage(): string[] {
  return [...observedRuntimeValidators].sort();
}

function requireRecord(value: unknown, path: string): JsonRecord {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${path} must be an object`);
  }
  return value as JsonRecord;
}

function requireExactRecord(value: unknown, path: string, fields: readonly string[], optionalFields: readonly string[] = []): JsonRecord {
  const record = requireRecord(value, path);
  const expected = new Set([...fields, ...optionalFields]);
  for (const key of Object.keys(record)) {
    if (!expected.has(key)) {
      throw new Error(`${path}.${key} is not permitted`);
    }
  }
  for (const field of fields) {
    requireField(record, field, path);
  }
  return record;
}

function requireField(record: JsonRecord, field: string, path: string): unknown {
  if (!(field in record)) {
    throw new Error(`${path}.${field} is required`);
  }
  return record[field];
}

function requireString(value: unknown, path: string): string {
  if (typeof value !== "string") {
    throw new Error(`${path} must be a string`);
  }
  return value;
}

function requireNonEmptyString(value: unknown, path: string): string {
  const result = requireString(value, path);
  if (result.length === 0) {
    throw new Error(`${path} must be non-empty`);
  }
  return result;
}

function requireNullableNonEmptyString(value: unknown, path: string): string | null {
  return value === null ? null : requireNonEmptyString(value, path);
}

function requireBoolean(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") {
    throw new Error(`${path} must be a boolean`);
  }
  return value;
}

function requireNumber(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${path} must be a finite number`);
  }
  return value;
}

function requireNonNegativeNumber(value: unknown, path: string): number {
  const result = requireNumber(value, path);
  if (result < 0) {
    throw new Error(`${path} must be non-negative`);
  }
  return result;
}

function requireArray(value: unknown, path: string): unknown[] {
  if (!Array.isArray(value)) {
    throw new Error(`${path} must be an array`);
  }
  return value;
}

function requireNonEmptyStrings(value: unknown, path: string): string[] {
  return requireArray(value, path).map((item, index) => requireNonEmptyString(item, `${path}[${index}]`));
}

function requireEnum(value: unknown, allowed: readonly string[], path: string): string {
  const result = requireString(value, path);
  if (!allowed.includes(result)) {
    throw new Error(`${path} has an invalid enum value`);
  }
  return result;
}

function requireNullableEnum(value: unknown, allowed: readonly string[], path: string): string | null {
  return value === null ? null : requireEnum(value, allowed, path);
}

function assertObjectArray(
  value: unknown,
  path: string,
  validator: (item: unknown, itemPath: string) => void,
): void {
  for (const [index, item] of requireArray(value, path).entries()) {
    validator(item, `${path}[${index}]`);
  }
}

function assertPositiveInteger(value: unknown, path: string): void {
  positiveInteger(requireNumber(value, path));
}

function assertNonNegativeInteger(value: unknown, path: string): void {
  nonNegativeInteger(requireNumber(value, path));
}

function assertAwareTimestamp(value: unknown, path: string): void {
  awareDateTime(requireString(value, path));
}

function assertNullableAwareTimestamp(value: unknown, path: string): void {
  if (value !== null) {
    assertAwareTimestamp(value, path);
  }
}

function assertNullablePositiveInteger(value: unknown, path: string): void {
  if (value !== null) {
    assertPositiveInteger(value, path);
  }
}

function assertNullableNonNegativeInteger(value: unknown, path: string): void {
  if (value !== null) {
    assertNonNegativeInteger(value, path);
  }
}

function assertNullableNonNegativeNumber(value: unknown, path: string): void {
  if (value !== null) {
    requireNonNegativeNumber(value, path);
  }
}

function assertJsonValue(value: unknown, path: string): void {
  if (value === null || typeof value === "string" || typeof value === "boolean") {
    return;
  }
  if (typeof value === "number") {
    requireNumber(value, path);
    return;
  }
  if (Array.isArray(value)) {
    for (const [index, item] of value.entries()) {
      assertJsonValue(item, `${path}[${index}]`);
    }
    return;
  }
  const record = requireRecord(value, path);
  for (const [key, item] of Object.entries(record)) {
    assertJsonValue(item, `${path}.${key}`);
  }
}

function assertJsonObject(value: unknown, path: string): void {
  markRuntimeValidator("JsonObject");
  const record = requireRecord(value, path);
  for (const [key, item] of Object.entries(record)) {
    assertJsonValue(item, `${path}.${key}`);
  }
}

function assertInstanceRef(value: unknown, path: string): void {
  markRuntimeValidator("InstanceRef");
  const record = requireExactRecord(value, path, ["substageInstanceId", "instanceRevision"]);
  requireNonEmptyString(record.substageInstanceId, `${path}.substageInstanceId`);
  assertPositiveInteger(record.instanceRevision, `${path}.instanceRevision`);
}

function assertResearchDesignRef(value: unknown, path: string): void {
  markRuntimeValidator("ResearchDesignRef");
  const record = requireExactRecord(value, path, ["researchDesignId", "version"]);
  requireNonEmptyString(record.researchDesignId, `${path}.researchDesignId`);
  assertPositiveInteger(record.version, `${path}.version`);
}

function assertRegistrySnapshot(value: unknown, path: string): void {
  markRuntimeValidator("RegistrySnapshotRef");
  const record = requireExactRecord(value, path, ["schemaVersion", "contentHash"]);
  assertPositiveInteger(record.schemaVersion, `${path}.schemaVersion`);
  sha256Hash(requireString(record.contentHash, `${path}.contentHash`));
}

function assertMultiAgentRecipeRef(value: unknown, path: string): void {
  markRuntimeValidator("MultiAgentRecipeRef");
  const record = requireExactRecord(value, path, ["recipeId", "recipeVersion"]);
  requireNonEmptyString(record.recipeId, `${path}.recipeId`);
  assertPositiveInteger(record.recipeVersion, `${path}.recipeVersion`);
}

function assertAgentRef(value: unknown, path: string): void {
  markRuntimeValidator("AgentRef");
  const record = requireExactRecord(value, path, ["agentId", "definitionVersion"]);
  requireNonEmptyString(record.agentId, `${path}.agentId`);
  assertPositiveInteger(record.definitionVersion, `${path}.definitionVersion`);
}

function assertSkillRef(value: unknown, path: string): void {
  markRuntimeValidator("SkillRef");
  const record = requireExactRecord(value, path, ["skillId", "definitionVersion"]);
  requireNonEmptyString(record.skillId, `${path}.skillId`);
  assertPositiveInteger(record.definitionVersion, `${path}.definitionVersion`);
}

function assertCapabilityRef(value: unknown, path: string): void {
  markRuntimeValidator("CapabilityRef");
  const record = requireRecord(value, path);
  if ("agentId" in record) {
    assertAgentRef(value, path);
  } else if ("skillId" in record) {
    assertSkillRef(value, path);
  } else {
    throw new Error(`${path} has an invalid CapabilityRef discriminator`);
  }
}

function assertExecutionCandidateRef(value: unknown, path: string): void {
  markRuntimeValidator("ExecutionCandidateRef");
  const record = requireRecord(value, path);
  if ("agentId" in record) {
    assertAgentRef(value, path);
  } else if ("skillId" in record) {
    assertSkillRef(value, path);
  } else if ("recipeId" in record) {
    assertMultiAgentRecipeRef(value, path);
  } else {
    throw new Error(`${path} has an invalid ExecutionCandidateRef discriminator`);
  }
}

function assertBindingRef(value: unknown, path: string): void {
  markRuntimeValidator("BindingRef");
  const record = requireExactRecord(value, path, ["bindingId", "bindingVersion"]);
  requireNonEmptyString(record.bindingId, `${path}.bindingId`);
  assertPositiveInteger(record.bindingVersion, `${path}.bindingVersion`);
}

function assertResearchUnderstanding(value: unknown, path: string): void {
  markRuntimeValidator("ResearchUnderstanding");
  const record = requireExactRecord(value, path, [
    "researchType", "userIntent", "materialSummary", "primaryResearchQuestions",
    "auxiliaryResearchDimensions", "targetOutputs", "evidenceBoundaries",
    "missingInputs", "riskNotes", "planningRationale",
  ]);
  requireNullableNonEmptyString(record.researchType, `${path}.researchType`);
  requireNonEmptyString(record.userIntent, `${path}.userIntent`);
  requireNullableNonEmptyString(record.materialSummary, `${path}.materialSummary`);
  const primaryResearchQuestions = requireNonEmptyStrings(
    record.primaryResearchQuestions,
    `${path}.primaryResearchQuestions`,
  );
  if (primaryResearchQuestions.length === 0) {
    throw new Error(`${path}.primaryResearchQuestions must contain at least one item`);
  }
  for (const field of [
    "auxiliaryResearchDimensions", "targetOutputs", "evidenceBoundaries", "missingInputs", "riskNotes",
  ]) {
    requireNonEmptyStrings(record[field], `${path}.${field}`);
  }
  requireNonEmptyString(record.planningRationale, `${path}.planningRationale`);
}

function assertPlanningComplexity(value: unknown, path: string): void {
  markRuntimeValidator("PlanningComplexitySnapshot");
  const record = requireExactRecord(value, path, [
    "problemClarity", "expectedStageSpan", "dependencyComplexity", "evidenceBurden",
    "externalActionRisk", "researchUncertainty", "rationale",
  ]);
  requireEnum(record.problemClarity, ["high", "medium", "low"], `${path}.problemClarity`);
  requireEnum(record.expectedStageSpan, ["one", "multiple", "unknown"], `${path}.expectedStageSpan`);
  for (const field of ["dependencyComplexity", "evidenceBurden", "externalActionRisk", "researchUncertainty"]) {
    requireEnum(record[field], ["low", "medium", "high"], `${path}.${field}`);
  }
  requireNonEmptyString(record.rationale, `${path}.rationale`);
}

function assertPlanningAttempt(value: unknown, path: string): void {
  markRuntimeValidator("PlanningAttempt");
  const record = requireExactRecord(value, path, [
    "planningAttemptId", "taskId", "requestId", "plannerCallIdempotencyKey", "plannerProviderCallRef",
    "additionalActionType", "additionalActionIdempotencyKey", "additionalProviderCallRef", "status",
    "startedAt", "finishedAt",
  ], ["userInputSnapshot", "planningSourceUsages"]);
  for (const field of ["planningAttemptId", "taskId", "requestId", "plannerCallIdempotencyKey"]) {
    requireNonEmptyString(record[field], `${path}.${field}`);
  }
  if (record.planningSourceUsages !== undefined) {
    assertPlanningSourceUsages(
      record.planningSourceUsages,
      `${path}.planningSourceUsages`,
      {
        requireExactRecord,
        requireNonEmptyString,
        requireNonEmptyStrings,
      },
    );
  }
  if (record.userInputSnapshot !== undefined) {
    const snapshot = requireExactRecord(record.userInputSnapshot, `${path}.userInputSnapshot`, ["sources", "baseResearchDesignRef"]);
    assertObjectArray(snapshot.sources, `${path}.userInputSnapshot.sources`, (value, sourcePath) => {
      const source = requireExactRecord(value, sourcePath, ["sourceKind", "sourceRef", "content"]);
      requireEnum(source.sourceKind, ["user_request", "user_revision", "plan_revision"], `${sourcePath}.sourceKind`);
      requireNonEmptyString(source.sourceRef, `${sourcePath}.sourceRef`);
      requireNonEmptyString(source.content, `${sourcePath}.content`);
    });
    const sources = snapshot.sources as JsonRecord[];
    if (!sources.length || sources[0].sourceKind !== "user_request"
        || sources.slice(1).some(row => row.sourceKind === "user_request")
        || new Set(sources.map(row => row.sourceRef)).size !== sources.length) {
      throw new Error(`${path}.userInputSnapshot has invalid source lineage`);
    }
    if (snapshot.baseResearchDesignRef !== null) {
      assertResearchDesignRef(snapshot.baseResearchDesignRef, `${path}.userInputSnapshot.baseResearchDesignRef`);
    }
  }
  requireNullableNonEmptyString(record.plannerProviderCallRef, `${path}.plannerProviderCallRef`);
  requireEnum(record.additionalActionType, ["none", "semantic_adequacy", "planner_repair"], `${path}.additionalActionType`);
  requireNullableNonEmptyString(record.additionalActionIdempotencyKey, `${path}.additionalActionIdempotencyKey`);
  requireNullableNonEmptyString(record.additionalProviderCallRef, `${path}.additionalProviderCallRef`);
  requireEnum(record.status, ["started", "completed", "failed"], `${path}.status`);
  assertAwareTimestamp(record.startedAt, `${path}.startedAt`);
  assertNullableAwareTimestamp(record.finishedAt, `${path}.finishedAt`);
}

function assertAcceptanceCriterion(value: unknown, path: string): void {
  markRuntimeValidator("AcceptanceCriterion");
  const record = requireExactRecord(value, path, [
    "criterionId", "description", "criterionKind", "coverageTargetIds", "requiredArtifactType",
    "judgmentRequirement", "evaluatorType", "evaluatorRef",
  ]);
  requireNonEmptyString(record.criterionId, `${path}.criterionId`);
  requireNonEmptyString(record.description, `${path}.description`);
  requireEnum(record.criterionKind, [
    "target_output", "artifact_quality", "constraint_compliance", "evidence_boundary_compliance",
    "external_action_guard", "risk_guard", "professional_judgment",
  ], `${path}.criterionKind`);
  requireNonEmptyStrings(record.coverageTargetIds, `${path}.coverageTargetIds`);
  requireNullableNonEmptyString(record.requiredArtifactType, `${path}.requiredArtifactType`);
  requireEnum(
    record.judgmentRequirement,
    ["deterministic", "research_synthesis", "domain_professional", "human_authority"],
    `${path}.judgmentRequirement`,
  );
  requireEnum(record.evaluatorType, ["deterministic", "agent", "human_gate"], `${path}.evaluatorType`);
  requireNullableNonEmptyString(record.evaluatorRef, `${path}.evaluatorRef`);
}

function assertSubstageDependency(value: unknown, path: string): void {
  markRuntimeValidator("SubstageDependency");
  const record = requireExactRecord(value, path, [
    "dependencyId", "upstreamInstanceRef", "conditionType", "requiredArtifactTypes", "confirmationTargetRef",
  ]);
  requireNonEmptyString(record.dependencyId, `${path}.dependencyId`);
  assertInstanceRef(record.upstreamInstanceRef, `${path}.upstreamInstanceRef`);
  requireEnum(record.conditionType, ["instance_completed", "artifact_usable", "confirmation_granted"], `${path}.conditionType`);
  requireNonEmptyStrings(record.requiredArtifactTypes, `${path}.requiredArtifactTypes`);
  requireNullableNonEmptyString(record.confirmationTargetRef, `${path}.confirmationTargetRef`);
}

function assertScopeBoundary(value: unknown, path: string): void {
  markRuntimeValidator("ScopeBoundary");
  const record = requireExactRecord(value, path, [
    "allowedObjectiveKinds", "excludedActions", "allowedMaterialRefs", "maxSubstageInstances",
  ]);
  for (const field of ["allowedObjectiveKinds", "excludedActions", "allowedMaterialRefs"]) {
    requireNonEmptyStrings(record[field], `${path}.${field}`);
  }
  assertNullablePositiveInteger(record.maxSubstageInstances, `${path}.maxSubstageInstances`);
}

function assertBudgetBoundary(value: unknown, path: string): void {
  markRuntimeValidator("BudgetBoundary");
  const record = requireExactRecord(value, path, [
    "maxModelCalls", "maxToolCalls", "maxWallTimeSec", "maxCostAmount", "currency",
  ]);
  for (const field of ["maxModelCalls", "maxToolCalls", "maxWallTimeSec"]) {
    assertNullableNonNegativeInteger(record[field], `${path}.${field}`);
  }
  assertNullableNonNegativeNumber(record.maxCostAmount, `${path}.maxCostAmount`);
  requireNullableNonEmptyString(record.currency, `${path}.currency`);
}

function assertExternalActionBoundary(value: unknown, path: string): void {
  markRuntimeValidator("ExternalActionBoundary");
  const record = requireExactRecord(value, path, [
    "allowedActionKinds", "confirmationRequiredKinds", "allowedDestinations",
  ]);
  for (const field of ["allowedActionKinds", "confirmationRequiredKinds", "allowedDestinations"]) {
    requireNonEmptyStrings(record[field], `${path}.${field}`);
  }
}

function assertRiskBoundary(value: unknown, path: string): void {
  markRuntimeValidator("RiskBoundary");
  const record = requireExactRecord(value, path, ["maxRiskClass", "prohibitedActions", "humanGateTriggers"]);
  requireEnum(record.maxRiskClass, ["low", "medium", "high"], `${path}.maxRiskClass`);
  requireNonEmptyStrings(record.prohibitedActions, `${path}.prohibitedActions`);
  requireNonEmptyStrings(record.humanGateTriggers, `${path}.humanGateTriggers`);
}

function assertAuthorizationSnapshot(value: unknown, path: string): void {
  markRuntimeValidator("AuthorizationSnapshot");
  const record = requireExactRecord(value, path, [
    "policyVersion", "allowedMemoryScopes", "allowedExternalActionKinds", "confirmationRefs",
  ]);
  requireNonEmptyString(record.policyVersion, `${path}.policyVersion`);
  for (const field of ["allowedMemoryScopes", "allowedExternalActionKinds", "confirmationRefs"]) {
    requireNonEmptyStrings(record[field], `${path}.${field}`);
  }
}

function assertBudgetSnapshot(value: unknown, path: string): void {
  markRuntimeValidator("BudgetSnapshot");
  const record = requireExactRecord(value, path, [
    "boundary", "consumedModelCalls", "consumedToolCalls", "consumedCostAmount",
  ]);
  assertBudgetBoundary(record.boundary, `${path}.boundary`);
  assertNonNegativeInteger(record.consumedModelCalls, `${path}.consumedModelCalls`);
  assertNonNegativeInteger(record.consumedToolCalls, `${path}.consumedToolCalls`);
  requireNonNegativeNumber(record.consumedCostAmount, `${path}.consumedCostAmount`);
}

function assertRiskSnapshot(value: unknown, path: string): void {
  markRuntimeValidator("RiskSnapshot");
  const record = requireExactRecord(value, path, ["boundary", "evaluatedRiskClass", "triggeredHumanGates"]);
  assertRiskBoundary(record.boundary, `${path}.boundary`);
  requireEnum(record.evaluatedRiskClass, ["low", "medium", "high"], `${path}.evaluatedRiskClass`);
  requireNonEmptyStrings(record.triggeredHumanGates, `${path}.triggeredHumanGates`);
}

function assertCompletionPolicy(value: unknown, path: string): void {
  markRuntimeValidator("CompletionPolicy");
  const record = requireExactRecord(value, path, [
    "requiredAssignmentIds", "successMode", "reviewerAssignmentIds", "reviewRequirement",
    "partialSuccessAllowed", "onReviewerReject",
  ]);
  requireNonEmptyStrings(record.requiredAssignmentIds, `${path}.requiredAssignmentIds`);
  requireEnum(record.successMode, ["all_required", "any_required"], `${path}.successMode`);
  requireNonEmptyStrings(record.reviewerAssignmentIds, `${path}.reviewerAssignmentIds`);
  requireEnum(record.reviewRequirement, ["none", "all_reviewers_approve", "any_reviewer_approves"], `${path}.reviewRequirement`);
  requireBoolean(record.partialSuccessAllowed, `${path}.partialSuccessAllowed`);
  requireEnum(record.onReviewerReject, ["new_revision_decision", "human_gate"], `${path}.onReviewerReject`);
}

function assertProgressPolicy(value: unknown, path: string): void {
  markRuntimeValidator("ProgressPolicy");
  const record = requireExactRecord(value, path, [
    "requiredAssignmentIds", "requiredArtifactTypes", "acceptedResultCodes",
  ]);
  requireNonEmptyStrings(record.requiredAssignmentIds, `${path}.requiredAssignmentIds`);
  requireNonEmptyStrings(record.requiredArtifactTypes, `${path}.requiredArtifactTypes`);
  requireEnum(record.acceptedResultCodes, ["progressed", "needs_input", "blocked_recoverable"], `${path}.acceptedResultCodes`);
}

function assertCompletionPolicyTemplate(value: unknown, path: string): void {
  markRuntimeValidator("CompletionPolicyTemplate");
  const record = requireExactRecord(value, path, [
    "requiredTemplateKeys", "successMode", "reviewerTemplateKeys", "reviewRequirement",
    "partialSuccessAllowed", "onReviewerReject",
  ]);
  requireNonEmptyStrings(record.requiredTemplateKeys, `${path}.requiredTemplateKeys`);
  requireEnum(record.successMode, ["all_required", "any_required"], `${path}.successMode`);
  requireNonEmptyStrings(record.reviewerTemplateKeys, `${path}.reviewerTemplateKeys`);
  requireEnum(record.reviewRequirement, ["none", "all_reviewers_approve", "any_reviewer_approves"], `${path}.reviewRequirement`);
  requireBoolean(record.partialSuccessAllowed, `${path}.partialSuccessAllowed`);
  requireEnum(record.onReviewerReject, ["new_revision_decision", "human_gate"], `${path}.onReviewerReject`);
}

function assertArtifactRequirementTemplate(value: unknown, path: string): void {
  markRuntimeValidator("ArtifactRequirementTemplate");
  const record = requireExactRecord(value, path, ["artifactType", "minimumCount", "sourceTemplateKeys"]);
  requireNonEmptyString(record.artifactType, `${path}.artifactType`);
  assertNonNegativeInteger(record.minimumCount, `${path}.minimumCount`);
  requireNonEmptyStrings(record.sourceTemplateKeys, `${path}.sourceTemplateKeys`);
}

function assertResultAssemblyPolicyTemplate(value: unknown, path: string): void {
  markRuntimeValidator("ResultAssemblyPolicyTemplate");
  const record = requireExactRecord(value, path, [
    "mode", "assemblerTemplateKey", "finalArtifactRequirements",
  ]);
  requireEnum(record.mode, ["primary_output", "designated_assembler", "artifact_set"], `${path}.mode`);
  requireNullableNonEmptyString(record.assemblerTemplateKey, `${path}.assemblerTemplateKey`);
  assertObjectArray(record.finalArtifactRequirements, `${path}.finalArtifactRequirements`, assertArtifactRequirementTemplate);
}

function assertArtifactRequirement(value: unknown, path: string): void {
  markRuntimeValidator("ArtifactRequirement");
  const record = requireExactRecord(value, path, ["artifactType", "minimumCount", "sourceAssignmentIds"]);
  requireNonEmptyString(record.artifactType, `${path}.artifactType`);
  assertNonNegativeInteger(record.minimumCount, `${path}.minimumCount`);
  requireNonEmptyStrings(record.sourceAssignmentIds, `${path}.sourceAssignmentIds`);
}

function assertResultAssemblyPolicy(value: unknown, path: string): void {
  markRuntimeValidator("ResultAssemblyPolicy");
  const record = requireExactRecord(value, path, [
    "mode", "assemblerAssignmentId", "finalArtifactRequirements",
  ]);
  requireEnum(record.mode, ["primary_output", "designated_assembler", "artifact_set"], `${path}.mode`);
  requireNullableNonEmptyString(record.assemblerAssignmentId, `${path}.assemblerAssignmentId`);
  assertObjectArray(record.finalArtifactRequirements, `${path}.finalArtifactRequirements`, assertArtifactRequirement);
}

function assertCoverageTarget(value: unknown, path: string): void {
  markRuntimeValidator("CoverageTarget");
  const record = requireExactRecord(value, path, [
    "targetId", "sourceType", "sourcePath", "description", "requiredArtifactTypes", "enforcementRef", "required",
  ]);
  requireNonEmptyString(record.targetId, `${path}.targetId`);
  requireEnum(record.sourceType, [
    "target_output", "constraint", "evidence_boundary", "external_action_boundary", "risk_boundary",
  ], `${path}.sourceType`);
  requireNonEmptyString(record.sourcePath, `${path}.sourcePath`);
  requireNonEmptyString(record.description, `${path}.description`);
  requireNonEmptyStrings(record.requiredArtifactTypes, `${path}.requiredArtifactTypes`);
  requireNullableNonEmptyString(record.enforcementRef, `${path}.enforcementRef`);
  requireBoolean(record.required, `${path}.required`);
}

function assertCoverageClaim(value: unknown, path: string): void {
  markRuntimeValidator("CoverageClaim");
  const record = requireExactRecord(value, path, [
    "targetId", "instanceRefs", "coverageMode", "acceptanceCriterionRefs", "derivationRuleRefs", "rationale",
  ]);
  requireNonEmptyString(record.targetId, `${path}.targetId`);
  assertObjectArray(record.instanceRefs, `${path}.instanceRefs`, assertInstanceRef);
  requireEnum(record.coverageMode, ["full", "partial", "none"], `${path}.coverageMode`);
  requireNonEmptyStrings(record.acceptanceCriterionRefs, `${path}.acceptanceCriterionRefs`);
  requireNonEmptyStrings(record.derivationRuleRefs, `${path}.derivationRuleRefs`);
  requireNonEmptyString(record.rationale, `${path}.rationale`);
}

function assertCoverageReport(value: unknown, path: string): void {
  markRuntimeValidator("ResearchDesignCoverageReport");
  const record = requireExactRecord(value, path, [
    "coverageReportId", "designRef", "planningAttemptId", "targets", "claims", "uncoveredRequiredTargetIds",
    "overloadedInstanceRefs", "missingDependencyRefs", "status", "evaluationTier", "evaluationMode",
    "semanticEvaluationRef", "automaticRepairCount", "policyVersion", "evaluatedAt",
  ]);
  requireNonEmptyString(record.coverageReportId, `${path}.coverageReportId`);
  assertResearchDesignRef(record.designRef, `${path}.designRef`);
  requireNonEmptyString(record.planningAttemptId, `${path}.planningAttemptId`);
  assertObjectArray(record.targets, `${path}.targets`, assertCoverageTarget);
  assertObjectArray(record.claims, `${path}.claims`, assertCoverageClaim);
  requireNonEmptyStrings(record.uncoveredRequiredTargetIds, `${path}.uncoveredRequiredTargetIds`);
  assertObjectArray(record.overloadedInstanceRefs, `${path}.overloadedInstanceRefs`, assertInstanceRef);
  requireNonEmptyStrings(record.missingDependencyRefs, `${path}.missingDependencyRefs`);
  requireEnum(record.status, ["passed_structural", "passed_semantic", "executable_with_gaps", "needs_revision"], `${path}.status`);
  if (record.status === "executable_with_gaps") {
    const targets = record.targets as CoverageTarget[];
    const claims = record.claims as CoverageClaim[];
    const uncovered = record.uncoveredRequiredTargetIds as string[];
    const gaps = claims.filter((claim) => claim.coverageMode !== "full");
    if (
      uncovered.length === 0 ||
      (record.overloadedInstanceRefs as unknown[]).length !== 0 ||
      (record.missingDependencyRefs as unknown[]).length !== 0 ||
      new Set(targets.map((target) => target.targetId)).size !== targets.length ||
      new Set(claims.map((claim) => claim.targetId)).size !== claims.length ||
      targets.length !== claims.length ||
      targets.some((target) => !claims.some((claim) => claim.targetId === target.targetId)) ||
      targets.some((target) => target.sourceType !== "target_output" &&
        !claims.some((claim) => claim.targetId === target.targetId &&
          claim.instanceRefs.length > 0 && claim.acceptanceCriterionRefs.length > 0)) ||
      gaps.length !== new Set(uncovered).size ||
      gaps.some((claim) => !uncovered.includes(claim.targetId) ||
        !targets.some((target) => target.targetId === claim.targetId && target.sourceType === "target_output"))
    ) throw new Error(`${path} executable gaps must be limited to target outputs`);
  }
  requireEnum(record.evaluationTier, ["lightweight", "partial", "sdr"], `${path}.evaluationTier`);
  requireEnum(record.evaluationMode, ["deterministic", "semantic_assisted"], `${path}.evaluationMode`);
  requireNullableNonEmptyString(record.semanticEvaluationRef, `${path}.semanticEvaluationRef`);
  assertNonNegativeInteger(record.automaticRepairCount, `${path}.automaticRepairCount`);
  requireNonEmptyString(record.policyVersion, `${path}.policyVersion`);
  assertAwareTimestamp(record.evaluatedAt, `${path}.evaluatedAt`);
}

function assertRepeatableDefinitionReentryAuthority(value: unknown, path: string): void {
  markRuntimeValidator("RepeatableDefinitionReentryAuthority");
  const record = requireExactRecord(value, path, ["policyVersion", "triggerDefinitionIds"]);
  requireEnum(record.policyVersion, ["repeatable_definition_reentry_v1"], `${path}.policyVersion`);
  const triggerDefinitionIds = requireNonEmptyStrings(record.triggerDefinitionIds, `${path}.triggerDefinitionIds`);
  if (triggerDefinitionIds.length === 0) {
    throw new Error(`${path}.triggerDefinitionIds must contain at least one definition id`);
  }
  if (new Set(triggerDefinitionIds).size !== triggerDefinitionIds.length) {
    throw new Error(`${path}.triggerDefinitionIds must be unique`);
  }
}

function assertSubstageInstance(value: unknown, path: string): void {
  markRuntimeValidator("SubstageInstance");
  const record = requireExactRecord(value, path, [
    "substageInstanceId", "instanceRevision", "substageDefinitionId", "substageDefinitionVersion", "stageKey",
    "objective", "inputRefs", "expectedArtifactTypes", "acceptanceCriteria", "dependencies", "sequenceHint",
    "repeatableReentryAuthority",
  ]);
  requireNonEmptyString(record.substageInstanceId, `${path}.substageInstanceId`);
  assertPositiveInteger(record.instanceRevision, `${path}.instanceRevision`);
  requireNonEmptyString(record.substageDefinitionId, `${path}.substageDefinitionId`);
  assertPositiveInteger(record.substageDefinitionVersion, `${path}.substageDefinitionVersion`);
  requireEnum(record.stageKey, ["problem_modeling", "solution_design", "execution_validation", "insight_generation"], `${path}.stageKey`);
  requireNonEmptyString(record.objective, `${path}.objective`);
  requireNonEmptyStrings(record.inputRefs, `${path}.inputRefs`);
  requireNonEmptyStrings(record.expectedArtifactTypes, `${path}.expectedArtifactTypes`);
  assertObjectArray(record.acceptanceCriteria, `${path}.acceptanceCriteria`, assertAcceptanceCriterion);
  assertObjectArray(record.dependencies, `${path}.dependencies`, assertSubstageDependency);
  assertNullableNonNegativeInteger(record.sequenceHint, `${path}.sequenceHint`);
  if (record.repeatableReentryAuthority !== null) {
    assertRepeatableDefinitionReentryAuthority(
      record.repeatableReentryAuthority,
      `${path}.repeatableReentryAuthority`,
    );
  }
}

function assertReentryExecutionInstance(value: unknown, path: string): void {
  markRuntimeValidator("ReentryExecutionInstance");
  const record = requireExactRecord(value, path, [
    "researchDesignVersion", "instance", "templateInstanceRef", "triggeringScientificStateVersion",
    "triggeringExecutionDecisionId", "previousRelevantInstanceRef", "triggeringStateSourceRefs",
    "reentryFingerprint", "createdAt",
  ]);
  assertPositiveInteger(record.researchDesignVersion, `${path}.researchDesignVersion`);
  assertSubstageInstance(record.instance, `${path}.instance`);
  const instance = requireRecord(record.instance, `${path}.instance`);
  if (instance.repeatableReentryAuthority === null) {
    throw new Error(`${path}.instance requires frozen repeatable re-entry authority`);
  }
  assertInstanceRef(record.templateInstanceRef, `${path}.templateInstanceRef`);
  assertPositiveInteger(record.triggeringScientificStateVersion, `${path}.triggeringScientificStateVersion`);
  requireNonEmptyString(record.triggeringExecutionDecisionId, `${path}.triggeringExecutionDecisionId`);
  assertInstanceRef(record.previousRelevantInstanceRef, `${path}.previousRelevantInstanceRef`);
  const sourceRefs = requireNonEmptyStrings(record.triggeringStateSourceRefs, `${path}.triggeringStateSourceRefs`);
  if (sourceRefs.length === 0) {
    throw new Error(`${path}.triggeringStateSourceRefs must contain at least one source ref`);
  }
  if (new Set(sourceRefs).size !== sourceRefs.length) {
    throw new Error(`${path}.triggeringStateSourceRefs must be unique`);
  }
  requireNonEmptyString(record.reentryFingerprint, `${path}.reentryFingerprint`);
  assertAwareTimestamp(record.createdAt, `${path}.createdAt`);

  const newInstanceId = requireNonEmptyString(instance.substageInstanceId, `${path}.instance.substageInstanceId`);
  const newInstanceRevision = instance.instanceRevision;
  for (const refField of ["templateInstanceRef", "previousRelevantInstanceRef"] as const) {
    const ref = requireRecord(record[refField], `${path}.${refField}`);
    if (ref.substageInstanceId === newInstanceId && ref.instanceRevision === newInstanceRevision) {
      throw new Error(`${path}.instance requires a new identity`);
    }
  }
}

function assertWorkflowProvenanceRef(value: unknown, path: string): void {
  if (value === null) return;
  const provenance = requireExactRecord(value, path, ["workflowId", "workflowVersion", "contentHash"]);
  requireNonEmptyString(provenance.workflowId, `${path}.workflowId`);
  assertPositiveInteger(provenance.workflowVersion, `${path}.workflowVersion`);
  sha256Hash(requireString(provenance.contentHash, `${path}.contentHash`));
}

function assertProjectCompletionRequirements(value: unknown, targetOutputs: string[], path: string): void {
  validateCompletionRequirements(value, targetOutputs, path, { requireExactRecord, requireNonEmptyString });
}

function assertDesign(value: unknown, path: string): void {
  markRuntimeValidator("ResearchDesignVersion");
  const record = requireExactRecord(value, path, [
    "researchDesignId", "taskId", "version", "supersedesVersion", "status", "planningMode",
    "planningComplexity", "goal", "understanding", "constraints", "scopeBoundary", "budgetBoundary",
    "externalActionBoundary", "riskBoundary", "workflowProvenanceRef", "projectCompletionRequirements",
    "instances", "coverageReport", "createdAt", "confirmedAt", "confirmationRef",
  ], ["workflowResponsibilityCoverage"]);
  if (record.workflowResponsibilityCoverage !== undefined) {
    assertWorkflowCoverage(
      record.workflowResponsibilityCoverage, `${path}.workflowResponsibilityCoverage`,
      requireExactRecord, requireNonEmptyString, requireNonEmptyStrings, assertInstanceRef,
    );
  }
  requireNonEmptyString(record.researchDesignId, `${path}.researchDesignId`);
  requireNonEmptyString(record.taskId, `${path}.taskId`);
  assertPositiveInteger(record.version, `${path}.version`);
  assertNullablePositiveInteger(record.supersedesVersion, `${path}.supersedesVersion`);
  requireEnum(record.status, ["draft", "confirmed", "superseded", "cancelled"], `${path}.status`);
  requireEnum(record.planningMode, ["lightweight", "partial", "sdr"], `${path}.planningMode`);
  assertPlanningComplexity(record.planningComplexity, `${path}.planningComplexity`);
  requireNonEmptyString(record.goal, `${path}.goal`);
  assertResearchUnderstanding(record.understanding, `${path}.understanding`);
  const understanding = requireRecord(record.understanding, `${path}.understanding`);
  const targetOutputs = requireNonEmptyStrings(
    understanding.targetOutputs,
    `${path}.understanding.targetOutputs`,
  );
  requireNonEmptyStrings(record.constraints, `${path}.constraints`);
  assertScopeBoundary(record.scopeBoundary, `${path}.scopeBoundary`);
  assertBudgetBoundary(record.budgetBoundary, `${path}.budgetBoundary`);
  assertExternalActionBoundary(record.externalActionBoundary, `${path}.externalActionBoundary`);
  assertRiskBoundary(record.riskBoundary, `${path}.riskBoundary`);
  assertWorkflowProvenanceRef(record.workflowProvenanceRef, `${path}.workflowProvenanceRef`);
  assertProjectCompletionRequirements(
    record.projectCompletionRequirements,
    targetOutputs,
    `${path}.projectCompletionRequirements`,
  );
  assertObjectArray(record.instances, `${path}.instances`, assertSubstageInstance);
  const coverage = record.coverageReport;
  if (coverage !== null) {
    assertCoverageReport(coverage, `${path}.coverageReport`);
  }
  assertTargetContributions(record, path);
  assertAwareTimestamp(record.createdAt, `${path}.createdAt`);
  assertNullableAwareTimestamp(record.confirmedAt, `${path}.confirmedAt`);
  requireNullableNonEmptyString(record.confirmationRef, `${path}.confirmationRef`);
}

function assertSubstageRuntimeState(value: unknown, path: string): void {
  markRuntimeValidator("SubstageRuntimeState");
  const record = requireExactRecord(value, path, [
    "substageInstanceId", "instanceRevision", "status", "activeExecutionDecisionId", "workUnitIds",
    "artifactRefs", "statusReasonCode", "updatedAt",
  ]);
  requireNonEmptyString(record.substageInstanceId, `${path}.substageInstanceId`);
  assertPositiveInteger(record.instanceRevision, `${path}.instanceRevision`);
  requireEnum(record.status, [
    "planned", "ready", "queued", "running", "reviewing", "needs_input", "blocked_recoverable",
    "insufficient_evidence", "completed", "failed", "cancelled", "skipped",
  ], `${path}.status`);
  requireNullableNonEmptyString(record.activeExecutionDecisionId, `${path}.activeExecutionDecisionId`);
  requireNonEmptyStrings(record.workUnitIds, `${path}.workUnitIds`);
  requireNonEmptyStrings(record.artifactRefs, `${path}.artifactRefs`);
  requireNullableNonEmptyString(record.statusReasonCode, `${path}.statusReasonCode`);
  assertAwareTimestamp(record.updatedAt, `${path}.updatedAt`);
}

function assertActorRef(value: unknown, path: string): void {
  markRuntimeValidator("ActorRef");
  const record = requireExactRecord(value, path, ["actorType", "actorId", "definitionVersion"]);
  const actorType = requireEnum(record.actorType, ["orchestrator", "agent"], `${path}.actorType`);
  requireNonEmptyString(record.actorId, `${path}.actorId`);
  assertNullablePositiveInteger(record.definitionVersion, `${path}.definitionVersion`);
  if ((actorType === "agent") !== (record.definitionVersion !== null)) {
    throw new Error(`${path}.definitionVersion does not match actorType`);
  }
}

function assertExecutorRef(value: unknown, path: string): void {
  markRuntimeValidator("ExecutorRef");
  const record = requireExactRecord(value, path, ["executorType", "executorId", "definitionVersion"]);
  const executorType = requireEnum(record.executorType, ["agent", "skill", "legacy_unknown"], `${path}.executorType`);
  const executorId = requireNonEmptyString(record.executorId, `${path}.executorId`);
  assertNullablePositiveInteger(record.definitionVersion, `${path}.definitionVersion`);
  if (executorType === "legacy_unknown") {
    if (executorId !== "legacy_unknown" || record.definitionVersion !== null) {
      throw new Error(`${path} has an invalid legacy_unknown identity`);
    }
  } else if (record.definitionVersion === null) {
    throw new Error(`${path}.definitionVersion is required for a live executor`);
  }
}

function assertRuntimeReadiness(value: unknown, path: string): void {
  markRuntimeValidator("RuntimeReadinessSnapshot");
  const record = requireExactRecord(value, path, [
    "capabilityRef", "ready", "runtimeBindingId", "checkedAt", "expiresAt", "reason",
  ]);
  assertCapabilityRef(record.capabilityRef, `${path}.capabilityRef`);
  requireBoolean(record.ready, `${path}.ready`);
  requireNullableNonEmptyString(record.runtimeBindingId, `${path}.runtimeBindingId`);
  assertAwareTimestamp(record.checkedAt, `${path}.checkedAt`);
  assertAwareTimestamp(record.expiresAt, `${path}.expiresAt`);
  requireNullableNonEmptyString(record.reason, `${path}.reason`);
}

function assertSemanticFitEvidence(value: unknown, path: string): void {
  markRuntimeValidator("SemanticFitEvidence");
  const record = requireExactRecord(value, path, [
    "objectiveMatch", "inputContractMatch", "outputContractMatch", "professionalJudgmentFit",
    "positiveRuleRefs", "negativeRuleRefs", "adapterRefs", "policyVersion", "rationale",
  ]);
  requireEnum(record.objectiveMatch, ["exact", "compatible", "none"], `${path}.objectiveMatch`);
  requireEnum(record.inputContractMatch, ["exact", "adapter_compatible", "none"], `${path}.inputContractMatch`);
  requireEnum(record.outputContractMatch, ["exact", "adapter_compatible", "partial", "none"], `${path}.outputContractMatch`);
  requireEnum(record.professionalJudgmentFit, ["required_supported", "not_required", "unsupported"], `${path}.professionalJudgmentFit`);
  for (const field of ["positiveRuleRefs", "negativeRuleRefs", "adapterRefs"]) {
    requireNonEmptyStrings(record[field], `${path}.${field}`);
  }
  requireNonEmptyString(record.policyVersion, `${path}.policyVersion`);
  requireNonEmptyString(record.rationale, `${path}.rationale`);
}

function assertCandidatePreference(value: unknown, path: string): void {
  markRuntimeValidator("CandidatePreference");
  const record = requireExactRecord(value, path, [
    "semanticFit", "semanticFitEvidence", "completionCoverage", "executableNow", "executionBlockerCodes",
    "riskClass", "estimatedCostAmount", "permissionBreadth", "supportsProfessionalJudgment",
    "activeWorkUnitCount", "lastAssignedAt",
  ]);
  requireEnum(record.semanticFit, ["exact", "compatible", "weak"], `${path}.semanticFit`);
  assertSemanticFitEvidence(record.semanticFitEvidence, `${path}.semanticFitEvidence`);
  requireEnum(record.completionCoverage, ["completion_capable", "progress_capable"], `${path}.completionCoverage`);
  requireBoolean(record.executableNow, `${path}.executableNow`);
  for (const [index, code] of requireArray(record.executionBlockerCodes, `${path}.executionBlockerCodes`).entries()) {
    requireEnum(code, [
      "required_input_missing", "authorization_denied", "risk_exceeded", "budget_exceeded",
      "runtime_unavailable", "provider_unavailable",
    ], `${path}.executionBlockerCodes[${index}]`);
  }
  requireEnum(record.riskClass, ["low", "medium", "high"], `${path}.riskClass`);
  assertNullableNonNegativeNumber(record.estimatedCostAmount, `${path}.estimatedCostAmount`);
  requireEnum(record.permissionBreadth, ["narrow", "standard", "broad"], `${path}.permissionBreadth`);
  requireBoolean(record.supportsProfessionalJudgment, `${path}.supportsProfessionalJudgment`);
  assertNonNegativeInteger(record.activeWorkUnitCount, `${path}.activeWorkUnitCount`);
  assertNullableAwareTimestamp(record.lastAssignedAt, `${path}.lastAssignedAt`);
}

function assertCandidateEvaluation(value: unknown, path: string): void {
  markRuntimeValidator("CandidateEvaluation");
  const record = requireExactRecord(value, path, [
    "candidateEvaluationId", "candidateRef", "sourceBindingRefs", "supportedRole", "eligible",
    "rejectionCodes", "preference", "rationale",
  ], ["executionMethodRoleEvaluations"]);
  if (record.executionMethodRoleEvaluations !== undefined) {
    assertExecutionMethodRoleEvaluations(
      record.executionMethodRoleEvaluations,
      `${path}.executionMethodRoleEvaluations`,
      methodReadbackValidators(),
    );
  }
  requireNonEmptyString(record.candidateEvaluationId, `${path}.candidateEvaluationId`);
  assertExecutionCandidateRef(record.candidateRef, `${path}.candidateRef`);
  assertObjectArray(record.sourceBindingRefs, `${path}.sourceBindingRefs`, assertBindingRef);
  requireNonEmptyString(record.supportedRole, `${path}.supportedRole`);
  requireBoolean(record.eligible, `${path}.eligible`);
  for (const [index, code] of requireArray(record.rejectionCodes, `${path}.rejectionCodes`).entries()) {
    requireEnum(code, [
      "disabled", "role_mismatch", "input_incompatible", "output_incompatible", "agent_skill_not_granted",
      "explicitly_not_for", "semantic_fit_insufficient",
    ], `${path}.rejectionCodes[${index}]`);
  }
  assertCandidatePreference(record.preference, `${path}.preference`);
  requireNonEmptyString(record.rationale, `${path}.rationale`);
}

function assertExecutionAssignment(value: unknown, path: string): void {
  markRuntimeValidator("ExecutionAssignment");
  assertExecutionAssignmentContract(value, path, methodReadbackValidators());
}

function assertDecision(value: unknown, path: string): void {
  markRuntimeValidator("ExecutionDecision");
  const record = requireExactRecord(value, path, [
    "executionDecisionId", "selectionRequestId", "decisionSequence", "supersedesExecutionDecisionId",
    "revisionReasonCode", "taskId", "researchDesignVersion", "substageInstanceId", "substageInstanceRevision",
    "registrySnapshotRef", "purpose", "strategy", "multiAgentRecipeRef", "candidateEvaluations",
    "fallbackInvoked", "fallbackReasonCode", "preferredCandidateEvaluationRefs", "assignments",
    "invocationContext", "completionPolicy", "progressPolicy", "resultAssemblyPolicy", "runtimeReadiness", "budgetSnapshot",
    "policyVersion", "rationale", "status", "outcome", "decidedAt", "closedAt",
  ], ["assignmentPreflightOutcomes", "executionMethodRef"]);
  for (const field of ["executionDecisionId", "selectionRequestId", "taskId", "substageInstanceId", "policyVersion", "rationale"]) {
    requireNonEmptyString(record[field], `${path}.${field}`);
  }
  for (const field of ["decisionSequence", "researchDesignVersion", "substageInstanceRevision"]) {
    assertPositiveInteger(record[field], `${path}.${field}`);
  }
  requireNullableNonEmptyString(record.supersedesExecutionDecisionId, `${path}.supersedesExecutionDecisionId`);
  requireNullableNonEmptyString(record.revisionReasonCode, `${path}.revisionReasonCode`);
  assertRegistrySnapshot(record.registrySnapshotRef, `${path}.registrySnapshotRef`);
  requireEnum(record.purpose, ["completion", "progress"], `${path}.purpose`);
  requireEnum(
    record.strategy,
    ["direct_skill", "skill_composition", "execution_method", "specialist_agent", "general_research_agent", "multi_agent"],
    `${path}.strategy`,
  );
  if (record.multiAgentRecipeRef !== null) {
    assertMultiAgentRecipeRef(record.multiAgentRecipeRef, `${path}.multiAgentRecipeRef`);
  }
  assertObjectArray(record.candidateEvaluations, `${path}.candidateEvaluations`, assertCandidateEvaluation);
  requireBoolean(record.fallbackInvoked, `${path}.fallbackInvoked`);
  requireNullableEnum(record.fallbackReasonCode, [
    "no_completion_coverage_preferred_candidate", "preferred_not_executable_inputs",
    "preferred_not_executable_authorization", "preferred_not_executable_budget", "preferred_not_executable_risk",
    "preferred_not_executable_provider", "preferred_not_executable_runtime",
  ], `${path}.fallbackReasonCode`);
  requireNonEmptyStrings(record.preferredCandidateEvaluationRefs, `${path}.preferredCandidateEvaluationRefs`);
  assertObjectArray(record.assignments, `${path}.assignments`, assertExecutionAssignment);
  if (
    record.executionMethodRef !== undefined &&
    record.executionMethodRef !== null
  ) {
    assertExecutionMethodReference(
      record.executionMethodRef,
      `${path}.executionMethodRef`,
      methodReadbackValidators(),
    );
  }
  if (record.assignmentPreflightOutcomes !== undefined) {
    assertObjectArray(record.assignmentPreflightOutcomes, `${path}.assignmentPreflightOutcomes`, (value, outcomePath) => {
      const row = requireExactRecord(value, outcomePath, ["assignmentId", "failureKind", "reasonCode", "fieldPaths", "inputArtifactSnapshots", "inputScientificStateVersion", "checkedAt"]);
      requireNonEmptyString(row.assignmentId, `${outcomePath}.assignmentId`);
      requireEnum(row.failureKind, ["material_unavailable", "contract_violation"], `${outcomePath}.failureKind`);
      requireNonEmptyString(row.reasonCode, `${outcomePath}.reasonCode`);
      requireNonEmptyStrings(row.fieldPaths, `${outcomePath}.fieldPaths`);
      assertObjectArray(row.inputArtifactSnapshots, `${outcomePath}.inputArtifactSnapshots`, assertCanonicalArtifactSnapshot);
      if (row.inputScientificStateVersion !== null) {
        assertPositiveInteger(row.inputScientificStateVersion, `${outcomePath}.inputScientificStateVersion`);
      }
      assertAwareTimestamp(row.checkedAt, `${outcomePath}.checkedAt`);
    });
    const outcomes = record.assignmentPreflightOutcomes as JsonRecord[];
    const assignments = record.assignments as JsonRecord[];
    if ((outcomes.length > 0 && record.strategy !== "execution_method")
        || new Set(outcomes.map(row => row.assignmentId)).size !== outcomes.length
        || outcomes.some(row => !assignments.some(item => item.assignmentId === row.assignmentId))) {
      throw new Error(`${path}.assignmentPreflightOutcomes has invalid assignment identity`);
    }
  }
  assertJsonObject(record.invocationContext, `${path}.invocationContext`);
  if (record.completionPolicy !== null) {
    assertCompletionPolicy(record.completionPolicy, `${path}.completionPolicy`);
  }
  if (record.progressPolicy !== null) {
    assertProgressPolicy(record.progressPolicy, `${path}.progressPolicy`);
  }
  assertResultAssemblyPolicy(record.resultAssemblyPolicy, `${path}.resultAssemblyPolicy`);
  assertObjectArray(record.runtimeReadiness, `${path}.runtimeReadiness`, assertRuntimeReadiness);
  assertBudgetSnapshot(record.budgetSnapshot, `${path}.budgetSnapshot`);
  requireEnum(record.status, ["proposed", "selected", "dispatched", "closed", "superseded", "cancelled"], `${path}.status`);
  requireNullableEnum(record.outcome, [
    "succeeded", "partial", "progressed", "needs_input", "rejected", "blocked", "failed", "cancelled",
  ], `${path}.outcome`);
  assertAwareTimestamp(record.decidedAt, `${path}.decidedAt`);
  assertNullableAwareTimestamp(record.closedAt, `${path}.closedAt`);
}

function assertCanonicalArtifactSnapshot(value: unknown, path: string): void {
  const record = requireExactRecord(value, path, [
    "artifactId", "artifactType", "version", "contentSha256",
  ]);
  requireNonEmptyString(record.artifactId, `${path}.artifactId`);
  requireNonEmptyString(record.artifactType, `${path}.artifactType`);
  assertPositiveInteger(record.version, `${path}.version`);
  const digest = requireNullableNonEmptyString(record.contentSha256, `${path}.contentSha256`);
  if (digest !== null && !/^[0-9a-f]{64}$/.test(digest)) {
    throw new Error(`${path}.contentSha256 must be a lower-case SHA-256 digest`);
  }
}

function assertWorkUnit(value: unknown, path: string): void {
  markRuntimeValidator("WorkUnit");
  assertWorkUnitContract(value, path, methodReadbackValidators());
}

function assertCapabilityCall(value: unknown, path: string): void {
  markRuntimeValidator("CapabilityCallV12");
  assertCapabilityCallContract(value, path, {
    requireExactRecord, requireNonEmptyString, assertPositiveInteger, assertActorRef, requireEnum,
    requireNullableNonEmptyString, requireNonEmptyStrings, assertAwareTimestamp,
    assertNullableAwareTimestamp, assertJsonObject,
  });
}

function assertResult(value: unknown, path: string): void {
  const record = requireExactRecord(value, path, [
    "resultId", "workUnitId", "capabilityCallId", "storageType", "storageScope", "storageRef",
    "contentSha256", "mimeType", "checkStatus", "checkResult", "sourceRefs", "artifactRefs",
    "scientificStateVersions", "createdAt",
  ]);
  for (const field of ["resultId", "workUnitId", "storageType", "storageRef", "mimeType"]) {
    requireNonEmptyString(record[field], `${path}.${field}`);
  }
  requireNullableNonEmptyString(record.capabilityCallId, `${path}.capabilityCallId`);
  requireNullableNonEmptyString(record.storageScope, `${path}.storageScope`);
  if (!/^[0-9a-f]{64}$/.test(requireString(record.contentSha256, `${path}.contentSha256`))) {
    throw new Error(`${path}.contentSha256 must be a lower-case SHA-256 digest`);
  }
  requireEnum(record.checkStatus, ["pending", "passed", "failed"], `${path}.checkStatus`);
  assertJsonObject(record.checkResult, `${path}.checkResult`);
  requireNonEmptyStrings(record.sourceRefs, `${path}.sourceRefs`);
  requireNonEmptyStrings(record.artifactRefs, `${path}.artifactRefs`);
  for (const [index, version] of requireArray(
    record.scientificStateVersions,
    `${path}.scientificStateVersions`,
  ).entries()) {
    assertPositiveInteger(version, `${path}.scientificStateVersions[${index}]`);
  }
  assertAwareTimestamp(record.createdAt, `${path}.createdAt`);
}

function assertArtifact(value: unknown, path: string): void {
  markRuntimeValidator("ArtifactV12");
  const record = requireExactRecord(value, path, [
    "artifactId", "workUnitId", "substageInstanceId", "sourceCapabilityCallId", "artifactType",
    "artifactState", "version", "sourceRefs", "validatorResult", "lineageKind", "sourceResultId",
  ]);
  for (const field of ["artifactId", "workUnitId", "substageInstanceId", "artifactType"]) {
    requireNonEmptyString(record[field], `${path}.${field}`);
  }
  requireNullableNonEmptyString(record.sourceCapabilityCallId, `${path}.sourceCapabilityCallId`);
  requireEnum(record.artifactState, [
    "usable", "needs_review", "needs_input", "insufficient_materials", "failed", "partial_success",
    "rejected_or_needs_revision",
  ], `${path}.artifactState`);
  assertPositiveInteger(record.version, `${path}.version`);
  requireNonEmptyStrings(record.sourceRefs, `${path}.sourceRefs`);
  assertJsonObject(record.validatorResult, `${path}.validatorResult`);
  requireNullableEnum(
    record.lineageKind,
    ["capability_call_output", "direct_work_unit_output"],
    `${path}.lineageKind`,
  );
  requireNullableNonEmptyString(record.sourceResultId, `${path}.sourceResultId`);
}

function assertFinalResultAuthority(value: unknown, path: string): void {
  const record = requireExactRecord(value, path, [
    "mode", "researchDesignVersion", "resultArtifactRefs", "sourceArtifacts", "assembledAt",
    "finalResultId", "integrityContractVersion",
  ]);
  requireEnum(record.mode, ["assembled_artifact", "artifact_set"], `${path}.mode`);
  assertPositiveInteger(record.researchDesignVersion, `${path}.researchDesignVersion`);
  requireNonEmptyStrings(record.resultArtifactRefs, `${path}.resultArtifactRefs`);
  assertObjectArray(record.sourceArtifacts, `${path}.sourceArtifacts`, assertCanonicalArtifactSnapshot);
  assertAwareTimestamp(record.assembledAt, `${path}.assembledAt`);
  requireNullableNonEmptyString(record.finalResultId, `${path}.finalResultId`);
  requireEnum(
    record.integrityContractVersion,
    [
      "legacy_v0",
      "scientific_completion_integrity_v1",
      "scientific_completion_integrity_v2",
      "scientific_completion_integrity_v3",
    ],
    `${path}.integrityContractVersion`,
  );
}

function assertScientificState(value: unknown, path: string): void {
  const record = requireExactRecord(value, path, [
    "scientificStateVersion", "researchDesignVersion", "scientificFacts", "executionState",
    "sourceRefs", "createdAt",
  ]);
  assertPositiveInteger(record.scientificStateVersion, `${path}.scientificStateVersion`);
  assertPositiveInteger(record.researchDesignVersion, `${path}.researchDesignVersion`);
  assertJsonObject(record.scientificFacts, `${path}.scientificFacts`);
  assertJsonObject(record.executionState, `${path}.executionState`);
  requireNonEmptyStrings(record.sourceRefs, `${path}.sourceRefs`);
  assertAwareTimestamp(record.createdAt, `${path}.createdAt`);
}

function assertCompletionDecision(value: unknown, path: string): void {
  assertCompletionDecisionContract(value, path, {
    requireExactRecord, requireNonEmptyString, requireNonEmptyStrings, requireEnum,
    assertPositiveInteger, assertAwareTimestamp, assertResearchDesignRef,
  });
}

function assertFinalResult(value: unknown, path: string): void {
  const record = requireRecord(value, path);
  const allowed = new Set([
    "finalResultId", "completionDecisionId", "scientificStateVersion", "disposition", "directAnswer",
    "keyFindings", "evidenceRefs", "scientificResult", "scientificSourceRefs", "uncertainty", "gaps",
    "limitations", "acceptedScientificFacts", "validatedAt", "validationOutcome",
  ]);
  for (const key of Object.keys(record)) {
    if (!allowed.has(key)) throw new Error(`${path}.${key} is not permitted`);
  }
  for (const field of [
    "finalResultId", "completionDecisionId", "scientificStateVersion", "disposition", "directAnswer",
    "keyFindings", "evidenceRefs", "uncertainty", "gaps", "limitations", "validatedAt", "validationOutcome",
  ]) {
    requireField(record, field, path);
  }
  requireNonEmptyString(record.finalResultId, `${path}.finalResultId`);
  requireNonEmptyString(record.completionDecisionId, `${path}.completionDecisionId`);
  assertPositiveInteger(record.scientificStateVersion, `${path}.scientificStateVersion`);
  requireEnum(record.disposition, ["full", "partial", "degraded", "evidence_gap"], `${path}.disposition`);
  requireNullableNonEmptyString(record.directAnswer, `${path}.directAnswer`);
  for (const field of ["keyFindings", "evidenceRefs", "uncertainty", "gaps", "limitations"]) {
    requireNonEmptyStrings(record[field], `${path}.${field}`);
  }
  if ("scientificResult" in record) assertJsonObject(record.scientificResult, `${path}.scientificResult`);
  if ("scientificSourceRefs" in record) {
    requireNonEmptyStrings(record.scientificSourceRefs, `${path}.scientificSourceRefs`);
  }
  if ("acceptedScientificFacts" in record) {
    assertJsonObject(record.acceptedScientificFacts, `${path}.acceptedScientificFacts`);
  }
  assertAwareTimestamp(record.validatedAt, `${path}.validatedAt`);
  if (record.validationOutcome !== null) {
    const outcome = requireExactRecord(record.validationOutcome, `${path}.validationOutcome`, [
      "contractVersion", "validatorRef", "status", "checkIds", "validatedAt",
    ]);
    const contractVersion = requireEnum(
      outcome.contractVersion,
      [
        "scientific_completion_integrity_v1",
        "scientific_completion_integrity_v2",
        "scientific_completion_integrity_v3",
      ],
      `${path}.validationOutcome.contractVersion`,
    );
    const validatorRef = requireEnum(
      outcome.validatorRef,
      [
        "core:scientific_completion_integrity:v1",
        "core:scientific_completion_integrity:v2",
        "core:scientific_completion_integrity:v3",
      ],
      `${path}.validationOutcome.validatorRef`,
    );
    const contractSuffix = contractVersion.slice(contractVersion.lastIndexOf("_v") + 1);
    const validatorSuffix = validatorRef.slice(validatorRef.lastIndexOf(":v") + 1);
    if (contractSuffix !== validatorSuffix) {
      throw new Error(`${path}.validationOutcome validator version must match contract version`);
    }
    requireEnum(outcome.status, ["passed", "failed", "unknown"], `${path}.validationOutcome.status`);
    requireNonEmptyStrings(outcome.checkIds, `${path}.validationOutcome.checkIds`);
    assertAwareTimestamp(outcome.validatedAt, `${path}.validationOutcome.validatedAt`);
  }
}

function assertConfirmation(value: unknown, path: string): void {
  markRuntimeValidator("ConfirmationV12");
  const record = requireExactRecord(value, path, [
    "confirmationId", "targetType", "targetId", "targetVersion", "decision", "actorId", "createdAt",
  ]);
  requireNonEmptyString(record.confirmationId, `${path}.confirmationId`);
  requireEnum(record.targetType, [
    "research_design", "stage_gate", "substage_gate", "artifact", "scope_expansion", "budget_expansion",
  ], `${path}.targetType`);
  requireNonEmptyString(record.targetId, `${path}.targetId`);
  assertPositiveInteger(record.targetVersion, `${path}.targetVersion`);
  requireEnum(record.decision, ["approved", "rejected", "request_revision"], `${path}.decision`);
  requireNonEmptyString(record.actorId, `${path}.actorId`);
  assertAwareTimestamp(record.createdAt, `${path}.createdAt`);
}

function assertArtifactRefs(value: unknown, path: string): string[] {
  return requireArray(value, path).map((item, index) => requireNonEmptyString(item, `${path}[${index}]`));
}

function assertInteractionVerdict(value: unknown, path: string): InteractionVerdict {
  markRuntimeValidator("InteractionVerdict");
  const verdict = requireExactRecord(value, path, ["verdictType", "decision", "rationaleCode"]);
  const verdictType = requireEnum(verdict.verdictType, ["handoff", "review", "acceptance", "rejection"], `${path}.verdictType`);
  const decision = requireEnum(verdict.decision, ["approved", "rejected", "insufficient_evidence"], `${path}.decision`);
  const rationaleCode = requireNonEmptyString(verdict.rationaleCode, `${path}.rationaleCode`);
  if (verdictType === "handoff" && (decision === "approved" || decision === "insufficient_evidence")) {
    return { verdictType: "handoff", decision, rationaleCode };
  }
  if (
    verdictType === "review" &&
    (decision === "approved" || decision === "rejected" || decision === "insufficient_evidence")
  ) {
    return {
      verdictType: "review",
      decision,
      rationaleCode,
    };
  }
  if (verdictType === "acceptance" && decision === "approved") {
    return { verdictType: "acceptance", decision, rationaleCode };
  }
  if (verdictType === "rejection" && (decision === "rejected" || decision === "insufficient_evidence")) {
    return { verdictType: "rejection", decision, rationaleCode };
  }
  throw new Error(`${path} has an invalid verdict type or decision`);
}

function assertInteraction(value: unknown, path: string): void {
  markRuntimeValidator("AgentInteractionRecord");
  const record = requireExactRecord(value, path, [
    "interactionId", "idempotencyKey", "taskId", "executionDecisionId", "sourceAssignmentId", "sourceWorkUnitId",
    "targetAssignmentId", "targetWorkUnitId", "interactionType", "inputArtifactRefs", "outputArtifactRefs",
    "verdict", "summary", "createdAt",
  ]);
  for (const field of [
    "interactionId", "idempotencyKey", "taskId", "executionDecisionId", "sourceAssignmentId", "sourceWorkUnitId",
    "summary",
  ]) {
    requireNonEmptyString(record[field], `${path}.${field}`);
  }
  const targetAssignmentId = requireNullableNonEmptyString(record.targetAssignmentId, `${path}.targetAssignmentId`);
  const targetWorkUnitId = requireNullableNonEmptyString(record.targetWorkUnitId, `${path}.targetWorkUnitId`);
  if ((targetAssignmentId === null) !== (targetWorkUnitId === null)) {
    throw new Error(`${path} target assignment and WorkUnit refs must be present together`);
  }
  const interactionType = requireEnum(record.interactionType, [
    "handoff_requested", "handoff_completed", "challenge_raised", "response_provided", "review_submitted",
    "acceptance_recorded", "target_output_reuse_accepted", "rejection_recorded",
  ], `${path}.interactionType`);
  const inputArtifactRefs = assertArtifactRefs(record.inputArtifactRefs, `${path}.inputArtifactRefs`);
  const outputArtifactRefs = assertArtifactRefs(record.outputArtifactRefs, `${path}.outputArtifactRefs`);
  const verdictValue = record.verdict;
  const verdict = verdictValue === null ? null : assertInteractionVerdict(verdictValue, `${path}.verdict`);
  assertAwareTimestamp(record.createdAt, `${path}.createdAt`);

  const requiresEvidence = new Set([
    "handoff_requested",
    "handoff_completed",
    "review_submitted",
    "acceptance_recorded", "target_output_reuse_accepted",
    "rejection_recorded",
  ]);
  if (!requiresEvidence.has(interactionType)) {
    return;
  }
  const expectedVerdictType =
    interactionType === "handoff_requested" || interactionType === "handoff_completed"
      ? "handoff"
      : interactionType === "review_submitted"
        ? "review"
        : (interactionType === "acceptance_recorded" || interactionType === "target_output_reuse_accepted")
          ? "acceptance"
          : "rejection";
  const hasArtifactEvidence = inputArtifactRefs.length > 0 || outputArtifactRefs.length > 0;
  if (!hasArtifactEvidence && verdict === null) {
    throw new Error(`${path} requires Artifact evidence or a ${expectedVerdictType} verdict`);
  }
  if (verdict !== null && verdict.verdictType !== expectedVerdictType) {
    throw new Error(`${path} has an incompatible structured verdict`);
  }
}

export function assertDispatchRecord(value: unknown, path = "DispatchRecord"): asserts value is DispatchRecord {
  markRuntimeValidator("DispatchRecord");
  const record = requireExactRecord(value, path, [
    "dispatchId", "taskId", "executionDecisionId", "workUnitId", "idempotencyKey", "executionNamespace", "status", "attemptCount",
    "leaseOwner", "leaseExpiresAt", "authorizationTaskRevision", "remoteRunId", "nextAttemptAt", "lastErrorCode",
    "lastFailureAt", "createdAt", "updatedAt",
  ]);
  for (const field of ["dispatchId", "taskId", "executionDecisionId", "workUnitId", "idempotencyKey"]) {
    requireNonEmptyString(record[field], `${path}.${field}`);
  }
  requireEnum(record.status, [
    "pending", "claimed", "submission_authorized", "submitted", "suppressed", "submission_unknown", "failed",
  ], `${path}.status`);
  assertNonNegativeInteger(record.attemptCount, `${path}.attemptCount`);
  assertNullableNonNegativeInteger(record.authorizationTaskRevision, `${path}.authorizationTaskRevision`);
  const leaseOwner = requireNullableNonEmptyString(record.leaseOwner, `${path}.leaseOwner`);
  const leaseExpiresAt = record.leaseExpiresAt;
  if ((leaseOwner === null) !== (leaseExpiresAt === null)) {
    throw new Error(`${path}.leaseOwner and leaseExpiresAt must be present together`);
  }
  requireNullableNonEmptyString(record.remoteRunId, `${path}.remoteRunId`);
  requireNullableNonEmptyString(record.executionNamespace, `${path}.executionNamespace`);
  requireNullableNonEmptyString(record.lastErrorCode, `${path}.lastErrorCode`);
  for (const field of ["leaseExpiresAt", "nextAttemptAt", "lastFailureAt"]) {
    assertNullableAwareTimestamp(record[field], `${path}.${field}`);
  }
  for (const field of ["createdAt", "updatedAt"]) {
    assertAwareTimestamp(record[field], `${path}.${field}`);
  }
}

/** Validates the frozen scalar and interaction boundary before a JSON document enters shared code. */
export function assertResearchTaskV12(value: unknown): asserts value is ResearchTaskV12 {
  markRuntimeValidator("ResearchTaskV12");
  const task = requireExactRecord(value, "ResearchTaskV12", [
    "taskId", "revision", "status", "executionMode", "pendingExecutionMode", "executionPolicyVersion",
    "activeResearchDesignRef", "registrySnapshotRef", "planningAttempts", "researchDesignVersions",
    "reentryExecutionInstances", "substageRuntimeStates", "executionDecisions", "workUnits", "capabilityCalls", "results", "artifacts", "confirmations",
    "agentInteractionRecords", "progressProjection", "finalResultAuthority", "scientificStates",
    "completionDecisions", "finalResults", "startedAt", "finishedAt",
  ]);
  requireNonEmptyString(task.taskId, "ResearchTaskV12.taskId");
  assertNonNegativeInteger(task.revision, "ResearchTaskV12.revision");
  requireEnum(task.status, [
    "draft", "not_started", "running", "awaiting_confirmation", "completed", "failed", "cancelled",
  ], "ResearchTaskV12.status");
  const executionModes = ["automatic_research", "stage_confirmation", "manual_step"] as const;
  requireEnum(task.executionMode, executionModes, "ResearchTaskV12.executionMode");
  requireNullableEnum(task.pendingExecutionMode, executionModes, "ResearchTaskV12.pendingExecutionMode");
  requireNonEmptyString(task.executionPolicyVersion, "ResearchTaskV12.executionPolicyVersion");
  if (task.activeResearchDesignRef !== null) {
    assertResearchDesignRef(task.activeResearchDesignRef, "ResearchTaskV12.activeResearchDesignRef");
  }
  if (task.progressProjection !== null) {
    assertJsonObject(task.progressProjection, "ResearchTaskV12.progressProjection");
  }
  if (task.finalResultAuthority !== null) {
    assertFinalResultAuthority(task.finalResultAuthority, "ResearchTaskV12.finalResultAuthority");
  }
  assertRegistrySnapshot(task.registrySnapshotRef, "ResearchTaskV12.registrySnapshotRef");
  assertNullableAwareTimestamp(task.startedAt, "ResearchTaskV12.startedAt");
  assertNullableAwareTimestamp(task.finishedAt, "ResearchTaskV12.finishedAt");

  const validators: readonly [string, (value: unknown, path: string) => void][] = [
    ["planningAttempts", assertPlanningAttempt],
    ["researchDesignVersions", assertDesign],
    ["reentryExecutionInstances", assertReentryExecutionInstance],
    ["substageRuntimeStates", assertSubstageRuntimeState],
    ["executionDecisions", assertDecision],
    ["workUnits", assertWorkUnit],
    ["capabilityCalls", assertCapabilityCall],
    ["results", assertResult],
    ["artifacts", assertArtifact],
    ["confirmations", assertConfirmation],
    ["agentInteractionRecords", assertInteraction],
  ];
  for (const [field, validator] of validators) {
    for (const [index, item] of requireArray(task[field], `ResearchTaskV12.${field}`).entries()) {
      validator(item, `ResearchTaskV12.${field}[${index}]`);
    }
  }
  for (const decision of task.executionDecisions as JsonRecord[]) {
    for (const outcome of (decision.assignmentPreflightOutcomes ?? []) as JsonRecord[]) {
      if (outcome.failureKind === "contract_violation" && (task.workUnits as JsonRecord[]).some(work => work.executionDecisionId === decision.executionDecisionId && work.assignmentId === outcome.assignmentId)) {
        throw new Error("a preflight outcome cannot have a WorkUnit");
      }
    }
  }
  assertObjectArray(task.scientificStates, "ResearchTaskV12.scientificStates", assertScientificState);
  assertObjectArray(task.completionDecisions, "ResearchTaskV12.completionDecisions", assertCompletionDecision);
  assertObjectArray(task.finalResults, "ResearchTaskV12.finalResults", assertFinalResult);

  if (task.activeResearchDesignRef !== null) {
    const activeRef = requireRecord(task.activeResearchDesignRef, "ResearchTaskV12.activeResearchDesignRef");
    const activeDesign = requireArray(task.researchDesignVersions, "ResearchTaskV12.researchDesignVersions")
      .map((item, index) => requireRecord(item, `ResearchTaskV12.researchDesignVersions[${index}]`))
      .find((design) =>
        design.researchDesignId === activeRef.researchDesignId && design.version === activeRef.version
      );
    if (activeDesign === undefined || activeDesign.status !== "confirmed") {
      throw new Error("ResearchTaskV12.activeResearchDesignRef must reference a confirmed Design");
    }
  }
}

export function parseResearchTaskV12(value: unknown): ResearchTaskV12 {
  assertResearchTaskV12(value);
  return value;
}

export function parseDispatchRecord(value: unknown): DispatchRecord {
  assertDispatchRecord(value);
  return value;
}

export function assertMultiAgentRecipeDefinition(value: unknown): asserts value is MultiAgentRecipeDefinition {
  markRuntimeValidator("MultiAgentRecipeDefinition");
  const recipe = requireExactRecord(value, "MultiAgentRecipeDefinition", [
    "recipeId", "recipeVersion", "substageDefinitionId", "substageDefinitionVersion", "applicabilityRuleRefs",
    "assignmentTemplates", "completionPolicyTemplate", "resultAssemblyPolicyTemplate", "enabled",
  ]);
  requireNonEmptyString(recipe.recipeId, "MultiAgentRecipeDefinition.recipeId");
  requireNonEmptyString(recipe.substageDefinitionId, "MultiAgentRecipeDefinition.substageDefinitionId");
  for (const field of ["recipeVersion", "substageDefinitionVersion"]) {
    assertPositiveInteger(recipe[field], `MultiAgentRecipeDefinition.${field}`);
  }
  requireNonEmptyStrings(recipe.applicabilityRuleRefs, "MultiAgentRecipeDefinition.applicabilityRuleRefs");
  assertObjectArray(recipe.assignmentTemplates, "MultiAgentRecipeDefinition.assignmentTemplates", (item, path) => {
    markRuntimeValidator("RecipeAssignmentTemplate");
    const record = requireExactRecord(item, path, [
      "templateKey", "role", "requiredSupportedRole", "objectiveTemplate", "dependencyTemplateKeys",
    ]);
    requireNonEmptyString(record.templateKey, `${path}.templateKey`);
    requireEnum(record.role, ["primary", "reviewer", "support"], `${path}.role`);
    requireEnum(record.requiredSupportedRole, ["primary", "reviewer", "support"], `${path}.requiredSupportedRole`);
    requireNonEmptyString(record.objectiveTemplate, `${path}.objectiveTemplate`);
    requireNonEmptyStrings(record.dependencyTemplateKeys, `${path}.dependencyTemplateKeys`);
  });
  assertCompletionPolicyTemplate(recipe.completionPolicyTemplate, "MultiAgentRecipeDefinition.completionPolicyTemplate");
  assertResultAssemblyPolicyTemplate(recipe.resultAssemblyPolicyTemplate, "MultiAgentRecipeDefinition.resultAssemblyPolicyTemplate");
  requireBoolean(recipe.enabled, "MultiAgentRecipeDefinition.enabled");
}

function methodReadbackValidators() {
  return {
    requireExactRecord,
    requireNonEmptyString,
    requireNullableNonEmptyString,
    requireNonEmptyStrings,
    requireEnum,
    requireBoolean,
    assertPositiveInteger,
    assertNullablePositiveInteger,
    assertObjectArray,
    assertBindingRef,
    assertActorRef,
    assertExecutorRef,
    assertSkillRef,
    assertAcceptanceCriterion,
    assertRegistrySnapshot,
    assertCanonicalArtifactSnapshot,
    assertJsonObject,
    assertAuthorizationSnapshot,
    assertBudgetSnapshot,
    assertRiskSnapshot,
    assertAwareTimestamp,
    assertNullableAwareTimestamp,
    assertSemanticFitEvidence,
  };
}
