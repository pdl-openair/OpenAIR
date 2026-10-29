import {
  asObject,
  parseStrictScalarNumber,
  text,
  type JsonObject,
} from "./researchRecordDataChartProjection";
import {
  convertReportedUnit,
  reportedUnitFamily,
} from "./reportedUnitConversion";

export function pvdObjects(value: unknown): JsonObject[] {
  return Array.isArray(value)
    ? value.map(asObject).filter((row): row is JsonObject => row !== null)
    : [];
}

export function pvdRecords(document: JsonObject): JsonObject[] {
  return pvdObjects(document.research_records ?? document.evidence_records);
}

export function pvdRecordAnchor(record: JsonObject, index: number): string {
  const identity = text(record.record_id).replace(/[^a-zA-Z0-9_-]/g, "-");
  return `pvd-source-record-${identity}-${index + 1}`;
}

export function pvdRecordLabel(record: JsonObject, index: number): string {
  return text(record.sample_id) || `来源记录 ${index + 1}`;
}

export function pvdSourceFor(
  record: JsonObject,
  document: JsonObject,
): JsonObject | null {
  const id = text(record.source_id);
  if (!id) return null;
  const matches = pvdObjects(document.source_provenance).filter(
    (source) => text(source.source_id) === id,
  );
  return matches.length === 1 ? matches[0] : null;
}

export function pvdSourceLabel(source: JsonObject | null): string {
  if (!source) return "";
  return text(source.citation) || text(source.doi) || text(source.url);
}

export interface PvdReportedValue {
  key: string;
  anchorId: string;
  label: string;
  sourceLabel: string;
  value: number;
  reportedValue: string;
  reportedUnit: string;
  convertedLabel: string | null;
  outcome: JsonObject;
  record: JsonObject;
}

export interface PvdReportedValueGroup {
  key: string;
  metric: string;
  unit: string;
  rows: PvdReportedValue[];
}

function finiteReportedNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  return parseStrictScalarNumber(value);
}

/** Positions reported scalars with explicit unit scales, without scientific judgment. */
export function projectPvdReportedValues(
  document: JsonObject,
): PvdReportedValueGroup[] {
  const records = pvdRecords(document);
  const identityCounts = new Map<string, number>();
  for (const record of records) {
    const id = text(record.record_id);
    identityCounts.set(id, (identityCounts.get(id) ?? 0) + 1);
  }
  const groups = new Map<string, PvdReportedValueGroup>();
  records.forEach((record, index) => {
    const id = text(record.record_id);
    const source = pvdSourceFor(record, document);
    const sourceLabel = pvdSourceLabel(source);
    if (
      !id ||
      identityCounts.get(id) !== 1 ||
      record.record_role === "unresolved_record_identity" ||
      !sourceLabel ||
      source?.access_state === "unverified" ||
      source?.access_state === "unavailable"
    )
      return;
    pvdObjects(record.outcomes).forEach((outcome, outcomeIndex) => {
      const metric = text(outcome.metric);
      const unit = text(outcome.unit);
      const reportedNumber = finiteReportedNumber(outcome.value);
      if (!metric || !unit || reportedNumber === null) return;
      const key = JSON.stringify([metric, reportedUnitFamily(unit)]);
      const group = groups.get(key) ?? { key, metric, unit, rows: [] };
      const converted = convertReportedUnit(
        String(outcome.value),
        unit,
        group.unit,
      );
      if (!converted) return;
      group.rows.push({
        key: `${index}:${outcomeIndex}`,
        anchorId: pvdRecordAnchor(record, index),
        label: pvdRecordLabel(record, index),
        sourceLabel,
        value: converted.value,
        reportedValue: String(outcome.value),
        reportedUnit: unit,
        convertedLabel: unit !== group.unit ? converted.label : null,
        outcome,
        record,
      });
      groups.set(key, group);
    });
  });
  return [...groups.values()].filter((group) => group.rows.length >= 2);
}
