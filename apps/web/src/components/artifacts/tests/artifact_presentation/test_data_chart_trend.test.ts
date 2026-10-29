import { describe, expect, it } from "vitest";
import { projectResearchRecordTrend } from "../../researchRecordTrendProjection";

type JsonObject = Record<string, unknown>;

interface RecordOptions {
  objectLabel?: string;
  metric?: string;
  resultUnit?: string;
  conditionName?: string;
  conditionUnit?: string;
  conditionStatus?: string;
  uncertainty?: string | null;
  pressure?: string;
  pressureUnit?: string;
}

function record(
  index: number,
  x: string,
  y: string,
  options: RecordOptions = {},
): JsonObject {
  const conditionStatus = options.conditionStatus ?? "reported";
  return {
    record_id: `rr:${String(index).padStart(32, "0")}`,
    research_object: options.objectLabel ?? "Material A",
    record_type: "quantitative_result",
    information_identity: "direct_source_fact",
    assertion_mode: "direct_observation",
    condition_status: conditionStatus,
    conditions: [
      {
        name: options.conditionName ?? "Temperature",
        status:
          conditionStatus === "partially_unknown" ? "unknown" : "reported",
        value_text: conditionStatus === "partially_unknown" ? null : x,
        unit: options.conditionUnit ?? "K",
      },
      ...(options.pressure === undefined
        ? []
        : [
            {
              name: "Pressure",
              status: "reported",
              value_text: options.pressure,
              unit: options.pressureUnit ?? "atm",
            },
          ]),
    ],
    result: {
      statement: `Conductivity is ${y}`,
      values: [
        {
          name: options.metric ?? "Conductivity",
          value_text: y,
          unit: options.resultUnit ?? "S/cm",
        },
      ],
      mechanism: null,
      comparison: null,
    },
    source: {
      title: `Source ${index}`,
      source_locator: `https://example.test/${index}`,
    },
    qualifiers: [],
    uncertainty: options.uncertainty ?? null,
    unknown_fields: [],
  };
}

describe("T2 ResearchRecord Condition Trend projection", () => {
  it("projects one numeric condition for the same object and preserves sources", () => {
    const result = projectResearchRecordTrend([
      record(1, "300", "12"),
      record(2, "400", "16"),
      record(3, "500", "21"),
    ]);

    expect(result).toMatchObject({
      metric: "Conductivity",
      resultUnit: "S/cm",
      objectLabel: "Material A",
      conditionName: "Temperature",
      conditionUnit: "K",
      recordCount: 3,
    });
    expect(result?.points.map(({ x, y }) => [x, y])).toEqual([
      [300, 12],
      [400, 16],
      [500, 21],
    ]);
    expect(result?.points[0]?.observations[0]).toMatchObject({
      reportedX: "300",
      reportedY: "12",
      sourceLabel: "Source 1",
    });
  });

  it("sorts out-of-order input by numeric X", () => {
    const result = projectResearchRecordTrend([
      record(1, "500", "21"),
      record(2, "300", "12"),
      record(3, "400", "16"),
    ]);

    expect(result?.points.map(({ x }) => x)).toEqual([300, 400, 500]);
  });

  it("accepts scientific notation for the condition axis", () => {
    const result = projectResearchRecordTrend([
      record(1, "1e-3", "12"),
      record(2, "1e-2", "16"),
      record(3, "1e-1", "21"),
    ]);

    expect(result?.points.map(({ x }) => x)).toEqual([0.001, 0.01, 0.1]);
    expect(
      result?.points.map(({ observations }) => observations[0]?.reportedX),
    ).toEqual(["1e-3", "1e-2", "1e-1"]);
  });

  it("preserves one additional fixed condition as context", () => {
    const result = projectResearchRecordTrend([
      record(1, "300", "12", { pressure: "1" }),
      record(2, "400", "16", { pressure: "1" }),
      record(3, "500", "21", { pressure: "1" }),
    ]);

    expect(result?.fixedConditions).toEqual([
      { name: "Pressure", value: "1", unit: "atm" },
    ]);
  });

  it("coalesces duplicate X with the same Y while retaining every source", () => {
    const result = projectResearchRecordTrend([
      record(1, "300", "12"),
      record(2, "300", "12.0"),
      record(3, "400", "16"),
      record(4, "500", "21"),
    ]);

    expect(result?.points).toHaveLength(3);
    expect(result?.points[0]?.observations).toHaveLength(2);
    expect(result?.recordCount).toBe(4);
  });

  it.each([
    [
      "only two distinct X values",
      [record(1, "300", "12"), record(2, "300", "12"), record(3, "400", "16")],
    ],
    [
      "different research objects",
      [
        record(1, "300", "12"),
        record(2, "400", "16", { objectLabel: "Material B" }),
        record(3, "500", "21"),
      ],
    ],
    [
      "metric mismatch",
      [
        record(1, "300", "12"),
        record(2, "400", "16", { metric: "Resistivity" }),
        record(3, "500", "21"),
      ],
    ],
    [
      "result unit mismatch",
      [
        record(1, "300", "12"),
        record(2, "400", "16", { resultUnit: "mS/cm" }),
        record(3, "500", "21"),
      ],
    ],
    [
      "condition name mismatch",
      [
        record(1, "300", "12"),
        record(2, "400", "16", { conditionName: "Pressure" }),
        record(3, "500", "21"),
      ],
    ],
    [
      "condition unit mismatch",
      [
        record(1, "300", "12"),
        record(2, "400", "16", { conditionUnit: "°C" }),
        record(3, "500", "21"),
      ],
    ],
    [
      "categorical condition",
      [
        record(1, "low", "12"),
        record(2, "medium", "16"),
        record(3, "high", "21"),
      ],
    ],
    [
      "inequality condition",
      [record(1, "<300", "12"), record(2, "400", "16"), record(3, "500", "21")],
    ],
    [
      "range condition",
      [
        record(1, "300-400", "12"),
        record(2, "500", "16"),
        record(3, "600", "21"),
      ],
    ],
    [
      "two changing conditions",
      [
        record(1, "300", "12", { pressure: "1" }),
        record(2, "400", "16", { pressure: "2" }),
        record(3, "500", "21", { pressure: "3" }),
      ],
    ],
    [
      "fixed condition is inconsistent",
      [
        record(1, "300", "12", { pressure: "1" }),
        record(2, "400", "16", { pressure: "1" }),
        record(3, "500", "21", { pressure: "2" }),
      ],
    ],
    [
      "partially unknown condition",
      [
        record(1, "300", "12"),
        record(2, "400", "16", { conditionStatus: "partially_unknown" }),
        record(3, "500", "21"),
      ],
    ],
    [
      "duplicate X with conflicting Y",
      [
        record(1, "300", "12"),
        record(2, "300", "13"),
        record(3, "400", "16"),
        record(4, "500", "21"),
      ],
    ],
    [
      "uncertainty requires interpretation",
      [
        record(1, "300", "12"),
        record(2, "400", "16", { uncertainty: "± 0.5" }),
        record(3, "500", "21"),
      ],
    ],
  ])("returns NO_TREND for %s", (_label, records) => {
    expect(projectResearchRecordTrend(records)).toBeNull();
  });
});
