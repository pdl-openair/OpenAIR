// CANONICAL_SHAPE_TEST_FIXTURE: verifies the presentation layer, not a real research run.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { WorkbenchArtifact } from "../../../../model/workbenchTask";
import {
  ScientificArtifactPresentation,
  scientificArtifactPresentationKind,
} from "../../ScientificArtifactPresentation";
import { artifactTypeLabel } from "../../../../lib/workbenchUi";
import {
  PRESENTATION_FIXTURE_NOTE,
  feedbackAnalysisFixture,
} from "./pvdFeedbackAnalysisPresentationFixtures";

/**
 * Production PVD artifacts carry the runtime artifact types (feedback_analysis,
 * candidate_analysis, feasibility_validation) and NO `substage` field — the
 * routing tests below mirror that shape exactly.
 */
function artifact(
  artifactType: string,
  content: Record<string, unknown>,
): WorkbenchArtifact {
  return {
    id: `artifact-${artifactType}`,
    type: artifactType,
    artifactType,
    substage: null as unknown as string,
    title: artifactType,
    format: "json",
    relatedStage: "execution_validation" as never,
    content: JSON.stringify(content),
  };
}

describe("S5 PVD feedback analysis presentation (canonical-shape fixtures)", () => {
  afterEach(() => {
    cleanup();
  });

  it("documents that fixtures are presentation-only, not real PVD runs", () => {
    expect(PRESENTATION_FIXTURE_NOTE).toContain("CANONICAL_SHAPE_TEST_FIXTURE");
    expect(PRESENTATION_FIXTURE_NOTE).toContain("not a real PVD run");
  });

  it("routes the runtime artifact types without a substage field", () => {
    expect(
      scientificArtifactPresentationKind(
        artifact("feedback_analysis", feedbackAnalysisFixture),
      ),
    ).toBe("pvd_feedback_analysis");
    expect(
      scientificArtifactPresentationKind(
        artifact("candidate_analysis", {
          analysis_status: "degraded",
          factor_assessments: [],
        }),
      ),
    ).toBe("pvd_candidate_direction");
    expect(
      scientificArtifactPresentationKind(
        artifact("feasibility_validation", {
          validation_status: "partial",
          direction_assessments: [],
        }),
      ),
    ).toBe("pvd_feasibility_validation");
  });

  it("labels the runtime artifact types in Chinese", () => {
    expect(artifactTypeLabel("feedback_analysis")).toBe("实验反馈与轮次分析");
    expect(artifactTypeLabel("candidate_analysis")).toBe("工艺因素与候选方向");
    expect(artifactTypeLabel("feasibility_validation")).toBe(
      "参数与实验方案可行性核验",
    );
  });

  it("renders status badges, readiness banner and findings without raw keys", () => {
    render(
      <ScientificArtifactPresentation
        artifact={artifact("feedback_analysis", feedbackAnalysisFixture)}
      />,
    );

    const root = screen.getByTestId("workflow-node-pvd-feedback-analysis");
    expect(within(root).getByText("实验反馈与轮次分析")).toBeInTheDocument();
    // analysis_status + experiment_ref
    expect(within(root).getByText("部分完成")).toBeInTheDocument();
    expect(within(root).getByText("PVD-R3-1")).toBeInTheDocument();
    // summary
    expect(
      within(root).getByText(/FWHM 由 1\.95° 降至 1\.62°/),
    ).toBeInTheDocument();
    // key status badges
    expect(within(root).getByText("存在偏差")).toBeInTheDocument();
    expect(within(root).getByText("测量谨慎使用")).toBeInTheDocument();
    expect(within(root).getByText("结果改善 ↑")).toBeInTheDocument();
    // downstream readiness banner
    expect(within(root).getByText("下一轮就绪判断")).toBeInTheDocument();
    expect(within(root).getByText("谨慎供下一轮使用")).toBeInTheDocument();
    // findings with epistemic states
    expect(within(root).getAllByTestId("pvd-feedback-finding-card")).toHaveLength(2);
    expect(within(root).getByText("已观察")).toBeInTheDocument();
    expect(within(root).getByText("推断")).toBeInTheDocument();
    expect(within(root).getByText(/不确定性：仅单次执行/)).toBeInTheDocument();
    // anomalies / confounders / causal boundary / unresolved questions
    expect(within(root).getByText(/轻微打火痕迹/)).toBeInTheDocument();
    expect(within(root).getByText(/共变/)).toBeInTheDocument();
    expect(within(root).getByText("因果未确立")).toBeInTheDocument();
    expect(within(root).getByText(/哪一套最终测量集是权威结果/)).toBeInTheDocument();
  });

  it("keeps deviations, measurement limits and upstream refs in technical details", () => {
    render(
      <ScientificArtifactPresentation
        artifact={artifact("feedback_analysis", feedbackAnalysisFixture)}
      />,
    );

    const root = screen.getByTestId("workflow-node-pvd-feedback-analysis");
    // Collapsed by default: deviations live behind the technical-details panel.
    expect(within(root).queryByText("执行偏差明细")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("执行偏差、测量限制与依据来源"));
    expect(within(root).getByText("执行偏差明细")).toBeInTheDocument();
    expect(within(root).getByText(/实际功率 245 W/)).toBeInTheDocument();
    expect(within(root).getByText(/两套最终测量集存在冲突/)).toBeInTheDocument();
    expect(within(root).getByText(/推荐依据：artifact:s8-round1/)).toBeInTheDocument();
  });
});
