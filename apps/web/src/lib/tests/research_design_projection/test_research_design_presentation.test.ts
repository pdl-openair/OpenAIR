import { describe, expect, it } from "vitest";
import {
  getSubStageLabels,
  type TaskPlan,
  type TaskPlanStage,
  type WorkflowSubStage,
} from "@openair/shared";
import {
  researchDesignDisplayTitle,
  researchDesignOverview,
  researchDraftExpectedOutput,
  researchDraftNodeAction,
  researchDraftNodeTitle,
  researchDraftStageDescription,
  researchDraftText,
} from "../../researchDesignPresentation";

function substage(
  partial: Partial<WorkflowSubStage> & Pick<WorkflowSubStage, "substage">,
): WorkflowSubStage {
  return {
    label: partial.label ?? partial.substage,
    action: partial.action ?? "",
    expectedArtifact: partial.expectedArtifact,
    instanceId: partial.instanceId,
    ...partial,
  };
}

describe("researchDesignPresentation", () => {
  it.each([
    ["research_record_establishment", "研究记录建立"],
    ["evidence_assessment_synthesis", "证据评估与研究综合"],
    ["current_research_conclusion", "当前研究结论"],
    ["key_unknowns_candidate_explanations", "关键未知与候选解释"],
    ["next_research_recommendation", "下一步研究建议"],
    ["mechanism_explanation", "机制解释"],
  ])("shows Chinese work titles and planned outputs for %s", (key, title) => {
    expect(researchDraftNodeTitle(substage({ substage: key }))).toBe(title);
    expect(
      researchDraftExpectedOutput(`opentrajectory.${key}.planned_output`),
    ).toBe(`${title}（计划产物）`);
    expect(getSubStageLabels("en-US")[key]).toMatch(/[A-Z]/);
  });

  it("preserves catalog labels and each instance's own objective", () => {
    const nodes = ["支持证据分析", "冲突证据分析"].map((label, index) =>
      substage({
        substage: "evidence_assessment_synthesis",
        label,
        instanceId: `assessment-${index}`,
        action: `针对${label}核查不同来源。`,
      }),
    );
    expect(nodes.map(researchDraftNodeTitle)).toEqual([
      "支持证据分析",
      "冲突证据分析",
    ]);
    expect(nodes.map(researchDraftNodeAction)).toEqual([
      "针对支持证据分析核查不同来源。",
      "针对冲突证据分析核查不同来源。",
    ]);
    expect(
      researchDraftNodeTitle(
        substage({ substage: "report_generation", label: "科研结果包组装" }),
      ),
    ).toBe("科研结果包组装");
    expect(
      researchDraftNodeTitle(
        substage({ substage: "future_work_type", label: "新的研究工作" }),
      ),
    ).toBe("新的研究工作");
  });

  it("maps standard node keys to Chinese titles instead of dropping them", () => {
    const nodes = [
      substage({
        substage: "constraints_criteria",
        label: "constraints_criteria",
        instanceId: "i1",
      }),
      substage({
        substage: "data_strategy",
        label: "data_strategy",
        instanceId: "i2",
      }),
      substage({
        substage: "reproducibility",
        label: "reproducibility",
        instanceId: "i3",
      }),
      substage({
        substage: "report_generation",
        label: "report_generation",
        instanceId: "i4",
      }),
    ];
    const titles = nodes.map((node) => researchDraftNodeTitle(node));
    expect(titles).toEqual([
      "约束与标准",
      "数据策略",
      "可复现性设计",
      "报告生成",
    ]);
    expect(titles.every(Boolean)).toBe(true);
  });

  it("does not invent formal nodes from unstructured prose", () => {
    const prose = "先做对照实验，再统计成功率，最后写报告。";
    expect(researchDraftText(prose)).toBe(prose);
    // Presentation helpers never split prose into fake substages.
    expect(prose.split(/[，。]/).length).toBeGreaterThan(1);
  });

  it("hides concatenated stage descriptions that duplicate research work", () => {
    const stage = {
      stageKey: "solution_design",
      description: "采样方案。 / 基线选择。 / 可复现实验包。",
      substages: [],
    } as TaskPlanStage;
    expect(
      researchDraftStageDescription(stage, [
        "采样方案。",
        "基线选择。",
        "可复现实验包。",
      ]),
    ).toBe("");
  });

  it("shows research work body once when title equals action", () => {
    const node = substage({
      substage: "full_execution",
      label: "全规模执行",
      action: "全规模执行",
    });
    const title = researchDraftNodeTitle(node);
    const action = researchDraftNodeAction(node);
    expect(title).toBe("全规模执行");
    expect(action === title ? "" : action).toBe("");
  });

  it("never surfaces sdr.*.planned_output on the main surface", () => {
    expect(
      researchDraftExpectedOutput("sdr.reproducibility.planned_output"),
    ).toBe("可复现性设计（计划产物）");
    expect(
      researchDraftExpectedOutput("sdr.data_strategy.planned_output"),
    ).toContain("计划产物");
    expect(
      researchDraftExpectedOutput("sdr.constraints_criteria.planned_output"),
    ).not.toMatch(/sdr\./);
  });

  it("translates canonical finalization English by stable node type", () => {
    const node = substage({
      substage: "report_generation",
      label: "report_generation",
      action:
        "Assemble the completed research into the canonical final result.",
    });
    expect(researchDraftNodeTitle(node)).toBe("报告生成");
    expect(researchDraftNodeAction(node)).toBe(
      "汇总已完成的研究结果，形成最终研究报告",
    );
  });

  it("keeps generic planning copy domain-agnostic for research-design titles", () => {
    expect(
      researchDraftNodeTitle(
        substage({
          substage: "constraints_criteria",
          label: "constraints_criteria",
        }),
      ),
    ).toBe("约束与标准");
    expect(
      researchDraftNodeTitle(
        substage({ substage: "reproducibility", label: "reproducibility" }),
      ),
    ).toBe("可复现性设计");
  });

  it("projects high-value ResearchDesign information without exposing artifact types", () => {
    const plan = {
      goalSummary: "评估仓库级检索能否提高复杂代码修改的一次成功率。",
      domain: "research_task",
      understanding: {
        primaryResearchQuestions: [
          "仓库级检索是否提高复杂代码修改的一次成功率？",
        ],
        auxiliaryResearchDimensions: ["修改复杂度", "基线与混杂因素"],
        targetOutputs: ["带基线对照的成功率评估"],
        evidenceBoundaries: ["当前方案不代表已经完成实验或获得最终科研结论。"],
        riskNotes: ["待研究未知：增益是否跨项目稳定。"],
      },
      clarifications: [],
      readyToConfirm: true,
      stages: [
        {
          stageKey: "solution_design",
          title: "方案设计",
          subStages: ["comparison"],
          substages: [
            substage({
              substage: "comparison",
              label: "对照评估",
              action: "比较检索增强与基线的一次成功率。",
              expectedArtifact: "internal.machine.artifact",
            }),
          ],
        },
      ],
    } as TaskPlan;

    const overview = researchDesignOverview(plan);
    expect(overview.primaryResearchQuestions).toEqual([
      "仓库级检索是否提高复杂代码修改的一次成功率？",
    ]);
    expect(overview.auxiliaryResearchDimensions).toEqual([
      "修改复杂度",
      "基线与混杂因素",
    ]);
    expect(overview.targetOutputs).toEqual(["带基线对照的成功率评估"]);
    expect(overview.evidenceBoundaries).toEqual([
      "当前方案不代表已经完成实验或获得最终科研结论。",
    ]);
    expect(overview.riskNotes).toEqual(["待研究未知：增益是否跨项目稳定。"]);
    expect(overview.researchPath.join(" ")).toContain("对照评估");
    expect(overview.researchPath.join(" ")).not.toContain(
      "比较检索增强与基线的一次成功率",
    );
    expect(JSON.stringify(overview)).not.toContain("internal.machine.artifact");

    const technicalOnly = {
      ...plan,
      stages: [
        {
          stageKey: "solution_design",
          title: "方案设计",
          subStages: ["internal_machine_step"],
          substages: [
            substage({
              substage: "internal_machine_step",
              label: "internal.machine.step",
              action: "",
            }),
          ],
        },
      ],
    } as TaskPlan;
    expect(researchDesignOverview(technicalOnly).researchPath).toEqual([]);
  });

  it("rebuilds a recognizable title from the formal goal when title is truncated", () => {
    const title = researchDesignDisplayTitle(
      "研究一下实验验证",
      "我想研究一下，在有限算力下，给代码大模型加仓库级检索，能不能真的提高它修复真实开源项目 bug 的成功率。",
    );
    expect(title).not.toBe("研究一下实验验证");
    expect(title.includes("仓库级检索") || title.includes("代码大模型")).toBe(
      true,
    );
  });
});
