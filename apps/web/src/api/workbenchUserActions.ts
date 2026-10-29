import { ApiError, buildAuthHeaders, getApiBaseUrl, parseJson } from "./http";
import {
  fetchTask,
  resumeTaskRun,
  stopTaskRun,
  submitControllerSupplement,
  type ControllerCommand,
} from "./client";

export type WorkbenchUserActionName =
  | "supplement_input"
  | "explicit_retry"
  | "continue_research"
  | "stop";

export type WorkbenchActionOperationState =
  | "idle"
  | "submitting"
  | "response_unknown"
  | "pending"
  | "confirmed"
  | "rejected"
  | "failed";

export interface ExplicitRetryCommandContext {
  taskId: string;
  workUnitId: string;
  dispatchId: string;
  executionDecisionId: string;
  capabilityCallId: string;
  attemptNumber: number;
  priorCapabilityCallId: string;
  priorAttemptNumber: number;
  canonicalPayloadFingerprint: string;
  ownerId: string;
  workspaceId: string;
  actorId: string;
  reasonCode: string;
}

export interface WorkbenchUserActionItem {
  itemId: string;
  kind: "external_result_unknown" | "explicit_retry_ready" | "structured_output_retry_ready" | "provide_input" | "artifact_review_required" | "supplement_pending" | "supplement_rejected";
  title: string;
  reason: string;
  workUnitId: string | null;
  objective: string | null;
  artifactRefs: string[];
  allowedActions: WorkbenchUserActionName[];
  commandContexts: Partial<{
    explicit_retry: ExplicitRetryCommandContext;
  }>;
}

export interface WorkbenchUserActionsSnapshot {
  schemaVersion: "workbench_user_actions_v1";
  lifecycleKind: "canonical_v1_2" | "legacy_durable_run";
  taskId: string;
  revision: number;
  taskStatus: string;
  executionStopped: boolean;
  allowedActions: WorkbenchUserActionName[];
  items: WorkbenchUserActionItem[];
  unrecoverable: boolean;
  unrecoverableMessage: string | null;
}

export class WorkbenchActionApiError extends Error {
  constructor(
    message: string,
    public readonly status: number | null = null,
    public readonly responseUnknown = false,
  ) {
    super(message);
    this.name = "WorkbenchActionApiError";
  }
}

async function actionFetch(url: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch {
    throw new WorkbenchActionApiError("服务端响应未送达。", null, true);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isAction(value: unknown): value is WorkbenchUserActionName {
  return value === "supplement_input"
    || value === "explicit_retry"
    || value === "continue_research"
    || value === "stop";
}

function hasCommonCommandContext(
  value: Record<string, unknown>,
  taskId: string,
  workUnitId: string,
): boolean {
  return value.taskId === taskId
    && value.workUnitId === workUnitId
    && isNonEmptyString(value.dispatchId)
    && isNonEmptyString(value.executionDecisionId)
    && isNonEmptyString(value.capabilityCallId)
    && Number.isSafeInteger(value.attemptNumber)
    && Number(value.attemptNumber) > 0
    && typeof value.canonicalPayloadFingerprint === "string"
    && /^[0-9a-f]{64}$/i.test(value.canonicalPayloadFingerprint)
    && isNonEmptyString(value.ownerId)
    && isNonEmptyString(value.workspaceId)
    && isNonEmptyString(value.actorId);
}

function parseActionItem(value: unknown, taskId: string): WorkbenchUserActionItem {
  if (!isRecord(value)
    || !isNonEmptyString(value.itemId)
    || !["external_result_unknown", "explicit_retry_ready", "structured_output_retry_ready", "provide_input", "artifact_review_required", "supplement_pending", "supplement_rejected"].includes(String(value.kind))
    || !isNonEmptyString(value.title)
    || !isNonEmptyString(value.reason)
    || (value.workUnitId !== null && !isNonEmptyString(value.workUnitId))
    || (value.objective !== undefined && value.objective !== null && !isNonEmptyString(value.objective))
    || (value.artifactRefs !== undefined && (!Array.isArray(value.artifactRefs) || !value.artifactRefs.every(isNonEmptyString)))
    || !Array.isArray(value.allowedActions)
    || !value.allowedActions.every(isAction)
    || !isRecord(value.commandContexts)
  ) {
    throw new WorkbenchActionApiError("用户操作项合同校验失败。", 200);
  }
  if ((value.allowedActions.includes("explicit_retry") && !["explicit_retry_ready", "structured_output_retry_ready"].includes(String(value.kind)))
    || (value.allowedActions.includes("supplement_input") && value.kind !== "provide_input")
  ) {
    throw new WorkbenchActionApiError("当前操作与目标步骤不一致，暂不开放。", 200);
  }
  const workUnitId = typeof value.workUnitId === "string" ? value.workUnitId : null;
  const contexts = value.commandContexts;
  if (value.allowedActions.includes("explicit_retry")) {
    const context = contexts.explicit_retry;
    if (!workUnitId || !isRecord(context) || !hasCommonCommandContext(context, taskId, workUnitId)
      || !isNonEmptyString(context.priorCapabilityCallId)
      || !Number.isSafeInteger(context.priorAttemptNumber)
      || Number(context.priorAttemptNumber) <= 0
      || !isNonEmptyString(context.reasonCode)
    ) {
      throw new WorkbenchActionApiError("明确重试合同校验失败。", 200);
    }
  }
  return {
    itemId: value.itemId,
    kind: value.kind as WorkbenchUserActionItem["kind"],
    title: value.title,
    reason: value.reason,
    workUnitId,
    objective: typeof value.objective === "string" ? value.objective : null,
    artifactRefs: Array.isArray(value.artifactRefs) ? value.artifactRefs : [],
    allowedActions: value.allowedActions as WorkbenchUserActionName[],
    commandContexts: contexts as WorkbenchUserActionItem["commandContexts"],
  };
}

function parseActionSnapshot(value: unknown): WorkbenchUserActionsSnapshot {
  if (!isRecord(value)
    || value.schemaVersion !== "workbench_user_actions_v1"
    || (value.lifecycleKind !== "canonical_v1_2" && value.lifecycleKind !== "legacy_durable_run")
    || !isNonEmptyString(value.taskId)
    || !Number.isSafeInteger(value.revision)
    || Number(value.revision) < 0
    || !isNonEmptyString(value.taskStatus)
    || typeof value.executionStopped !== "boolean"
    || !Array.isArray(value.allowedActions)
    || !value.allowedActions.every(isAction)
    || !Array.isArray(value.items)
    || typeof value.unrecoverable !== "boolean"
    || (value.unrecoverableMessage !== null && typeof value.unrecoverableMessage !== "string")
  ) {
    throw new WorkbenchActionApiError("用户操作合同校验失败。", 200);
  }
  const taskId = value.taskId;
  const items = value.items.map((item) => parseActionItem(item, taskId));
  if (items.some((item) => item.allowedActions.includes("supplement_input")) && !value.allowedActions.includes("supplement_input")) {
    throw new WorkbenchActionApiError("当前操作权限存在不一致，暂不开放。", 200);
  }
  return {
    schemaVersion: "workbench_user_actions_v1",
    lifecycleKind: value.lifecycleKind,
    taskId,
    revision: Number(value.revision),
    taskStatus: value.taskStatus,
    executionStopped: value.executionStopped,
    allowedActions: value.allowedActions,
    items,
    unrecoverable: value.unrecoverable,
    unrecoverableMessage: value.unrecoverableMessage,
  };
}

export async function fetchWorkbenchUserActions(
  taskId: string,
  expectedRevision?: number | null,
): Promise<WorkbenchUserActionsSnapshot> {
  const res = await actionFetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/user-actions`, {
    headers: buildAuthHeaders({ Accept: "application/json" }),
  });
  const payload = await parseJson<unknown>(res);
  const snapshot = parseActionSnapshot(payload);
  if (snapshot.taskId !== taskId || (expectedRevision != null && snapshot.revision !== expectedRevision)) {
    throw new WorkbenchActionApiError("用户操作权限与当前任务版本不一致。", 409);
  }
  return snapshot;
}

export async function retryCanonicalExternalResult(
  context: ExplicitRetryCommandContext,
  expectedTaskRevision: number,
  commandRequestId: string,
): Promise<Record<string, unknown>> {
  const res = await actionFetch(
    `${getApiBaseUrl()}/api/tasks/${encodeURIComponent(context.taskId)}/work-units/${encodeURIComponent(context.workUnitId)}/external-result/retry`,
    {
      method: "POST",
      headers: buildAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        taskId: context.taskId,
        workUnitId: context.workUnitId,
        dispatchId: context.dispatchId,
        executionDecisionId: context.executionDecisionId,
        priorCapabilityCallId: context.priorCapabilityCallId,
        priorAttemptNumber: context.priorAttemptNumber,
        commandRequestId,
        canonicalPayloadFingerprint: context.canonicalPayloadFingerprint,
        expectedTaskRevision,
        ownerId: context.ownerId,
        workspaceId: context.workspaceId,
        actorId: context.actorId,
        reasonCode: context.reasonCode,
      }),
    },
  );
  return parseJson<Record<string, unknown>>(res);
}

export async function submitSupplementCommand(
  taskId: string,
  text: string,
  idempotencyKey: string,
): Promise<ControllerCommand> {
  try {
    const task = await fetchTask(taskId);
    if (!task || typeof task.revision !== "number") {
      throw new WorkbenchActionApiError("当前任务状态暂不可确认，请刷新后重试。", null, true);
    }
    return await submitControllerSupplement(taskId, text, idempotencyKey, task.revision);
  } catch (error) {
    if (error instanceof WorkbenchActionApiError) throw error;
    if (error instanceof TypeError) {
      throw new WorkbenchActionApiError("补充信息响应未送达。", null, true);
    }
    if (error instanceof ApiError) {
      if (error.status === 409) {
        throw new WorkbenchActionApiError("当前任务状态已变化，补充信息未提交。请刷新后重试。", 409);
      }
      if (error.status === 422) {
        const detail = typeof error.detail === "string" ? error.detail : "";
        throw new WorkbenchActionApiError(
          detail === "supplement_text_not_informative"
            ? "当前还需要补充具体信息。请回答协作区里列出的问题；仅输入「继续」或「确认」无法推进。"
            : "补充信息格式不符合要求。",
          422,
        );
      }
      throw new WorkbenchActionApiError("暂时无法提交补充信息。", error.status);
    }
    throw new WorkbenchActionApiError("暂时无法提交补充信息。");
  }
}

export async function fetchControllerCommandReceipt(taskId: string, commandId: string): Promise<ControllerCommand> {
  const res = await actionFetch(
    `${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/controller-commands/${encodeURIComponent(commandId)}`,
    { headers: buildAuthHeaders({ Accept: "application/json" }) },
  );
  if (res.status === 404) throw new WorkbenchActionApiError("尚未发现该提交。", 404);
  return parseJson<ControllerCommand>(res);
}

export async function discoverControllerCommandReceipt(
  taskId: string,
  idempotencyKey: string,
): Promise<ControllerCommand> {
  const res = await actionFetch(
    `${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/controller-commands`,
    { headers: buildAuthHeaders({ Accept: "application/json", "Idempotency-Key": idempotencyKey }) },
  );
  if (res.status === 404) throw new WorkbenchActionApiError("尚未发现该提交。", 404);
  return parseJson<ControllerCommand>(res);
}

export function requestCanonicalStop(taskId: string, revision: number): ReturnType<typeof stopTaskRun> {
  return stopTaskRun(taskId, revision);
}

export function requestCanonicalResume(taskId: string, revision: number): ReturnType<typeof resumeTaskRun> {
  return resumeTaskRun(taskId, revision);
}
