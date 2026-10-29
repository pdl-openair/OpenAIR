import type { Task } from "@openair/shared";

export interface RenameSubmitGate {
  tryStart(): boolean;
  cancel(): void;
  reset(): void;
}

/** Coordinates Enter/Blur/Escape so one edit gesture can issue at most one PATCH. */
export function createRenameSubmitGate(): RenameSubmitGate {
  let state: "idle" | "submitting" | "cancelled" = "idle";
  return {
    tryStart() {
      if (state !== "idle") return false;
      state = "submitting";
      return true;
    },
    cancel() {
      if (state === "idle") state = "cancelled";
    },
    reset() {
      state = "idle";
    },
  };
}

export function optimisticallyRenameTask(tasks: readonly Task[], taskId: string, title: string): Task[] {
  return tasks.map((task) => task.taskId === taskId ? { ...task, title } : task);
}

/** Roll back only the title field of the target row; preserve all concurrent updates. */
export function rollbackTaskTitle(tasks: readonly Task[], taskId: string, previousTitle: string): Task[] {
  return tasks.map((task) => task.taskId === taskId ? { ...task, title: previousTitle } : task);
}

/** A successful mutation is authoritative: adopt the Task returned by the API. */
export function acceptRenamedTask(tasks: readonly Task[], returnedTask: Task): Task[] {
  return tasks.map((task) => task.taskId === returnedTask.taskId ? returnedTask : task);
}
