import { describe, expect, it } from "vitest";
import { projectWorkbenchUserState } from "../../workbenchUserState";

describe("projectWorkbenchUserState", () => {
  const base = {
    planningStatusActive: false,
    planningStatus: null,
    isPlanEditableDraft: false,
    isNotStartedConfirmed: false,
    taskStatus: "running",
    creationStatus: "confirmed" as const,
    executionDisplayStatus: "running",
    interruptionKind: null as string | null,
    executionStopped: false,
    awaitingUserConfirmation: false,
  };

  it.each([
    {
      name: "completed research",
      patch: { taskStatus: "completed" },
      expected: "execution_completed",
    },
    {
      name: "research awaiting user decision",
      patch: { awaitingUserConfirmation: true },
      expected: "pending_user",
    },
  ])("does not present $name as ongoing execution", ({ patch, expected }) => {
    expect(projectWorkbenchUserState({ ...base, ...patch })).toBe(expected);
  });

  it("maps planning session failure to plan_generation_failed", () => {
    expect(
      projectWorkbenchUserState({
        ...base,
        taskStatus: "draft",
        planningStatusActive: true,
        planningStatus: { sessionId: "s1", status: "failed" },
      }),
    ).toBe("plan_generation_failed");
  });

  it("keeps external_result_unknown inside plan_generating", () => {
    expect(
      projectWorkbenchUserState({
        ...base,
        taskStatus: "draft",
        planningStatusActive: true,
        planningStatus: { sessionId: "s1", status: "external_result_unknown" },
      }),
    ).toBe("plan_generating");
  });

  it("maps draft awaiting confirmation to plan_pending_confirmation", () => {
    expect(
      projectWorkbenchUserState({
        ...base,
        taskStatus: "draft",
        creationStatus: "awaiting_confirmation",
        isPlanEditableDraft: true,
      }),
    ).toBe("plan_pending_confirmation");
  });

  it("maps confirmed not_started to plan_confirmed", () => {
    expect(
      projectWorkbenchUserState({
        ...base,
        taskStatus: "not_started",
        creationStatus: "confirmed",
        isNotStartedConfirmed: true,
        executionDisplayStatus: "not_started",
      }),
    ).toBe("plan_confirmed");
  });

  it("keeps a failed task with no plan as plan generation failure", () => {
    expect(
      projectWorkbenchUserState({
        ...base,
        planNotYetFormed: true,
        taskStatus: "failed",
        creationStatus: "planning",
        executionDisplayStatus: "failed",
        interruptionKind: "system_failure",
      }),
    ).toBe("plan_generation_failed");
  });

  it("keeps a failed task that already has a plan as execution failure", () => {
    expect(
      projectWorkbenchUserState({
        ...base,
        planNotYetFormed: false,
        taskStatus: "failed",
        executionDisplayStatus: "failed",
        interruptionKind: "system_failure",
      }),
    ).toBe("execution_failed");
  });

  it("treats an in-flight planning retry as plan generation, not a settled failure", () => {
    expect(
      projectWorkbenchUserState({
        ...base,
        planNotYetFormed: true,
        planningStatusActive: true,
        taskStatus: "failed",
        planningStatus: { sessionId: "s1", status: "running" },
      }),
    ).toBe("plan_generating");
  });

  it("maps user stop to execution_stopped", () => {
    expect(
      projectWorkbenchUserState({
        ...base,
        interruptionKind: "user_stop",
        executionStopped: true,
      }),
    ).toBe("execution_stopped");
  });
});
