import {
  asObject,
  parseStrictScalarNumber,
  projectableResearchRecord,
  text,
  type JsonObject,
} from "./researchRecordDataChartProjection";

export {
  parseStrictScalarNumber,
  researchRecordAnchorId,
} from "./researchRecordDataChartProjection";

export interface ComparisonDatum {
  recordId: string;
  anchorId: string;
  objectLabel: string;
  value: number;
  reportedValue: string;
  sourceLabel: string;
}

export interface ComparisonViewModel {
  metric: string;
  unit: string;
  conditionLabel: string;
  rows: ComparisonDatum[];
  minimum: number;
  maximum: number;
  difference: number;
  differenceLabel: string;
  lowestObjects: string[];
  highestObjects: string[];
}

interface ComparisonGroup {
  metric: string;
  unit: string;
  order: number;
  invalid: boolean;
  rows: ComparisonDatum[];
  conditionFingerprints: Set<string>;
  conditionLabels: Set<string>;
}

function computedNumberLabel(value: number): string {
  if (Object.is(value, -0)) return "0";
  return Number(value.toPrecision(12)).toString();
}

/**
 * Presentation-only T1 projection. It intentionally fails closed when a
 * comparison would need unit conversion, context interpretation, uncertainty
 * handling, or selection among multiple values from one ResearchRecord.
 */
export function projectResearchRecordComparison(
  records: JsonObject[],
): ComparisonViewModel | null {
  const groups = new Map<string, ComparisonGroup>();
  const metricUnits = new Map<string, Set<string>>();
  const invalidMetrics = new Set<string>();
  let groupOrder = 0;

  records.forEach((record, recordIndex) => {
    const result = asObject(record.result);
    const values = Array.isArray(result?.values) ? result.values : [];
    const base = projectableResearchRecord(record, recordIndex);
    const seenKeys = new Set<string>();

    values.forEach((rawValue) => {
      const value = asObject(rawValue);
      const metric = text(value?.name);
      if (!metric) return;
      const unit = text(value?.unit);
      if (!unit) {
        invalidMetrics.add(metric);
        return;
      }

      const units = metricUnits.get(metric) ?? new Set<string>();
      units.add(unit);
      metricUnits.set(metric, units);

      const key = JSON.stringify([metric, unit]);
      let group = groups.get(key);
      if (!group) {
        group = {
          metric,
          unit,
          order: groupOrder,
          invalid: false,
          rows: [],
          conditionFingerprints: new Set<string>(),
          conditionLabels: new Set<string>(),
        };
        groups.set(key, group);
        groupOrder += 1;
      }

      if (seenKeys.has(key)) {
        group.invalid = true;
        return;
      }
      seenKeys.add(key);

      const numeric = parseStrictScalarNumber(value?.value_text);
      if (!base || numeric === null) {
        group.invalid = true;
        return;
      }
      group.conditionFingerprints.add(base.conditionFingerprint);
      group.conditionLabels.add(base.conditionLabel);
      group.rows.push({
        recordId: base.recordId,
        anchorId: base.anchorId,
        objectLabel: base.objectLabel,
        value: numeric,
        reportedValue: text(value?.value_text),
        sourceLabel: base.sourceLabel,
      });
    });
  });

  const eligible = [...groups.values()]
    .sort((left, right) => left.order - right.order)
    .find((group) => {
      if (group.invalid || invalidMetrics.has(group.metric)) return false;
      if ((metricUnits.get(group.metric)?.size ?? 0) !== 1) return false;
      if (group.rows.length < 2 || group.conditionFingerprints.size !== 1)
        return false;
      return (
        new Set(group.rows.map((row) => row.objectLabel)).size ===
        group.rows.length
      );
    });
  if (!eligible) return null;

  const values = eligible.rows.map((row) => row.value);
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const difference = maximum - minimum;
  return {
    metric: eligible.metric,
    unit: eligible.unit,
    conditionLabel: [...eligible.conditionLabels][0] ?? "",
    rows: eligible.rows,
    minimum,
    maximum,
    difference,
    differenceLabel: computedNumberLabel(difference),
    lowestObjects: eligible.rows
      .filter((row) => row.value === minimum)
      .map((row) => row.objectLabel),
    highestObjects: eligible.rows
      .filter((row) => row.value === maximum)
      .map((row) => row.objectLabel),
  };
}
