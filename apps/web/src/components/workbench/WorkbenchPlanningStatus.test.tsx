import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ResearchStartSessionStatus } from "../../api/client";
import { projectResearchStartWaitingView } from "../../lib/researchStartWaiting";
import { WorkbenchPlanningStatus } from "./WorkbenchPlanningStatus";

function renderStatus(snapshot: ResearchStartSessionStatus) {
  return render(
    <WorkbenchPlanningStatus
      planningStatus={snapshot}
      planningWaitingView={projectResearchStartWaitingView(snapshot, false, { inWorkbench: true })}
      planningElapsedLabel={null}
    />,
  );
}

describe("WorkbenchPlanningStatus token cost notice", () => {
  it("renders the projected cost notice with real token usage", () => {
    renderStatus({
      sessionId: "s1",
      status: "running",
      currentPhase: "planning",
      tokenUsage: { totalTokens: 1310 },
    } as ResearchStartSessionStatus);

    const notice = screen.getByTestId("workbench-planning-cost-notice");
    expect(notice).toBeInTheDocument();
    expect(notice).toHaveTextContent("正在调用规划模型");
    expect(notice).toHaveTextContent("1,310 tokens");
  });

  it("omits the cost notice when the projection has none", () => {
    render(
      <WorkbenchPlanningStatus
        planningStatus={null}
        planningWaitingView={{
          title: "任务准备状态",
          message: "正在确认当前研究状态。",
          terminal: false,
          unknown: false,
          shouldOpenWorkbench: false,
          shouldReturnHome: false,
          level: "progress",
          elapsedSeconds: null,
          progressCode: null,
          nextActionHint: null,
          costNotice: null,
        }}
        planningElapsedLabel={null}
      />,
    );

    expect(screen.queryByTestId("workbench-planning-cost-notice")).not.toBeInTheDocument();
  });
});
