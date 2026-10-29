import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WorkbenchFlowNav } from "./WorkbenchFlowNav";

const semanticTitle = "将用户描述转化为可研究、可审计的过程对齐问题，并明确边界与验收条件";
const secondaryObjective = "这段 objective 仅用于确认左侧导航不再重复渲染副文案。";

function renderFlowNav() {
  return render(
    <WorkbenchFlowNav
      language="zh-CN"
      phaseAgg={{ problem: "running", method: "not_started", experiment: "not_started", report: "not_started" } as never}
      progressSteps={[{ id: "problem", label: "问题建模", status: "running" }] as never}
      stagesByPhase={{
        problem: {
          subStages: [{
            id: "substage-1",
            label: semanticTitle,
            objective: secondaryObjective,
            status: "running",
            projectionStatus: "running",
          }],
        },
      } as never}
      viewingPhase="problem"
      currentPagePhase="problem"
      currentSubStageIds={["substage-1"]}
      selectedSubStage="substage-1"
      onSelectPhase={vi.fn()}
      onSelectSubStage={vi.fn()}
    />,
  );
}

describe("WorkbenchFlowNav substage display", () => {
  it("keeps the semantic title, number, status, and active state while omitting the secondary objective", () => {
    renderFlowNav();

    const title = screen.getByTitle(semanticTitle);
    expect(title).toHaveTextContent(semanticTitle);
    expect(title).toHaveClass("line-clamp-2");
    expect(screen.queryByText(secondaryObjective)).not.toBeInTheDocument();
    expect(screen.getByText("1.1")).toBeInTheDocument();
    expect(screen.getByTestId("workbench-left-substage-status-substage-1")).toHaveTextContent("执行中");
    expect(title.closest("button")).toHaveAttribute("aria-current", "step");
  });
});
