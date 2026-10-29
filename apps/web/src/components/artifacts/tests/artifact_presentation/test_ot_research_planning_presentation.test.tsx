import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import retrievalStrategyFixture from "../../../../../../../skills/opentrajectory/retrieval-matrix-generation/examples/response.json";
import type { WorkbenchArtifact } from "../../../../model/workbenchTask";
import { ArtifactRenderer } from "../../ArtifactRenderer";
import { scientificArtifactPresentationKind } from "../../ScientificArtifactPresentation";
import { SubstageArtifactFooter } from "../../../workbench/SubstageArtifactFooter";

const problemFramingFixture = {
  intent_understanding: {
    original_user_query:
      "研究 4.5V 以上、2C 以上条件下的锂金属电池醚类电解液设计。",
    research_object: "锂金属电池高电压快充条件下的醚类电解液设计",
    target_metrics: ["库伦效率", "循环寿命", "界面稳定性"],
    boundary_conditions: ["4.5V 以上", "2C 以上"],
    comparison_baseline: "常规碳酸酯电解液",
    output_intent: "comparison",
    requested_source_scope: "同行评议实验研究",
    ambiguities: ["具体锂盐未知", "测试温度未知"],
  },
  constraints_criteria: {
    interpreted_constraints: [
      {
        constraint_name: "voltage",
        original_phrase: "4.5V 以上",
        constraint_type: "condition_threshold",
        interpreted_value: "4.5V 以上",
        candidate_values: [],
        semantic_state: "confirmed",
        domain_rationale: "用户明确给出了高电压边界。",
        downstream_search_terms: [
          "high-voltage lithium metal battery",
          "4.5 V",
        ],
        evidence_basis: "4.5V 以上",
      },
      {
        constraint_name: "temperature",
        original_phrase: "未提供测试温度",
        constraint_type: "experimental_condition",
        interpreted_value: null,
        candidate_values: ["25 °C", "60 °C"],
        semantic_state: "unknown",
        domain_rationale: "温度会改变电化学性能的可比性。",
        downstream_search_terms: ["test temperature"],
        evidence_basis: null,
      },
    ],
    missing_information: [
      {
        information: "测试温度",
        why_needed: "温度会影响性能结果的可比性。",
        affected_constraint_names: ["temperature"],
      },
    ],
    research_risks: [
      {
        risk: "不同研究的快充定义可能不一致",
        downstream_impact: "跨研究比较前需要核对倍率和测试协议。",
        affected_constraint_names: ["charge_rate"],
      },
    ],
  },
};

function artifact(
  artifactType: string,
  content: Record<string, unknown>,
): WorkbenchArtifact {
  return {
    id: `artifact-${artifactType}`,
    type: "execution_output",
    artifactType,
    title: artifactType,
    format: "json",
    relatedStage: "problem_modeling",
    content: JSON.stringify(content),
  };
}

describe("OpenTrajectory research planning presentation", () => {
  it("presents problem framing as research scope while preserving formal details", () => {
    const input = artifact(
      "opentrajectory.problem_framing",
      problemFramingFixture,
    );

    expect(scientificArtifactPresentationKind(input)).toBe("problem_framing");
    render(<ArtifactRenderer artifact={input} bodyOnly />);

    const view = screen.getByTestId("ot-problem-framing-view");
    expect(
      within(view).getByRole("heading", { name: "研究问题与边界" }),
    ).toBeInTheDocument();
    const coreCard = within(view)
      .getByRole("heading", { name: "研究什么" })
      .closest("section");
    const boundaryCard = within(view)
      .getByRole("heading", { name: "已确认边界" })
      .closest("section");
    const openCard = within(view)
      .getByRole("heading", { name: "仍需确认" })
      .closest("section");
    expect(coreCard).toHaveClass("bg-surface-soft", "border-l-brand");
    expect(boundaryCard).toHaveClass("bg-surface-soft", "border-l-success");
    expect(openCard).toHaveClass("bg-surface-soft", "border-l-warning");
    expect(view.querySelector(".bg-white")).not.toBeInTheDocument();
    expect(
      within(view).getByText(
        problemFramingFixture.intent_understanding.research_object,
      ),
    ).toBeInTheDocument();
    expect(within(view).getByText("常规碳酸酯电解液")).toBeInTheDocument();
    expect(within(view).getByText("测试温度")).toBeInTheDocument();
    expect(
      within(view).getByText("不同研究的快充定义可能不一致"),
    ).toBeInTheDocument();
    expect(within(view).getAllByText("测试温度")).toHaveLength(1);

    expect(
      within(view).queryByText("Intent Understanding"),
    ).not.toBeInTheDocument();
    expect(
      within(view).queryByText("Constraints Criteria"),
    ).not.toBeInTheDocument();
    expect(
      within(view).queryByText("opentrajectory.problem_framing"),
    ).not.toBeInTheDocument();
    expect(
      within(view).queryByText("condition_threshold"),
    ).not.toBeInTheDocument();
    expect(
      within(view).queryByText("experimental_condition"),
    ).not.toBeInTheDocument();

    expect(
      within(view).queryByText("high-voltage lithium metal battery"),
    ).not.toBeInTheDocument();
    fireEvent.click(within(view).getByText("查看详情"));
    expect(
      within(view).getByText(
        problemFramingFixture.intent_understanding.original_user_query,
      ),
    ).toBeInTheDocument();
    expect(
      within(view).getByText("high-voltage lithium metal battery"),
    ).toBeInTheDocument();
    expect(within(view).getByText("条件阈值")).toBeInTheDocument();
    expect(within(view).getByText("实验条件")).toBeInTheDocument();
    expect(
      within(view).queryByText("condition_threshold"),
    ).not.toBeInTheDocument();
    expect(
      within(view).queryByText("experimental_condition"),
    ).not.toBeInTheDocument();
  });

  it("uses user-facing research planning titles in the Workbench artifact selector", () => {
    const problem = artifact(
      "opentrajectory.problem_framing",
      problemFramingFixture,
    );
    const retrieval = artifact(
      "opentrajectory.retrieval_strategy_design",
      retrievalStrategyFixture,
    );
    const artifacts = [problem, retrieval].map((item) => ({
      ...item,
      taskId: "task-t14",
      subStageInstanceId: "sub-t14",
    }));

    render(
      <SubstageArtifactFooter
        taskId="task-t14"
        substageInstanceId="sub-t14"
        artifactRefs={artifacts.map((item) => item.id)}
        artifacts={artifacts}
        selection={null}
        artifactHref={(artifactId) =>
          `/workbench/task-t14?artifact=${artifactId}`
        }
        onSelectArtifact={() => undefined}
      />,
    );

    expect(
      screen.getByRole("link", { name: "研究问题与边界" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "证据检索计划" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("opentrajectory.problem_framing"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("opentrajectory.retrieval_strategy_design"),
    ).not.toBeInTheDocument();
  });

  it("presents the retrieval matrix as an evidence search plan and keeps technical fields on demand", () => {
    const input = artifact(
      "opentrajectory.retrieval_strategy_design",
      retrievalStrategyFixture,
    );

    expect(scientificArtifactPresentationKind(input)).toBe(
      "retrieval_strategy_design",
    );
    render(<ArtifactRenderer artifact={input} bodyOnly />);

    const view = screen.getByTestId("ot-retrieval-strategy-view");
    expect(
      within(view).getByRole("heading", { name: "证据检索计划" }),
    ).toBeInTheDocument();
    expect(within(view).getByText("核心主题")).toBeInTheDocument();
    expect(within(view).getByText("机制导向")).toBeInTheDocument();
    expect(within(view).getAllByText("优先级：高").length).toBeGreaterThan(0);
    expect(within(view).getAllByText("必须包含").length).toBeGreaterThan(0);
    expect(within(view).getAllByText("排除条件").length).toBeGreaterThan(0);
    expect(view.querySelector(".bg-white")).not.toBeInTheDocument();
    expect(
      within(view).getByText(
        "Core recall entry for anode fast-charging cycling stability.",
      ),
    ).toBeInTheDocument();
    expect(
      within(view).queryByText("hard carbon anode"),
    ).not.toBeInTheDocument();

    expect(
      within(view).queryByText("Assembled Facets"),
    ).not.toBeInTheDocument();
    expect(within(view).queryByText("Core Entities")).not.toBeInTheDocument();
    expect(within(view).queryByText("Query Facets")).not.toBeInTheDocument();
    expect(
      within(view).queryByText("opentrajectory.retrieval_strategy_design"),
    ).not.toBeInTheDocument();

    const firstExpandedQuery =
      retrievalStrategyFixture.expanded_query_variants[0].query;
    expect(
      within(view).queryByText(firstExpandedQuery),
    ).not.toBeInTheDocument();
    fireEvent.click(within(view).getByText("查看详情"));
    expect(
      within(view).getByText("完整检索词（7 个方向）"),
    ).toBeInTheDocument();
    expect(within(view).getByText("扩展检索式（8 条）")).toBeInTheDocument();
    expect(within(view).getByText("来源与数据库约束")).toBeInTheDocument();
    expect(within(view).getByText("质量与结果协议")).toBeInTheDocument();
    expect(
      within(view).queryByText(firstExpandedQuery),
    ).not.toBeInTheDocument();

    fireEvent.click(within(view).getByText("完整检索词（7 个方向）"));
    expect(within(view).getByText(/hard carbon anode/)).toBeInTheDocument();
    fireEvent.click(within(view).getByText("扩展检索式（8 条）"));
    expect(within(view).getByText(firstExpandedQuery)).toBeInTheDocument();
    fireEvent.click(within(view).getByText("质量与结果协议"));
    expect(within(view).getByText(/仅使用通用布尔检索式/)).toBeInTheDocument();
    expect(
      within(view).queryByText("generic_boolean_only"),
    ).not.toBeInTheDocument();
    expect(within(view).queryByText("search_matrix")).not.toBeInTheDocument();
    expect(
      within(view).queryByText("expanded_query_variants"),
    ).not.toBeInTheDocument();
  });
});
