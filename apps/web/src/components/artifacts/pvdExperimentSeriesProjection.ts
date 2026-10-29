import {
  asObject,
  parseStrictScalarNumber,
  text,
  type JsonObject,
  type NormalizedResearchCondition,
} from "./researchRecordDataChartProjection";
import {
  pvdObjects,
  projectPvdReportedValues,
  type PvdReportedValue,
} from "./pvdReportedValueProjection";
import {
  convertReportedUnit,
  reportedUnitBase,
  reportedUnitFamily,
} from "./reportedUnitConversion";
import type {
  TrendDatum,
  TrendViewModel,
} from "./researchRecordTrendProjection";
import { pvdFieldLabel } from "./pvdRecordFieldLabels";

interface Condition extends NormalizedResearchCondition {
  identity: string;
  rawValue: string;
  rawUnit: string;
  number: number | null;
  semantics: string;
  qualifier: string;
}

interface Candidate {
  row: PvdReportedValue;
  scope: string;
  material: string;
  objectLabel: string;
  measurement: string;
  conditions: Map<string, Condition>;
}

export interface PvdExperimentSeries extends TrendViewModel {
  key: string;
  sourceLabel: string;
  seriesId: string;
}

/** Source refs identify provenance, not a changing experimental condition. */
function canonicalValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalValue);
  const object = asObject(value);
  if (!object) return value;
  return Object.fromEntries(
    Object.keys(object)
      .filter((key) => key !== "provenance_refs")
      .sort()
      .map((key) => [key, canonicalValue(object[key])]),
  );
}

function stable(value: unknown): string {
  return JSON.stringify(canonicalValue(value)) ?? "null";
}

function conditionFor(name: string, value: unknown): Condition | null {
  const fact = asObject(value);
  if (!fact) return null;
  if (fact.fact_status === "not_applicable")
    return {
      name,
      value: "不适用",
      unit: "",
      identity: stable(fact),
      rawValue: "不适用",
      rawUnit: "",
      number: null,
      semantics: "not_applicable",
      qualifier: "",
    };
  if (
    fact.fact_status !== "known" ||
    fact.value == null ||
    fact.uncertainty != null
  )
    return null;
  const rawValue =
    typeof fact.value === "object" ? stable(fact.value) : String(fact.value);
  if (!rawValue.trim()) return null;
  const rawUnit = text(fact.unit);
  const unit = reportedUnitBase(rawUnit);
  const numeric = parseStrictScalarNumber(rawValue);
  const converted =
    numeric === null ? null : convertReportedUnit(rawValue, rawUnit, unit);
  if (numeric !== null && !converted) return null;
  const normalized = converted?.label ?? rawValue;
  return {
    name,
    value: normalized,
    unit,
    rawValue,
    rawUnit,
    number: converted?.value ?? null,
    semantics: JSON.stringify([text(fact.definition), text(fact.qualifier)]),
    qualifier: text(fact.qualifier),
    identity: stable({
      status: fact.fact_status,
      value: normalized,
      unit,
      definition: text(fact.definition),
      qualifier: text(fact.qualifier),
    }),
  };
}

function conditionsFor(
  section: unknown,
  additional?: unknown,
): Map<string, Condition> | null {
  if (section != null && !asObject(section)) return null;
  const object = asObject(section) ?? {};
  const entries: [string, unknown][] = Object.entries(object).filter(
    ([key]) => key !== "other_conditions",
  );
  for (const [prefix, values] of [
    ["other:", object.other_conditions],
    ["context:", additional],
  ] as const) {
    if (values != null && !Array.isArray(values)) return null;
    if (Array.isArray(values) && pvdObjects(values).length !== values.length)
      return null;
    for (const entry of pvdObjects(values)) {
      const key = text(entry.key);
      if (!key) return null;
      entries.push([prefix + key, entry.fact]);
    }
  }
  const conditions = new Map<string, Condition>();
  for (const [key, value] of entries.sort(([left], [right]) =>
    left.localeCompare(right),
  )) {
    const condition = conditionFor(key, value);
    if (!condition || conditions.has(key)) return null;
    conditions.set(key, condition);
  }
  return conditions;
}

function signature(
  conditions: Map<string, Condition>,
  except?: string,
): string {
  return JSON.stringify(
    [...conditions]
      .filter(([key]) => key !== except)
      .map(([key, condition]) => [key, condition.identity]),
  );
}

function candidateFor(row: PvdReportedValue): Candidate | null {
  const material = conditionsFor(row.record.material);
  const process = conditionsFor(row.record.process, row.record.other_context);
  if (!material?.size || !process?.size || row.outcome.uncertainty != null)
    return null;
  const measurement = conditionsFor(
    Object.fromEntries(
      [
        "measurement_method",
        "measurement_geometry",
        "sign_convention",
        "correction_state",
      ]
        .filter((key) => row.outcome[key] != null)
        .map((key) => [key, row.outcome[key]]),
    ),
  );
  if (!measurement) return null;
  return {
    row,
    scope: JSON.stringify([
      text(row.record.source_id),
      text(row.record.study_id),
      text(row.record.series_id),
    ]),
    material: signature(material),
    objectLabel: [...material.values()]
      .map(
        ({ name, value, unit }) =>
          `${pvdFieldLabel(name)}：${value}${unit ? ` ${unit}` : ""}`,
      )
      .join(" · "),
    measurement: JSON.stringify([
      text(row.outcome.metric),
      text(row.outcome.definition),
      reportedUnitFamily(row.reportedUnit),
      signature(measurement),
    ]),
    conditions: process,
  };
}

function seriesFor(
  key: string,
  axis: string,
  candidates: Candidate[],
): PvdExperimentSeries | null {
  const ordered = [...candidates].sort((left, right) =>
    text(left.row.record.record_id).localeCompare(
      text(right.row.record.record_id),
    ),
  );
  const first = ordered[0];
  if (
    !first ||
    new Set(ordered.map(({ row }) => text(row.record.record_id))).size !==
      ordered.length
  )
    return null;
  const firstCondition = first.conditions.get(axis)!;
  const xUnit = firstCondition.rawUnit;
  const yUnit = first.row.reportedUnit;
  const points = new Map<number, TrendDatum>();
  for (const candidate of ordered) {
    const condition = candidate.conditions.get(axis)!;
    const row = candidate.row;
    const x = convertReportedUnit(condition.rawValue, condition.rawUnit, xUnit);
    const y = convertReportedUnit(row.reportedValue, row.reportedUnit, yUnit);
    if (!x || !y) return null;
    const observation = {
      recordId: text(row.record.record_id),
      anchorId: row.anchorId,
      reportedX: condition.rawValue,
      reportedY: row.reportedValue,
      sourceLabel: row.sourceLabel,
      reportedXUnit: condition.rawUnit,
      reportedYUnit: row.reportedUnit,
      ...(condition.rawUnit !== xUnit ? { displayX: x.label } : {}),
      ...(row.reportedUnit !== yUnit ? { displayY: y.label } : {}),
    };
    const existing = points.get(x.value);
    if (existing && existing.y !== y.value) return null;
    if (existing) existing.observations.push(observation);
    else
      points.set(x.value, {
        x: x.value,
        y: y.value,
        observations: [observation],
      });
  }
  if (points.size < 3) return null;
  return {
    key,
    sourceLabel: first.row.sourceLabel,
    seriesId: text(first.row.record.series_id),
    metric: text(first.row.outcome.metric),
    resultUnit: yUnit,
    objectLabel: first.objectLabel,
    conditionName: axis,
    conditionUnit: xUnit,
    fixedConditions: [...first.conditions]
      .filter(([name]) => name !== axis)
      .map(([, { name, value, unit }]) => ({ name, value, unit })),
    points: [...points.values()].sort((left, right) => left.x - right.x),
    recordCount: ordered.length,
  };
}

/** Group only supplied source/study/series identities and matching reported facts. */
export function projectPvdExperimentSeries(
  document: JsonObject,
): PvdExperimentSeries[] {
  const buckets = new Map<string, { axis: string; candidates: Candidate[] }>();
  const rows = projectPvdReportedValues(document).flatMap(
    (group) => group.rows,
  );
  for (const row of rows) {
    const candidate = candidateFor(row);
    if (!candidate) continue;
    for (const [axis, condition] of candidate.conditions) {
      if (
        axis.startsWith("context:") ||
        condition.number === null ||
        !condition.rawUnit
      )
        continue;
      if (condition.qualifier) continue;
      const axisIdentity = JSON.stringify([
        axis,
        condition.unit,
        condition.semantics,
      ]);
      const key = JSON.stringify([
        candidate.scope,
        candidate.material,
        candidate.measurement,
        axisIdentity,
        signature(candidate.conditions, axis),
      ]);
      const bucket = buckets.get(key) ?? { axis, candidates: [] };
      bucket.candidates.push(candidate);
      buckets.set(key, bucket);
    }
  }
  return [...buckets]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, { axis, candidates }]) => seriesFor(key, axis, candidates))
    .filter((series): series is PvdExperimentSeries => series !== null);
}
