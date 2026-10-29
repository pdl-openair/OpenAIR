// CANONICAL_SHAPE_TEST_FIXTURE: deterministic schema data, not research evidence.
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ArtifactRenderer } from "../../ArtifactRenderer";
import { projectPvdExperimentSeries } from "../../pvdExperimentSeriesProjection";
import type { JsonObject } from "../../researchRecordDataChartProjection";
import type { WorkbenchArtifact } from "../../../../model/workbenchTask";

function fact(value: unknown, unit?: string) {
  return { fact_status: "known", value, ...(unit ? { unit } : {}) };
}

function record(
  index: number,
  x: number | string,
  y: number | string,
): JsonObject {
  return {
    record_id: `record-${index}`,
    source_id: "paper",
    sample_id: `样品 ${index}`,
    material: { film_composition: fact("TiN"), substrate: fact("Si") },
    process: { pressure: fact(x, "Pa"), power: fact(200, "W") },
    outcomes: [
      {
        metric: "硬度",
        value: y,
        unit: "GPa",
        measurement_method: fact("纳米压痕"),
      },
    ],
  };
}

function body(
  records = [record(1, 0.2, 12), record(2, 0.4, 15), record(3, 0.6, 14)],
): JsonObject {
  return {
    research_records: records,
    source_provenance: [
      {
        source_id: "paper",
        citation: "假数据合同测试来源",
        access_state: "verified",
        doi: "10.0000/contract-series",
      },
    ],
  };
}

function process(row: JsonObject) {
  return row.process as JsonObject;
}
function outcomes(row: JsonObject) {
  return row.outcomes as JsonObject[];
}

describe("PVD experimental series admission", () => {
  it("compares structured material facts independent of key order and keeps them readable", () => {
    const input = body();
    const rows = input.research_records as JsonObject[];
    rows.forEach((row, index) => {
      row.material = {
        target_composition: fact(
          index === 1 ? { N: 0.5, Ti: 0.5 } : { Ti: 0.5, N: 0.5 },
        ),
      };
    });
    const series = projectPvdExperimentSeries(input);
    expect(series).toHaveLength(1);
    expect(series[0].objectLabel).toBe('靶材组成：{"N":0.5,"Ti":0.5}');
  });

  it("identifies one changing parameter, sorts X, and retains fixed conditions and anchors", () => {
    const input = body([
      record(3, 0.6, 14),
      record(1, 0.2, 12),
      record(2, 0.4, 15),
    ]);
    const series = projectPvdExperimentSeries(input);
    expect(series).toHaveLength(1);
    expect(series[0]).toMatchObject({
      metric: "硬度",
      conditionName: "pressure",
      conditionUnit: "Pa",
      resultUnit: "GPa",
      fixedConditions: [{ name: "power", value: "200", unit: "W" }],
    });
    expect(series[0].points.map(({ x, y }) => [x, y])).toEqual([
      [0.2, 12],
      [0.4, 15],
      [0.6, 14],
    ]);
    expect(series[0].points[0].observations[0]).toMatchObject({
      recordId: "record-1",
      reportedX: "0.2",
      sourceLabel: "假数据合同测试来源",
      anchorId: "pvd-source-record-record-1-2",
    });
  });

  it("converts both axes and fixed conditions while preserving raw values and units", () => {
    const input = body();
    const rows = input.research_records as JsonObject[];
    process(rows[1]).pressure = fact("0.0004", "kPa");
    process(rows[1]).power = fact(200, "W");
    outcomes(rows[1])[0].value = 15000;
    outcomes(rows[1])[0].unit = "MPa";
    process(rows[0]).substrate_bias = fact(1, "V");
    process(rows[1]).substrate_bias = fact(1000, "mV");
    process(rows[2]).substrate_bias = fact(1, "V");
    const series = projectPvdExperimentSeries(input);
    expect(series).toHaveLength(1);
    expect(series[0].points[1]).toMatchObject({
      x: 0.4,
      y: 15,
      observations: [
        {
          reportedX: "0.0004",
          reportedXUnit: "kPa",
          displayX: "0.4",
          reportedY: "15000",
          reportedYUnit: "MPa",
          displayY: "15",
        },
      ],
    });
    expect(JSON.stringify(input)).toContain('"value":"0.0004"');
  });

  it("keeps different source identities separate even when citations coincide", () => {
    const input = body([
      record(1, 1, 10),
      record(2, 2, 11),
      record(3, 3, 12),
      record(4, 1, 20),
      record(5, 2, 21),
      record(6, 3, 22),
    ]);
    const rows = input.research_records as JsonObject[];
    rows.slice(3).forEach((row) => {
      row.source_id = "paper-b";
    });
    (input.source_provenance as JsonObject[]).push({
      source_id: "paper-b",
      citation: "假数据合同测试来源",
      access_state: "verified",
    });
    expect(projectPvdExperimentSeries(input)).toHaveLength(2);
  });

  it("respects explicit study and series identifiers and keeps multiple valid series", () => {
    const input = body([
      record(1, 1, 10),
      record(2, 2, 11),
      record(3, 3, 12),
      record(4, 1, 20),
      record(5, 2, 21),
      record(6, 3, 22),
    ]);
    (input.research_records as JsonObject[]).forEach((row, index) => {
      row.series_id = index < 3 ? "series-a" : "series-b";
      row.study_id = "study";
    });
    expect(
      projectPvdExperimentSeries(input).map((item) => item.seriesId),
    ).toEqual(["series-a", "series-b"]);
  });

  it("does not discard a valid series because an unrelated record is incomplete", () => {
    const input = body();
    (input.research_records as JsonObject[]).push({
      record_id: "incomplete",
      source_id: "paper",
      outcomes: [{ metric: "硬度", value: 99, unit: "GPa" }],
    });
    expect(projectPvdExperimentSeries(input)).toHaveLength(1);
  });

  it("separates multiple performance metrics without mixing observations", () => {
    const input = body();
    (input.research_records as JsonObject[]).forEach((row, index) =>
      outcomes(row).push({
        metric: "弹性模量",
        value: 100 + index,
        unit: "GPa",
      }),
    );
    expect(
      projectPvdExperimentSeries(input)
        .map(({ metric }) => metric)
        .sort(),
    ).toEqual(["弹性模量", "硬度"].sort());
  });

  it("coalesces identical observations at one X without averaging or dropping provenance", () => {
    const input = body();
    (input.research_records as JsonObject[]).push(record(4, 0.2, "12.0"));
    const series = projectPvdExperimentSeries(input);
    expect(series[0].points).toHaveLength(3);
    expect(series[0].points[0].observations).toHaveLength(2);
    expect(series[0].recordCount).toBe(4);
  });

  it("supports a supplied numeric custom process parameter", () => {
    const input = body();
    (input.research_records as JsonObject[]).forEach((row, index) => {
      process(row).pressure = fact(0.5, "Pa");
      process(row).other_conditions = [
        { key: "pulse_frequency", fact: fact(100 + index * 100, "Hz") },
      ];
    });
    expect(projectPvdExperimentSeries(input)[0].conditionName).toBe(
      "other:pulse_frequency",
    );
  });

  it.each([
    [
      "only two distinct parameter values",
      (rows: JsonObject[]) => {
        process(rows[2]).pressure = fact(0.4, "Pa");
        outcomes(rows[2])[0].value = 15;
      },
    ],
    [
      "two parameters changing together",
      (rows: JsonObject[]) =>
        rows.forEach((row, index) => {
          process(row).power = fact(200 + index * 50, "W");
        }),
    ],
    [
      "different materials",
      (rows: JsonObject[]) => {
        rows[2].material = { film_composition: fact("AlN") };
      },
    ],
    [
      "missing material identity",
      (rows: JsonObject[]) => {
        rows[2].material = {};
      },
    ],
    [
      "unknown fixed condition",
      (rows: JsonObject[]) => {
        process(rows[2]).power = { fact_status: "not_reported" };
      },
    ],
    [
      "unknown varying parameter",
      (rows: JsonObject[]) => {
        process(rows[2]).pressure = { fact_status: "unknown" };
      },
    ],
    [
      "categorical varying parameter",
      (rows: JsonObject[]) => {
        process(rows[2]).pressure = fact("high", "Pa");
      },
    ],
    [
      "range performance",
      (rows: JsonObject[]) => {
        outcomes(rows[2])[0].value = "12–14";
      },
    ],
    [
      "uncertainty requiring interpretation",
      (rows: JsonObject[]) => {
        outcomes(rows[2])[0].uncertainty = "±0.5";
      },
    ],
    [
      "measurement method differs",
      (rows: JsonObject[]) => {
        outcomes(rows[2])[0].measurement_method = fact("不同方法");
      },
    ],
    [
      "measurement definition differs",
      (rows: JsonObject[]) => {
        outcomes(rows[2])[0].definition = "不同定义";
      },
    ],
    [
      "series identities differ",
      (rows: JsonObject[]) => {
        rows[2].series_id = "another-series";
      },
    ],
    [
      "study identities differ",
      (rows: JsonObject[]) => {
        rows[2].study_id = "another-study";
      },
    ],
    [
      "duplicate record identities",
      (rows: JsonObject[]) => {
        rows[2].record_id = rows[1].record_id;
      },
    ],
    [
      "unresolved record role",
      (rows: JsonObject[]) => {
        rows[2].record_role = "unresolved_record_identity";
      },
    ],
    [
      "malformed named condition",
      (rows: JsonObject[]) => {
        process(rows[2]).other_conditions = [null];
      },
    ],
    [
      "qualified parameter",
      (rows: JsonObject[]) => {
        process(rows[2]).pressure = { ...fact(0.6, "Pa"), qualifier: "约" };
      },
    ],
  ])("naturally omits a trend for %s", (_name, change) => {
    const input = body();
    change(input.research_records as JsonObject[]);
    expect(projectPvdExperimentSeries(input)).toEqual([]);
  });

  it("rejects conflicting performance at the same parameter rather than choosing or averaging", () => {
    const input = body();
    (input.research_records as JsonObject[]).push(record(4, 0.2, 99));
    expect(projectPvdExperimentSeries(input)).toEqual([]);
  });

  it("omits unresolved and duplicate source identities", () => {
    const input = body();
    input.source_provenance = [];
    expect(projectPvdExperimentSeries(input)).toEqual([]);
    input.source_provenance = [
      { source_id: "paper", citation: "A" },
      { source_id: "paper", citation: "B" },
    ];
    expect(projectPvdExperimentSeries(input)).toEqual([]);
  });
});

function open(document: JsonObject) {
  render(
    <ArtifactRenderer
      bodyOnly
      artifact={
        {
          id: "test-series",
          format: "json",
          artifactType: "pvd.research_records",
          title: "合同测试",
          content: JSON.stringify(document),
        } as WorkbenchArtifact
      }
    />,
  );
}

describe("PVD series through the formal artifact renderer", () => {
  it("draws a parameter-performance line with original conversion values and exact source anchors", () => {
    const input = body();
    const rows = input.research_records as JsonObject[];
    process(rows[1]).pressure = fact("0.0004", "kPa");
    outcomes(rows[1])[0].value = 15000;
    outcomes(rows[1])[0].unit = "MPa";
    open(input);
    const chart = screen.getByTestId("pvd-experiment-trends");
    expect(chart).toHaveTextContent("硬度 随 压力 的变化");
    expect(chart).toHaveTextContent("压力 (Pa)");
    expect(chart).toHaveTextContent("硬度 (GPa)");
    expect(chart).toHaveTextContent("0.4 Pa → 15 GPa");
    expect(chart).toHaveTextContent("原文：0.0004 kPa → 15000 MPa");
    expect(chart.querySelectorAll("polyline")).toHaveLength(1);
    expect(within(chart).getAllByTestId("trend-point")).toHaveLength(3);
    const link = within(chart).getAllByRole("link", { name: /查看来源/ })[1];
    expect(
      document.getElementById(link.getAttribute("href")!.slice(1)),
    ).toHaveTextContent("15000 MPa");
    expect(screen.getByTestId("pvd-reported-values-chart")).toBeInTheDocument();
  });

  it("keeps the existing reported-value chart and raw records when no series qualifies", () => {
    open(body([record(1, 0.2, 12), record(2, 0.4, 15)]));
    expect(
      screen.queryByTestId("pvd-experiment-trends"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("pvd-reported-values-chart")).toBeInTheDocument();
    expect(
      screen.getByTestId("pvd-research-record-view"),
    ).not.toHaveTextContent("暂无趋势");
  });

  it("uses finite SVG positions for large positive and negative scalars", () => {
    open(
      body([
        record(1, "-1e308", "-1e308"),
        record(2, "0", "0"),
        record(3, "1e308", "1e308"),
      ]),
    );
    const chart = screen.getByTestId("pvd-experiment-trends");
    expect(chart.querySelector("polyline")?.getAttribute("points")).not.toMatch(
      /NaN|Infinity/,
    );
    for (const circle of chart.querySelectorAll("circle")) {
      expect(Number.isFinite(Number(circle.getAttribute("cx")))).toBe(true);
      expect(Number.isFinite(Number(circle.getAttribute("cy")))).toBe(true);
    }
  });
});
