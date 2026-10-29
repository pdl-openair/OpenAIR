import { describe, expect, it } from "vitest";
import { projectResearchRecordComparison } from "../../researchRecordComparisonProjection";
import { projectResearchRecordContext } from "../../researchRecordContextProjection";
import { projectResearchRecordTrend } from "../../researchRecordTrendProjection";

type JsonObject = Record<string, unknown>;

interface ConditionInput {
  name: string;
  value: string | null;
  unit: string | null;
  status?: "reported" | "unknown";
}

interface RecordOptions {
  metric?: string;
  unit?: string;
  conditions?: ConditionInput[];
  conditionStatus?: string;
  uncertainty?: unknown;
  unknownFields?: string[];
}

function record(
  index: number,
  objectLabel: string,
  valueText: string,
  options: RecordOptions = {},
): JsonObject {
  const conditions = options.conditions ?? [
    { name: "Temperature", value: String(300 + index * 100), unit: "K" },
  ];
  return {
    record_id: `rr:${String(index).padStart(32, "0")}`,
    research_object: objectLabel,
    record_type: "quantitative_result",
    information_identity: "direct_source_fact",
    assertion_mode: "direct_observation",
    condition_status: options.conditionStatus ?? "reported",
    conditions: conditions.map((condition) => ({
      name: condition.name,
      status: condition.status ?? "reported",
      value_text: condition.value,
      unit: condition.unit,
    })),
    result: {
      statement: `${objectLabel} reported ${valueText}`,
      values: [
        {
          name: options.metric ?? "Yield",
          value_text: valueText,
          unit: options.unit ?? "%",
        },
      ],
      mechanism: null,
      comparison: null,
    },
    source: {
      title: `Source ${objectLabel}`,
      source_locator: `https://example.test/${index}`,
    },
    qualifiers: [],
    uncertainty: options.uncertainty ?? null,
    unknown_fields: options.unknownFields ?? [],
  };
}

describe("T3 ResearchRecord Context-different projection", () => {
  it("shows one explicit condition difference", () => {
    const result = projectResearchRecordContext([
      record(1, "Experiment A", "68", {
        conditions: [{ name: "Temperature", value: "300", unit: "K" }],
      }),
      record(2, "Experiment B", "81", {
        conditions: [{ name: "Temperature", value: "400", unit: "K" }],
      }),
    ]);

    expect(result).toMatchObject({
      metric: "Yield",
      unit: "%",
      differentConditionNames: ["Temperature"],
    });
    expect(result?.rows.map(({ reportedValue }) => reportedValue)).toEqual([
      "68",
      "81",
    ]);
  });

  it("shows multiple condition differences", () => {
    const result = projectResearchRecordContext([
      record(1, "Experiment A", "68", {
        conditions: [
          { name: "Temperature", value: "300", unit: "K" },
          { name: "Pressure", value: "1", unit: "atm" },
        ],
      }),
      record(2, "Experiment B", "81", {
        conditions: [
          { name: "Temperature", value: "400", unit: "K" },
          { name: "Pressure", value: "2", unit: "atm" },
        ],
      }),
    ]);

    expect(result?.differentConditionNames).toEqual([
      "Pressure",
      "Temperature",
    ]);
  });

  it("separates common conditions from different conditions", () => {
    const result = projectResearchRecordContext([
      record(1, "Experiment A", "68", {
        conditions: [
          { name: "Temperature", value: "300", unit: "K" },
          { name: "Duration", value: "2", unit: "h" },
        ],
      }),
      record(2, "Experiment B", "81", {
        conditions: [
          { name: "Temperature", value: "400", unit: "K" },
          { name: "Duration", value: "2", unit: "h" },
        ],
      }),
    ]);

    expect(result?.differentConditionNames).toEqual(["Temperature"]);
    expect(result?.commonConditions).toEqual([
      { name: "Duration", value: "2", unit: "h" },
    ]);
  });

  it("preserves directly displayable source-grounded uncertainty as text", () => {
    const result = projectResearchRecordContext([
      record(1, "Experiment A", "68", {
        conditions: [{ name: "Temperature", value: "300", unit: "K" }],
        uncertainty: "Calibration drift was not quantified by the source.",
      }),
      record(2, "Experiment B", "81", {
        conditions: [{ name: "Temperature", value: "400", unit: "K" }],
      }),
    ]);

    expect(result?.rows[0]?.uncertaintyLabel).toBe(
      "Calibration drift was not quantified by the source.",
    );
  });

  it.each([
    [
      "metric mismatch",
      [
        record(1, "A", "68", { metric: "Yield" }),
        record(2, "B", "81", { metric: "Selectivity" }),
      ],
    ],
    [
      "unit mismatch",
      [
        record(1, "A", "68", { unit: "%" }),
        record(2, "B", "81", { unit: "fraction" }),
      ],
    ],
    ["inequality value", [record(1, "A", "68"), record(2, "B", "<81")]],
    ["range value", [record(1, "A", "68"), record(2, "B", "78-81")]],
    ["plus/minus value", [record(1, "A", "68"), record(2, "B", "81 ± 3")]],
    [
      "condition requiring natural-language interpretation",
      [
        record(1, "A", "68", {
          conditions: [
            { name: "Temperature", value: "see Methods section", unit: null },
          ],
        }),
        record(2, "B", "81", {
          conditions: [{ name: "Temperature", value: "400", unit: "K" }],
        }),
      ],
    ],
    [
      "partially unknown conditions",
      [
        record(1, "A", "68", {
          conditions: [
            {
              name: "Temperature",
              value: null,
              unit: null,
              status: "unknown",
            },
          ],
          conditionStatus: "partially_unknown",
        }),
        record(2, "B", "81"),
      ],
    ],
    [
      "uncertainty requiring statistical interpretation",
      [
        record(1, "A", "68", { uncertainty: "±0.3 (SD)" }),
        record(2, "B", "81"),
      ],
    ],
  ])("returns NO_CHART for %s", (_label, records) => {
    expect(projectResearchRecordContext(records)).toBeNull();
  });

  it("does not take a T1 Comparison candidate", () => {
    const records = [
      record(1, "Sample A", "68", {
        conditions: [{ name: "Temperature", value: "300", unit: "K" }],
      }),
      record(2, "Sample B", "81", {
        conditions: [{ name: "Temperature", value: "300", unit: "K" }],
      }),
    ];

    expect(projectResearchRecordComparison(records)).not.toBeNull();
    expect(projectResearchRecordContext(records)).toBeNull();
  });

  it("does not take a T2 Trend candidate", () => {
    const records = [
      record(1, "Sample A", "68", {
        conditions: [{ name: "Temperature", value: "300", unit: "K" }],
      }),
      record(2, "Sample A", "74", {
        conditions: [{ name: "Temperature", value: "400", unit: "K" }],
      }),
      record(3, "Sample A", "81", {
        conditions: [{ name: "Temperature", value: "500", unit: "K" }],
      }),
    ];

    expect(projectResearchRecordTrend(records)).not.toBeNull();
    expect(projectResearchRecordContext(records)).toBeNull();
  });
});
