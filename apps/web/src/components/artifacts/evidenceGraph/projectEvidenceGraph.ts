export type EvidenceDisposition =
  "supported" | "refuted" | "limited" | "uncertain";

type JsonObject = Record<string, unknown>;

export interface ProjectedEvidenceNode {
  recordId: string;
  researchObject: string;
  resultStatement: string;
  sourceTitle: string;
  sourceLabel: string;
  sourceLocator: string;
  provider: string;
  sectionName: string;
  doi: string;
  arxivId: string;
  recordType: string;
  uncertainty: string;
  conflicted: boolean;
  gapRelated: boolean;
}

export interface ProjectedJudgmentNode {
  /** Local rendering key only; not a scientific identity. */
  viewKey: string;
  statement: string;
  disposition: EvidenceDisposition;
  scopeBoundary: string;
  rationale: string;
  confidence: number | null;
}

export interface ProjectedEvidenceRelation {
  /** Local rendering key only; not a scientific identity. */
  viewKey: string;
  sourceRecordId: string;
  targetJudgmentKey: string;
  disposition: EvidenceDisposition;
}

export interface ProjectedConflict {
  /** Local rendering key only; not a scientific identity. */
  viewKey: string;
  statement: string;
  implication: string;
  recordIds: string[];
}

export interface ProjectedEvidenceGap {
  /** Local rendering key only; not a scientific identity. */
  viewKey: string;
  gap: string;
  impact: string;
  relatedRecordIds: string[];
}

export interface EvidenceGraphProjection {
  evidenceNodes: ProjectedEvidenceNode[];
  judgmentNodes: ProjectedJudgmentNode[];
  relations: ProjectedEvidenceRelation[];
  conflicts: ProjectedConflict[];
  gaps: ProjectedEvidenceGap[];
  omitted: {
    researchRecords: number;
    judgments: number;
    conflicts: number;
    gaps: number;
  };
}

const RECORD_ID_PATTERN = /^rr:[0-9a-f]{32}$/;
const DISPOSITIONS = new Set<EvidenceDisposition>([
  "supported",
  "refuted",
  "limited",
  "uncertain",
]);

function asObject(value: unknown): JsonObject | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : null;
}

function asObjectArray(value: unknown): JsonObject[] {
  return Array.isArray(value)
    ? value.map(asObject).filter((item): item is JsonObject => Boolean(item))
    : [];
}

function scalar(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function numeric(value: unknown): number | null {
  return typeof value === "number" &&
    Number.isFinite(value) &&
    value >= 0 &&
    value <= 1
    ? value
    : null;
}

function recordIdArray(value: unknown, minimum = 0): string[] | null {
  if (!Array.isArray(value) || value.length < minimum) return null;
  const ids = value.map(scalar);
  if (ids.some((id) => !RECORD_ID_PATTERN.test(id))) return null;
  if (new Set(ids).size !== ids.length) return null;
  return [...ids].sort();
}

function disposition(value: unknown): EvidenceDisposition | null {
  const candidate = scalar(value) as EvidenceDisposition;
  return DISPOSITIONS.has(candidate) ? candidate : null;
}

function assessmentDocument(value: unknown): JsonObject {
  const outer = asObject(value) ?? {};
  return asObject(outer.evidence_assessment_and_research_synthesis) ?? outer;
}

function researchRecordRows(value: unknown): JsonObject[] {
  if (Array.isArray(value)) return asObjectArray(value);
  const document = asObject(value);
  return document ? asObjectArray(document.research_records) : [];
}

interface ParsedSource {
  title: string;
  label: string;
  locator: string;
  provider: string;
  sectionName: string;
  doi: string;
  arxivId: string;
}

function parseSource(source: JsonObject): ParsedSource | null {
  const title = scalar(source.title);
  const doi = scalar(source.doi);
  const arxivId = scalar(source.arxiv_id);
  const provider = scalar(source.provider);
  const locator = scalar(source.source_locator);
  const sectionName = scalar(source.section_name);

  // ResearchRecord Authority requires provider + source_locator. Requiring those
  // here prevents the graph from manufacturing a provenance identity from a
  // partial object while preserving DOI exactly as supplied by the record.
  if (!provider || !locator) return null;

  const normalizedDoi = doi.replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "");
  const identity =
    title ||
    (normalizedDoi ? `DOI ${normalizedDoi}` : "") ||
    (arxivId ? `arXiv ${arxivId}` : "") ||
    provider;
  const qualifiers = [
    title && normalizedDoi ? `DOI ${normalizedDoi}` : "",
    title && !normalizedDoi && arxivId ? `arXiv ${arxivId}` : "",
    sectionName,
  ].filter(Boolean);

  return {
    title,
    label: [identity, ...qualifiers].join(" · "),
    locator,
    provider,
    sectionName,
    doi,
    arxivId,
  };
}

interface ParsedResearchRecord {
  recordId: string;
  researchObject: string;
  resultStatement: string;
  sourceTitle: string;
  sourceLabel: string;
  sourceLocator: string;
  provider: string;
  sectionName: string;
  doi: string;
  arxivId: string;
  recordType: string;
  uncertainty: string;
}

function parseResearchRecord(row: JsonObject): ParsedResearchRecord | null {
  const recordId = scalar(row.record_id);
  const researchObject = scalar(row.research_object);
  const result = asObject(row.result);
  const resultStatement = scalar(result?.statement);
  const source = asObject(row.source);
  const sourceIdentity = source ? parseSource(source) : null;

  if (
    !RECORD_ID_PATTERN.test(recordId) ||
    !researchObject ||
    !resultStatement ||
    !sourceIdentity
  ) {
    return null;
  }

  return {
    recordId,
    researchObject,
    resultStatement,
    sourceTitle: sourceIdentity.title,
    sourceLabel: sourceIdentity.label,
    sourceLocator: sourceIdentity.locator,
    provider: sourceIdentity.provider,
    sectionName: sourceIdentity.sectionName,
    doi: sourceIdentity.doi,
    arxivId: sourceIdentity.arxivId,
    recordType: scalar(row.record_type),
    uncertainty: scalar(row.uncertainty),
  };
}

function canonicalJudgmentKey(row: {
  statement: string;
  disposition: EvidenceDisposition;
  recordIds: string[];
  scopeBoundary: string;
  rationale: string;
  confidence: number | null;
}): string {
  return JSON.stringify([
    row.statement,
    row.disposition,
    row.recordIds,
    row.scopeBoundary,
    row.rationale,
    row.confidence,
  ]);
}

function canonicalConflictKey(row: Omit<ProjectedConflict, "viewKey">): string {
  return JSON.stringify([row.statement, row.implication, row.recordIds]);
}

function canonicalGapKey(row: Omit<ProjectedEvidenceGap, "viewKey">): string {
  return JSON.stringify([row.gap, row.impact, row.relatedRecordIds]);
}

/**
 * Deterministically projects already-authorized OT assessment facts into a
 * read-only Evidence Graph view model.
 *
 * No scientific relation is inferred here. Evidence -> Judgment edges are
 * created only from evidence_judgments[].record_ids. Conflict and gap links are
 * separate auxiliary relations and never become support/refute/limit/uncertain
 * edges. Any assessment row that references a missing or ambiguous
 * ResearchRecord is omitted as a whole so the renderer cannot silently present
 * a partial scientific statement.
 */
export function projectEvidenceGraph(
  assessmentInput: unknown,
  researchRecordsInput: unknown,
): EvidenceGraphProjection {
  const assessment = assessmentDocument(assessmentInput);
  const recordRows = researchRecordRows(researchRecordsInput);

  const recordIdCounts = new Map<string, number>();
  for (const row of recordRows) {
    const rawRecordId = scalar(row.record_id);
    if (!RECORD_ID_PATTERN.test(rawRecordId)) continue;
    recordIdCounts.set(rawRecordId, (recordIdCounts.get(rawRecordId) ?? 0) + 1);
  }
  const parsedRecords = recordRows.map(parseResearchRecord);

  const recordMap = new Map<string, ParsedResearchRecord>();
  let omittedResearchRecords = 0;
  for (const record of parsedRecords) {
    if (!record || recordIdCounts.get(record.recordId) !== 1) {
      omittedResearchRecords += 1;
      continue;
    }
    recordMap.set(record.recordId, record);
  }

  const rawJudgments = asObjectArray(assessment.evidence_judgments);
  const parsedJudgments: Array<{
    statement: string;
    disposition: EvidenceDisposition;
    recordIds: string[];
    scopeBoundary: string;
    rationale: string;
    confidence: number | null;
  }> = [];
  let omittedJudgments = 0;

  for (const row of rawJudgments) {
    const statement = scalar(row.statement);
    const relation = disposition(row.disposition);
    const recordIds = recordIdArray(row.record_ids, 1);
    const scopeBoundary = scalar(row.scope_boundary);
    const rationale = scalar(row.rationale);
    const confidence = numeric(row.confidence);

    if (
      !statement ||
      !relation ||
      !recordIds ||
      !scopeBoundary ||
      !rationale ||
      confidence === null ||
      recordIds.some((recordId) => !recordMap.has(recordId))
    ) {
      omittedJudgments += 1;
      continue;
    }

    parsedJudgments.push({
      statement,
      disposition: relation,
      recordIds,
      scopeBoundary,
      rationale,
      confidence,
    });
  }

  parsedJudgments.sort((left, right) =>
    canonicalJudgmentKey(left).localeCompare(canonicalJudgmentKey(right)),
  );

  const judgmentNodes: ProjectedJudgmentNode[] = parsedJudgments.map(
    (row, index) => ({
      viewKey: String(index + 1).padStart(2, "0"),
      statement: row.statement,
      disposition: row.disposition,
      scopeBoundary: row.scopeBoundary,
      rationale: row.rationale,
      confidence: row.confidence,
    }),
  );

  const relations: ProjectedEvidenceRelation[] = [];
  // Keep supplied records visible even before a judgment cites them.
  const graphRecordIds = new Set(recordMap.keys());
  parsedJudgments.forEach((row, index) => {
    const targetJudgmentKey = judgmentNodes[index].viewKey;
    for (const recordId of row.recordIds) {
      graphRecordIds.add(recordId);
      relations.push({
        viewKey: `${recordId}->judgment:${targetJudgmentKey}`,
        sourceRecordId: recordId,
        targetJudgmentKey,
        disposition: row.disposition,
      });
    }
  });
  relations.sort((left, right) => left.viewKey.localeCompare(right.viewKey));

  const rawConflicts = asObjectArray(assessment.conflicts);
  const parsedConflicts: Array<Omit<ProjectedConflict, "viewKey">> = [];
  let omittedConflicts = 0;
  for (const row of rawConflicts) {
    const statement = scalar(row.statement);
    const implication = scalar(row.implication);
    const recordIds = recordIdArray(row.record_ids, 2);
    if (
      !statement ||
      !implication ||
      !recordIds ||
      recordIds.some((recordId) => !recordMap.has(recordId))
    ) {
      omittedConflicts += 1;
      continue;
    }
    parsedConflicts.push({ statement, implication, recordIds });
    recordIds.forEach((recordId) => graphRecordIds.add(recordId));
  }
  parsedConflicts.sort((left, right) =>
    canonicalConflictKey(left).localeCompare(canonicalConflictKey(right)),
  );
  const conflicts: ProjectedConflict[] = parsedConflicts.map((row, index) => ({
    viewKey: String(index + 1).padStart(2, "0"),
    ...row,
  }));
  const conflictedRecordIds = new Set(
    conflicts.flatMap((row) => row.recordIds),
  );

  const rawGaps = asObjectArray(assessment.evidence_gaps);
  const parsedGaps: Array<Omit<ProjectedEvidenceGap, "viewKey">> = [];
  let omittedGaps = 0;
  for (const row of rawGaps) {
    const gap = scalar(row.gap);
    const impact = scalar(row.impact);
    const relatedIds = recordIdArray(row.related_record_ids, 0);
    if (
      !gap ||
      !impact ||
      !relatedIds ||
      relatedIds.some((recordId) => !recordMap.has(recordId))
    ) {
      omittedGaps += 1;
      continue;
    }
    parsedGaps.push({ gap, impact, relatedRecordIds: relatedIds });
    relatedIds.forEach((recordId) => graphRecordIds.add(recordId));
  }
  parsedGaps.sort((left, right) =>
    canonicalGapKey(left).localeCompare(canonicalGapKey(right)),
  );
  const gaps: ProjectedEvidenceGap[] = parsedGaps.map((row, index) => ({
    viewKey: String(index + 1).padStart(2, "0"),
    ...row,
  }));
  const gapRelatedRecordIds = new Set(
    gaps.flatMap((row) => row.relatedRecordIds),
  );

  const evidenceNodes: ProjectedEvidenceNode[] = [...graphRecordIds]
    .sort()
    .map((recordId) => {
      const record = recordMap.get(recordId)!;
      return {
        recordId,
        researchObject: record.researchObject,
        resultStatement: record.resultStatement,
        sourceTitle: record.sourceTitle,
        sourceLabel: record.sourceLabel,
        sourceLocator: record.sourceLocator,
        provider: record.provider,
        sectionName: record.sectionName,
        doi: record.doi,
        arxivId: record.arxivId,
        recordType: record.recordType,
        uncertainty: record.uncertainty,
        conflicted: conflictedRecordIds.has(recordId),
        gapRelated: gapRelatedRecordIds.has(recordId),
      };
    });

  return {
    evidenceNodes,
    judgmentNodes,
    relations,
    conflicts,
    gaps,
    omitted: {
      researchRecords: omittedResearchRecords,
      judgments: omittedJudgments,
      conflicts: omittedConflicts,
      gaps: omittedGaps,
    },
  };
}
