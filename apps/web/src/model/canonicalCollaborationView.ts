import {
  CanonicalCollaborationContractError as ContractError,
  buildCanonicalCollaborationView as buildView,
  decodeCanonicalCollaborationView as decodeView,
  parseCanonicalCollaborationPayload as parsePayload,
} from "./canonicalCollaborationViewCore.mjs";

export type CanonicalCollaborationRole = "执行工作" | "复核工作" | "支持工作";
export type CanonicalCollaborationAllowedAction =
  | "supplement_input"
  | "explicit_retry"
  | "stop";
export type CanonicalCollaborationWorkUnitStatus =
  | "queued"
  | "running"
  | "reviewing"
  | "completed"
  | "failed"
  | "blocked"
  | "cancelled"
  | "external_result_unknown";
export type CanonicalCollaborationArtifactState =
  | "usable"
  | "needs_review"
  | "needs_input"
  | "insufficient_materials"
  | "failed"
  | "partial_success"
  | "rejected_or_needs_revision";

export type CanonicalCollaborationContractErrorKind =
  | "legacy_shape"
  | "unknown_schema"
  | "lifecycle_mismatch"
  | "source_identity_mismatch"
  | "revision_mismatch"
  | "invalid_contract";

export const CanonicalCollaborationContractError = ContractError as unknown as {
  new (kind: CanonicalCollaborationContractErrorKind, message: string): Error & {
    kind: CanonicalCollaborationContractErrorKind;
  };
};

export interface CanonicalCollaborationSnapshot {
  schemaVersion: "canonical_collaboration_v1";
  lifecycleKind: "canonical_v1_2";
  taskId: string;
  taskStatus: "draft" | "not_started" | "running" | "awaiting_confirmation" | "completed" | "failed" | "cancelled";
  revision: number;
  revisionFence: { kind: "research_task_v1_2_revision"; revision: number };
  sourceIdentity: string | null;
  executionDecision: null | {
    executionDecisionId: string;
    decisionSequence: number;
    status: "selected" | "dispatched" | "closed";
    outcome: "succeeded" | "partial" | "progressed" | "needs_input" | "rejected" | "blocked" | "failed" | "cancelled" | null;
    researchDesignVersion: number;
    substageInstanceId: string;
    substageInstanceRevision: number;
    decidedAt: string;
    closedAt: string | null;
  };
  workUnits: Array<{
    workUnitId: string;
    assignmentId: string;
    role: CanonicalCollaborationRole;
    objective: string;
    responsibleActorId: string;
    responsibleActorType: string;
    status: CanonicalCollaborationWorkUnitStatus;
    parallelGroupId: string | null;
    dependencyWorkUnitIds: string[];
    waiting: null | {
      kind: "dependency" | "system_review" | "user_action" | "runtime_unavailable" | "external_result_unknown" | "command_pending" | "blocked";
      reason: string;
      reasonCode: string;
      blockingWorkUnitIds: string[];
    };
    reviewerWorkUnitIds: string[];
    inputArtifactRefs: string[];
    outputArtifactRefs: string[];
    needsReviewArtifactRefs: string[];
    failureReasonCode: string | null;
    dispatchedAt: string;
    finishedAt: string | null;
  }>;
  handoffs: Array<{
    handoffId: string;
    state: "requested" | "completed";
    fromWorkUnitId: string;
    toWorkUnitId: string;
    summary: string;
    artifactRefs: string[];
    verdict: null | {
      decision: "approved" | "rejected" | "insufficient_evidence";
      rationaleCode: string;
    };
    occurredAt: string;
  }>;
  reviews: Array<{
    reviewId: string;
    reviewerWorkUnitId: string;
    targetWorkUnitId: string | null;
    artifactRefs: string[];
    decision: "approved" | "rejected" | "insufficient_evidence";
    rationaleCode: string;
    submittedAt: string;
  }>;
  artifacts: Array<{
    artifactId: string;
    workUnitId: string;
    artifactType: string;
    artifactState: CanonicalCollaborationArtifactState;
    version: number;
    sourceCapabilityCallId: string | null;
  }>;
  userActionItems: Array<{
    itemId: string;
    kind: "provide_input" | "artifact_review_required" | "external_result_unknown" | "explicit_retry_ready" | "supplement_pending" | "supplement_rejected";
    reason: string;
    workUnitId: string | null;
    artifactRefs: string[];
    allowedActions: CanonicalCollaborationAllowedAction[];
  }>;
  allowedActions: CanonicalCollaborationAllowedAction[];
}

export interface CanonicalCollaborationArtifactView {
  key: string;
  label: string;
  state: CanonicalCollaborationArtifactState;
  stateLabel: string;
  version: number;
}

export interface CanonicalCollaborationView {
  schemaVersion: "canonical_collaboration_v1";
  lifecycleKind: "canonical_v1_2";
  taskId: string;
  taskStatus: CanonicalCollaborationSnapshot["taskStatus"];
  revision: number;
  hasExecutionDecision: boolean;
  currentWork: Array<{
    key: string;
    title: string;
    isParallel: boolean;
    items: Array<{
      key: string;
      role: CanonicalCollaborationRole;
      objective: string;
      status: CanonicalCollaborationWorkUnitStatus;
      statusLabel: string;
      dispatchedAt: string | null;
      finishedAt: string | null;
      artifacts: CanonicalCollaborationArtifactView[];
      needsReviewCount: number;
    }>;
  }>;
  waitingAndDependencies: Array<{
    key: string;
    workKey: string;
    role: CanonicalCollaborationRole;
    objective: string;
    reason: string;
    kind: "dependency" | "system_review" | "user_action" | "runtime_unavailable" | "external_result_unknown" | "command_pending" | "blocked";
    dependencies: Array<{ key: string; label: string; role: CanonicalCollaborationRole }>;
    reviewers: Array<{ key: string; label: string; role: CanonicalCollaborationRole }>;
  }>;
  reviewAndHandoffs: Array<
    | {
      key: string;
      type: "handoff";
      occurredAt: string;
      state: "requested" | "completed";
      payload: {
        summary: string;
        stateLabel: string;
        from: { key: string; label: string; role: CanonicalCollaborationRole };
        to: { key: string; label: string; role: CanonicalCollaborationRole };
        artifacts: CanonicalCollaborationArtifactView[];
        verdictLabel: string | null;
      };
    }
    | {
      key: string;
      type: "review";
      occurredAt: string;
      state: "approved" | "rejected" | "insufficient_evidence";
      payload: {
        reviewer: { key: string; label: string; role: CanonicalCollaborationRole };
        target: { key: string; label: string; role: CanonicalCollaborationRole } | null;
        decisionLabel: string;
        artifacts: CanonicalCollaborationArtifactView[];
      };
    }
  >;
  userActionItems: Array<{
    key: string;
    kind: "provide_input" | "artifact_review_required" | "external_result_unknown" | "explicit_retry_ready" | "supplement_pending" | "supplement_rejected";
    title: string;
    reason: string;
    work: { key: string; label: string; role: CanonicalCollaborationRole } | null;
    artifacts: CanonicalCollaborationArtifactView[];
    allowedActions: Array<{ action: CanonicalCollaborationAllowedAction; label: string }>;
  }>;
  allowedActions: CanonicalCollaborationAllowedAction[];
}

export function parseCanonicalCollaborationPayload(
  input: unknown,
  options: { expectedRevision?: number | null } = {},
): CanonicalCollaborationSnapshot {
  return parsePayload(input, options) as CanonicalCollaborationSnapshot;
}

export function buildCanonicalCollaborationView(
  snapshot: CanonicalCollaborationSnapshot,
): CanonicalCollaborationView {
  return buildView(snapshot) as CanonicalCollaborationView;
}

export function decodeCanonicalCollaborationView(
  input: unknown,
  options: { expectedRevision?: number | null } = {},
): CanonicalCollaborationView {
  return decodeView(input, options) as CanonicalCollaborationView;
}
