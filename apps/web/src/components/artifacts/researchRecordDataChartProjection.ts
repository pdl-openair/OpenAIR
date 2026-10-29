export type JsonObject = Record<string, unknown>;

export interface NormalizedResearchCondition {
  name: string;
  value: string;
  unit: string;
}

export interface ProjectableResearchRecord {
  recordId: string;
  anchorId: string;
  objectLabel: string;
  conditions: NormalizedResearchCondition[];
  conditionFingerprint: string;
  conditionLabel: string;
  sourceLabel: string;
  uncertaintyLabel: string;
}

interface ProjectionOptions {
  allowUncertainty?: boolean;
}

const STRICT_SCALAR_NUMBER = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/;

export function asObject(value: unknown): JsonObject | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : null;
}

export function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function stringListIsEmpty(value: unknown): boolean {
  return Array.isArray(value) && value.length === 0;
}

export function researchRecordAnchorId(
  recordId: string,
  index: number,
): string {
  const stable = recordId.replace(/[^a-zA-Z0-9_-]/g, "-") || `row-${index + 1}`;
  return `research-record-${stable}-${index + 1}`;
}

export function parseStrictScalarNumber(value: unknown): number | null {
  const raw = text(value);
  if (!STRICT_SCALAR_NUMBER.test(raw)) return null;
  const numeric = Number(raw);
  return Number.isFinite(numeric) ? numeric : null;
}

function sourceLabel(source: JsonObject | null): string {
  if (!source) return "";
  const title = text(source.title);
  if (title) return title;
  const doi = text(source.doi).replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "");
  if (doi) return `DOI ${doi}`;
  const arxiv = text(source.arxiv_id);
  if (arxiv) return `arXiv ${arxiv}`;
  return text(source.source_locator) || text(source.provider);
}

function conditionContext(
  record: JsonObject,
): Pick<
  ProjectableResearchRecord,
  "conditions" | "conditionFingerprint" | "conditionLabel"
> | null {
  const status = text(record.condition_status);
  const conditions = record.conditions;
  if (!Array.isArray(conditions)) return null;

  if (status === "not_applicable") {
    return conditions.length === 0
      ? {
          conditions: [],
          conditionFingerprint: "not_applicable",
          conditionLabel: "条件不适用",
        }
      : null;
  }
  if (status !== "reported" || conditions.length === 0) return null;

  const normalized = conditions.map((entry) => {
    const condition = asObject(entry);
    if (!condition || text(condition.status) !== "reported") return null;
    const name = text(condition.name);
    const value = text(condition.value_text);
    const unit = condition.unit === null ? "" : text(condition.unit);
    if (!name || !value) return null;
    return { name, value, unit };
  });
  if (normalized.some((entry) => entry === null)) return null;

  const sorted = normalized
    .filter((entry): entry is NormalizedResearchCondition => entry !== null)
    .sort(
      (left, right) =>
        left.name.localeCompare(right.name) ||
        left.value.localeCompare(right.value) ||
        left.unit.localeCompare(right.unit),
    );
  if (new Set(sorted.map(({ name }) => name)).size !== sorted.length)
    return null;

  return {
    conditions: sorted,
    conditionFingerprint: JSON.stringify(sorted),
    conditionLabel: sorted
      .map(
        ({ name, value, unit }) => `${name} ${value}${unit ? ` ${unit}` : ""}`,
      )
      .join(" · "),
  };
}

/**
 * Shared fail-closed admission for ResearchRecord-backed data charts.
 * It never interprets prose, converts units, or resolves uncertainty.
 */
export function projectableResearchRecord(
  record: JsonObject,
  index: number,
  options: ProjectionOptions = {},
): ProjectableResearchRecord | null {
  const recordId = text(record.record_id);
  const objectLabel = text(record.research_object);
  const source = sourceLabel(asObject(record.source));
  const context = conditionContext(record);
  if (!recordId || !objectLabel || !source || !context) return null;
  if (text(record.information_identity) !== "direct_source_fact") return null;
  if (
    !stringListIsEmpty(record.unknown_fields) ||
    !stringListIsEmpty(record.qualifiers)
  )
    return null;
  const uncertaintyLabel = text(record.uncertainty);
  if (
    (record.uncertainty !== null && typeof record.uncertainty !== "string") ||
    (record.uncertainty !== null && !options.allowUncertainty)
  )
    return null;
  return {
    recordId,
    anchorId: researchRecordAnchorId(recordId, index),
    objectLabel,
    sourceLabel: source,
    uncertaintyLabel,
    ...context,
  };
}
