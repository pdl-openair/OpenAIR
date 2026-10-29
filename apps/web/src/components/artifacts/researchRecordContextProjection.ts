import { projectResearchRecordComparison } from "./researchRecordComparisonProjection";
import {
  asObject,
  parseStrictScalarNumber,
  projectableResearchRecord,
  text,
  type JsonObject,
  type NormalizedResearchCondition,
  type ProjectableResearchRecord,
} from "./researchRecordDataChartProjection";
import { projectResearchRecordTrend } from "./researchRecordTrendProjection";

export interface ContextDatum {
  recordId: string;
  anchorId: string;
  objectLabel: string;
  reportedValue: string;
  conditions: NormalizedResearchCondition[];
  uncertaintyLabel: string;
  sourceLabel: string;
}

export interface ContextViewModel {
  metric: string;
  unit: string;
  rows: ContextDatum[];
  commonConditions: NormalizedResearchCondition[];
  differentConditionNames: string[];
}

interface ContextCandidate {
  base: ProjectableResearchRecord;
  metric: string;
  unit: string;
  reportedValue: string;
}

const INTERPRETIVE_UNCERTAINTY =
  /(?:±|\+\s*\/\s*-|\b(?:sd|sem|ci|confidence interval|standard deviation|standard error)\b|\bp\s*[<=>])/i;
const DIRECT_CONDITION_VALUE = /^[\p{L}\p{N}._/+%°-]+$/u;

function candidateFor(
  record: JsonObject,
  index: number,
): ContextCandidate | null {
  const base = projectableResearchRecord(record, index, {
    allowUncertainty: true,
  });
  const result = asObject(record.result);
  const values = Array.isArray(result?.values) ? result.values : [];
  if (!base || base.conditions.length === 0 || values.length !== 1) return null;
  if (base.conditions.some(({ value }) => !DIRECT_CONDITION_VALUE.test(value)))
    return null;
  if (
    base.uncertaintyLabel &&
    INTERPRETIVE_UNCERTAINTY.test(base.uncertaintyLabel)
  )
    return null;

  const value = asObject(values[0]);
  const metric = text(value?.name);
  const unit = text(value?.unit);
  const reportedValue = text(value?.value_text);
  if (!metric || !unit || parseStrictScalarNumber(reportedValue) === null)
    return null;
  return { base, metric, unit, reportedValue };
}

function sameConditionNames(
  left: NormalizedResearchCondition[],
  right: NormalizedResearchCondition[],
): boolean {
  return (
    left.length === right.length &&
    left.every((condition, index) => condition.name === right[index]?.name)
  );
}

/**
 * Presentation-only T3 projection. Values remain reported observations: this
 * model deliberately computes no ranking, delta, error bound, or attribution.
 */
export function projectResearchRecordContext(
  records: JsonObject[],
): ContextViewModel | null {
  if (records.length < 2) return null;
  if (
    projectResearchRecordTrend(records) ||
    projectResearchRecordComparison(records)
  )
    return null;

  const candidates = records.map(candidateFor);
  if (candidates.some((candidate) => candidate === null)) return null;
  const admitted = candidates.filter(
    (candidate): candidate is ContextCandidate => candidate !== null,
  );
  const first = admitted[0];
  if (!first) return null;

  if (
    new Set(admitted.map(({ base }) => base.recordId)).size !== admitted.length
  )
    return null;
  if (
    admitted.some(
      ({ base, metric, unit }) =>
        metric !== first.metric ||
        unit !== first.unit ||
        !sameConditionNames(first.base.conditions, base.conditions),
    )
  )
    return null;

  const differentConditionNames = first.base.conditions
    .filter((_condition, index) => {
      const reported = new Set(
        admitted.map(({ base }) => {
          const current = base.conditions[index];
          return JSON.stringify([current?.value, current?.unit]);
        }),
      );
      return reported.size > 1;
    })
    .map(({ name }) => name);
  if (differentConditionNames.length === 0) return null;

  const differentNames = new Set(differentConditionNames);
  return {
    metric: first.metric,
    unit: first.unit,
    rows: admitted.map(({ base, reportedValue }) => ({
      recordId: base.recordId,
      anchorId: base.anchorId,
      objectLabel: base.objectLabel,
      reportedValue,
      conditions: base.conditions.filter(({ name }) =>
        differentNames.has(name),
      ),
      uncertaintyLabel: base.uncertaintyLabel,
      sourceLabel: base.sourceLabel,
    })),
    commonConditions: first.base.conditions.filter(
      ({ name }) => !differentNames.has(name),
    ),
    differentConditionNames,
  };
}
