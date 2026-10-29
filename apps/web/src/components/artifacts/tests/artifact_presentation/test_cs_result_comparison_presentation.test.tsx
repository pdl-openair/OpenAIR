import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { WorkbenchArtifact } from "../../../../model/workbenchTask";
import {
  ScientificArtifactPresentation,
  scientificArtifactPresentationKind,
} from "../../ScientificArtifactPresentation";
import { artifactTypeLabel } from "../../../../lib/workbenchUi";
import {
  actualMetricRecordsFixture,
  comparisonJudgmentComparableFixture,
  comparisonJudgmentIndeterminateFixture,
  invalidNestedComparisonFixture,
  PRESENTATION_FIXTURE_NOTE,
} from "./csResultComparisonPresentationFixtures";

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
    relatedStage: "execution_validation" as never,
    content: JSON.stringify(content),
  };
}

describe("T14 CS result comparison presentation (canonical-shape fixtures)", () => {
  it("documents that fixtures are presentation-only, not real CS runs", () => {
    expect(PRESENTATION_FIXTURE_NOTE).toContain("CANONICAL_SHAPE_TEST_FIXTURE");
    expect(PRESENTATION_FIXTURE_NOTE).toContain("not a real CS run");
  });

  it("routes actual_metric_records and reproduction_comparison_judgment to dedicated scientific presentation", () => {
    expect(
      scientificArtifactPresentationKind(
        artifact("actual_metric_records", actualMetricRecordsFixture),
      ),
    ).toBe("cs_actual_metric_records");
    expect(
      scientificArtifactPresentationKind(
        artifact(
          "reproduction_comparison_judgment",
          comparisonJudgmentComparableFixture,
        ),
      ),
    ).toBe("cs_reproduction_comparison_judgment");
    expect(artifactTypeLabel("actual_metric_records")).toBe("本次实际结果");
    expect(artifactTypeLabel("reproduction_comparison_judgment")).toBe(
      "结果比较与达标判断",
    );
  });

  it("renders actual metric records as a measurement-first table without comparison semantics", () => {
    render(
      <ScientificArtifactPresentation
        artifact={artifact("actual_metric_records", actualMetricRecordsFixture)}
      />,
    );

    const root = screen.getByTestId("cs-actual-metric-records");
    expect(within(root).getByText("本次实际结果")).toBeInTheDocument();
    expect(within(root).getByText("记录完整")).toBeInTheDocument();
    expect(within(root).getByText("Avg.")).toBeInTheDocument();
    expect(within(root).getByText("81.58")).toBeInTheDocument();
    expect(within(root).getByText("STS-B")).toBeInTheDocument();
    expect(within(root).getAllByText("—").length).toBeGreaterThan(0);
    expect(within(root).getByText(/提取失败（未找到该指标）/)).toBeInTheDocument();
    expect(within(root).getByText(/存在 1 条部分失败记录/)).toBeInTheDocument();
    expect(
      within(root).getByText("stdout table missing STS-B column"),
    ).toBeInTheDocument();

    expect(within(root).queryByText("参考结果")).not.toBeInTheDocument();
    expect(within(root).queryByText("达到判据")).not.toBeInTheDocument();
    expect(within(root).queryByText("未达到判据")).not.toBeInTheDocument();
    expect(within(root).queryByText("meets_rule")).not.toBeInTheDocument();
    expect(within(root).queryByText("source_trace")).not.toBeInTheDocument();
    expect(
      within(root).queryByText("execution://run-simcse-demo"),
    ).not.toBeInTheDocument();

    fireEvent.click(within(root).getByText("技术详情与核查信息"));
    expect(
      within(root).getByText("execution://run-simcse-demo"),
    ).toBeInTheDocument();
  });

  it("renders comparison with comparability before verdict and preserves indeterminate", () => {
    render(
      <ScientificArtifactPresentation
        artifact={artifact(
          "reproduction_comparison_judgment",
          comparisonJudgmentIndeterminateFixture,
        )}
      />,
    );

    const root = screen.getByTestId("cs-reproduction-comparison-judgment");
    const comparability = within(root).getByTestId("cs-comparison-comparability");
    const metrics = within(root).getByTestId("cs-comparison-metric-table");
    const verdict = within(root).getByTestId("cs-comparison-verdict");

    expect(root.compareDocumentPosition(comparability) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(
      comparability.compareDocumentPosition(metrics) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      metrics.compareDocumentPosition(verdict) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    expect(within(comparability).getByText("当前不可直接比较")).toBeInTheDocument();
    expect(within(comparability).getByText("数据划分")).toBeInTheDocument();
    expect(within(comparability).getByText("不一致")).toBeInTheDocument();
    expect(
      within(comparability).getByText(/存在条件差异记录/),
    ).toBeInTheDocument();
    expect(within(comparability).queryByText("data_split")).not.toBeInTheDocument();
    expect(
      within(comparability).queryByText("opaque_mismatch_record"),
    ).not.toBeInTheDocument();

    expect(within(metrics).getByText("Avg.")).toBeInTheDocument();
    expect(within(metrics).getByText("81.58")).toBeInTheDocument();
    expect(within(metrics).getByText("81.5")).toBeInTheDocument();
    expect(within(metrics).getByText("精确一致")).toBeInTheDocument();
    expect(within(metrics).getByText("无法判定")).toBeInTheDocument();

    expect(
      within(verdict).getByTestId("cs-comparison-overall-verdict"),
    ).toHaveTextContent("无法判定");
    expect(within(verdict).queryByText("未达到判据")).not.toBeInTheDocument();
    expect(within(verdict).queryByText("does_not_meet_standard")).not.toBeInTheDocument();
    expect(within(root).getByText("可比性尚未建立")).toBeInTheDocument();
    expect(within(root).queryByText("not_comparable")).not.toBeInTheDocument();
    expect(within(root).queryByText("overall_verdict")).not.toBeInTheDocument();
    expect(
      within(root).queryByText("comparability_not_established"),
    ).not.toBeInTheDocument();
  });

  it("renders comparable judgment rows with formal rule and delta labels", () => {
    render(
      <ScientificArtifactPresentation
        artifact={artifact(
          "reproduction_comparison_judgment",
          comparisonJudgmentComparableFixture,
        )}
      />,
    );

    const root = screen.getByTestId("cs-reproduction-comparison-judgment");
    const verdict = within(root).getByTestId("cs-comparison-verdict");
    const metrics = within(root).getByTestId("cs-comparison-metric-table");
    expect(within(root).getByText("可以比较")).toBeInTheDocument();
    expect(within(metrics).getByText("绝对容差（容差 0.1）")).toBeInTheDocument();
    expect(within(metrics).getByText("达到判据")).toBeInTheDocument();
    expect(
      within(verdict).getByTestId("cs-comparison-overall-verdict"),
    ).toHaveTextContent("达到判据");
    expect(
      within(root).getByText(/差值（正式计算结果）符号差 0\.08/),
    ).toBeInTheDocument();
    expect(within(root).queryByText("absolute_tolerance")).not.toBeInTheDocument();
    expect(within(root).queryByText("signed_delta_decimal")).not.toBeInTheDocument();
  });

  it("fail-closes invalid nested comparison payloads without crashing", () => {
    render(
      <ScientificArtifactPresentation
        artifact={artifact(
          "reproduction_comparison_judgment",
          invalidNestedComparisonFixture,
        )}
      />,
    );

    const root = screen.getByTestId("cs-reproduction-comparison-judgment");
    expect(
      within(root).getByText(/比较条件明细当前无法安全结构化展示/),
    ).toBeInTheDocument();
    expect(
      within(root).getByText(/逐指标比较当前无法安全结构化展示/),
    ).toBeInTheDocument();
    expect(within(root).queryByText("verdict")).not.toBeInTheDocument();
    expect(within(root).queryByText("pass")).not.toBeInTheDocument();
  });

  it("keeps reproduction_conclusion on its dedicated renderer", () => {
    expect(
      scientificArtifactPresentationKind(
        artifact("reproduction_conclusion", {
          verdict: "indeterminate",
          answer: { text: "尚不能形成复现结论。" },
          scope: { text: "冻结评测范围" },
          execution_status: "succeeded",
          evidence_sufficiency: "insufficient",
          limitations: [],
          unknowns: [],
          next_steps: [],
        }),
      ),
    ).toBe("cs_reproduction_conclusion");

    render(
      <ScientificArtifactPresentation
        artifact={artifact("reproduction_conclusion", {
          verdict: "indeterminate",
          answer: { text: "尚不能形成复现结论。" },
          scope: { text: "冻结评测范围" },
          execution_status: "succeeded",
          evidence_sufficiency: "insufficient",
          limitations: [],
          unknowns: [],
          next_steps: [],
        })}
      />,
    );
    expect(screen.getByTestId("workflow-node-cs-conclusion")).toBeInTheDocument();
    expect(screen.getByText("复现结论：当前无法判定")).toBeInTheDocument();
  });
});
