import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { WorkbenchArtifact } from "../../../../model/workbenchTask";
import {
  ScientificArtifactPresentation,
  scientificArtifactPresentationKind,
} from "../../ScientificArtifactPresentation";

function artifact(
  artifactType: string,
  content: Record<string, unknown>,
): WorkbenchArtifact {
  return {
    id: `artifact-${artifactType}`,
    type: artifactType,
    artifactType,
    title: artifactType,
    format: "json",
    relatedStage: "report" as never,
    content: JSON.stringify(content),
  };
}

describe("ScientificArtifactPresentation OpenTrajectory canonical outputs", () => {
  it("recognizes evidence assessment as the Evidence Graph presentation", () => {
    const input = artifact("evidence_assessment_and_research_synthesis", {
      direct_assessment: "当前证据支持有边界的判断。",
      evidence_judgments: [],
      conflicts: [],
      limitations: [],
      evidence_gaps: [],
      synthesis_warnings: [],
    });

    expect(scientificArtifactPresentationKind(input)).toBe("evidence_graph");
  });

  it("renders Condition Trend in the ResearchRecord reading flow", () => {
    const trendRecord = (index: number, x: string, y: string) => ({
      record_id: `rr:${String(index).padStart(32, "0")}`,
      research_object: "Material A",
      record_type: "quantitative_result",
      information_identity: "direct_source_fact",
      assertion_mode: "direct_observation",
      condition_status: "reported",
      conditions: [
        { name: "Temperature", status: "reported", value_text: x, unit: "K" },
        { name: "Pressure", status: "reported", value_text: "1", unit: "atm" },
      ],
      result: {
        statement: `Conductivity is ${y} S/cm.`,
        values: [{ name: "Conductivity", value_text: y, unit: "S/cm" }],
      },
      source_quote: `Measured at ${x} K.`,
      source: {
        title: `Trend source ${index}`,
        source_locator: `https://example.test/trend/${index}`,
      },
      qualifiers: [],
      uncertainty: null,
      unknown_fields: [],
    });
    const input = artifact("research_records", {
      research_records: [
        trendRecord(1, "500", "21"),
        trendRecord(2, "300", "12"),
        trendRecord(3, "400", "16"),
      ],
    });

    render(<ScientificArtifactPresentation artifact={input} />);

    const chart = screen.getByTestId("data-chart-trend");
    expect(chart).toHaveAccessibleName("Conductivity 随 Temperature 的变化图");
    expect(chart).toHaveTextContent("研究对象：Material A");
    expect(chart).toHaveTextContent("300 K → 12 S/cm");
    expect(chart).toHaveTextContent("400 K → 16 S/cm");
    expect(chart).toHaveTextContent("500 K → 21 S/cm");
    expect(chart).toHaveTextContent("其他条件：Pressure 1 atm");
    expect(chart).toHaveTextContent("3 条 ResearchRecord");
    expect(screen.getAllByTestId("trend-point")).toHaveLength(3);
    expect(
      screen.queryByTestId("data-chart-comparison"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "查看来源：Trend source 2" }),
    ).toHaveAttribute(
      "href",
      "#research-record-rr-00000000000000000000000000000002-2",
    );
  });

  it("renders no Trend shell when two conditions change", () => {
    const input = artifact("research_records", {
      research_records: [
        ["300", "1", "12"],
        ["400", "2", "16"],
        ["500", "3", "21"],
      ].map(([temperature, pressure, value], index) => ({
        record_id: `rr:${String(index + 1).padStart(32, "0")}`,
        research_object: "Material A",
        record_type: "quantitative_result",
        information_identity: "direct_source_fact",
        assertion_mode: "direct_observation",
        condition_status: "reported",
        conditions: [
          {
            name: "Temperature",
            status: "reported",
            value_text: temperature,
            unit: "K",
          },
          {
            name: "Pressure",
            status: "reported",
            value_text: pressure,
            unit: "atm",
          },
        ],
        result: {
          statement: `Reported conductivity ${value} S/cm.`,
          values: [{ name: "Conductivity", value_text: value, unit: "S/cm" }],
        },
        source: { title: `Counterexample ${index + 1}` },
        qualifiers: [],
        uncertainty: null,
        unknown_fields: [],
      })),
    });

    render(<ScientificArtifactPresentation artifact={input} />);

    expect(screen.queryByTestId("data-chart-trend")).not.toBeInTheDocument();
    expect(screen.queryByText(/暂无图表/)).not.toBeInTheDocument();
    expect(screen.getAllByText(/Reported conductivity/)).toHaveLength(3);
  });

  it("renders Context-different values, condition differences, caution, uncertainty, and sources in the ResearchRecord flow", () => {
    const contextRecord = (
      index: number,
      objectLabel: string,
      value: string,
      temperature: string,
      pressure: string,
      uncertainty: string | null = null,
    ) => ({
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
          value_text: temperature,
          unit: "K",
        },
        {
          name: "Pressure",
          status: "reported",
          value_text: pressure,
          unit: "atm",
        },
        {
          name: "Duration",
          status: "reported",
          value_text: "2",
          unit: "h",
        },
      ],
      result: {
        statement: `${objectLabel} reported ${value}% yield.`,
        values: [{ name: "Yield", value_text: value, unit: "%" }],
      },
      source_quote: `Reported yield for ${objectLabel}.`,
      source: {
        title: `Context source ${index}`,
        source_locator: `https://example.test/context/${index}`,
      },
      qualifiers: [],
      uncertainty,
      unknown_fields: [],
    });
    const input = artifact("research_records", {
      research_records: [
        contextRecord(
          1,
          "Experiment A",
          "68",
          "300",
          "1",
          "Calibration drift was not quantified by the source.",
        ),
        contextRecord(2, "Experiment B", "81", "400", "2"),
      ],
    });

    render(<ScientificArtifactPresentation artifact={input} />);

    const chart = screen.getByTestId("data-chart-context");
    expect(chart).toHaveAccessibleName("Yield 条件差异下的已报告数值");
    expect(chart).toHaveTextContent("Experiment A");
    expect(chart).toHaveTextContent("68 %");
    expect(chart).toHaveTextContent("Experiment B");
    expect(chart).toHaveTextContent("81 %");
    expect(chart).toHaveTextContent("共同条件：Duration 2 h");
    expect(chart).toHaveTextContent("Temperature");
    expect(chart).toHaveTextContent("Pressure");
    expect(chart).toHaveTextContent(
      "不确定性：Calibration drift was not quantified by the source.",
    );
    expect(chart).toHaveTextContent("不能据此认为各组严格可比");
    expect(chart).toHaveTextContent("不能把数值差异归因于某个条件");
    expect(chart).toHaveTextContent("2 条 ResearchRecord");
    expect(screen.getAllByText(/reported \d+% yield/)).toHaveLength(2);
    expect(
      screen.getByRole("link", { name: "查看来源：Context source 1" }),
    ).toHaveAttribute(
      "href",
      "#research-record-rr-00000000000000000000000000000001-1",
    );
  });

  it("renders a trustworthy Comparison inside the ResearchRecord reading flow", () => {
    const comparisonRecord = (
      index: number,
      objectLabel: string,
      valueText: string,
    ) => ({
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
          value_text: "300",
          unit: "K",
        },
      ],
      result: {
        statement: `${objectLabel} band gap is ${valueText} eV.`,
        values: [{ name: "Band gap", value_text: valueText, unit: "eV" }],
        mechanism: null,
        comparison: null,
      },
      source_quote: `${objectLabel} was measured at 300 K.`,
      source: {
        title: `Source ${objectLabel}`,
        doi: null,
        arxiv_id: null,
        section_name: "Results",
        source_locator: `https://example.test/${index}`,
      },
      qualifiers: [],
      uncertainty: null,
      unknown_fields: [],
    });
    const input = artifact("research_records", {
      research_records: [
        comparisonRecord(1, "Sample A", "1.8"),
        comparisonRecord(2, "Sample B", "2.1"),
        comparisonRecord(3, "Sample C", "1.6"),
      ],
    });

    render(<ScientificArtifactPresentation artifact={input} />);

    const chart = screen.getByTestId("data-chart-comparison");
    expect(chart).toHaveAccessibleName("Band gap 对比图");
    expect(chart).toHaveTextContent("Band gap 对比");
    expect(chart).toHaveTextContent("最高：Sample B 2.1 eV");
    expect(chart).toHaveTextContent("最低：Sample C 1.6 eV");
    expect(chart).toHaveTextContent("相差 0.5 eV");
    expect(chart).toHaveTextContent("Temperature 300 K · 3 条 ResearchRecord");
    expect(
      screen.getByRole("link", { name: "查看来源：Source Sample A" }),
    ).toHaveAttribute(
      "href",
      "#research-record-rr-00000000000000000000000000000001-1",
    );
  });

  it("leaves the ResearchRecord reading flow unchanged when no safe Comparison exists", () => {
    const input = artifact("research_records", {
      research_records: [
        {
          record_id: "rr:00000000000000000000000000000001",
          research_object: "Sample A",
          record_type: "quantitative_result",
          information_identity: "direct_source_fact",
          assertion_mode: "direct_observation",
          condition_status: "reported",
          conditions: [
            {
              name: "Temperature",
              status: "reported",
              value_text: "300",
              unit: "K",
            },
          ],
          result: {
            statement: "The band gap was below 1.8 eV.",
            values: [{ name: "Band gap", value_text: "<1.8", unit: "eV" }],
          },
          source_quote: "The source reports a bounded value.",
          source: {
            title: "Source A",
            source_locator: "https://example.test/a",
          },
          qualifiers: [],
          uncertainty: null,
          unknown_fields: [],
        },
      ],
    });

    render(<ScientificArtifactPresentation artifact={input} />);

    expect(
      screen.queryByTestId("data-chart-comparison"),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("data-chart-context")).not.toBeInTheDocument();
    expect(screen.queryByText(/暂无图表/)).not.toBeInTheDocument();
    expect(
      screen.getByText("The band gap was below 1.8 eV."),
    ).toBeInTheDocument();
  });

  it("recognizes and renders ResearchRecord-first evidence instead of expecting legacy evidence_items", () => {
    const input = artifact("research_records", {
      research_records: [
        {
          record_id: "rr:00000000000000000000000000000001",
          research_object: "AlScN thin film",
          record_type: "quantitative_result",
          information_identity: "direct_source_fact",
          assertion_mode: "direct_observation",
          condition_status: "reported",
          conditions: [
            {
              name: "Sc content",
              status: "reported",
              value_text: "27",
              unit: "%",
            },
          ],
          result: {
            statement:
              "The reported piezoelectric response increased with Sc content in this sample series.",
            values: [{ name: "d33,f", value_text: "18", unit: "pC/N" }],
          },
          source_quote:
            "The source reports the measured response for this deposited film.",
          source: {
            title: "Example AlScN study",
            doi: "10.1000/example",
            arxiv_id: null,
            section_name: "Results",
            source_locator: "https://doi.org/10.1000/example",
          },
          uncertainty:
            "Measurement method differs from some comparison studies.",
          unknown_fields: ["substrate temperature"],
        },
      ],
    });

    expect(scientificArtifactPresentationKind(input)).toBe("evidence");
    render(<ScientificArtifactPresentation artifact={input} />);

    expect(screen.getByTestId("ot-research-record-view")).toBeInTheDocument();
    expect(screen.getByText("来源研究记录")).toBeInTheDocument();
    expect(screen.getByText(/AlScN thin film/)).toBeInTheDocument();
    expect(screen.getByText(/d33,f：18 pC\/N/)).toBeInTheDocument();
    expect(screen.getByText(/Sc content：27 %/)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /Example AlScN study/ }),
    ).toHaveAttribute("href", "https://doi.org/10.1000/example");
    expect(
      screen.getByText(/未报告 \/ 未确认：substrate temperature/),
    ).toBeInTheDocument();
  });

  it("preserves partially unknown research conditions instead of presenting them as reported", () => {
    const input = artifact("research_records", {
      research_records: [
        {
          record_id: "rr:00000000000000000000000000000003",
          research_object: "AlScN thin film",
          record_type: "quantitative_result",
          information_identity: "direct_source_fact",
          assertion_mode: "direct_observation",
          condition_status: "partially_unknown",
          conditions: [
            {
              name: "Sc content",
              status: "reported",
              value_text: "27",
              unit: "%",
            },
            {
              name: "humidity",
              status: "unknown",
              value_text: null,
              unit: null,
            },
          ],
          result: {
            statement:
              "The response was measured for this sample, but humidity was not reported.",
            values: [{ name: "d33,f", value_text: "18", unit: "pC/N" }],
          },
          source_quote: "Humidity was not reported in the source conditions.",
          source: {
            title: "Example partially specified AlScN study",
            doi: null,
            arxiv_id: null,
            section_name: "Methods",
            source_locator: "",
          },
          uncertainty: null,
          unknown_fields: [],
        },
      ],
    });

    render(<ScientificArtifactPresentation artifact={input} />);

    expect(screen.getByText(/Sc content：27 %/)).toBeInTheDocument();
    expect(screen.getByText(/humidity：未报告/)).toBeInTheDocument();
    expect(screen.getByText("条件信息：部分未报告")).toBeInTheDocument();
  });

  it("renders final synthesis as direct answer, supported conclusions, conflicts, gaps, leads, and clearly unvalidated hypotheses", () => {
    const input = artifact("final_synthesis", {
      direct_answer: {
        answer:
          "Current evidence supports a conditional increase rather than a universal monotonic gain.",
        supporting_record_ids: [
          "rr:00000000000000000000000000000001",
          "rr:00000000000000000000000000000002",
        ],
        caveats: [
          "Deposition and measurement conditions are not fully comparable.",
        ],
        confidence: 0.78,
      },
      evidence_supported_conclusions: [
        {
          statement:
            "Higher Sc content is associated with stronger reported piezoelectric response within comparable series.",
          epistemic_status: "boundary_limited",
          supporting_record_ids: ["rr:00000000000000000000000000000001"],
          evidence_basis:
            "Multiple source-grounded records report the same direction under bounded conditions.",
          scope_boundary:
            "Applies to the reported sputtered AlScN conditions rather than all AlScN films.",
          uncertainties: ["Cross-study measurement protocols differ."],
          confidence: 0.82,
        },
      ],
      conflicts: [
        {
          statement:
            "The magnitude of improvement differs substantially across studies.",
          record_ids: [
            "rr:00000000000000000000000000000001",
            "rr:00000000000000000000000000000002",
          ],
          implication:
            "Do not rank compositions across incomparable process conditions.",
        },
      ],
      uncertainties: ["Some studies omit stress state."],
      evidence_gaps: [
        {
          gap: "Matched process-condition comparisons are sparse.",
          impact:
            "The effective Sc-content interval cannot yet be generalized confidently.",
        },
      ],
      research_leads: [
        {
          title: "Matched-condition comparison",
          research_question:
            "How does Sc content change response when process conditions are held comparable?",
          rationale: "This isolates composition from process confounding.",
        },
      ],
      hypotheses: [
        {
          title: "Process-mediated gain",
          statement:
            "Part of the reported gain is mediated by process-dependent structural quality.",
          falsifiable_prediction:
            "Under matched structure quality, the cross-study gain should narrow.",
          falsification_conditions: [
            "The gain remains unchanged after structure quality is matched.",
          ],
        },
      ],
    });

    expect(scientificArtifactPresentationKind(input)).toBe("final_synthesis");
    render(<ScientificArtifactPresentation artifact={input} />);

    expect(screen.getByTestId("ot-final-synthesis-view")).toBeInTheDocument();
    expect(
      screen.getByText(/Current evidence supports a conditional increase/),
    ).toBeInTheDocument();
    expect(screen.getByText("证据支持的关键结论")).toBeInTheDocument();
    expect(screen.getByTestId("ot-final-conflicts")).toHaveTextContent(
      "The magnitude of improvement differs substantially",
    );
    expect(screen.getByTestId("ot-final-uncertainties-gaps")).toHaveTextContent(
      "Matched process-condition comparisons are sparse",
    );
    expect(screen.getByTestId("ot-final-research-leads")).toHaveTextContent(
      "Matched-condition comparison",
    );
    expect(screen.getByTestId("ot-final-hypotheses")).toHaveTextContent(
      "尚不是已经建立的事实",
    );
    expect(screen.getByTestId("ot-final-hypotheses")).toHaveTextContent(
      "什么结果会推翻它",
    );
  });
});
