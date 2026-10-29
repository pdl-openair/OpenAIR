import { useMemo } from "react";
import {
  type RecoveryProjectionInput,
  type RecoveryUiModel,
  projectRecoveryUi,
} from "../lib/taskRecovery";

export interface TaskRecoveryProjection extends RecoveryUiModel {
  fence: RecoveryProjectionInput["fence"];
}

export function projectTaskRecovery(input: RecoveryProjectionInput): TaskRecoveryProjection {
  return { ...projectRecoveryUi(input), fence: input.fence };
}

/** Keeps rendering derived from explicit server facts and the current fence only. */
export function useTaskRecoveryProjection(input: RecoveryProjectionInput): TaskRecoveryProjection {
  return useMemo(
    () => projectTaskRecovery(input),
    [
      input.fence.taskId,
      input.fence.expectedTaskRevision,
      input.fence.sourceRunId,
      input.response,
      input.responseUnknown,
      input.phase,
      input.eligibleForCreate,
      input.hasCurrentMessageId,
      input.hasPendingCache,
      input.unavailableReason,
      input.failure,
      input.error,
    ],
  );
}
