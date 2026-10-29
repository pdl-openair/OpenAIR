// CANONICAL_SHAPE_TEST_FIXTURE: exercises rendering, not a real PVD run.
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { WorkbenchArtifact } from "../../../../model/workbenchTask";
import { ArtifactRenderer } from "../../ArtifactRenderer";
import { scientificArtifactPresentationKind } from "../../ScientificArtifactPresentation";
import { projectPvdReportedValues } from "../../pvdReportedValueProjection";
import { convertReportedUnit } from "../../reportedUnitConversion";

function fixture() {
  return {
    research_records: [
      {
        record_id: "test-record-a",
        source_id: "test-source",
        sample_id: "样品 A",
        record_role: "explicit_research_record",
        material: {
          film_composition: { fact_status: "known", value: "AlScN" },
        },
        process: { pressure: { fact_status: "known", value: 0.3, unit: "Pa" } },
        outcomes: [
          { metric: "硬度", value: 12, unit: "GPa", uncertainty: "±0.5 GPa" },
        ],
        provenance_refs: ["test-evidence-a"],
      },
      {
        record_id: "test-record-b",
        source_id: "test-source",
        sample_id: "样品 B",
        record_role: "explicit_research_record",
        material: {
          film_composition: { fact_status: "known", value: "AlScN" },
        },
        process: {
          pressure: { fact_status: "known", value: 0.6, unit: "Pa" },
          substrate_temperature: { fact_status: "not_reported" },
        },
        outcomes: [{ metric: "硬度", value: "15", unit: "GPa" }],
        provenance_refs: ["test-evidence-b"],
      },
    ],
    source_provenance: [
      {
        source_id: "test-source",
        source_type: "journal_article",
        citation: "合同形状测试文献（非真实运行）",
        doi: "10.0000/presentation-test",
        access_state: "verified",
      },
    ],
    coverage: { decision_coverage_sufficient: false },
    unresolved_gaps: [{ description: "沉积温度未报告，迁移判断仍有限制。" }],
    warnings: [],
  };
}

function artifact(
  document: object,
  type = "pvd.research_records",
  skill?: string,
): WorkbenchArtifact {
  return {
    id: "test-pvd-artifact",
    type,
    artifactType: type,
    title: "文献证据闭环",
    format: "json",
    relatedStage: "execution_validation",
    relatedSkillId: skill,
    content: JSON.stringify(document),
  };
}

describe("PVD reported values in the canonical artifact reading flow", () => {
  it("keeps finalized Evidence readable when no formal research records exist and omits system warnings", () => {
    const document = {
      research_records: [],
      evidence_packet: {
        evidence_items: [
          {
            evidence_id: "test-final-evidence",
            source_anchor_id: "test-source-anchor",
            claim: "合同形状测试：报告条件存在可追溯依据。",
            evidence_quote:
              "Original source quote preserved without translation.",
            section_name: "Methods",
            arxiv_id: "2601.00000",
            conditions: [{ name: "pressure", value_text: "0.3", unit: "Pa" }],
            metric: null,
            qualifiers: ["条件不同，不可直接比较。"],
          },
        ],
      },
      warnings: ["SYSTEM_PROVIDER_FAILURE_MUST_NOT_RENDER"],
      unresolved_gaps: [{ description: "独立研究记录身份尚未整理。" }],
    };
    const { container } = render(
      <ArtifactRenderer artifact={artifact(document)} bodyOnly />,
    );
    expect(screen.getByTestId("pvd-verified-evidence")).toHaveTextContent(
      "已核验文献证据 · 1 条",
    );
    expect(
      screen.getByText("合同形状测试：报告条件存在可追溯依据。"),
    ).toBeInTheDocument();
    expect(screen.getByText("独立研究记录身份尚未整理。")).toBeInTheDocument();
    expect(
      screen.queryByTestId("pvd-reported-values-chart"),
    ).not.toBeInTheDocument();
    expect(container.textContent).not.toContain(
      "SYSTEM_PROVIDER_FAILURE_MUST_NOT_RENDER",
    );
  });
  it("renders real bar elements through ArtifactRenderer and links to exact records, conditions and provenance", () => {
    render(<ArtifactRenderer artifact={artifact(fixture())} bodyOnly />);
    const chart = screen.getByTestId("pvd-reported-values-chart");
    expect(chart).toHaveAccessibleName("硬度 原文报告值（GPa）");
    expect(chart).toHaveTextContent("12 GPa");
    expect(chart).toHaveTextContent("15 GPa");
    expect(chart).toHaveTextContent("±0.5 GPa");
    expect(chart).toHaveTextContent("0.3 Pa");
    expect(chart).toHaveTextContent("0.6 Pa");
    const links = within(chart).getAllByRole("link");
    for (const link of links) {
      const target = document.getElementById(
        link.getAttribute("href")!.slice(1),
      );
      expect(target).toHaveTextContent(link.textContent!.split(" · ")[0]);
    }
    expect(
      screen.getAllByRole("link", {
        name: "合同形状测试文献（非真实运行）",
      })[0],
    ).toHaveAttribute("href", "https://doi.org/10.0000/presentation-test");
    expect(chart).toHaveTextContent("不表示各组严格可比");
    expect(chart).not.toHaveTextContent("最高");
    expect(chart).not.toHaveTextContent("相差");
    expect(screen.getByTestId("pvd-research-record-view")).toHaveTextContent(
      "当前决策所需证据尚未充分覆盖",
    );
  });

  it.each([
    "pvd_literature_evidence_acquisition_closure",
    "pvd_research_record_structuring",
  ])(
    "routes the complete output of %s without assuming a single artifact type",
    (skill) => {
      const document = fixture();
      const body =
        skill === "pvd_research_record_structuring"
          ? {
              evidence_records: document.research_records,
              source_provenance: document.source_provenance,
            }
          : document;
      const input = artifact(body, "skill_result", skill);
      expect(scientificArtifactPresentationKind(input)).toBe(
        "pvd_research_records",
      );
      render(<ArtifactRenderer artifact={input} bodyOnly />);
      expect(
        screen.getByTestId("pvd-reported-values-chart"),
      ).toBeInTheDocument();
    },
  );

  it("naturally omits charts for qualitative/single observations and keeps unknowns and original content", () => {
    const document = fixture();
    document.research_records[0].outcomes[0].value = "取向改善";
    render(<ArtifactRenderer artifact={artifact(document)} bodyOnly />);
    expect(
      screen.queryByTestId("pvd-reported-values-chart"),
    ).not.toBeInTheDocument();
    const view = screen.getByTestId("pvd-research-record-view");
    expect(view).toHaveTextContent("取向改善");
    expect(view).toHaveTextContent("衬底温度：未报告");
    expect(view).toHaveTextContent("沉积温度未报告，迁移判断仍有限制。");
    expect(view).not.toHaveTextContent("暂无图表");
    expect(view).not.toHaveTextContent("无法绘图");
    expect(view).not.toHaveTextContent("补充数据");
    expect(screen.queryByTestId("data-chart-trend")).not.toBeInTheDocument();
  });

  it("does not infer PVD identity from an arbitrary research_records payload", () => {
    expect(
      scientificArtifactPresentationKind(
        artifact(fixture(), "unrelated_result"),
      ),
    ).toBeNull();
  });

  it("converts explicit compatible units and preserves original values and units", () => {
    const document = fixture();
    document.research_records.push({
      ...document.research_records[1],
      record_id: "test-c",
      outcomes: [{ metric: "硬度", value: 12000, unit: "MPa" }],
    });
    const groups = projectPvdReportedValues(document);
    expect(groups).toHaveLength(1);
    expect(groups[0].unit).toBe("GPa");
    expect(groups[0].rows.map((row) => row.value)).toEqual([12, 15, 12]);
    render(<ArtifactRenderer artifact={artifact(document)} bodyOnly />);
    expect(screen.getByTestId("pvd-reported-values-chart")).toHaveTextContent(
      "原文：12000 MPa；换算：12 GPa",
    );
    document.research_records[2].outcomes[0].unit = "unrecognized-unit";
    expect(projectPvdReportedValues(document)[0].rows).toHaveLength(2);
  });

  it("uses exact decimal shifts and rejects conversions requiring context or producing overflow", () => {
    expect(convertReportedUnit("0.3", "MPa", "GPa")).toEqual({
      value: 0.0003,
      label: "0.0003",
    });
    expect(convertReportedUnit("1.20e3", "nm", "µm")).toEqual({
      value: 1.2,
      label: "1.2",
    });
    expect(convertReportedUnit("-250", "mV", "V")).toEqual({
      value: -0.25,
      label: "-0.25",
    });
    expect(convertReportedUnit("20", "°C", "K")).toBeNull();
    expect(convertReportedUnit("20", "Pa", "m")).toBeNull();
    expect(convertReportedUnit("1e308", "GPa", "Pa")).toBeNull();
    expect(convertReportedUnit("1e-323", "nm", "m")).toBeNull();
  });

  it.each(["12–15", ">15", "15 ± 0.5", "NaN", "Infinity", ""])(
    "does not coerce ambiguous reported value %s into a scalar",
    (value) => {
      const document = fixture();
      document.research_records[0].outcomes[0].value = value;
      expect(projectPvdReportedValues(document)).toEqual([]);
    },
  );

  it("requires unambiguous record and source identities; ungrounded records remain readable", () => {
    const document = fixture();
    document.source_provenance.push({ ...document.source_provenance[0] });
    expect(projectPvdReportedValues(document)).toEqual([]);
    document.source_provenance.pop();
    document.research_records[1].record_id =
      document.research_records[0].record_id;
    expect(projectPvdReportedValues(document)).toEqual([]);
    document.research_records[1].record_id = "test-b";
    document.research_records[0].record_role = "unresolved_record_identity";
    expect(projectPvdReportedValues(document)).toEqual([]);
    render(<ArtifactRenderer artifact={artifact(document)} bodyOnly />);
    expect(screen.getByTestId("pvd-research-record-view")).toHaveTextContent(
      "样品 A",
    );
  });

  it("omits unverifiable sources and missing units without hiding records", () => {
    const document = fixture();
    document.source_provenance[0].access_state = "unverified";
    expect(projectPvdReportedValues(document)).toEqual([]);
    document.source_provenance[0].access_state = "verified";
    document.research_records[0].outcomes[0].unit = "";
    expect(projectPvdReportedValues(document)).toEqual([]);
  });

  it("renders zero, negative and very large finite scalars without non-finite bar geometry", () => {
    const document = fixture();
    document.research_records[0].outcomes[0].value = -1e308;
    document.research_records[1].outcomes[0].value = "1e308";
    const { container, unmount } = render(
      <ArtifactRenderer artifact={artifact(document)} bodyOnly />,
    );
    expect(container.innerHTML).not.toMatch(
      /(?:width|left):[^;]*(?:NaN|Infinity)/,
    );
    unmount();
    document.research_records[0].outcomes[0].value = 0;
    document.research_records[1].outcomes[0].value = "0";
    render(<ArtifactRenderer artifact={artifact(document)} bodyOnly />);
    expect(screen.getByTestId("pvd-reported-values-chart")).toHaveTextContent(
      "0 GPa",
    );
  });
});
