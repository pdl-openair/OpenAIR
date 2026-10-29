import type { ResearchStartSessionStatus } from "../api/client";

/** User-facing workbench states from docs/02_产品与Workflow/OpenAIR/OpenAIR 工作台状态与信息结构.md */
export type WorkbenchUserStateId =
  | "plan_generating"
  | "plan_generation_failed"
  | "plan_pending_confirmation"
  | "plan_confirmed"
  | "executing"
  | "pending_user"
  | "execution_failed"
  | "execution_completed"
  | "execution_stopped";

export const WORKBENCH_USER_STATE_LABELS: Record<WorkbenchUserStateId, string> = {
  plan_generating: "方案生成中",
  plan_generation_failed: "方案生成失败",
  plan_pending_confirmation: "方案待确认",
  plan_confirmed: "方案已确认",
  executing: "执行中",
  pending_user: "待处理",
  execution_failed: "执行失败",
  execution_completed: "执行完成",
  execution_stopped: "执行停止",
};

export interface ProjectWorkbenchUserStateInput {
  planningStatusActive: boolean;
  planningStatus: ResearchStartSessionStatus | null;
  /** Durable task has neither a creation plan nor a research design. */
  planNotYetFormed?: boolean;
  isPlanEditableDraft: boolean;
  isNotStartedConfirmed: boolean;
  taskStatus: string;
  creationStatus?: string;
  executionDisplayStatus: string;
  interruptionKind?: string | null;
  executionStopped?: boolean;
  awaitingUserConfirmation?: boolean;
}

const PLANNING_STILL_OPEN = new Set(["queued", "running", "worker_unavailable"]);

/** Failure before any plan exists is plan generation failure, not an execution failure. */
export function isPlanGenerationFailure(input: {
  planNotYetFormed?: boolean;
  taskStatus?: string;
  creationStatus?: string;
  planningStatus?: string | null;
}): boolean {
  if (!input.planNotYetFormed) return false;
  const planningStatus = input.planningStatus ?? null;
  if (planningStatus && PLANNING_STILL_OPEN.has(planningStatus)) return false;
  return (
    input.taskStatus === "failed"
    || input.creationStatus === "failed"
    || planningStatus === "failed"
  );
}

export function projectWorkbenchUserState(
  input: ProjectWorkbenchUserStateInput,
): WorkbenchUserStateId {
  if (isPlanGenerationFailure({
    planNotYetFormed: input.planNotYetFormed,
    taskStatus: input.taskStatus,
    creationStatus: input.creationStatus,
    planningStatus: input.planningStatus?.status ?? null,
  })) {
    return "plan_generation_failed";
  }
  if (input.planningStatusActive) {
    if (input.planningStatus?.status === "failed") {
      return "plan_generation_failed";
    }
    return "plan_generating";
  }
  if (input.isPlanEditableDraft) {
    return "plan_pending_confirmation";
  }
  if (input.isNotStartedConfirmed) {
    return "plan_confirmed";
  }
  if (input.taskStatus === "completed") {
    return "execution_completed";
  }
  if (
    input.interruptionKind === "user_stop"
    || input.interruptionKind === "execution_cancelled"
    || input.executionStopped
  ) {
    return "execution_stopped";
  }
  if (
    input.taskStatus === "failed"
    || input.interruptionKind === "system_failure"
    || input.interruptionKind === "external_result_unknown"
  ) {
    return "execution_failed";
  }
  if (
    input.awaitingUserConfirmation
    || input.executionDisplayStatus === "needs_input"
    || input.taskStatus === "awaiting_confirmation"
  ) {
    return "pending_user";
  }
  if (input.taskStatus === "running") {
    return "executing";
  }
  if (
    input.taskStatus === "not_started"
    && input.creationStatus === "confirmed"
  ) {
    return "plan_confirmed";
  }
  return "executing";
}
