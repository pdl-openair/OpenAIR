import {
  asObject,
  parseStrictScalarNumber,
  projectableResearchRecord,
  text,
  type JsonObject,
  type NormalizedResearchCondition,
  type ProjectableResearchRecord,
} from "./researchRecordDataChartProjection";

export interface TrendObservation {
  recordId: string;
  anchorId: string;
  reportedX: string;
  reportedY: string;
  sourceLabel: string;
  /** Unified axis labels; reported values remain unchanged for provenance. */
  displayX?: string;
  displayY?: string;
  reportedXUnit?: string;
  reportedYUnit?: string;
}

export interface TrendDatum {
  x: number;
  y: number;
  observations: TrendObservation[];
}

export interface TrendViewModel {
  metric: string;
  resultUnit: string;
  objectLabel: string;
  conditionName: string;
  conditionUnit: string;
  fixedConditions: NormalizedResearchCondition[];
  points: TrendDatum[];
  recordCount: number;
}

interface TrendCandidate {
  base: ProjectableResearchRecord;
  metric: string;
  resultUnit: string;
  y: number;
  reportedY: string;
}

function candidateFor(
  record: JsonObject,
  index: number,
): TrendCandidate | null {
  const base = projectableResearchRecord(record, index);
  const result = asObject(record.result);
  const values = Array.isArray(result?.values) ? result.values : [];
  if (!base || base.conditions.length === 0 || values.length !== 1) return null;

  const value = asObject(values[0]);
  const metric = text(value?.name);
  const resultUnit = text(value?.unit);
  const reportedY = text(value?.value_text);
  const y = parseStrictScalarNumber(reportedY);
  if (!metric || !resultUnit || y === null) return null;
  return { base, metric, resultUnit, y, reportedY };
}

function sameConditionSchema(
  left: NormalizedResearchCondition[],
  right: NormalizedResearchCondition[],
): boolean {
  return (
    left.length === right.length &&
    left.every(
      (condition, index) =>
        condition.name === right[index]?.name &&
        condition.unit === right[index]?.unit,
    )
  );
}

/**
 * Presentation-only T2 projection. It admits exactly one mechanically
 * identifiable numeric condition axis and otherwise fails closed.
 */
export function projectResearchRecordTrend(
  records: JsonObject[],
): TrendViewModel | null {
  if (records.length < 3) return null;

  const candidates = records.map(candidateFor);
  if (candidates.some((candidate) => candidate === null)) return null;
  const admitted = candidates.filter(
    (candidate): candidate is TrendCandidate => candidate !== null,
  );
  const first = admitted[0];
  if (!first) return null;

  if (
    new Set(admitted.map(({ base }) => base.recordId)).size !== admitted.length
  )
    return null;
  if (
    admitted.some(
      ({ base, metric, resultUnit }) =>
        base.objectLabel !== first.base.objectLabel ||
        metric !== first.metric ||
        resultUnit !== first.resultUnit ||
        !sameConditionSchema(first.base.conditions, base.conditions),
    )
  )
    return null;

  const varyingIndexes = first.base.conditions
    .map((_condition, index) => {
      const values = new Set(
        admitted.map(({ base }) => base.conditions[index]?.value),
      );
      return values.size > 1 ? index : -1;
    })
    .filter((index) => index >= 0);
  if (varyingIndexes.length !== 1) return null;

  const varyingIndex = varyingIndexes[0];
  const varyingCondition = first.base.conditions[varyingIndex];
  if (!varyingCondition?.unit) return null;

  const points = new Map<number, TrendDatum>();
  for (const candidate of admitted) {
    const condition = candidate.base.conditions[varyingIndex];
    const x = parseStrictScalarNumber(condition?.value);
    if (x === null || !condition) return null;

    const observation: TrendObservation = {
      recordId: candidate.base.recordId,
      anchorId: candidate.base.anchorId,
      reportedX: condition.value,
      reportedY: candidate.reportedY,
      sourceLabel: candidate.base.sourceLabel,
    };
    const existing = points.get(x);
    if (existing) {
      if (existing.y !== candidate.y) return null;
      existing.observations.push(observation);
    } else {
      points.set(x, { x, y: candidate.y, observations: [observation] });
    }
  }
  if (points.size < 3) return null;

  return {
    metric: first.metric,
    resultUnit: first.resultUnit,
    objectLabel: first.base.objectLabel,
    conditionName: varyingCondition.name,
    conditionUnit: varyingCondition.unit,
    fixedConditions: first.base.conditions.filter(
      (_condition, index) => index !== varyingIndex,
    ),
    points: [...points.values()].sort((left, right) => left.x - right.x),
    recordCount: admitted.length,
  };
}
