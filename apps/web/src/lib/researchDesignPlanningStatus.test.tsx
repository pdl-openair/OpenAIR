import { describe, expect, it } from "vitest";
import { formatElapsedSeconds, projectResearchStartWaitingView } from "./researchStartWaiting";
import type { ResearchStartSessionStatus } from "../api/client";

describe("research planning status timing", () => {
  it("keeps planning progress as non-terminal while running", () => {
    const snapshot = {
      status: "running",
      currentPhase: "planning",
      userMessage: "正在组织研究方案。",
      planningProgress: {
        code: "planning_design",
        message: "正在组织研究方案。",
        elapsedSeconds: 27,
        startedAt: "2026-08-24T08:07:44Z",
        updatedAt: "2026-08-24T08:08:11Z",
      },
    } as ResearchStartSessionStatus;
    const view = projectResearchStartWaitingView(snapshot);
    expect(view.terminal).toBe(false);
    expect(view.level).toBe("progress");
    expect(view.message).toContain("正在组织研究方案");
  });

  it("freezes elapsed seconds after design_ready and refresh uses the same authority", () => {
    const snapshot = {
      status: "completed",
      currentPhase: "planning",
      result: { taskId: "task_7daf18df7229486db93b8061a650fdad" },
      planningProgress: {
        code: "design_ready",
        message: "研究方案已形成，可以查看并确认。",
        elapsedSeconds: 55,
        startedAt: "2026-08-24T08:07:44Z",
        updatedAt: "2026-08-24T08:08:38Z",
      },
    } as ResearchStartSessionStatus;
    const first = projectResearchStartWaitingView(snapshot);
    const second = projectResearchStartWaitingView(snapshot);
    expect(first.elapsedSeconds).toBe(55);
    expect(second.elapsedSeconds).toBe(55);
    expect(formatElapsedSeconds(first.elapsedSeconds)).toBe("已等待 55 秒");
  });

  it("keeps planning failure inside the workbench without forcing a return home", () => {
    const view = projectResearchStartWaitingView(
      { sessionId: "s1", status: "failed" } as ResearchStartSessionStatus,
      false,
      { inWorkbench: true },
    );
    expect(view.shouldReturnHome).toBe(false);
    expect(view.nextActionHint).toContain("重试");
    expect(view.title).toContain("没有形成");
  });

  it("uses cross-domain organizing copy without materials-boundary wording", () => {
    const snapshot = {
      status: "running",
      currentPhase: "organizing",
      planningProgress: {
        code: "organizing_intent",
        message: null,
        elapsedSeconds: 12,
        startedAt: "2026-08-24T08:07:44Z",
        updatedAt: "2026-08-24T08:07:56Z",
      },
    } as ResearchStartSessionStatus;
    const view = projectResearchStartWaitingView(snapshot);
    expect(view.message).toContain("研究范围");
    expect(view.message).not.toContain("材料边界");
  });
});
