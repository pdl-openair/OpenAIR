import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { WorkbenchArtifact } from "../../../../model/workbenchTask";
import { ArtifactRenderer } from "../../ArtifactRenderer";
import { actualMetricRecordsFixture } from "./csResultComparisonPresentationFixtures";

function artifact(
  type: string,
  document: Record<string, unknown>,
): WorkbenchArtifact {
  return {
    id: "stable-result",
    type,
    artifactType: type,
    title: "研究结果",
    format: "json",
    relatedStage: "execution_validation" as never,
    content: JSON.stringify(document),
  };
}
// Exact offline document from three-line-ui-probe.json; not a real research run.
const probe = {
  scope: { text: "SCOPE_OBJECT_PROBE" },
  reasoning_summary: { text: "SCIENTIFIC_ANSWER_OBJECT_PROBE" },
  assumptions: [],
  uncertainty: { text: "UNCERTAINTY_OBJECT_PROBE" },
  next_action: { text: "NEXT_ACTION_OBJECT_PROBE" },
};
function show(type: string, document: Record<string, unknown>) {
  return render(
    <ArtifactRenderer artifact={artifact(type, document)} bodyOnly />,
  );
}

describe("workflow result content fidelity through production parent chain", () => {
  it.each([
    "current_research_conclusion",
    "key_unknowns_and_candidate_explanations",
    "next_research_recommendation",
  ])("preserves all formal fields for %s", (type) => {
    show(type, probe);
    for (const value of [
      "SCOPE_OBJECT_PROBE",
      "SCIENTIFIC_ANSWER_OBJECT_PROBE",
      "UNCERTAINTY_OBJECT_PROBE",
      "NEXT_ACTION_OBJECT_PROBE",
    ])
      expect(screen.getByText(value)).toBeVisible();
  });

  it("keeps labels distinct through the production workflow renderer without rewriting original keys", () => {
    const document = {
      reasoning_summary: { current_conclusion: "当前结论", confounding_risks: ["混杂风险内容"] },
      uncertainty: { implications: "研究意义一", implication: "研究意义二", unfamiliar_field: "未映射内容" },
      next_action: { priority_study: "优先研究", immediate_action: "先做试验", recommended_design: "固定基准" },
      scope: { evidence_boundary: "仅限已有证据" },
      assumptions: { causal_assumptions: "固定其他变量" },
    };
    const original = JSON.stringify(document);
    const { container } = show("current_research_conclusion", document);
    for (const title of ["混杂风险", "当前行动", "建议的研究设计", "证据边界", "因果推断前提", "unfamiliar_field"]) {
      expect(screen.getByText(title)).toBeVisible();
    }
    expect(screen.getByText("研究意义一")).toBeVisible();
    expect(screen.getByText("研究意义二")).toBeVisible();
    expect(container).not.toHaveTextContent("补充研究信息");
    expect(container).not.toHaveTextContent("补充研究判断");
    expect(JSON.stringify(document)).toBe(original);
  });

  it("retains strings, object arrays, nested evidence and meaningful false/zero without mutating source", () => {
    const document = {
      scope: "原始范围",
      reasoning_summary: [
        "字符串答案",
        {
          text: "对象答案",
          evidence: { source_id: "Evidence:source-7", locator: "page 12" },
          limitations: ["仅覆盖条件 A"],
        },
      ],
      assumptions: [
        { text: "前提", nested: { measurement: 0, confirmed: false } },
      ],
      uncertainty: ["未知一", "未知二", null, "", {}],
      next_action: { deeper: ["采集下一轮样本", { note: "嵌套说明" }] },
    };
    const before = JSON.stringify(document);
    const { container } = show("current_research_conclusion", document);
    for (const value of [
      "原始范围",
      "字符串答案",
      "对象答案",
      "Evidence:source-7",
      "page 12",
      "仅覆盖条件 A",
      "前提",
      "0",
      "否",
      "未知一",
      "未知二",
      "采集下一轮样本",
      "嵌套说明",
    ])
      expect(screen.getByText(value)).toBeVisible();
    expect(container).not.toHaveTextContent("[object Object]");
    expect(JSON.stringify(document)).toBe(before);
  });

  it("renders scalar formal fields and omits empty fields", () => {
    show("current_research_conclusion", {
      scope: null,
      reasoning_summary: 0,
      assumptions: false,
      uncertainty: {},
      next_action: [],
    });
    expect(screen.getByText("0")).toBeVisible();
    expect(screen.getByText("否")).toBeVisible();
    expect(screen.queryByText("适用范围")).not.toBeInTheDocument();
    expect(screen.queryByText("限制与不确定性")).not.toBeInTheDocument();
    expect(screen.queryByText("下一步")).not.toBeInTheDocument();
  });

  it("keeps CS status and evidence members alongside its answer", () => {
    show("reproduction_conclusion", {
      answer: {
        text: "有边界的复现答案",
        evidence: { reference_id: "evidence:cs-9" },
      },
      scope: { text: "限定数据集", limitation: "尚未覆盖其他语言" },
      limitations: [{ text: "样本限制", detail: "仅一个随机种子" }],
      unknowns: ["泛化仍未知"],
      next_steps: { text: "增加随机种子", condition: "固定同一配置" },
      execution_status: "succeeded",
      evidence_sufficiency: "partial",
      verdict: "indeterminate",
    });
    for (const value of [
      "有边界的复现答案",
      "evidence:cs-9",
      "限定数据集",
      "尚未覆盖其他语言",
      "样本限制",
      "仅一个随机种子",
      "泛化仍未知",
      "增加随机种子",
      "固定同一配置",
    ])
      expect(screen.getByText(value)).toBeVisible();
    expect(screen.getByTestId("workflow-node-cs-conclusion")).toHaveTextContent(
      "当前无法判定",
    );
  });

  it("retains dedicated CS table and exposes additional source members only on request", () => {
    show("actual_metric_records", {
      ...actualMetricRecordsFixture,
      extra_limit: { text: "新增原始限制" },
    });
    expect(
      within(screen.getByTestId("cs-actual-metric-records")).getByText("81.58"),
    ).toBeVisible();
    expect(screen.queryByText("新增原始限制")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看完整结构化内容" }));
    expect(screen.getByText("新增原始限制")).toBeVisible();
  });

  it("preserves source expansion, selected unchanged text and scroll on updates; suspicious HTML stays text", () => {
    const html = '<img src=x onerror="alert(1)"><script>alert(2)</script>';
    const first = artifact("current_research_conclusion", {
      ...probe,
      extra: { text: "稳定选中文本" },
      next_action: html,
    });
    const { container, rerender } = render(
      <div data-testid="reader">
        <ArtifactRenderer artifact={first} bodyOnly />
      </div>,
    );
    fireEvent.click(screen.getByRole("button", { name: "查看完整结构化内容" }));
    const selected = screen.getByText("稳定选中文本");
    const range = document.createRange();
    range.selectNodeContents(selected);
    window.getSelection()?.removeAllRanges();
    window.getSelection()?.addRange(range);
    const reader = screen.getByTestId("reader");
    reader.scrollTop = 91;
    rerender(
      <div data-testid="reader">
        <ArtifactRenderer
          artifact={{
            ...first,
            content: JSON.stringify({
              ...probe,
              extra: { text: "稳定选中文本" },
              next_action: html,
              new_member: "更新信息",
            }),
          }}
          bodyOnly
        />
      </div>,
    );
    expect(
      screen.getByRole("button", { name: "收起完整结构化内容" }),
    ).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("稳定选中文本")).toBe(selected);
    expect(window.getSelection()?.toString()).toBe("稳定选中文本");
    expect(reader.scrollTop).toBe(91);
    expect(screen.getByText("更新信息")).toBeVisible();
    expect(screen.getAllByText(html).length).toBeGreaterThan(0);
    expect(container.querySelector("img, script")).toBeNull();
    rerender(
      <div data-testid="reader">
        <ArtifactRenderer
          artifact={artifact("current_research_conclusion", {
            ...probe,
            extra: "切换后的内容",
          })}
          bodyOnly
        />
      </div>,
    );
    expect(screen.getByText("切换后的内容")).toBeVisible();
    expect(screen.queryByText("更新信息")).not.toBeInTheDocument();
    window.getSelection()?.removeAllRanges();
  });
});
