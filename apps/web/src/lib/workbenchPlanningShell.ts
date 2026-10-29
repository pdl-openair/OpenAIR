export type PlanningShellLoadStatus = "loading" | "ready" | "error";

export function planningOccupiesWorkbenchShell(input: {
  planningSessionId: string | null;
  taskId: string | null | undefined;
  sessionAccessError: string | null;
  loadStatus: PlanningShellLoadStatus;
  loadErrorKind?: string;
  taskStatus?: string;
  creationStatus?: string;
}): boolean {
  if (!input.planningSessionId || !input.taskId || input.sessionAccessError) {
    return false;
  }
  if (input.loadStatus === "loading") return true;
  if (
    input.loadStatus === "error" &&
    (input.loadErrorKind === "notFound" || input.loadErrorKind === "rateLimited")
  ) {
    return true;
  }
  if (input.loadStatus !== "ready") return false;
  const { taskStatus, creationStatus } = input;
  if (
    creationStatus === "confirmed" ||
    taskStatus === "running" ||
    taskStatus === "not_started" ||
    taskStatus === "completed" ||
    taskStatus === "failed" ||
    taskStatus === "cancelled"
  ) {
    return false;
  }
  return (
    taskStatus === "draft" ||
    creationStatus === "awaiting_confirmation" ||
    creationStatus === "revision_requested"
  );
}
