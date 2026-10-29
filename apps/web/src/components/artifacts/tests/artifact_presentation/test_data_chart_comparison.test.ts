import { describe, expect, it } from "vitest";
import {
  parseStrictScalarNumber,
  projectResearchRecordComparison,
} from "../../researchRecordComparisonProjection";

type JsonObject = Record<string, unknown>;

function record(
  index: number,
  objectLabel: string,
  valueText: string,
  options: {
    metric?: string;
    unit?: string | null;
    temperature?: string;
    uncertainty?: string | null;
  } = {},
): JsonObject {
  return {
    record_id: `rr:${String(index).padStart(32, "0")}`,
    research_object: objectLabel,
    record_type: "quantitative_result",
    information_identity: "direct_source_fact",
    assertion_mode: "direct_observation",
    condition_status: "reported",
    conditions: [
      {
        name: "Temperature",
        status: "reported",
        value_text: options.temperature ?? "300",
        unit: "K",
      },
    ],
    result: {
      statement: `${objectLabel} reported ${valueText}`,
      values: [
        {
          name: options.metric ?? "Band gap",
          value_text: valueText,
          unit: options.unit === undefined ? "eV" : options.unit,
        },
      ],
      mechanism: null,
      comparison: null,
    },
    source: {
      title: `Source ${objectLabel}`,
      doi: null,
      arxiv_id: null,
      source_locator: `https://example.test/${index}`,
      provider: "test",
    },
    qualifiers: [],
    uncertainty: options.uncertainty ?? null,
    unknown_fields: [],
  };
}

describe("T1 ResearchRecord Comparison projection", () => {
  it.each([
    ["12", 12],
    ["12.4", 12.4],
    ["-0.35", -0.35],
    ["1.2e-3", 0.0012],
  ])("mechanically accepts scalar numeric value %s", (input, expected) => {
    expect(parseStrictScalarNumber(input)).toBe(expected);
  });

  it.each([
    "<5",
    ">10",
    "10–15",
    "10-15",
    "1.2 ± 0.3",
    "约 20",
    "20%",
    "ND",
    "not significant",
  ])("rejects value requiring scientific interpretation: %s", (input) => {
    expect(parseStrictScalarNumber(input)).toBeNull();
  });

  it("projects two objects with the same metric, unit, and condition", () => {
    const result = projectResearchRecordComparison([
      record(1, "Sample A", "1.8"),
      record(2, "Sample B", "2.1"),
    ]);

    expect(result).toMatchObject({
      metric: "Band gap",
      unit: "eV",
      conditionLabel: "Temperature 300 K",
      differenceLabel: "0.3",
      highestObjects: ["Sample B"],
      lowestObjects: ["Sample A"],
    });
    expect(result?.rows.map((row) => row.reportedValue)).toEqual([
      "1.8",
      "2.1",
    ]);
  });

  it("projects three objects without dropping source traceability", () => {
    const result = projectResearchRecordComparison([
      record(1, "Sample A", "1.8"),
      record(2, "Sample B", "2.1"),
      record(3, "Sample C", "1.6"),
    ]);

    expect(result?.rows).toHaveLength(3);
    expect(result?.rows.map((row) => row.sourceLabel)).toEqual([
      "Source Sample A",
      "Source Sample B",
      "Source Sample C",
    ]);
    expect(result?.differenceLabel).toBe("0.5");
  });

  it("supports negative and scientific-notation scalar values", () => {
    const result = projectResearchRecordComparison([
      record(1, "Group negative", "-0.35"),
      record(2, "Group small", "1.2e-3"),
    ]);

    expect(result?.minimum).toBe(-0.35);
    expect(result?.maximum).toBe(0.0012);
    expect(result?.rows.map((row) => row.reportedValue)).toEqual([
      "-0.35",
      "1.2e-3",
    ]);
  });

  it("accepts a separated percentage value and unit", () => {
    const result = projectResearchRecordComparison([
      record(1, "Group A", "20", { metric: "Yield", unit: "%" }),
      record(2, "Group B", "25", { metric: "Yield", unit: "%" }),
    ]);

    expect(result).toMatchObject({
      metric: "Yield",
      unit: "%",
      differenceLabel: "5",
    });
  });

  it.each([
    ["single record", [record(1, "Sample A", "1.8")]],
    [
      "metric mismatch",
      [
        record(1, "Sample A", "1.8"),
        record(2, "Sample B", "2.1", { metric: "Conductivity" }),
      ],
    ],
    [
      "unit mismatch",
      [
        record(1, "Sample A", "1.8"),
        record(2, "Sample B", "2.1", { unit: "meV" }),
      ],
    ],
    [
      "inequality",
      [record(1, "Sample A", "1.8"), record(2, "Sample B", "<2.1")],
    ],
    ["range", [record(1, "Sample A", "1.8"), record(2, "Sample B", "1.9-2.1")]],
    [
      "plus/minus",
      [record(1, "Sample A", "1.8"), record(2, "Sample B", "2.1 ± 0.2")],
    ],
    [
      "embedded percent",
      [
        record(1, "Sample A", "20", { metric: "Yield", unit: "%" }),
        record(2, "Sample B", "25%", { metric: "Yield", unit: "%" }),
      ],
    ],
    [
      "missing object identity",
      [record(1, "Sample A", "1.8"), record(2, "", "2.1")],
    ],
    [
      "context difference",
      [
        record(1, "Sample A", "1.8"),
        record(2, "Sample B", "2.1", { temperature: "350" }),
      ],
    ],
    [
      "reported uncertainty",
      [
        record(1, "Sample A", "1.8"),
        record(2, "Sample B", "2.1", {
          uncertainty: "measurement method differs",
        }),
      ],
    ],
    [
      "non-null empty uncertainty",
      [
        record(1, "Sample A", "1.8"),
        record(2, "Sample B", "2.1", { uncertainty: "" }),
      ],
    ],
  ])("returns NO_CHART for %s", (_label, records) => {
    expect(projectResearchRecordComparison(records)).toBeNull();
  });
});
