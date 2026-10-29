import { buildAuthHeaders, getApiBaseUrl, parseJson } from "./http";

import {
  type CanonicalTaskStartStatus,
  type StartRequestReceipt,
} from "./taskStartSemantics";

export {
  durableStartIntentStorageKey,
  isTaskStartStatusFenceCurrent,
  observeAutomaticResearchStart,
  readDurableStartIntent,
  rememberDurableStartIntent,
  settleDurableStartIntent,
  taskStartStatusMessage,
  type AutomaticResearchStartObservation,
  type CanonicalStartResponsibility,
  type CanonicalTaskStartState,
  type CanonicalTaskStartStatus,
  type StartRequestReceipt,
  type TaskStartStatusFence,
} from "./taskStartSemantics";

const START_STATES = new Set([
  "accepted",
  "running",
  "committed",
  "blocked",
  "failed",
  "not_committed",
  "unknown",
]);
const START_RESPONSIBILITIES = new Set(["SYSTEM", "USER", "NONE", "TERMINAL"]);

function validateCanonicalTaskStartStatus(
  payload: CanonicalTaskStartStatus,
  taskId: string,
): CanonicalTaskStartStatus {
  const blockerRevision = payload.blockerRevision ?? null;
  const reasonSummary = payload.reasonSummary ?? null;
  if (
    payload.taskId !== taskId
    || "selectionRequestId" in (payload as object)
    || !START_STATES.has(payload.state)
    || !Number.isInteger(payload.taskRevision)
    || payload.taskRevision < 0
    || typeof payload.startAllowed !== "boolean"
    || !START_RESPONSIBILITIES.has(payload.responsibility)
    || typeof payload.retryable !== "boolean"
    || (reasonSummary !== null && (typeof reasonSummary !== "string" || reasonSummary.length === 0))
    || (blockerRevision !== null && (!Number.isInteger(blockerRevision) || blockerRevision < 0))
    || (payload.state === "blocked" && (
      !payload.blockingReasonCode
      || blockerRevision !== payload.taskRevision
      || !reasonSummary
    ))
    || (payload.state === "failed" && !payload.failureReasonCode)
  ) {
    throw new Error("invalid canonical task start status response");
  }
  return payload;
}

function validateStartRequestReceipt(
  payload: StartRequestReceipt,
  taskId: string,
  selectionRequestId: string,
): StartRequestReceipt {
  const revision = payload.taskRevision ?? null;
  if (
    payload.taskId !== taskId
    || payload.selectionRequestId !== selectionRequestId
    || !START_STATES.has(payload.state)
    || (revision !== null && (!Number.isInteger(revision) || revision < 0))
    || "startAllowed" in (payload as object)
    || "responsibility" in (payload as object)
    || "retryable" in (payload as object)
    || "blockerRevision" in (payload as object)
    || "reasonSummary" in (payload as object)
    || (payload.state === "blocked" && (!payload.blockingReasonCode || revision === null))
    || (payload.state === "failed" && !payload.failureReasonCode)
    || (payload.state === "committed" && !payload.executionDecisionId)
  ) {
    throw new Error("invalid start request receipt response");
  }
  return payload;
}

export async function fetchCurrentTaskStartStatus(
  taskId: string,
): Promise<CanonicalTaskStartStatus> {
  const response = await fetch(
    `${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/execution-start-status`,
    { headers: buildAuthHeaders() },
  );
  const payload = await parseJson<CanonicalTaskStartStatus>(response);
  return validateCanonicalTaskStartStatus(payload, taskId);
}

export async function fetchTaskStartStatus(
  taskId: string,
  selectionRequestId: string,
): Promise<StartRequestReceipt> {
  const response = await fetch(
    `${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/execution-selections/${encodeURIComponent(selectionRequestId)}`,
    { headers: buildAuthHeaders() },
  );
  const payload = await parseJson<StartRequestReceipt>(response);
  return validateStartRequestReceipt(payload, taskId, selectionRequestId);
}
