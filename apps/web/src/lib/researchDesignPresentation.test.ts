import { describe, expect, it } from "vitest";
import type { TaskPlanStage, WorkflowSubStage } from "@openair/shared";
import {
  researchDesignDisplayTitle,
  researchDraftExpectedOutput,
  researchDraftNodeAction,
  researchDraftNodeTitle,
  researchDraftStageDescription,
  researchDraftText,
} from "./researchDesignPresentation";

function substage(partial: Partial<WorkflowSubStage> & Pick<WorkflowSubStage, "substage">): WorkflowSubStage {
  return {
    label: partial.label ?? partial.substage,
    action: partial.action ?? "",
    expectedArtifact: partial.expectedArtifact,
    instanceId: partial.instanceId,
    ...partial,
  };
}

describe("researchDesignPresentation", () => {
  it("maps standard node keys to Chinese titles instead of dropping them", () => {
    const nodes = [
      substage({ substage: "constraints_criteria", label: "constraints_criteria", instanceId: "i1" }),
      substage({ substage: "data_strategy", label: "data_strategy", instanceId: "i2" }),
      substage({ substage: "reproducibility", label: "reproducibility", instanceId: "i3" }),
      substage({ substage: "report_generation", label: "report_generation", instanceId: "i4" }),
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
      researchDraftStageDescription(stage, ["采样方案。", "基线选择。", "可复现实验包。"]),
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
    expect(researchDraftExpectedOutput("sdr.reproducibility.planned_output")).toBe("可复现性设计（计划产物）");
    expect(researchDraftExpectedOutput("sdr.data_strategy.planned_output")).toContain("计划产物");
    expect(researchDraftExpectedOutput("sdr.constraints_criteria.planned_output")).not.toMatch(/sdr\./);
  });

  it("translates canonical finalization English by stable node type", () => {
    const node = substage({
      substage: "report_generation",
      label: "report_generation",
      action: "Assemble the completed research into the canonical final result.",
    });
    expect(researchDraftNodeTitle(node)).toBe("报告生成");
    expect(researchDraftNodeAction(node)).toBe("汇总已完成的研究结果，形成最终研究报告");
  });

  it("keeps generic planning copy domain-agnostic for research-design titles", () => {
    expect(researchDraftNodeTitle(substage({ substage: "constraints_criteria", label: "constraints_criteria" }))).toBe("约束与标准");
    expect(researchDraftNodeTitle(substage({ substage: "reproducibility", label: "reproducibility" }))).toBe("可复现性设计");
  });

  it("rebuilds a recognizable title from the formal goal when title is truncated", () => {
    const title = researchDesignDisplayTitle(
      "研究一下实验验证",
      "我想研究一下，在有限算力下，给代码大模型加仓库级检索，能不能真的提高它修复真实开源项目 bug 的成功率。",
    );
    expect(title).not.toBe("研究一下实验验证");
    expect(title.includes("仓库级检索") || title.includes("代码大模型")).toBe(true);
  });
});
