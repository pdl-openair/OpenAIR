import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WorkbenchFlowNav } from "../components/workbench/WorkbenchFlowNav";
import { mapStageStatusToNav, aggregatePhaseStatus, type PhaseNavStatus } from "./pagePhases";
import { progressStatusLabel, flowProjectionStatusLabel } from "./workbenchProgress";
import { navStatusLabel } from "./navStatus";
import { userVisibleStatusLabel, mapScreenStatusToNav } from "../model/workbenchScreenCore.mjs";

const EXACT_AWAITING_CONFIRMATION_LABEL = "待确认";

function renderLeftNav(opts: {
  navStatus: PhaseNavStatus;
  projectionStatus: string;
  subId: string;
}) {
  return render(
    <WorkbenchFlowNav
      language="zh-CN"
      phaseAgg={{ problem: opts.navStatus, method: "not_started", experiment: "not_started", report: "not_started" } as never}
      progressSteps={[
        {
          id: "problem",
          label: "问题建模",
          status: opts.navStatus,
          projectionStatus: opts.projectionStatus,
        },
      ] as never}
      stagesByPhase={{
        problem: {
          subStages: [{
            id: opts.subId,
            label: "研究问题凝练",
            status: opts.navStatus,
            projectionStatus: opts.projectionStatus,
          }],
        },
      } as never}
      viewingPhase="problem"
      currentPagePhase="problem"
      currentSubStageIds={[opts.subId]}
      selectedSubStage={opts.subId}
      onSelectPhase={vi.fn()}
      onSelectSubStage={vi.fn()}
    />,
  );
}

describe("blocked_recoverable status semantics (P0 STEP 2)", () => {
  it("does not map blocked_recoverable to pending / 待确认", () => {
    expect(mapStageStatusToNav("blocked_recoverable")).toBe("blocked");
    expect(mapScreenStatusToNav("blocked_recoverable")).toBe("blocked");
    expect(mapStageStatusToNav("blocked_recoverable")).not.toBe("pending");
    expect(progressStatusLabel("blocked")).toBe("执行受阻");
    expect(progressStatusLabel("blocked")).not.toBe(EXACT_AWAITING_CONFIRMATION_LABEL);
    expect(userVisibleStatusLabel("blocked_recoverable")).toBe("执行受阻");
    expect(userVisibleStatusLabel("blocked_recoverable")).not.toBe(EXACT_AWAITING_CONFIRMATION_LABEL);
    expect(navStatusLabel("blocked")).toBe("执行受阻");
  });

  it("keeps generic pending as 待处理, not 待确认", () => {
    expect(progressStatusLabel("pending")).toBe("待处理");
    expect(progressStatusLabel("pending")).not.toBe(EXACT_AWAITING_CONFIRMATION_LABEL);
    expect(navStatusLabel("pending")).toBe("待处理");
    expect(navStatusLabel("pending")).not.toBe(EXACT_AWAITING_CONFIRMATION_LABEL);
  });

  it("keeps genuine awaiting_confirmation as 待确认", () => {
    expect(userVisibleStatusLabel("awaiting_confirmation")).toBe(EXACT_AWAITING_CONFIRMATION_LABEL);
    expect(flowProjectionStatusLabel("awaiting_confirmation", "pending")).toBe(EXACT_AWAITING_CONFIRMATION_LABEL);
  });

  it("keeps needs_input / running / completed / failed independent", () => {
    expect(mapStageStatusToNav("needs_input")).toBe("pending");
    expect(userVisibleStatusLabel("needs_input")).toBe("待用户补充");
    expect(mapStageStatusToNav("running")).toBe("running");
    expect(progressStatusLabel("running")).toBe("进行中");
    expect(userVisibleStatusLabel("running")).toBe("执行中");
    expect(mapStageStatusToNav("completed")).toBe("completed");
    expect(progressStatusLabel("completed")).toBe("已完成");
    expect(mapStageStatusToNav("failed")).toBe("failed");
    expect(progressStatusLabel("failed")).toBe("执行失败");
  });

  it("aggregates blocked without collapsing it into pending", () => {
    expect(aggregatePhaseStatus(["not_started", "blocked"])).toBe("blocked");
    expect(aggregatePhaseStatus(["pending", "blocked"])).toBe("blocked");
    expect(aggregatePhaseStatus(["running", "blocked"])).toBe("running");
    expect(aggregatePhaseStatus(["failed", "blocked"])).toBe("failed");
  });

  it("renders consistent top/left/substage labels for blocked_recoverable", () => {
    const topLabel = userVisibleStatusLabel("blocked_recoverable");
    const stageNav = mapScreenStatusToNav("blocked_recoverable");
    const leftLabel = progressStatusLabel(stageNav);

    renderLeftNav({ navStatus: "blocked", projectionStatus: "blocked_recoverable", subId: "sub-blocked" });

    expect(topLabel).toBe("执行受阻");
    expect(leftLabel).toBe("执行受阻");
    expect(screen.getByTestId("workbench-left-stage-status-problem")).toHaveTextContent("执行受阻");
    expect(screen.getByTestId("workbench-left-substage-status-sub-blocked")).toHaveTextContent("执行受阻");
    expect(screen.getByTestId("workbench-left-stage-status-problem").textContent).not.toBe(EXACT_AWAITING_CONFIRMATION_LABEL);
  });

  it("still renders genuine awaiting_confirmation as 待确认 in left nav", () => {
    renderLeftNav({ navStatus: "pending", projectionStatus: "awaiting_confirmation", subId: "sub-confirm" });

    expect(screen.getByTestId("workbench-left-stage-status-problem").textContent).toBe(EXACT_AWAITING_CONFIRMATION_LABEL);
    expect(screen.getByTestId("workbench-left-substage-status-sub-confirm").textContent).toBe(EXACT_AWAITING_CONFIRMATION_LABEL);
    expect(screen.queryByText("执行受阻")).not.toBeInTheDocument();
  });

  it("keeps needs_input as 待用户补充 in left nav", () => {
    renderLeftNav({ navStatus: "pending", projectionStatus: "needs_input", subId: "sub-input" });

    expect(screen.getByTestId("workbench-left-stage-status-problem")).toHaveTextContent("待用户补充");
    expect(screen.getByTestId("workbench-left-substage-status-sub-input")).toHaveTextContent("待用户补充");
    expect(screen.getByTestId("workbench-left-stage-status-problem").textContent).not.toBe(EXACT_AWAITING_CONFIRMATION_LABEL);
    expect(screen.queryByText("执行受阻")).not.toBeInTheDocument();
  });

  it("never shows exact 待确认 for non-awaiting_confirmation statuses", () => {
    const cases: Array<{ projectionStatus: string; expected: string; navStatus: PhaseNavStatus }> = [
      { projectionStatus: "needs_input", expected: "待用户补充", navStatus: "pending" },
      { projectionStatus: "blocked_recoverable", expected: "执行受阻", navStatus: "blocked" },
      { projectionStatus: "blocked", expected: "执行受阻", navStatus: "blocked" },
      { projectionStatus: "insufficient_evidence", expected: "证据不足", navStatus: "pending" },
      { projectionStatus: "insufficient_materials", expected: "材料不足", navStatus: "pending" },
      { projectionStatus: "external_result_unknown", expected: "结果待核查", navStatus: "pending" },
    ];

    expect(progressStatusLabel("pending")).not.toBe(EXACT_AWAITING_CONFIRMATION_LABEL);
    expect(navStatusLabel("pending")).not.toBe(EXACT_AWAITING_CONFIRMATION_LABEL);

    for (const row of cases) {
      expect(flowProjectionStatusLabel(row.projectionStatus, row.navStatus)).toBe(row.expected);
      expect(flowProjectionStatusLabel(row.projectionStatus, row.navStatus)).not.toBe(EXACT_AWAITING_CONFIRMATION_LABEL);
      expect(userVisibleStatusLabel(row.projectionStatus)).not.toBe(EXACT_AWAITING_CONFIRMATION_LABEL);
    }

    for (const row of cases) {
      const subId = `sub-${row.projectionStatus}`;
      const view = renderLeftNav({
        navStatus: row.navStatus,
        projectionStatus: row.projectionStatus,
        subId,
      });
      expect(screen.getByTestId("workbench-left-stage-status-problem").textContent).toBe(row.expected);
      expect(screen.getByTestId("workbench-left-stage-status-problem").textContent).not.toBe(EXACT_AWAITING_CONFIRMATION_LABEL);
      expect(screen.getByTestId(`workbench-left-substage-status-${subId}`).textContent).toBe(row.expected);
      expect(screen.getByTestId(`workbench-left-substage-status-${subId}`).textContent).not.toBe(EXACT_AWAITING_CONFIRMATION_LABEL);
      view.unmount();
    }
  });

  it("external_result_unknown copy must not contain user-gate 待确认/等待确认", () => {
    const userGate = /待确认|等待确认/;
    const labels = [
      flowProjectionStatusLabel("external_result_unknown", "pending"),
      userVisibleStatusLabel("external_result_unknown"),
    ];
    for (const label of labels) {
      expect(label).toBe("结果待核查");
      expect(label).not.toMatch(userGate);
    }

    renderLeftNav({
      navStatus: "pending",
      projectionStatus: "external_result_unknown",
      subId: "sub-unknown",
    });
    const stage = screen.getByTestId("workbench-left-stage-status-problem").textContent ?? "";
    const sub = screen.getByTestId("workbench-left-substage-status-sub-unknown").textContent ?? "";
    expect(stage).toBe("结果待核查");
    expect(sub).toBe("结果待核查");
    expect(stage).not.toMatch(userGate);
    expect(sub).not.toMatch(userGate);
  });
});
