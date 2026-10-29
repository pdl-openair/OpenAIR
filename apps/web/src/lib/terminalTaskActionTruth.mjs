/**
 * A failed Run is immutable, but its Task can still accept context for a
 * Recovery successor. Completed and cancelled Tasks remain closed to input.
 */

/** @typedef {"draft"|"not_started"|"running"|"awaiting_confirmation"|"completed"|"failed"|"cancelled"} TaskStatus */

/** @type {ReadonlyArray<"completed"|"failed"|"cancelled">} */
export const TERMINAL_TASK_STATUSES = Object.freeze(["completed", "failed", "cancelled"]);

/** @type {ReadonlyArray<"completed"|"cancelled">} */
export const CONTROLLER_SUPPLEMENT_BLOCKED_TASK_STATUSES = Object.freeze(["completed", "cancelled"]);

export const TERMINAL_CONTROLLER_SUPPLEMENT_REASON = "当前任务已经结束，不能继续提交补充。";

/** @param {string | null | undefined} status */
export function isTerminalTaskStatus(status) {
  return TERMINAL_TASK_STATUSES.includes(
    /** @type {"completed"|"failed"|"cancelled"} */ (status),
  );
}

/** @param {string | null | undefined} status */
export function canSubmitControllerSupplement(status) {
  return !CONTROLLER_SUPPLEMENT_BLOCKED_TASK_STATUSES.includes(
    /** @type {"completed"|"cancelled"} */ (status),
  );
}

/**
 * @param {string | null | undefined} status
 * @returns {string | null}
 */
export function controllerSupplementBlockedReason(status) {
  return canSubmitControllerSupplement(status) ? null : TERMINAL_CONTROLLER_SUPPLEMENT_REASON;
}
