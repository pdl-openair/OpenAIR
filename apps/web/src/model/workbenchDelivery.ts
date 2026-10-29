import type { Task } from "@openair/shared";

/** Preserve the original current-result selection; bind judgment to that result. */
export function projectWorkbenchDelivery(task: Task) {
  const finalResult =
    task.finalResult &&
    task.finalResultAuthority?.status === "current" &&
    task.finalResultAuthority.finalResultId === task.finalResult.finalResultId
      ? task.finalResult
      : null;
  const summary = task.completionDecision;
  const completionDecision =
    task.status === "completed" &&
    finalResult &&
    summary &&
    summary.completionDecisionId === finalResult.completionDecisionId &&
    summary.scientificStateVersion === finalResult.scientificStateVersion
      ? summary
      : null;
  return { finalResult, completionDecision };
}
