import type {
  ActorRef,
  ISODateTime,
  JsonValue,
  PositiveInteger,
  Sha256Hash,
} from "./researchOrchestration";

export type ResearchCapabilityCallStatusV12 =
  "queued" | "running" | "succeeded" | "failed" | "external_result_unknown";

export interface CapabilityCallV12 {
  capabilityCallId: string;
  idempotencyKey: string;
  attemptNumber: PositiveInteger;
  workUnitId: string;
  substageInstanceId: string;
  requestingActorRef: ActorRef;
  capabilityType: "skill" | "tool" | "agent_runtime";
  capabilityId: string;
  runtimeBindingId: string | null;
  status: ResearchCapabilityCallStatusV12;
  inputRefs: string[];
  outputArtifactRefs: string[];
  startedAt: ISODateTime;
  finishedAt: ISODateTime | null;
  internalCallInputSnapshot?: InternalCapabilityCallInput | null;
}

interface CallValidators {
  requireExactRecord(
    value: unknown,
    path: string,
    fields: readonly string[],
    optionalFields?: readonly string[],
  ): Record<string, unknown>;
  requireNonEmptyString(value: unknown, path: string): string;
  assertPositiveInteger(value: unknown, path: string): void;
  assertActorRef(value: unknown, path: string): void;
  requireEnum(
    value: unknown,
    choices: readonly string[],
    path: string,
  ): unknown;
  requireNullableNonEmptyString(value: unknown, path: string): unknown;
  requireNonEmptyStrings(value: unknown, path: string): unknown;
  assertAwareTimestamp(value: unknown, path: string): void;
  assertNullableAwareTimestamp(value: unknown, path: string): void;
  assertJsonObject(value: unknown, path: string): void;
}

export interface InternalCapabilityCallInput {
  contractVersion: 1;
  parentCapabilityCallId: string;
  registrySnapshotHash: Sha256Hash;
  sourceBindingRef: { bindingId: string; bindingVersion: PositiveInteger };
  parameters: Record<string, JsonValue>;
  resolvedExecutionPolicy: Record<string, JsonValue> | null;
  executionTimeoutSeconds: number | null;
  contentHash: Sha256Hash;
}

function assertInternalCallInput(
  value: unknown,
  path: string,
  validators: CallValidators,
): void {
  const record = validators.requireExactRecord(value, path, [
    "contractVersion",
    "parentCapabilityCallId",
    "registrySnapshotHash",
    "sourceBindingRef",
    "parameters",
    "resolvedExecutionPolicy",
    "executionTimeoutSeconds",
    "contentHash",
  ]);
  if (record.contractVersion !== 1)
    throw new Error(`${path}.contractVersion must be 1`);
  validators.requireNonEmptyString(
    record.parentCapabilityCallId,
    `${path}.parentCapabilityCallId`,
  );
  for (const key of ["registrySnapshotHash", "contentHash"]) {
    if (
      typeof record[key] !== "string" ||
      !/^sha256:[0-9a-f]{64}$/.test(record[key])
    ) {
      throw new Error(`${path}.${key} must be a sha256 reference`);
    }
  }
  const binding = validators.requireExactRecord(
    record.sourceBindingRef,
    `${path}.sourceBindingRef`,
    ["bindingId", "bindingVersion"],
  );
  validators.requireNonEmptyString(
    binding.bindingId,
    `${path}.sourceBindingRef.bindingId`,
  );
  validators.assertPositiveInteger(
    binding.bindingVersion,
    `${path}.sourceBindingRef.bindingVersion`,
  );
  validators.assertJsonObject(record.parameters, `${path}.parameters`);
  if (record.resolvedExecutionPolicy !== null) {
    validators.assertJsonObject(
      record.resolvedExecutionPolicy,
      `${path}.resolvedExecutionPolicy`,
    );
  }
  const timeout = record.executionTimeoutSeconds;
  if (
    timeout !== null &&
    (typeof timeout !== "number" || !Number.isFinite(timeout) || timeout <= 0)
  ) {
    throw new Error(`${path}.executionTimeoutSeconds must be positive or null`);
  }
  // Persistence Authority verifies the content hash; this readback validator checks wire shape.
}

export function assertCapabilityCallContract(
  value: unknown,
  path: string,
  validators: CallValidators,
): void {
  const {
    requireExactRecord,
    requireNonEmptyString,
    assertPositiveInteger,
    assertActorRef,
    requireEnum,
    requireNullableNonEmptyString,
    requireNonEmptyStrings,
    assertAwareTimestamp,
    assertNullableAwareTimestamp,
  } = validators;
  const record = requireExactRecord(
    value,
    path,
    [
      "capabilityCallId",
      "idempotencyKey",
      "attemptNumber",
      "workUnitId",
      "substageInstanceId",
      "requestingActorRef",
      "capabilityType",
      "capabilityId",
      "runtimeBindingId",
      "status",
      "inputRefs",
      "outputArtifactRefs",
      "startedAt",
      "finishedAt",
    ],
    ["internalCallInputSnapshot"],
  );
  for (const field of [
    "capabilityCallId",
    "idempotencyKey",
    "workUnitId",
    "substageInstanceId",
    "capabilityId",
  ]) {
    requireNonEmptyString(record[field], `${path}.${field}`);
  }
  assertPositiveInteger(record.attemptNumber, `${path}.attemptNumber`);
  assertActorRef(record.requestingActorRef, `${path}.requestingActorRef`);
  requireEnum(
    record.capabilityType,
    ["skill", "tool", "agent_runtime"],
    `${path}.capabilityType`,
  );
  requireNullableNonEmptyString(
    record.runtimeBindingId,
    `${path}.runtimeBindingId`,
  );
  requireEnum(
    record.status,
    ["queued", "running", "succeeded", "failed", "external_result_unknown"],
    `${path}.status`,
  );
  requireNonEmptyStrings(record.inputRefs, `${path}.inputRefs`);
  requireNonEmptyStrings(
    record.outputArtifactRefs,
    `${path}.outputArtifactRefs`,
  );
  assertAwareTimestamp(record.startedAt, `${path}.startedAt`);
  assertNullableAwareTimestamp(record.finishedAt, `${path}.finishedAt`);
  if (record.internalCallInputSnapshot != null) {
    assertInternalCallInput(
      record.internalCallInputSnapshot,
      `${path}.internalCallInputSnapshot`,
      validators,
    );
  }
}
