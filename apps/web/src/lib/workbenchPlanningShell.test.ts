import { describe, expect, it } from "vitest";
import { planningOccupiesWorkbenchShell } from "./workbenchPlanningShell";
import { formingWorkbenchTaskModel } from "../model/workbenchTask";
import { STAGE_LABELS } from "@openair/shared";

describe("planningOccupiesWorkbenchShell", () => {
  const base = {
    planningSessionId: "session-1",
    taskId: "task-1",
    sessionAccessError: null as string | null,
    loadStatus: "loading" as const,
  };

  it("keeps the workbench shell while the reserved task is still missing", () => {
    expect(
      planningOccupiesWorkbenchShell({
        ...base,
        loadStatus: "error",
        loadErrorKind: "notFound",
      }),
    ).toBe(true);
  });

  it("does not occupy the shell after the durable task has left planning", () => {
    expect(
      planningOccupiesWorkbenchShell({
        ...base,
        loadStatus: "ready",
        taskStatus: "running",
        creationStatus: "confirmed",
      }),
    ).toBe(false);
  });
});

describe("formingWorkbenchTaskModel", () => {
  it("projects the four canonical stages as not started", () => {
    const model = formingWorkbenchTaskModel({
      taskId: "task-1",
      title: "城市内涝预警",
    });
    expect(model.taskStatus).toBe("draft");
    expect(model.stages.map((stage) => stage.stage)).toEqual([
      "problem_modeling",
      "solution_design",
      "execution_validation",
      "insight_generation",
    ]);
    expect(model.stages.every((stage) => stage.status === "not_started")).toBe(true);
    expect(model.stages[0]?.label).toBe(STAGE_LABELS.problem_modeling);
  });
});
