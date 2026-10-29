import { describe, expect, it } from "vitest";
import type { Task } from "@openair/shared";
import {
  acceptRenamedTask,
  createRenameSubmitGate,
  optimisticallyRenameTask,
  rollbackTaskTitle
} from "./taskBoardState";

function task(taskId: string, title: string): Task {
  return {
    taskId,
    ownerId: "owner-1",
    workspaceId: "workspace-1",
    createdBy: "owner-1",
    title,
    currentStage: "problem_modeling",
    status: "running",
    stages: ["problem_modeling"],
    artifacts: [],
    confirmations: [],
    capabilityCalls: []
  };
}

describe("TaskBoard rename state", () => {
  it("allows Enter once and suppresses the following Blur", () => {
    const gate = createRenameSubmitGate();
    expect(gate.tryStart()).toBe(true);
    expect(gate.tryStart()).toBe(false);
  });

  it("allows a Blur-only commit once", () => {
    const gate = createRenameSubmitGate();
    expect(gate.tryStart()).toBe(true);
    expect(gate.tryStart()).toBe(false);
  });

  it("allows Escape to cancel without a commit", () => {
    const gate = createRenameSubmitGate();
    gate.cancel();
    expect(gate.tryStart()).toBe(false);
  });

  it("rolls back only the target title and preserves concurrent row updates", () => {
    const taskA = task("a", "A");
    const taskB = task("b", "B");
    const optimistic = optimisticallyRenameTask([taskA, taskB], "a", "A2");
    const concurrentA = { ...optimistic[0], status: "completed" as const };
    const concurrentB = { ...optimistic[1], updatedAt: "2026-08-06T12:00:00Z" };

    const rolledBack = rollbackTaskTitle([concurrentA, concurrentB], "a", "A");

    expect(rolledBack[0]).toMatchObject({ title: "A", status: "completed" });
    expect(rolledBack[1]).toBe(concurrentB);
  });

  it("adopts the authoritative Task returned by a successful rename", () => {
    const returned = { ...task("a", "Server title"), revision: 8 };
    const result = acceptRenamedTask([task("a", "Optimistic"), task("b", "B")], returned);
    expect(result[0]).toBe(returned);
  });
});
