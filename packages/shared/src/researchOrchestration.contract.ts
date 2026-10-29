import type {
  AcceptanceCriterion,
  ActorRef,
  AgentInteractionRecord,
  ArtifactV12,
  CandidateEvaluation,
  CapabilityCallV12,
  ConfirmationV12,
  CoverageClaim,
  CoverageTarget,
  DispatchRecord,
  ExecutionAssignment,
  ExecutionDecision,
  ExecutorRef,
  InteractionVerdict,
  JsonValue,
  MultiAgentRecipeDefinition,
  PlanningAttempt,
  ProjectCompletionRequirement,
  RegistrySnapshotRef,
  ResearchDesignCoverageReport,
  ResearchDesignRef,
  ResearchDesignVersion,
  ResultV1,
  ResearchTaskV12,
  RuntimeReadinessSnapshot,
  SubstageInstance,
  SubstageRuntimeState,
  WorkUnit,
  WorkflowProvenanceRef,
} from "./researchOrchestration";
import {
  awareDateTime,
  assertMultiAgentRecipeDefinition,
  getResearchOrchestrationRuntimeValidatorCoverage,
  nonNegativeInteger,
  parseDispatchRecord,
  parseResearchTaskV12,
  positiveInteger,
  researchOrchestrationRuntimeValidatorNames,
  resetResearchOrchestrationRuntimeValidatorCoverage,
  sha256Hash,
} from "./researchOrchestration";
import sharedValidFixture from "../test-fixtures/research-orchestration-v1.2-valid.json";
import sharedInvalidFixture from "../test-fixtures/research-orchestration-v1.2-invalid.json";

const registrySnapshot = {
  schemaVersion: positiveInteger(1),
  contentHash: sha256Hash(`sha256:${"a".repeat(64)}`),
} satisfies RegistrySnapshotRef;

const designRef = {
  researchDesignId: "rd_contract_full",
  version: positiveInteger(1),
} satisfies ResearchDesignRef;

const planningAttempt = {
  planningAttemptId: "planning_contract_full",
  taskId: "task_contract_full",
  requestId: "request_contract_full",
  plannerCallIdempotencyKey: "planner:request_contract_full",
  plannerProviderCallRef: "provider_call_contract_full",
  additionalActionType: "none",
  additionalActionIdempotencyKey: null,
  additionalProviderCallRef: null,
  status: "completed",
  startedAt: "2026-07-28T00:00:00Z",
  finishedAt: "2026-07-28T00:00:01Z",
} satisfies PlanningAttempt;

const acceptanceCriterion = {
  criterionId: "criterion_contract_full",
  description: "The output is traceable to the supplied source.",
  criterionKind: "target_output",
  coverageTargetIds: ["target_contract_full"],
  requiredArtifactType: "evidence_summary",
  judgmentRequirement: "deterministic",
  evaluatorType: "deterministic",
  evaluatorRef: null,
} satisfies AcceptanceCriterion;

const researchSynthesisCriterion = {
  ...acceptanceCriterion,
  judgmentRequirement: "research_synthesis",
} satisfies AcceptanceCriterion;

const substageInstance = {
  substageInstanceId: "sub_contract_full",
  instanceRevision: positiveInteger(1),
  substageDefinitionId: "sdr:evidence_synthesis",
  substageDefinitionVersion: positiveInteger(1),
  stageKey: "solution_design",
  objective: "Create the bounded evidence summary.",
  inputRefs: ["material_contract"],
  expectedArtifactTypes: ["evidence_summary"],
  acceptanceCriteria: [acceptanceCriterion],
  dependencies: [],
  sequenceHint: nonNegativeInteger(0),
  repeatableReentryAuthority: null,
} satisfies SubstageInstance;

const coverageTarget = {
  targetId: "target_contract_full",
  sourceType: "target_output",
  sourcePath: "understanding.targetOutputs[0]",
  description: "Produce an evidence summary.",
  requiredArtifactTypes: ["evidence_summary"],
  enforcementRef: null,
  required: true,
} satisfies CoverageTarget;

const coverageClaim = {
  targetId: "target_contract_full",
  instanceRefs: [{ substageInstanceId: "sub_contract_full", instanceRevision: positiveInteger(1) }],
  coverageMode: "full",
  acceptanceCriterionRefs: ["criterion_contract_full"],
  derivationRuleRefs: ["coverage-policy.v1:target-output"],
  rationale: "The instance emits and validates the required artifact.",
} satisfies CoverageClaim;

const coverageReport = {
  coverageReportId: "coverage_contract_full",
  designRef,
  planningAttemptId: "planning_contract_full",
  targets: [coverageTarget],
  claims: [coverageClaim],
  uncoveredRequiredTargetIds: [],
  overloadedInstanceRefs: [],
  missingDependencyRefs: [],
  status: "passed_structural",
  evaluationTier: "lightweight",
  evaluationMode: "deterministic",
  semanticEvaluationRef: null,
  automaticRepairCount: nonNegativeInteger(0),
  policyVersion: "coverage-policy.v1",
  evaluatedAt: "2026-07-28T00:00:02Z",
} satisfies ResearchDesignCoverageReport;

const workflowProvenanceRef = null satisfies WorkflowProvenanceRef | null;

const projectCompletionRequirement = {
  completionRequirementId: "completion_contract_full",
  targetOutputRef: "understanding.targetOutputs[0]",
  description: "evidence_summary",
} satisfies ProjectCompletionRequirement;

const researchDesign = {
  researchDesignId: "rd_contract_full",
  taskId: "task_contract_full",
  version: positiveInteger(1),
  supersedesVersion: null,
  status: "confirmed",
  planningMode: "lightweight",
  planningComplexity: {
    problemClarity: "high",
    expectedStageSpan: "one",
    dependencyComplexity: "low",
    evidenceBurden: "low",
    externalActionRisk: "low",
    researchUncertainty: "low",
    rationale: "The target is bounded and structurally testable.",
  },
  goal: "Produce a traceable evidence summary.",
  understanding: {
    researchType: "evidence_synthesis",
    userIntent: "Review one bounded body of evidence.",
    materialSummary: "The supplied source is available.",
    primaryResearchQuestions: ["What does the supplied evidence support within its stated boundary?"],
    auxiliaryResearchDimensions: [],
    targetOutputs: ["evidence_summary"],
    evidenceBoundaries: ["supplied_material_only"],
    missingInputs: [],
    riskNotes: [],
    planningRationale: "One independently verifiable instance is sufficient.",
  },
  constraints: ["cite_source_refs"],
  scopeBoundary: {
    allowedObjectiveKinds: ["evidence_synthesis"],
    excludedActions: ["external_publish"],
    allowedMaterialRefs: ["material_contract"],
    maxSubstageInstances: positiveInteger(4),
  },
  budgetBoundary: {
    maxModelCalls: nonNegativeInteger(2),
    maxToolCalls: nonNegativeInteger(1),
    maxWallTimeSec: nonNegativeInteger(120),
    maxCostAmount: 2,
    currency: "USD",
  },
  externalActionBoundary: {
    allowedActionKinds: [],
    confirmationRequiredKinds: [],
    allowedDestinations: [],
  },
  riskBoundary: {
    maxRiskClass: "low",
    prohibitedActions: ["external_publish"],
    humanGateTriggers: [],
  },
  workflowProvenanceRef,
  projectCompletionRequirements: [projectCompletionRequirement],
  instances: [substageInstance],
  coverageReport,
  createdAt: "2026-07-28T00:00:01Z",
  confirmedAt: "2026-07-28T00:00:03Z",
  confirmationRef: "confirmation_design_contract_full",
} satisfies ResearchDesignVersion;

const responsibleActor = {
  actorType: "orchestrator",
  actorId: "openair_orchestrator",
  definitionVersion: null,
} satisfies ActorRef;

const executor = {
  executorType: "skill",
  executorId: "evidence_synthesis_skill",
  definitionVersion: positiveInteger(1),
} satisfies ExecutorRef;

const readiness = {
  capabilityRef: { skillId: "evidence_synthesis_skill", definitionVersion: positiveInteger(1) },
  ready: true,
  runtimeBindingId: "runtime_binding_contract",
  checkedAt: "2026-07-28T00:00:03Z",
  expiresAt: "2026-07-28T00:05:03Z",
  reason: null,
} satisfies RuntimeReadinessSnapshot;

const candidateEvaluation = {
  candidateEvaluationId: "candidate_contract_full",
  candidateRef: { skillId: "evidence_synthesis_skill", definitionVersion: positiveInteger(1) },
  sourceBindingRefs: [
    {
      bindingId: "bind:sdr:evidence_synthesis:skill:evidence_synthesis_skill",
      bindingVersion: positiveInteger(1),
    },
  ],
  supportedRole: "tool",
  eligible: true,
  rejectionCodes: [],
  preference: {
    semanticFit: "exact",
    semanticFitEvidence: {
      objectiveMatch: "exact",
      inputContractMatch: "exact",
      outputContractMatch: "exact",
      professionalJudgmentFit: "not_required",
      positiveRuleRefs: ["candidate-policy.v1:exact-contract"],
      negativeRuleRefs: [],
      adapterRefs: [],
      policyVersion: "candidate-policy.v1",
      rationale: "The registered Skill contracts match exactly.",
    },
    completionCoverage: "completion_capable",
    executableNow: true,
    executionBlockerCodes: [],
    riskClass: "low",
    estimatedCostAmount: 0.1,
    permissionBreadth: "narrow",
    supportsProfessionalJudgment: false,
    activeWorkUnitCount: nonNegativeInteger(0),
    lastAssignedAt: null,
  },
  rationale: "The registered direct Skill closes the deterministic criterion.",
} satisfies CandidateEvaluation;

const assignment = {
  assignmentId: "assignment_contract_full",
  recipeTemplateKey: null,
  role: "primary",
  completionCoverage: "completion_capable",
  objective: "Produce the traceable evidence summary.",
  sourceBindingRefs: candidateEvaluation.sourceBindingRefs,
  responsibleActorRef: responsibleActor,
  executorRef: executor,
  allowedSkillRefs: [],
  inputRefs: ["material_contract"],
  expectedArtifactTypes: ["evidence_summary"],
  acceptanceCriteria: [acceptanceCriterion],
  dependencyAssignmentIds: [],
} satisfies ExecutionAssignment;

const budgetSnapshot = {
  boundary: {
    maxModelCalls: nonNegativeInteger(2),
    maxToolCalls: nonNegativeInteger(1),
    maxWallTimeSec: nonNegativeInteger(120),
    maxCostAmount: 2,
    currency: "USD",
  },
  consumedModelCalls: nonNegativeInteger(0),
  consumedToolCalls: nonNegativeInteger(0),
  consumedCostAmount: 0,
};

const decision = {
  executionDecisionId: "decision_contract_full",
  selectionRequestId: "selection_contract_full",
  decisionSequence: positiveInteger(1),
  supersedesExecutionDecisionId: null,
  revisionReasonCode: null,
  taskId: "task_contract_full",
  researchDesignVersion: positiveInteger(1),
  substageInstanceId: "sub_contract_full",
  substageInstanceRevision: positiveInteger(1),
  registrySnapshotRef: registrySnapshot,
  purpose: "completion",
  strategy: "direct_skill",
  multiAgentRecipeRef: null,
  candidateEvaluations: [candidateEvaluation],
  fallbackInvoked: false,
  fallbackReasonCode: null,
  preferredCandidateEvaluationRefs: ["candidate_contract_full"],
  assignments: [assignment],
  invocationContext: {},
  completionPolicy: {
    requiredAssignmentIds: ["assignment_contract_full"],
    successMode: "all_required",
    reviewerAssignmentIds: [],
    reviewRequirement: "none",
    partialSuccessAllowed: false,
    onReviewerReject: "new_revision_decision",
  },
  progressPolicy: null,
  resultAssemblyPolicy: {
    mode: "primary_output",
    assemblerAssignmentId: null,
    finalArtifactRequirements: [
      {
        artifactType: "evidence_summary",
        minimumCount: nonNegativeInteger(1),
        sourceAssignmentIds: ["assignment_contract_full"],
      },
    ],
  },
  runtimeReadiness: [readiness],
  budgetSnapshot,
  policyVersion: "decision-policy.v1",
  rationale: "The exact registered Skill is executable.",
  status: "dispatched",
  outcome: null,
  decidedAt: "2026-07-28T00:00:04Z",
  closedAt: null,
} satisfies ExecutionDecision;

const skillCompositionStrategy: ExecutionDecision["strategy"] = "skill_composition";

const workUnit = {
  workUnitId: "wu_contract_full",
  idempotencyKey: "workunit:decision_contract_full:assignment_contract_full",
  taskId: "task_contract_full",
  executionDecisionId: "decision_contract_full",
  assignmentId: "assignment_contract_full",
  researchDesignVersion: positiveInteger(1),
  substageInstanceId: "sub_contract_full",
  substageInstanceRevision: positiveInteger(1),
  substageDefinitionId: "sdr:evidence_synthesis",
  substageDefinitionVersion: positiveInteger(1),
  registrySnapshotRef: registrySnapshot,
  responsibleActorRef: responsibleActor,
  executorRef: executor,
  instruction: "Produce the traceable evidence summary.",
  inputArtifactRefs: [],
  inputArtifactSnapshots: [],
  inputMemoryRefs: ["material_contract"],
  invocationContext: {},
  allowedSkillRefs: [],
  authorizationSnapshot: {
    policyVersion: "authorization.v1",
    allowedMemoryScopes: ["task"],
    allowedExternalActionKinds: [],
    confirmationRefs: ["confirmation_design_contract_full"],
  },
  budgetSnapshot: { ...budgetSnapshot, consumedToolCalls: nonNegativeInteger(1), consumedCostAmount: 0.1 },
  riskSnapshot: {
    boundary: {
      maxRiskClass: "low",
      prohibitedActions: ["external_publish"],
      humanGateTriggers: [],
    },
    evaluatedRiskClass: "low",
    triggeredHumanGates: [],
  },
  expectedArtifactTypes: ["evidence_summary"],
  acceptanceCriteria: [acceptanceCriterion],
  stopConditions: ["criterion_satisfied"],
  dependencyWorkUnitIds: [],
  executionGroupId: null,
  policyVersion: "decision-policy.v1",
  sourceMode: "live",
  dispatchable: true,
  status: "reviewing",
  terminalReasonCode: null,
  dispatchedAt: "2026-07-28T00:00:04Z",
  frozenAt: "2026-07-28T00:00:04Z",
  finishedAt: null,
} satisfies WorkUnit;

const capabilityCall = {
  capabilityCallId: "call_contract_full",
  idempotencyKey: "call:wu_contract_full:1",
  attemptNumber: positiveInteger(1),
  workUnitId: "wu_contract_full",
  substageInstanceId: "sub_contract_full",
  requestingActorRef: responsibleActor,
  capabilityType: "skill",
  capabilityId: "evidence_synthesis_skill",
  runtimeBindingId: "runtime_binding_contract",
  status: "succeeded",
  inputRefs: ["material_contract"],
  outputArtifactRefs: ["artifact_contract_full"],
  startedAt: "2026-07-28T00:00:05Z",
  finishedAt: "2026-07-28T00:00:07Z",
} satisfies CapabilityCallV12;

const result = {
  resultId: "result_contract_full",
  workUnitId: "wu_contract_full",
  capabilityCallId: "call_contract_full",
  storageType: "local",
  storageScope: "task_contract_full",
  storageRef: "durable/research-dispatch/dispatch_contract_full/result.json",
  contentSha256: "b".repeat(64),
  mimeType: "application/json",
  checkStatus: "pending",
  checkResult: { reasonCode: "durable_review_required" },
  sourceRefs: ["material_contract"],
  artifactRefs: ["artifact_contract_full"],
  scientificStateVersions: [],
  createdAt: "2026-07-28T00:00:07Z",
} satisfies ResultV1;

const artifact = {
  artifactId: "artifact_contract_full",
  workUnitId: "wu_contract_full",
  substageInstanceId: "sub_contract_full",
  sourceCapabilityCallId: "call_contract_full",
  artifactType: "evidence_summary",
  artifactState: "needs_review",
  version: positiveInteger(1),
  sourceRefs: ["material_contract"],
  validatorResult: { traceable: true, sourceCount: 1, notes: null },
  lineageKind: "capability_call_output",
  sourceResultId: "result_contract_full",
} satisfies ArtifactV12;

const confirmation = {
  confirmationId: "confirmation_design_contract_full",
  targetType: "research_design",
  targetId: "rd_contract_full",
  targetVersion: positiveInteger(1),
  decision: "approved",
  actorId: "owner_contract",
  createdAt: "2026-07-28T00:00:03Z",
} satisfies ConfirmationV12;

const interaction = {
  interactionId: "interaction_contract_full",
  idempotencyKey: "interaction:review:wu_contract_full",
  taskId: "task_contract_full",
  executionDecisionId: "decision_contract_full",
  sourceAssignmentId: "assignment_contract_full",
  sourceWorkUnitId: "wu_contract_full",
  targetAssignmentId: null,
  targetWorkUnitId: null,
  interactionType: "review_submitted",
  inputArtifactRefs: [],
  outputArtifactRefs: ["artifact_contract_full"] as [string, ...string[]],
  verdict: null,
  summary: "The deterministic output was submitted for review.",
  createdAt: "2026-07-28T00:00:08Z",
} satisfies AgentInteractionRecord;

const runtimeState = {
  substageInstanceId: "sub_contract_full",
  instanceRevision: positiveInteger(1),
  status: "reviewing",
  activeExecutionDecisionId: "decision_contract_full",
  workUnitIds: ["wu_contract_full"],
  artifactRefs: ["artifact_contract_full"],
  statusReasonCode: null,
  updatedAt: "2026-07-28T00:00:08Z",
} satisfies SubstageRuntimeState;

const researchTaskFixture = {
  taskId: "task_contract_full",
  revision: nonNegativeInteger(4),
  status: "running",
  executionMode: "stage_confirmation",
  pendingExecutionMode: null,
  executionPolicyVersion: "execution-policy.v1",
  activeResearchDesignRef: designRef,
  registrySnapshotRef: registrySnapshot,
  planningAttempts: [planningAttempt],
  researchDesignVersions: [researchDesign],
  reentryExecutionInstances: [],
  substageRuntimeStates: [runtimeState],
  executionDecisions: [decision],
  workUnits: [workUnit],
  capabilityCalls: [capabilityCall],
  results: [result],
  artifacts: [artifact],
  confirmations: [confirmation],
  agentInteractionRecords: [interaction],
  progressProjection: null,
  finalResultAuthority: null,
  scientificStates: [],
  completionDecisions: [],
  finalResults: [],
  startedAt: "2026-07-28T00:00:04Z",
  finishedAt: null,
} satisfies ResearchTaskV12;

const recipeFixture = {
  recipeId: "recipe_producer_reviewer",
  recipeVersion: positiveInteger(1),
  substageDefinitionId: "sdr:evidence_synthesis",
  substageDefinitionVersion: positiveInteger(1),
  applicabilityRuleRefs: ["recipe-policy.v1:review-required"],
  assignmentTemplates: [
    {
      templateKey: "producer",
      role: "primary",
      requiredSupportedRole: "primary",
      objectiveTemplate: "Produce the evidence artifact.",
      dependencyTemplateKeys: [],
    },
    {
      templateKey: "reviewer",
      role: "reviewer",
      requiredSupportedRole: "reviewer",
      objectiveTemplate: "Review the evidence artifact.",
      dependencyTemplateKeys: ["producer"],
    },
  ],
  completionPolicyTemplate: {
    requiredTemplateKeys: ["producer", "reviewer"],
    successMode: "all_required",
    reviewerTemplateKeys: ["reviewer"],
    reviewRequirement: "all_reviewers_approve",
    partialSuccessAllowed: false,
    onReviewerReject: "new_revision_decision",
  },
  resultAssemblyPolicyTemplate: {
    mode: "designated_assembler",
    assemblerTemplateKey: "producer",
    finalArtifactRequirements: [
      {
        artifactType: "evidence_summary",
        minimumCount: nonNegativeInteger(1),
        sourceTemplateKeys: ["producer"],
      },
    ],
  },
  enabled: true,
} satisfies MultiAgentRecipeDefinition;

const dispatchFixture = {
  dispatchId: "dispatch_contract_full",
  taskId: "task_contract_full",
  executionDecisionId: "decision_contract_full",
  workUnitId: "wu_contract_full",
  idempotencyKey: "workunit:decision_contract_full:assignment_contract_full",
  executionNamespace: null,
  status: "pending",
  attemptCount: nonNegativeInteger(0),
  leaseOwner: null,
  leaseExpiresAt: null,
  authorizationTaskRevision: null,
  remoteRunId: null,
  nextAttemptAt: null,
  lastErrorCode: null,
  lastFailureAt: null,
  createdAt: "2026-07-28T00:00:04Z",
  updatedAt: "2026-07-28T00:00:04Z",
} satisfies DispatchRecord;

const rejectedLegacyCommand = {
  ...researchTaskFixture,
  // @ts-expect-error legacy command authority is forbidden by ResearchTaskV12.
  selectedCapabilities: [{ id: "agent_must_not_enter_v12" }],
} satisfies ResearchTaskV12;

const rejectedNullability = {
  actorType: "agent",
  // @ts-expect-error actorId is required and never nullable.
  actorId: null,
  definitionVersion: positiveInteger(1),
} satisfies ActorRef;

const rejectedUnawareTimestamp = {
  ...planningAttempt,
  startedAt: "2026-07-28T00:00:00",
};
// @ts-expect-error ISODateTime must reject a naive timestamp.
const strictTimestampEvidence = rejectedUnawareTimestamp satisfies PlanningAttempt;

const rejectedNonPositiveDecisionSequence = {
  ...decision,
  decisionSequence: 0,
};
// @ts-expect-error decisionSequence must be a positive integer.
const strictPositiveIntegerEvidence = rejectedNonPositiveDecisionSequence satisfies ExecutionDecision;

const rejectedFractionalDispatchCount = {
  ...dispatchFixture,
  attemptCount: 1.5,
};
// @ts-expect-error attemptCount must be a non-negative integer.
const strictNonNegativeIntegerEvidence = rejectedFractionalDispatchCount satisfies DispatchRecord;

const rejectedContentHash = {
  ...registrySnapshot,
  contentHash: "sha256:not-a-real-digest",
};
// @ts-expect-error contentHash must be a lower-case SHA-256 digest.
const strictSha256Evidence = rejectedContentHash satisfies RegistrySnapshotRef;

const rejectedInteractionWithoutEvidence = {
  ...interaction,
  outputArtifactRefs: [],
};
// @ts-expect-error review_submitted requires an Artifact or structured verdict.
const interactionEvidenceMatrix = rejectedInteractionWithoutEvidence satisfies AgentInteractionRecord;

const handoffRequestedVerdictOnly = {
  ...interaction,
  interactionType: "handoff_requested",
  inputArtifactRefs: [],
  outputArtifactRefs: [],
  verdict: { verdictType: "handoff", decision: "approved", rationaleCode: "handoff_received" },
} satisfies AgentInteractionRecord;

const handoffCompletedVerdictOnly = {
  ...interaction,
  interactionType: "handoff_completed",
  inputArtifactRefs: [],
  outputArtifactRefs: [],
  verdict: { verdictType: "handoff", decision: "insufficient_evidence", rationaleCode: "handoff_incomplete" },
} satisfies AgentInteractionRecord;

const reviewVerdictOnly = {
  ...interaction,
  interactionType: "review_submitted",
  inputArtifactRefs: [],
  outputArtifactRefs: [],
  verdict: { verdictType: "review", decision: "rejected", rationaleCode: "criterion_missing" },
} satisfies AgentInteractionRecord;

const acceptanceVerdictOnly = {
  ...interaction,
  interactionType: "acceptance_recorded",
  inputArtifactRefs: [],
  outputArtifactRefs: [],
  verdict: { verdictType: "acceptance", decision: "approved", rationaleCode: "criteria_met" },
} satisfies AgentInteractionRecord;

const rejectionVerdictOnly = {
  ...interaction,
  interactionType: "rejection_recorded",
  inputArtifactRefs: [],
  outputArtifactRefs: [],
  verdict: { verdictType: "rejection", decision: "rejected", rationaleCode: "criteria_not_met" },
} satisfies AgentInteractionRecord;

const rejectedRequiredOne = {
  ...coverageTarget,
  // @ts-expect-error required must be a boolean, never a numeric coercion.
  required: 1,
} satisfies CoverageTarget;

const rejectedRequiredZero = {
  ...coverageTarget,
  // @ts-expect-error required must be a boolean, never a numeric coercion.
  required: 0,
} satisfies CoverageTarget;

const rejectedRequiredString = {
  ...coverageTarget,
  // @ts-expect-error required must be a boolean, never a string coercion.
  required: "true",
} satisfies CoverageTarget;

const rejectedAcceptanceVerdict = {
  verdictType: "acceptance",
  decision: "rejected",
  rationaleCode: "not_permitted",
  // @ts-expect-error acceptance verdicts can only approve.
} satisfies InteractionVerdict;

resetResearchOrchestrationRuntimeValidatorCoverage();
const sharedFixtureResearchTask = parseResearchTaskV12(sharedValidFixture.researchTask);
assertMultiAgentRecipeDefinition(sharedValidFixture.multiAgentRecipeDefinition);
const sharedFixtureDispatchRecord = parseDispatchRecord(sharedValidFixture.dispatchRecord);
for (const validTimestamp of [
  "2024-02-29T23:59:59.123456Z",
  "2026-01-01T00:00:00+05:30",
  "2026-01-01T00:00:00-08:00",
  "2026-01-01T00:00:00+23:59",
]) {
  awareDateTime(validTimestamp);
}
for (const invalidTimestamp of [
  "0000-01-01T00:00:00Z",
  "2023-02-29T00:00:00Z",
  "2024-04-31T00:00:00Z",
  "2026-01-01T24:00:00Z",
  "2026-01-01T23:59:60Z",
  "2026-01-01T00:00:00+24:00",
  "2026-01-01T00:00:00+00:60",
]) {
  let rejected = false;
  try {
    awareDateTime(invalidTimestamp);
  } catch {
    rejected = true;
  }
  if (!rejected) {
    throw new Error(`invalid aware timestamp was accepted: ${invalidTimestamp}`);
  }
}

function cloneJson(value: JsonValue): JsonValue {
  if (Array.isArray(value)) {
    return value.map(cloneJson);
  }
  if (typeof value === "object" && value !== null) {
    const clone: { [key: string]: JsonValue } = {};
    for (const key of Object.keys(value)) {
      clone[key] = cloneJson(value[key]);
    }
    return clone;
  }
  return value;
}

function importedJsonValue(value: unknown, path: string): JsonValue {
  if (value === null || typeof value === "string" || typeof value === "boolean") {
    return value;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new Error(`${path} must contain only finite JSON numbers`);
    }
    return value;
  }
  if (Array.isArray(value)) {
    return value.map((item, index) => importedJsonValue(item, `${path}[${index}]`));
  }
  if (typeof value === "object") {
    const converted: { [key: string]: JsonValue } = {};
    for (const [key, item] of Object.entries(value)) {
      converted[key] = importedJsonValue(item, `${path}.${key}`);
    }
    return converted;
  }
  throw new Error(`${path} is not JSON-compatible`);
}

function mutationRecord(value: JsonValue, path: string): { [key: string]: JsonValue } {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${path} must be an object`);
  }
  return value;
}

function mutationString(value: JsonValue | undefined, path: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${path} must be a non-empty string`);
  }
  return value;
}

function mutationTarget(target: string): JsonValue {
  if (target === "researchTask") {
    return cloneJson(sharedValidFixture.researchTask);
  }
  if (target === "multiAgentRecipeDefinition") {
    return cloneJson(sharedValidFixture.multiAgentRecipeDefinition);
  }
  if (target === "dispatchRecord") {
    return cloneJson(sharedValidFixture.dispatchRecord);
  }
  throw new Error(`unknown runtime mutation target: ${target}`);
}

function applyRuntimeMutation(value: JsonValue): {
  target: string;
  name: string;
  candidate: JsonValue;
  covers: string[];
  expectedErrorIncludes: string | null;
} {
  const mutation = mutationRecord(value, "runtimeMutation");
  const target = mutationString(mutation.target, "runtimeMutation.target");
  const name = mutationString(mutation.name, "runtimeMutation.name");
  if (!Array.isArray(mutation.path) || mutation.path.length === 0) {
    throw new Error(`runtime mutation ${name} requires a path`);
  }
  const candidate = mutationTarget(target);
  if (mutation.setupFields !== undefined) {
    const candidateRecord = mutationRecord(candidate, `runtime mutation ${name} target`);
    const setupFields = mutationRecord(mutation.setupFields, `runtime mutation ${name}.setupFields`);
    for (const [field, setupValue] of Object.entries(setupFields)) {
      if (!(field in candidateRecord)) {
        throw new Error(`runtime mutation ${name} has an invalid setup field: ${field}`);
      }
      candidateRecord[field] = cloneJson(setupValue);
    }
  }
  let parent = candidate;
  for (const segment of mutation.path.slice(0, -1)) {
    if (typeof segment === "number" && Number.isInteger(segment)) {
      if (!Array.isArray(parent) || parent[segment] === undefined) {
        throw new Error(`runtime mutation ${name} has an invalid array path`);
      }
      parent = parent[segment];
    } else if (typeof segment === "string") {
      const record = mutationRecord(parent, `runtime mutation ${name}`);
      if (!(segment in record)) {
        throw new Error(`runtime mutation ${name} has an invalid object path`);
      }
      parent = record[segment];
    } else {
      throw new Error(`runtime mutation ${name} has an invalid path segment`);
    }
  }
  const finalSegment = mutation.path[mutation.path.length - 1];
  const operation = mutationString(mutation.operation, `runtime mutation ${name}.operation`);
  if (typeof finalSegment === "number" && Number.isInteger(finalSegment)) {
    if (!Array.isArray(parent)) {
      throw new Error(`runtime mutation ${name} has an invalid final array path`);
    }
    if (operation === "delete") {
      parent.splice(finalSegment, 1);
    } else {
      const replacement = mutation.valueToken === "positiveInfinity" ? Number.POSITIVE_INFINITY : mutation.value;
      if (replacement === undefined) {
        throw new Error(`runtime mutation ${name} has no replacement`);
      }
      parent[finalSegment] = replacement;
    }
  } else if (typeof finalSegment === "string") {
    const record = mutationRecord(parent, `runtime mutation ${name}`);
    if (operation === "delete") {
      delete record[finalSegment];
    } else {
      const replacement = mutation.valueToken === "positiveInfinity" ? Number.POSITIVE_INFINITY : mutation.value;
      if (replacement === undefined) {
        throw new Error(`runtime mutation ${name} has no replacement`);
      }
      record[finalSegment] = replacement;
    }
  } else {
    throw new Error(`runtime mutation ${name} has an invalid final path segment`);
  }
  if (!Array.isArray(mutation.covers)) {
    throw new Error(`runtime mutation ${name} requires coverage labels`);
  }
  const covers = mutation.covers.map((label, index) =>
    mutationString(label, `runtime mutation ${name}.covers[${index}]`),
  );
  const expectedErrorIncludes = mutation.expectedErrorIncludes === undefined
    ? null
    : mutationString(mutation.expectedErrorIncludes, `runtime mutation ${name}.expectedErrorIncludes`);
  return { target, name, candidate, covers, expectedErrorIncludes };
}

function parseMutationTarget(target: string, candidate: JsonValue): void {
  if (target === "researchTask") {
    parseResearchTaskV12(candidate);
  } else if (target === "multiAgentRecipeDefinition") {
    assertMultiAgentRecipeDefinition(candidate);
  } else if (target === "dispatchRecord") {
    parseDispatchRecord(candidate);
  } else {
    throw new Error(`unknown runtime mutation target: ${target}`);
  }
}

for (const [name, path, value] of [
  [
    "research-synthesis-runtime-accepted",
    ["researchDesignVersions", 0, "instances", 0, "acceptanceCriteria", 0, "judgmentRequirement"],
    "research_synthesis",
  ],
  [
    "skill-composition-runtime-accepted",
    ["executionDecisions", 0, "strategy"],
    "skill_composition",
  ],
] as const) {
  const mutation = applyRuntimeMutation({
    name,
    target: "researchTask",
    path: [...path],
    operation: "set",
    value,
    covers: [],
  });
  parseResearchTaskV12(mutation.candidate);
}
console.log("T2A_BATCH1_TS_VALID_VARIANTS_ACCEPTED=2");

let rejectedRuntimeMutationCount = 0;
const declaredRuntimeMutationCoverage = new Set<string>();
const expectedRuntimeMutationCoverage = [...researchOrchestrationRuntimeValidatorNames].sort();
const expectedRuntimeMutationCoverageSet = new Set<string>(expectedRuntimeMutationCoverage);
for (const [index, mutationValue] of sharedInvalidFixture.runtimeMutationMatrix.entries()) {
  const mutation = applyRuntimeMutation(importedJsonValue(mutationValue, `runtimeMutationMatrix[${index}]`));
  resetResearchOrchestrationRuntimeValidatorCoverage();
  let rejected = false;
  let rejectionReason = "";
  try {
    parseMutationTarget(mutation.target, mutation.candidate);
  } catch (error) {
    rejected = true;
    rejectionReason = error instanceof Error ? error.message : String(error);
  }
  if (!rejected) {
    throw new Error(`runtime mutation was accepted: ${mutation.name}`);
  }
  if (mutation.expectedErrorIncludes !== null && !rejectionReason.includes(mutation.expectedErrorIncludes)) {
    throw new Error(
      `runtime mutation ${mutation.name} rejected for the wrong reason: ${rejectionReason}`,
    );
  }
  const observedCoverage = new Set(getResearchOrchestrationRuntimeValidatorCoverage());
  for (const label of mutation.covers) {
    if (!expectedRuntimeMutationCoverageSet.has(label)) {
      throw new Error(`runtime mutation ${mutation.name} declares unknown validator coverage: ${label}`);
    }
    if (!observedCoverage.has(label)) {
      throw new Error(`runtime mutation ${mutation.name} did not execute declared validator: ${label}`);
    }
    declaredRuntimeMutationCoverage.add(label);
  }
  rejectedRuntimeMutationCount += 1;
}
const runtimeMutationCoverage = [...declaredRuntimeMutationCoverage].sort();
if (runtimeMutationCoverage.join(",") !== expectedRuntimeMutationCoverage.join(",")) {
  throw new Error(
    `runtime validator coverage mismatch: expected=${expectedRuntimeMutationCoverage.join(",")} ` +
      `observed=${runtimeMutationCoverage.join(",")}`,
  );
}
console.log(`T01_R2_TS_RUNTIME_MUTATIONS_REJECTED=${rejectedRuntimeMutationCount}`);
console.log(`T01_R2_TS_RUNTIME_COVERAGE=${runtimeMutationCoverage.join(",")}`);

let sharedInvalidFixtureRejected = false;
try {
  parseDispatchRecord(sharedInvalidFixture.dispatchRecord);
} catch {
  sharedInvalidFixtureRejected = true;
}
if (!sharedInvalidFixtureRejected) {
  throw new Error("shared invalid research orchestration fixture was accepted");
}

void researchTaskFixture;
void researchSynthesisCriterion;
void skillCompositionStrategy;
void recipeFixture;
void dispatchFixture;
void rejectedLegacyCommand;
void rejectedNullability;
void strictTimestampEvidence;
void strictPositiveIntegerEvidence;
void strictNonNegativeIntegerEvidence;
void strictSha256Evidence;
void interactionEvidenceMatrix;
void handoffRequestedVerdictOnly;
void handoffCompletedVerdictOnly;
void reviewVerdictOnly;
void acceptanceVerdictOnly;
void rejectionVerdictOnly;
void rejectedRequiredOne;
void rejectedRequiredZero;
void rejectedRequiredString;
void rejectedAcceptanceVerdict;
void sharedFixtureResearchTask;
void sharedFixtureDispatchRecord;
