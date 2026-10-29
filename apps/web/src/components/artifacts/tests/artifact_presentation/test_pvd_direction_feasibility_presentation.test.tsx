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
  candidateDirectionFixture,
  feasibilityBlockFixture,
  feasibilityConditionalFixture,
  feasibilityDeferFixture,
  malformedCandidateFixture,
  malformedFeasibilityFixture,
  candidateDirectionFeedbackRoundFixture,
} from "./pvdDirectionFeasibilityPresentationFixtures";

function artifact(
  artifactType: string,
  content: Record<string, unknown>,
  substage?: string,
): WorkbenchArtifact {
  return {
    id: `artifact-${artifactType}`,
    type: artifactType,
    artifactType,
    substage: substage ?? artifactType,
    title: artifactType,
    format: "json",
    relatedStage: "execution_validation" as never,
    content: JSON.stringify(content),
  };
}

describe("T14 PVD direction & feasibility presentation (canonical-shape fixtures)", () => {
  afterEach(() => {
    cleanup();
  });

  it("documents that fixtures are presentation-only, not real PVD runs", () => {
    expect(PRESENTATION_FIXTURE_NOTE).toContain("CANONICAL_SHAPE_TEST_FIXTURE");
    expect(PRESENTATION_FIXTURE_NOTE).toContain("not a real PVD run");
  });

  it("routes Cap5/Cap6 to dedicated scientific presentation and labels", () => {
    expect(
      scientificArtifactPresentationKind(
        artifact(
          "process_factor_candidate_experiment_analysis",
          candidateDirectionFixture,
        ),
      ),
    ).toBe("pvd_candidate_direction");
    expect(
      scientificArtifactPresentationKind(
        artifact(
          "parameter_experiment_plan_feasibility_validation",
          feasibilityConditionalFixture,
        ),
      ),
    ).toBe("pvd_feasibility_validation");
    expect(
      artifactTypeLabel("process_factor_candidate_experiment_analysis"),
    ).toBe("工艺因素与候选方向");
    expect(
      artifactTypeLabel("parameter_experiment_plan_feasibility_validation"),
    ).toBe("参数与实验方案可行性核验");
  });

  it("renders candidate direction as factor → direction → role → rationale without inventing concrete settings", () => {
    render(
      <ScientificArtifactPresentation
        artifact={artifact(
          "process_factor_candidate_experiment_analysis",
          candidateDirectionFixture,
        )}
      />,
    );

    const root = screen.getByTestId("workflow-node-pvd-candidate-direction");
    expect(within(root).getByText("工艺因素与候选方向")).toBeInTheDocument();
    // analysis_status
    expect(within(root).getByText("分析完整")).toBeInTheDocument();
    // 4-parameter summary cards
    expect(within(root).getAllByTestId("pvd-controlled-param-card")).toHaveLength(4);
    // Factor name via researchPresentationParameterName
    expect(within(root).getAllByText("功率").length).toBeGreaterThan(0);
    // Directions translated
    expect(within(root).getAllByText("增加 ↑").length).toBeGreaterThan(0);
    // Roles translated (new schema uses role field)
    expect(within(root).getAllByText("主要调整").length).toBeGreaterThan(0);
    expect(within(root).getAllByText("保持不变").length).toBeGreaterThan(0);
    // Confidence tags in summary cards
    expect(within(root).getAllByText(/证据/).length).toBeGreaterThan(0);
    // Rationale present
    expect(
      within(root).getByText("优先验证功率方向是否带来可观测响应变化。"),
    ).toBeInTheDocument();
    // Limitations — in collapse, click to expand first
    fireEvent.click(within(root).getByText("假设与限制"));
    expect(
      within(root).getByText(
        "本结果仅为方向判断，不构成具体参数设置或可执行实验方案。",
      ),
    ).toBeInTheDocument();
  });

  it("renders feasibility gate first and distinguishes conditional / defer / block", () => {
    const { rerender } = render(
      <ScientificArtifactPresentation
        artifact={artifact(
          "parameter_experiment_plan_feasibility_validation",
          feasibilityConditionalFixture,
        )}
      />,
    );

    let root = screen.getByTestId("workflow-node-pvd-feasibility-validation");
    expect(within(root).getAllByText(/条件.*放行|满足条件后可进入推荐/).length).toBeGreaterThan(0);
    expect(within(root).getAllByText(/满足条件后可执行|条件可执行/).length).toBeGreaterThan(0);
    expect(within(root).getAllByText("条件放行").length).toBeGreaterThan(0);
    expect(
      within(root).getAllByText("确认冷却回路可支撑更高功率").length,
    ).toBeGreaterThan(0);
    expect(within(root).getByText("长时间稳定性仍未知")).toBeInTheDocument();
    expect(
      within(root).queryByText("node6:factor_assessment:power"),
    ).not.toBeInTheDocument();
    expect(within(root).queryByText("direction_refs")).not.toBeInTheDocument();
    expect(within(root).queryByText("scope_exact")).not.toBeInTheDocument();

    fireEvent.click(within(root).getByText("跨参数核验与完整限制"));
    expect(within(root).getAllByText(/跨参数核验/).length).toBeGreaterThan(0);

    rerender(
      <ScientificArtifactPresentation
        artifact={artifact(
          "parameter_experiment_plan_feasibility_validation",
          feasibilityDeferFixture,
        )}
      />,
    );
    root = screen.getByTestId("workflow-node-pvd-feasibility-validation");
    expect(within(root).getAllByText("暂缓").length).toBeGreaterThan(0);
    expect(within(root).getAllByText("信息不足").length).toBeGreaterThan(0);
    expect(
      within(root).getAllByText("缺少靶材热负载上限确认").length,
    ).toBeGreaterThan(0);

    rerender(
      <ScientificArtifactPresentation
        artifact={artifact(
          "parameter_experiment_plan_feasibility_validation",
          feasibilityBlockFixture,
        )}
      />,
    );
    root = screen.getByTestId("workflow-node-pvd-feasibility-validation");
    expect(within(root).getAllByText("阻止").length).toBeGreaterThan(0);
    expect(within(root).getAllByText("不可执行").length).toBeGreaterThan(0);
    expect(
      within(root).getAllByText("拟提高方向超过设备最大功率硬上限").length,
    ).toBeGreaterThan(0);
  });

  it("fail-closes malformed Cap5/Cap6 rows without inventing Title Case fields", () => {
    const { rerender } = render(
      <ScientificArtifactPresentation
        artifact={artifact(
          "process_factor_candidate_experiment_analysis",
          malformedCandidateFixture,
        )}
      />,
    );
    const direction = screen.getByTestId("workflow-node-pvd-candidate-direction");
    // malformed fixture has no is_controlled_four_parameter=true rows and
    // no valid role/confidence/risk — should not invent Title Case fields or leak raw keys
    expect(within(direction).queryByText("Unknown Direction")).not.toBeInTheDocument();
    expect(within(direction).queryByText("UNKNOWN_DIRECTION")).not.toBeInTheDocument();
    // The recommended_next_experiment_focus rationale still shows (it's valid)
    expect(
      within(direction).getByText("should not invent names from malformed rows"),
    ).toBeInTheDocument();

    rerender(
      <ScientificArtifactPresentation
        artifact={artifact(
          "parameter_experiment_plan_feasibility_validation",
          malformedFeasibilityFixture,
        )}
      />,
    );
    const feasibility = screen.getByTestId(
      "workflow-node-pvd-feasibility-validation",
    );
    // malformed feasibility status should not surface as raw value
    expect(
      within(feasibility).queryByText("Not A Real Status"),
    ).not.toBeInTheDocument();
    expect(
      within(feasibility).queryByText("NOT_A_REAL_STATUS"),
    ).not.toBeInTheDocument();
    // gate label (release) should still be there
    expect(within(feasibility).getByText("可进入推荐")).toBeInTheDocument();
  });

  it("keeps Cap7 round recommendation on its dedicated renderer", () => {
    expect(
      scientificArtifactPresentationKind(
        artifact("round_experiment_plan_parameter_recommendation", {
          decision_status: "recommendation",
          experiments: [],
        }),
      ),
    ).toBe("pvd_round_recommendation");
  });
});

describe("S6 feedback-round (history-mode) candidate analysis", () => {
  afterEach(() => {
    cleanup();
  });

  it("routes and renders recommendation parameters with uncertainty tags", () => {
    expect(
      scientificArtifactPresentationKind(
        artifact("candidate_analysis", candidateDirectionFeedbackRoundFixture),
      ),
    ).toBe("pvd_candidate_direction");

    render(
      <ScientificArtifactPresentation
        artifact={artifact("candidate_analysis", candidateDirectionFeedbackRoundFixture)}
      />,
    );

    const root = screen.getByTestId("workflow-node-pvd-candidate-direction");
    expect(within(root).getByText("反馈轮（基于实验诊断）")).toBeInTheDocument();
    expect(within(root).getByText("分析受限")).toBeInTheDocument();
    // 4 recommendation parameters recognized via is_recommendation_parameter
    expect(within(root).getAllByTestId("pvd-controlled-param-card")).toHaveLength(4);
    expect(within(root).getAllByText("方向未确定").length).toBeGreaterThan(0);
    expect(within(root).getAllByText("保持不变").length).toBeGreaterThan(0);
    expect(within(root).getAllByText("不确定性高").length).toBeGreaterThan(0);
    // focus banner with factor_refs
    expect(within(root).getByText("下一轮优先关注")).toBeInTheDocument();
    expect(within(root).getAllByText("功率").length).toBeGreaterThan(0);
    expect(within(root).getAllByText("氩气流量（Ar）").length).toBeGreaterThan(0);
    // no factor collapsed into 背景 section
    expect(within(root).queryByText(/背景与混杂因素/)).not.toBeInTheDocument();
  });
});
