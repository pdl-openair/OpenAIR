import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import {
  hasScientificResultContent,
  ScientificResultDisplayValue,
} from "../../scientificResultDisplayValue";

afterEach(cleanup);

describe("scientific result user content", () => {
  it("keeps scientific content and source locators while excluding nested diagnostics", () => {
    const value = {
      answer: "当前证据不足以确定因果关系",
      scope: { responsibility: "仅适用于本轮样本" },
      uncertainty: [{ summary: "尚未完成对照实验" }],
      evidence: { source_id: "文献 A", locator: "第 12 页" },
      custom_scientific_detail: "保留新增科学信息",
      next_action: { text: "开展对照实验", workUnitId: "INTERNAL_UNIT" },
      provenance: { sourceArtifactId: "INTERNAL_ARTIFACT", locator: "附录 B" },
    };
    const original = JSON.stringify(value);
    const { container } = render(
      <ScientificResultDisplayValue value={value} />,
    );
    for (const text of [
      "当前证据不足以确定因果关系",
      "仅适用于本轮样本",
      "尚未完成对照实验",
      "文献 A",
      "第 12 页",
      "保留新增科学信息",
      "开展对照实验",
      "附录 B",
    ])
      expect(screen.getByText(text)).toBeVisible();
    expect(screen.getByText("回答")).toBeVisible();
    expect(screen.getAllByText("来源位置")).toHaveLength(2);
    for (const text of [
      "responsibility",
      "INTERNAL_UNIT",
      "INTERNAL_ARTIFACT",
    ])
      expect(container).not.toHaveTextContent(text);
    expect(JSON.stringify(value)).toBe(original);
  });

  it("labels research decisions precisely and retains unmapped English field names", () => {
    const value = {
      uncertainty: { priority: "high", non_claims: ["尚未确认偏差"], confounding_risks: ["存在混杂"], reporting_rule: "区分观察与推断" },
      next_action: { recommended_direction: "比较缓解措施", immediate_action: "先做小规模试验", recommended_design: "固定基准", decision_rule: "满足条件后继续", pre_registration_contents: ["登记主要指标"] },
      assumptions: { causal_assumptions: "固定其他变量", analysis_assumption: "按任务分层" },
      scope: { current_status: "strategy_only", applicability_boundary: "仅限当前样本" },
      newly_added_scientific_field: "新增内容完整保留",
    };
    const original = JSON.stringify(value);
    const { container } = render(<ScientificResultDisplayValue value={value} />);
    for (const title of ["优先级", "混杂风险", "结果报告要求", "建议的研究方向", "当前行动", "建议的研究设计", "行动决策条件", "预先登记的内容", "因果推断前提", "分析前提", "当前状态", "适用边界"]) {
      expect(screen.getByText(title)).toBeVisible();
    }
    expect(screen.getByText("高")).toBeVisible();
    expect(screen.getByText("newly_added_scientific_field")).toBeVisible();
    expect(screen.getByText("新增内容完整保留")).toBeVisible();
    expect(container).not.toHaveTextContent("补充研究信息");
    expect(JSON.stringify(value)).toBe(original);
  });

  it("does not lose different original fields that share one Chinese label", () => {
    render(<ScientificResultDisplayValue value={{ implications: "研究意义一", implication: "研究意义二" }} />);
    expect(screen.getAllByText("研究意义")).toHaveLength(2);
    expect(screen.getByText("研究意义一")).toBeVisible();
    expect(screen.getByText("研究意义二")).toBeVisible();
  });

  it("does not create a scientific section for diagnostics alone", () => {
    expect(
      hasScientificResultContent({ provenance: { workUnitId: "internal" } }),
    ).toBe(false);
    expect(
      hasScientificResultContent({ measurement: 0, confirmed: false }),
    ).toBe(true);
  });
});
