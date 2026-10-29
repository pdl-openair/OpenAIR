export interface ProjectCompletionRequirement {
  completionRequirementId: string;
  targetOutputRef: string;
  description: string;
  workflowResponsibilityKey?: string;
  contributingResponsibilityKeys?: string[];
}

/** Cross-object checks apply only to the new explicit contribution contract. */
export function assertTargetContributions(
  design: Record<string, unknown>,
  path: string,
): void {
  const requirements =
    design.projectCompletionRequirements as ProjectCompletionRequirement[];
  const responsibilities = design.workflowResponsibilityCoverage as
    WorkflowResponsibilityCoverage[] | undefined;
  type Ref = { substageInstanceId: string; instanceRevision: number };
  const report = design.coverageReport as {
    targets: { targetId: string; sourcePath: string }[];
    claims: {
      targetId: string;
      instanceRefs: Ref[];
      acceptanceCriterionRefs: string[];
    }[];
  } | null;
  const instances = design.instances as (Ref & {
    acceptanceCriteria: { criterionId: string; coverageTargetIds: string[] }[];
  })[];
  const identity = (ref: Ref) =>
    `${ref.substageInstanceId}:${ref.instanceRevision}`;
  for (const requirement of requirements) {
    const keys = requirement.contributingResponsibilityKeys;
    if (keys === undefined) continue;
    const rows = keys.map((key) =>
      responsibilities?.find((row) => row.responsibilityKey === key),
    );
    if (rows.some((row) => row === undefined) || report === null)
      throw new Error(
        `${path} contributions require current responsibilities and coverage`,
      );
    const targets = report.targets.filter(
      (row) => row.sourcePath === requirement.targetOutputRef,
    );
    const claims = report.claims.filter(
      (row) => targets.length === 1 && row.targetId === targets[0].targetId,
    );
    const expected = new Set(
      rows.flatMap((row) => row!.instanceRefs.map(identity)),
    );
    const actual = new Set(claims[0]?.instanceRefs.map(identity));
    if (
      claims.length !== 1 ||
      actual.size !== expected.size ||
      [...expected].some((ref) => !actual.has(ref))
    )
      throw new Error(
        `${path} target claim must cover every current contribution`,
      );
    for (const ref of expected) {
      const instance = instances.find((row) => identity(row) === ref);
      if (
        !instance?.acceptanceCriteria.some(
          (criterion) =>
            claims[0].acceptanceCriterionRefs.includes(criterion.criterionId) &&
            criterion.coverageTargetIds.includes(targets[0].targetId),
        )
      )
        throw new Error(
          `${path} each current contribution needs a target acceptance criterion`,
        );
    }
  }
}

export function assertCompletionTargetSet(
  descriptionsByRef: ReadonlyMap<string, string>,
  targetOutputs: readonly string[],
  path: string,
): void {
  const expectedRefs = targetOutputs.map(
    (_, index) => `understanding.targetOutputs[${index}]`,
  );
  if (
    descriptionsByRef.size !== expectedRefs.length ||
    expectedRefs.some((ref) => !descriptionsByRef.has(ref))
  ) {
    throw new Error(`${path} must cover every target output exactly once`);
  }
  for (const [index, targetOutput] of targetOutputs.entries()) {
    if (descriptionsByRef.get(expectedRefs[index]) !== targetOutput) {
      throw new Error(`${path} must preserve target output semantics`);
    }
  }
}

export interface TargetOutputReuseAcceptance {
  researchDesignId: string;
  researchDesignVersion: number;
  targetOutputRef: string;
  responsibilityKey: string;
  reusedResultRefs: string[];
  acceptanceArtifactRef: string;
  verdict: "accepted";
}
export interface TargetOutputReuseJudgment {
  targetOutputRef: string;
  responsibilityKey: string;
  reusedResultRefs: string[];
  verdict: "accepted";
  rationale: string;
}

export interface WorkflowResponsibilityCoverage {
  responsibilityKey: string;
  definitionId: string;
  requirement: "core" | "conditional";
  coverageMode: "planned_work" | "reused_result" | "not_selected";
  instanceRefs: { substageInstanceId: string; instanceRevision: number }[];
  reusedResultRefs: string[];
  rationale: string;
}
export interface CompletionRequirementAssessment {
  completionRequirementId: string;
  targetOutputRef: string;
  status: "satisfied" | "unmet" | "unresolved";
  coverageTargetId?: string | null;
  acceptanceCriterionRefs?: string[];
  gaps?: string[];
  uncertainty?: string[];
  fulfillmentStatus?:
    "planned_work" | "not_applicable" | "reuse_pending" | "reuse_accepted";
}
export interface RoundEndEvidence {
  reason: "confirmed_scope_settled";
  instanceRefs: { substageInstanceId: string; instanceRevision: number }[];
  sourceDecisionRefs: string[];
  sourceArtifactRefs: string[];
  nonExecutionRefs?: {
    substageInstanceId: string;
    instanceRevision: number;
    reasonCode: string;
  }[];
}
export interface ProjectCompletionDecisionDetails {
  completionAuthorityVersion?:
    "project_completion_v1" | "project_completion_v2" | null;
  roundEnd?: RoundEndEvidence | null;
  researchDesignRef?: { researchDesignId: string; version: number } | null;
  canComplete?: boolean | null;
  requirementAssessments?: CompletionRequirementAssessment[];
  gaps?: string[];
  uncertainty?: string[];
  limitations?: string[];
  rationale?: string | null;
}
interface Validators {
  requireExactRecord: (
    value: unknown,
    path: string,
    fields: readonly string[],
    optionalFields?: readonly string[],
  ) => Record<string, unknown>;
  requireNonEmptyString: (value: unknown, path: string) => string;
  requireNonEmptyStrings: (value: unknown, path: string) => string[];
  requireEnum: (
    value: unknown,
    allowed: readonly string[],
    path: string,
  ) => string;
  assertPositiveInteger: (value: unknown, path: string) => void;
  assertAwareTimestamp: (value: unknown, path: string) => void;
  assertResearchDesignRef: (value: unknown, path: string) => void;
}
export function assertCompletionDecisionContract(
  value: unknown,
  path: string,
  v: Validators,
): void {
  const record = v.requireExactRecord(
    value,
    path,
    [
      "completionDecisionId",
      "scientificStateVersion",
      "disposition",
      "sourceRefs",
      "decidedAt",
    ],
    [
      "completionAuthorityVersion",
      "roundEnd",
      "researchDesignRef",
      "canComplete",
      "requirementAssessments",
      "gaps",
      "uncertainty",
      "limitations",
      "rationale",
    ],
  );
  v.requireNonEmptyString(
    record.completionDecisionId,
    `${path}.completionDecisionId`,
  );
  v.assertPositiveInteger(
    record.scientificStateVersion,
    `${path}.scientificStateVersion`,
  );
  v.requireEnum(
    record.disposition,
    ["full", "partial", "degraded", "evidence_gap"],
    `${path}.disposition`,
  );
  v.requireNonEmptyStrings(record.sourceRefs, `${path}.sourceRefs`);
  v.assertAwareTimestamp(record.decidedAt, `${path}.decidedAt`);
  if (record.completionAuthorityVersion != null)
    v.requireEnum(
      record.completionAuthorityVersion,
      ["project_completion_v1", "project_completion_v2"],
      `${path}.completionAuthorityVersion`,
    );
  if (record.completionAuthorityVersion === "project_completion_v2") {
    if (record.canComplete !== false)
      throw new Error(`${path}.canComplete must preserve the unmet goal`);
    assertRoundEnd(record.roundEnd, `${path}.roundEnd`, v);
  } else if (record.roundEnd != null) {
    throw new Error(`${path}.roundEnd requires project_completion_v2`);
  }
  if (record.researchDesignRef != null)
    v.assertResearchDesignRef(
      record.researchDesignRef,
      `${path}.researchDesignRef`,
    );
  if (record.canComplete != null && typeof record.canComplete !== "boolean")
    throw new Error(`${path}.canComplete must be boolean`);
  if (record.rationale != null)
    v.requireNonEmptyString(record.rationale, `${path}.rationale`);
  for (const field of ["gaps", "uncertainty", "limitations"]) {
    if (record[field] !== undefined)
      v.requireNonEmptyStrings(record[field], `${path}.${field}`);
  }
  if (record.requirementAssessments !== undefined) {
    if (!Array.isArray(record.requirementAssessments))
      throw new Error(`${path}.requirementAssessments must be an array`);
    for (const [index, raw] of record.requirementAssessments.entries()) {
      const rowPath = `${path}.requirementAssessments[${index}]`;
      const row = v.requireExactRecord(
        raw,
        rowPath,
        ["completionRequirementId", "targetOutputRef", "status"],
        [
          "coverageTargetId",
          "acceptanceCriterionRefs",
          "gaps",
          "uncertainty",
          "fulfillmentStatus",
        ],
      );
      v.requireNonEmptyString(
        row.completionRequirementId,
        `${rowPath}.completionRequirementId`,
      );
      v.requireNonEmptyString(
        row.targetOutputRef,
        `${rowPath}.targetOutputRef`,
      );
      v.requireEnum(
        row.status,
        ["satisfied", "unmet", "unresolved"],
        `${rowPath}.status`,
      );
      if (row.coverageTargetId != null)
        v.requireNonEmptyString(
          row.coverageTargetId,
          `${rowPath}.coverageTargetId`,
        );
      for (const field of ["acceptanceCriterionRefs", "gaps", "uncertainty"]) {
        if (row[field] !== undefined)
          v.requireNonEmptyStrings(row[field], `${rowPath}.${field}`);
      }
      if (row.fulfillmentStatus !== undefined)
        v.requireEnum(
          row.fulfillmentStatus,
          ["planned_work", "not_applicable", "reuse_pending", "reuse_accepted"],
          `${rowPath}.fulfillmentStatus`,
        );
    }
  }
}
export function assertWorkflowCoverage(
  value: unknown,
  path: string,
  exact: Validators["requireExactRecord"],
  text: Validators["requireNonEmptyString"],
  strings: Validators["requireNonEmptyStrings"],
  instance: (value: unknown, path: string) => void,
): void {
  if (!Array.isArray(value)) throw new Error(`${path} must be an array`);
  const owners = new Set<string>();
  for (const [index, raw] of value.entries()) {
    const rowPath = `${path}[${index}]`;
    const row = exact(raw, rowPath, [
      "responsibilityKey",
      "definitionId",
      "requirement",
      "coverageMode",
      "instanceRefs",
      "reusedResultRefs",
      "rationale",
    ]);
    const owner = text(row.responsibilityKey, `${rowPath}.responsibilityKey`);
    if (owners.has(owner))
      throw new Error(`${path} responsibility keys must be unique`);
    owners.add(owner);
    text(row.definitionId, `${rowPath}.definitionId`);
    text(row.rationale, `${rowPath}.rationale`);
    if (!["core", "conditional"].includes(String(row.requirement)))
      throw new Error(`${rowPath}.requirement invalid`);
    if (!Array.isArray(row.instanceRefs))
      throw new Error(`${rowPath}.instanceRefs must be an array`);
    row.instanceRefs.forEach((ref, i) =>
      instance(ref, `${rowPath}.instanceRefs[${i}]`),
    );
    const reused = strings(row.reusedResultRefs, `${rowPath}.reusedResultRefs`);
    const refs = row.instanceRefs.map((ref) => JSON.stringify(ref));
    if (
      new Set(refs).size !== refs.length ||
      new Set(reused).size !== reused.length
    )
      throw new Error(`${rowPath} duplicate refs`);
    const valid =
      row.coverageMode === "planned_work"
        ? refs.length > 0 && reused.length === 0
        : row.coverageMode === "reused_result"
          ? refs.length === 0 && reused.length > 0
          : row.coverageMode === "not_selected" &&
            row.requirement === "conditional" &&
            refs.length === 0 &&
            reused.length === 0;
    if (!valid) throw new Error(`${rowPath} invalid responsibility coverage`);
  }
}

export function assertProjectCompletionRequirements(
  value: unknown,
  targetOutputs: string[],
  path: string,
  v: Pick<Validators, "requireExactRecord" | "requireNonEmptyString">,
): void {
  if (!Array.isArray(value)) throw new Error(`${path} must be an array`);
  const requirements = value;
  if (requirements.length === 0) {
    throw new Error(`${path} must contain at least one item`);
  }
  const ids = new Set<string>();
  const descriptionsByRef = new Map<string, string>();
  for (const [index, item] of requirements.entries()) {
    const itemPath = `${path}[${index}]`;
    const requirement = v.requireExactRecord(
      item,
      itemPath,
      ["completionRequirementId", "targetOutputRef", "description"],
      ["workflowResponsibilityKey", "contributingResponsibilityKeys"],
    );
    if (requirement.workflowResponsibilityKey !== undefined) {
      v.requireNonEmptyString(
        requirement.workflowResponsibilityKey,
        `${itemPath}.workflowResponsibilityKey`,
      );
    }
    const contributors = requirement.contributingResponsibilityKeys;
    if (contributors !== undefined) {
      if (
        !Array.isArray(contributors) ||
        contributors.length === 0 ||
        new Set(contributors).size !== contributors.length
      )
        throw new Error(
          `${itemPath}.contributingResponsibilityKeys must be distinct and non-empty`,
        );
      contributors.forEach((key, i) =>
        v.requireNonEmptyString(
          key,
          `${itemPath}.contributingResponsibilityKeys[${i}]`,
        ),
      );
      if (
        requirement.workflowResponsibilityKey !== undefined &&
        (contributors.length !== 1 ||
          contributors[0] !== requirement.workflowResponsibilityKey)
      )
        throw new Error(
          `${itemPath} contributions cannot change the frozen owner`,
        );
    }
    const id = v.requireNonEmptyString(
      requirement.completionRequirementId,
      `${itemPath}.completionRequirementId`,
    );
    const ref = v.requireNonEmptyString(
      requirement.targetOutputRef,
      `${itemPath}.targetOutputRef`,
    );
    const description = v.requireNonEmptyString(
      requirement.description,
      `${itemPath}.description`,
    );
    if (ids.has(id))
      throw new Error(`${path} completionRequirementId values must be unique`);
    if (descriptionsByRef.has(ref))
      throw new Error(`${path} targetOutputRef values must be unique`);
    ids.add(id);
    descriptionsByRef.set(ref, description);
  }
  assertCompletionTargetSet(descriptionsByRef, targetOutputs, path);
}

function assertRoundEnd(value: unknown, path: string, v: Validators): void {
  const record = v.requireExactRecord(
    value,
    path,
    ["reason", "instanceRefs", "sourceDecisionRefs", "sourceArtifactRefs"],
    ["nonExecutionRefs"],
  );
  v.requireEnum(record.reason, ["confirmed_scope_settled"], `${path}.reason`);
  if (!Array.isArray(record.instanceRefs) || record.instanceRefs.length === 0)
    throw new Error(`${path}.instanceRefs must contain the settled scope`);
  const identities = new Set<string>();
  for (const [index, raw] of record.instanceRefs.entries()) {
    const rowPath = `${path}.instanceRefs[${index}]`;
    const row = v.requireExactRecord(raw, rowPath, [
      "substageInstanceId",
      "instanceRevision",
    ]);
    v.requireNonEmptyString(
      row.substageInstanceId,
      `${rowPath}.substageInstanceId`,
    );
    v.assertPositiveInteger(
      row.instanceRevision,
      `${rowPath}.instanceRevision`,
    );
    const identity = JSON.stringify([
      row.substageInstanceId,
      row.instanceRevision,
    ]);
    if (identities.has(identity))
      throw new Error(`${path}.instanceRefs must be unique`);
    identities.add(identity);
  }
  if (record.nonExecutionRefs !== undefined) {
    if (
      !Array.isArray(record.nonExecutionRefs) ||
      record.nonExecutionRefs.length === 0
    )
      throw new Error(
        `${path}.nonExecutionRefs must contain recorded boundaries`,
      );
    const nonExecuted = new Set<string>();
    for (const [index, raw] of record.nonExecutionRefs.entries()) {
      const rowPath = `${path}.nonExecutionRefs[${index}]`;
      const row = v.requireExactRecord(raw, rowPath, [
        "substageInstanceId",
        "instanceRevision",
        "reasonCode",
      ]);
      v.requireNonEmptyString(
        row.substageInstanceId,
        `${rowPath}.substageInstanceId`,
      );
      v.assertPositiveInteger(
        row.instanceRevision,
        `${rowPath}.instanceRevision`,
      );
      v.requireNonEmptyString(row.reasonCode, `${rowPath}.reasonCode`);
      const identity = JSON.stringify([
        row.substageInstanceId,
        row.instanceRevision,
      ]);
      if (!identities.has(identity) || nonExecuted.has(identity))
        throw new Error(
          `${path}.nonExecutionRefs must uniquely belong to the settled scope`,
        );
      nonExecuted.add(identity);
    }
  }
  for (const field of ["sourceDecisionRefs", "sourceArtifactRefs"]) {
    const refs = v.requireNonEmptyStrings(record[field], `${path}.${field}`);
    if (refs.length === 0 || new Set(refs).size !== refs.length)
      throw new Error(`${path}.${field} must contain unique recorded sources`);
  }
}
