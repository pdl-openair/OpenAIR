import type {
  JsonValue,
  PositiveInteger,
  CandidateRejectionCode,
  CandidateExecutionBlockerCode,
  SemanticFitEvidence,
} from "./researchOrchestration";

export interface ExecutionMethodAssignmentMetadata {
  executionMethodRoleId?: string | null;
  executionMethodRoleControl?: Record<string, JsonValue> | null;
  executionMethodIteration?: PositiveInteger | null;
}

export interface ExecutionMethodWorkMetadata extends ExecutionMethodAssignmentMetadata {
  executionMethodRef?: Record<string, JsonValue> | null;
  resolvedParameters?: Record<string, JsonValue> | null;
  resolvedExecutionPolicy?: Record<string, JsonValue> | null;
  inputScientificStateVersion?: PositiveInteger | null;
}

export interface ExecutionMethodRoleEvaluation {
  roleRef: string;
  eligible: boolean;
  rejectionCodes: CandidateRejectionCode[];
  executableNow: boolean;
  executionBlockerCodes: CandidateExecutionBlockerCode[];
  semanticFitEvidence: SemanticFitEvidence;
}

interface MethodValidators {
  requireExactRecord(
    value: unknown,
    path: string,
    fields: readonly string[],
    optionalFields?: readonly string[],
  ): Record<string, unknown>;
  requireNonEmptyString(value: unknown, path: string): string;
  requireNullableNonEmptyString(value: unknown, path: string): string | null;
  requireNonEmptyStrings(value: unknown, path: string): string[];
  requireEnum(value: unknown, choices: readonly string[], path: string): string;
  requireBoolean(value: unknown, path: string): boolean;
  assertObjectArray(
    value: unknown,
    path: string,
    validator: (value: unknown, path: string) => void,
  ): void;
  assertPositiveInteger(value: unknown, path: string): void;
  assertNullablePositiveInteger(value: unknown, path: string): void;
  assertBindingRef(value: unknown, path: string): void;
  assertActorRef(value: unknown, path: string): void;
  assertExecutorRef(value: unknown, path: string): void;
  assertSkillRef(value: unknown, path: string): void;
  assertAcceptanceCriterion(value: unknown, path: string): void;
  assertRegistrySnapshot(value: unknown, path: string): void;
  assertCanonicalArtifactSnapshot(value: unknown, path: string): void;
  assertJsonObject(value: unknown, path: string): void;
  assertAuthorizationSnapshot(value: unknown, path: string): void;
  assertBudgetSnapshot(value: unknown, path: string): void;
  assertRiskSnapshot(value: unknown, path: string): void;
  assertAwareTimestamp(value: unknown, path: string): void;
  assertNullableAwareTimestamp(value: unknown, path: string): void;
  assertSemanticFitEvidence(value: unknown, path: string): void;
}

export function assertExecutionMethodReference(
  value: unknown,
  path: string,
  validators: MethodValidators,
): void {
  validators.assertJsonObject(value, path);
  const record = value as Record<string, unknown>;
  validators.requireNonEmptyString(record.methodId, `${path}.methodId`);
  validators.assertPositiveInteger(
    record.methodVersion,
    `${path}.methodVersion`,
  );
}

function assertMethodMetadata(
  record: Record<string, unknown>,
  path: string,
  validators: MethodValidators,
): void {
  if (record.executionMethodRoleId !== undefined) {
    validators.requireNullableNonEmptyString(
      record.executionMethodRoleId,
      `${path}.executionMethodRoleId`,
    );
  }
  for (const field of [
    "executionMethodRoleControl",
    "resolvedParameters",
    "resolvedExecutionPolicy",
  ]) {
    if (record[field] != null)
      validators.assertJsonObject(record[field], `${path}.${field}`);
  }
  if (record.executionMethodRef != null) {
    assertExecutionMethodReference(
      record.executionMethodRef,
      `${path}.executionMethodRef`,
      validators,
    );
  }
  for (const field of [
    "executionMethodIteration",
    "inputScientificStateVersion",
  ]) {
    if (record[field] !== undefined)
      validators.assertNullablePositiveInteger(
        record[field],
        `${path}.${field}`,
      );
  }
}

export function assertExecutionMethodRoleEvaluations(
  value: unknown,
  path: string,
  validators: MethodValidators,
): void {
  const roles = new Set<string>();
  validators.assertObjectArray(value, path, (item, itemPath) => {
    const row = validators.requireExactRecord(item, itemPath, [
      "roleRef",
      "eligible",
      "rejectionCodes",
      "executableNow",
      "executionBlockerCodes",
      "semanticFitEvidence",
    ]);
    const role = validators.requireNonEmptyString(
      row.roleRef,
      `${itemPath}.roleRef`,
    );
    if (roles.has(role)) throw new Error(`${path} has duplicate roleRef`);
    roles.add(role);
    const eligible = validators.requireBoolean(
      row.eligible,
      `${itemPath}.eligible`,
    );
    const executable = validators.requireBoolean(
      row.executableNow,
      `${itemPath}.executableNow`,
    );
    const rejections = validators.requireNonEmptyStrings(
      row.rejectionCodes,
      `${itemPath}.rejectionCodes`,
    );
    const blockers = validators.requireNonEmptyStrings(
      row.executionBlockerCodes,
      `${itemPath}.executionBlockerCodes`,
    );
    for (const code of rejections)
      validators.requireEnum(
        code,
        [
          "disabled",
          "role_mismatch",
          "input_incompatible",
          "output_incompatible",
          "agent_skill_not_granted",
          "explicitly_not_for",
          "semantic_fit_insufficient",
        ],
        `${itemPath}.rejectionCodes`,
      );
    for (const code of blockers)
      validators.requireEnum(
        code,
        [
          "required_input_missing",
          "authorization_denied",
          "risk_exceeded",
          "budget_exceeded",
          "runtime_unavailable",
          "provider_unavailable",
        ],
        `${itemPath}.executionBlockerCodes`,
      );
    if (
      eligible === Boolean(rejections.length) ||
      executable === Boolean(blockers.length) ||
      (!eligible && executable)
    )
      throw new Error(`${itemPath} has inconsistent role evidence`);
    validators.assertSemanticFitEvidence(
      row.semanticFitEvidence,
      `${itemPath}.semanticFitEvidence`,
    );
  });
}

export function assertExecutionAssignmentContract(
  value: unknown,
  path: string,
  validators: MethodValidators,
): void {
  const {
    requireExactRecord,
    requireNonEmptyString,
    requireNullableNonEmptyString,
    requireNonEmptyStrings,
    requireEnum,
    assertObjectArray,
    assertBindingRef,
    assertActorRef,
    assertExecutorRef,
    assertSkillRef,
    assertAcceptanceCriterion,
  } = validators;
  const record = requireExactRecord(
    value,
    path,
    [
      "assignmentId",
      "recipeTemplateKey",
      "role",
      "completionCoverage",
      "objective",
      "sourceBindingRefs",
      "responsibleActorRef",
      "executorRef",
      "allowedSkillRefs",
      "inputRefs",
      "expectedArtifactTypes",
      "acceptanceCriteria",
      "dependencyAssignmentIds",
    ],
    [
      "executionMethodRoleId",
      "executionMethodRoleControl",
      "executionMethodIteration",
    ],
  );
  assertMethodMetadata(record, path, validators);
  requireNonEmptyString(record.assignmentId, `${path}.assignmentId`);
  requireNullableNonEmptyString(
    record.recipeTemplateKey,
    `${path}.recipeTemplateKey`,
  );
  requireEnum(record.role, ["primary", "reviewer", "support"], `${path}.role`);
  requireEnum(
    record.completionCoverage,
    ["completion_capable", "progress_capable"],
    `${path}.completionCoverage`,
  );
  requireNonEmptyString(record.objective, `${path}.objective`);
  assertObjectArray(
    record.sourceBindingRefs,
    `${path}.sourceBindingRefs`,
    assertBindingRef,
  );
  assertActorRef(record.responsibleActorRef, `${path}.responsibleActorRef`);
  assertExecutorRef(record.executorRef, `${path}.executorRef`);
  assertObjectArray(
    record.allowedSkillRefs,
    `${path}.allowedSkillRefs`,
    assertSkillRef,
  );
  requireNonEmptyStrings(record.inputRefs, `${path}.inputRefs`);
  requireNonEmptyStrings(
    record.expectedArtifactTypes,
    `${path}.expectedArtifactTypes`,
  );
  assertObjectArray(
    record.acceptanceCriteria,
    `${path}.acceptanceCriteria`,
    assertAcceptanceCriterion,
  );
  requireNonEmptyStrings(
    record.dependencyAssignmentIds,
    `${path}.dependencyAssignmentIds`,
  );
}

export function assertWorkUnitContract(
  value: unknown,
  path: string,
  validators: MethodValidators,
): void {
  const {
    requireExactRecord,
    requireNonEmptyString,
    requireNullableNonEmptyString,
    requireNonEmptyStrings,
    requireEnum,
    requireBoolean,
    assertObjectArray,
    assertPositiveInteger,
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
  } = validators;
  const record = requireExactRecord(
    value,
    path,
    [
      "workUnitId",
      "idempotencyKey",
      "taskId",
      "executionDecisionId",
      "assignmentId",
      "researchDesignVersion",
      "substageInstanceId",
      "substageInstanceRevision",
      "substageDefinitionId",
      "substageDefinitionVersion",
      "registrySnapshotRef",
      "responsibleActorRef",
      "executorRef",
      "instruction",
      "inputArtifactRefs",
      "inputArtifactSnapshots",
      "inputMemoryRefs",
      "invocationContext",
      "allowedSkillRefs",
      "authorizationSnapshot",
      "budgetSnapshot",
      "riskSnapshot",
      "expectedArtifactTypes",
      "acceptanceCriteria",
      "stopConditions",
      "dependencyWorkUnitIds",
      "executionGroupId",
      "policyVersion",
      "sourceMode",
      "dispatchable",
      "status",
      "terminalReasonCode",
      "dispatchedAt",
      "frozenAt",
      "finishedAt",
    ],
    [
      "executionMethodRoleId",
      "executionMethodRoleControl",
      "executionMethodIteration",
      "executionMethodRef",
      "resolvedParameters",
      "resolvedExecutionPolicy",
      "inputScientificStateVersion",
    ],
  );
  assertMethodMetadata(record, path, validators);
  for (const field of [
    "workUnitId",
    "idempotencyKey",
    "taskId",
    "executionDecisionId",
    "assignmentId",
    "substageInstanceId",
    "substageDefinitionId",
    "instruction",
    "policyVersion",
  ]) {
    requireNonEmptyString(record[field], `${path}.${field}`);
  }
  for (const field of [
    "researchDesignVersion",
    "substageInstanceRevision",
    "substageDefinitionVersion",
  ]) {
    assertPositiveInteger(record[field], `${path}.${field}`);
  }
  assertRegistrySnapshot(
    record.registrySnapshotRef,
    `${path}.registrySnapshotRef`,
  );
  assertActorRef(record.responsibleActorRef, `${path}.responsibleActorRef`);
  assertExecutorRef(record.executorRef, `${path}.executorRef`);
  for (const field of [
    "inputArtifactRefs",
    "inputMemoryRefs",
    "expectedArtifactTypes",
    "stopConditions",
    "dependencyWorkUnitIds",
  ]) {
    requireNonEmptyStrings(record[field], `${path}.${field}`);
  }
  assertObjectArray(
    record.inputArtifactSnapshots,
    `${path}.inputArtifactSnapshots`,
    assertCanonicalArtifactSnapshot,
  );
  assertJsonObject(record.invocationContext, `${path}.invocationContext`);
  assertObjectArray(
    record.allowedSkillRefs,
    `${path}.allowedSkillRefs`,
    assertSkillRef,
  );
  assertAuthorizationSnapshot(
    record.authorizationSnapshot,
    `${path}.authorizationSnapshot`,
  );
  assertBudgetSnapshot(record.budgetSnapshot, `${path}.budgetSnapshot`);
  assertRiskSnapshot(record.riskSnapshot, `${path}.riskSnapshot`);
  assertObjectArray(
    record.acceptanceCriteria,
    `${path}.acceptanceCriteria`,
    assertAcceptanceCriterion,
  );
  requireNullableNonEmptyString(
    record.executionGroupId,
    `${path}.executionGroupId`,
  );
  requireEnum(
    record.sourceMode,
    ["live", "legacy_projection"],
    `${path}.sourceMode`,
  );
  requireBoolean(record.dispatchable, `${path}.dispatchable`);
  requireEnum(
    record.status,
    [
      "queued",
      "running",
      "reviewing",
      "completed",
      "failed",
      "blocked",
      "cancelled",
      "external_result_unknown",
    ],
    `${path}.status`,
  );
  requireNullableNonEmptyString(
    record.terminalReasonCode,
    `${path}.terminalReasonCode`,
  );
  assertAwareTimestamp(record.dispatchedAt, `${path}.dispatchedAt`);
  assertAwareTimestamp(record.frozenAt, `${path}.frozenAt`);
  assertNullableAwareTimestamp(record.finishedAt, `${path}.finishedAt`);
}
