/** Readback of the existing canonical planning-source provenance contract. */
import type { Sha256Hash } from "./researchOrchestration";

export interface PlanningSourceUsage {
  sourceKind: string;
  sourceId: string;
  sourceVersion: string;
  sourceArtifactRef: string;
  sourceContentHash: Sha256Hash;
  adoptedSourcePaths: string[];
  notAdoptedSourcePaths: string[];
  revalidationSourcePaths: string[];
}

interface SourceValidators {
  requireExactRecord(
    value: unknown,
    path: string,
    fields: readonly string[],
  ): Record<string, unknown>;
  requireNonEmptyString(value: unknown, path: string): string;
  requireNonEmptyStrings(value: unknown, path: string): string[];
}

function assertSourceUsage(
  value: unknown,
  path: string,
  validators: SourceValidators,
): Record<string, unknown> {
  const record = validators.requireExactRecord(value, path, [
    "sourceKind",
    "sourceId",
    "sourceVersion",
    "sourceArtifactRef",
    "sourceContentHash",
    "adoptedSourcePaths",
    "notAdoptedSourcePaths",
    "revalidationSourcePaths",
  ]);
  for (const field of [
    "sourceKind",
    "sourceId",
    "sourceVersion",
    "sourceArtifactRef",
  ]) {
    validators.requireNonEmptyString(record[field], `${path}.${field}`);
  }
  if (
    typeof record.sourceContentHash !== "string" ||
    !/^sha256:[0-9a-f]{64}$/.test(record.sourceContentHash)
  ) {
    throw new Error(`${path}.sourceContentHash must be a sha256 hash`);
  }
  const paths = [
    "adoptedSourcePaths",
    "notAdoptedSourcePaths",
    "revalidationSourcePaths",
  ].flatMap((field) =>
    validators.requireNonEmptyStrings(record[field], `${path}.${field}`),
  );
  if (!paths.length || new Set(paths).size !== paths.length) {
    throw new Error(`${path} must classify distinct, disjoint source paths`);
  }
  return record;
}

export function assertPlanningSourceUsages(
  value: unknown,
  path: string,
  validators: SourceValidators,
): void {
  if (!Array.isArray(value)) throw new Error(`${path} must be an array`);
  const identities = new Set<string>();
  for (const [index, source] of value.entries()) {
    const record = assertSourceUsage(source, `${path}[${index}]`, validators);
    const identity = JSON.stringify([
      record.sourceKind,
      record.sourceId,
      record.sourceVersion,
    ]);
    if (identities.has(identity))
      throw new Error(`${path} contains duplicate source identities`);
    identities.add(identity);
  }
}
