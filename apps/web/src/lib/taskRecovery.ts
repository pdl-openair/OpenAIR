import type {
  RecoveryApiError,
  RecoveryErrorCode,
  RecoveryRequestResponse,
} from "../api/client";

export type RecoveryUiState =
  | "unavailable"
  | "creating"
  | "reconciling"
  | "external_result_unknown"
  | "awaiting_user_confirmation"
  | "confirmed"
  | "failed";

export interface RecoveryFence {
  taskId: string;
  expectedTaskRevision: number;
  sourceRunId: string;
}

export interface RecoveryFailureContext {
  failedStep: string | null;
  reason: string | null;
  retainedWork: string | null;
}

export interface RecoveryActionSlot {
  kind: "retry" | "replan" | "confirm";
  label: string;
  available: boolean;
  unavailableReason: string | null;
}

export interface RecoveryDisplay {
  title: string;
  reason: string;
  failedStep: string;
  retainedFacts: string[];
  retryEffect: string;
  replanEffect: string;
  nextStep: string;
  successorFact: string | null;
}

export interface RecoveryTechnicalDetails {
  recoveryRequestId: string | null;
  state: string | null;
  code: RecoveryErrorCode | null;
  taskRevision: number | null;
  expectedTaskRevision: number;
  sourceRunId: string;
}

export interface RecoveryUiModel {
  state: RecoveryUiState;
  display: RecoveryDisplay;
  actionSlots: readonly RecoveryActionSlot[];
  authoritativeRefreshRequired: boolean;
  technical: RecoveryTechnicalDetails;
}

export interface RecoveryProjectionInput {
  fence: RecoveryFence;
  response: unknown;
  responseUnknown: boolean;
  phase: "idle" | "creating" | "confirming" | "reconciling";
  eligibleForCreate: boolean;
  hasCurrentMessageId: boolean;
  hasPendingCache: boolean;
  /** User-facing reason when the authoritative inputs needed to create recovery are incomplete. */
  unavailableReason?: string | null;
  failure: RecoveryFailureContext;
  error: RecoveryApiError | null;
}

const UNAVAILABLE = "暂不可用";

const ERROR_MESSAGES: Record<RecoveryErrorCode, string> = {
  recovery_not_found: "当前恢复状态暂无法确认，请重新检查任务进度。",
  recovery_stale: "任务状态已更新，请刷新后再尝试恢复。",
  recovery_mutex_conflict: "已有恢复操作正在处理，请先查看当前状态。",
  recovery_terminal: "当前恢复操作已结束，不能继续此操作。",
  recovery_preflight_failed: "当前还不满足恢复条件，请刷新任务状态后再试。",
  recovery_successor_exists: "任务已进入后续执行，请刷新查看最新进度。",
  idempotency_payload_conflict: "当前操作与之前提交的内容不一致，请修改后重新提交。",
  recovery_requires_postgres: "恢复功能暂不可用，请稍后重试。",
  legacy_recovery_read_only: "此恢复记录仅供查阅，不会再创建新的执行。",
};

function nonEmptyText(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function validRevision(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function validAllowedActions(value: unknown): value is Array<"confirm"> {
  return Array.isArray(value) && value.every((item) => item === "confirm");
}

/** Runtime validation prevents malformed transport data from becoming a confirmation. */
export function isRecoveryRequestResponse(value: unknown): value is RecoveryRequestResponse {
  if (!value || typeof value !== "object") return false;
  const response = value as Partial<RecoveryRequestResponse>;
  const facts = response.preservedFacts;
  if (!facts) return false;
  return nonEmptyText(response.recoveryRequestId)
    && nonEmptyText(response.taskId)
    && nonEmptyText(response.sourceRunId)
    && (response.recoveryKind === "retry" || response.recoveryKind === "replan")
    && (response.state === "requested" || response.state === "confirmed" || response.state === "cancelled")
    && validRevision(response.expectedTaskRevision)
    && validRevision(response.taskRevision)
    && (response.successorRunId === null || nonEmptyText(response.successorRunId))
    && typeof response.replayed === "boolean"
    && validAllowedActions(response.allowedActions)
    && nonEmptyText(facts.sourceRunState)
    && validRevision(facts.sourceRunNumber)
    && validRevision(facts.preservedArtifactCount);
}

export function recoveryFenceMatches(response: RecoveryRequestResponse, fence: RecoveryFence): boolean {
  return response.taskId === fence.taskId
    && response.expectedTaskRevision === fence.expectedTaskRevision
    && response.sourceRunId === fence.sourceRunId;
}

function technical(input: RecoveryProjectionInput, response: RecoveryRequestResponse | null): RecoveryTechnicalDetails {
  return {
    recoveryRequestId: response?.recoveryRequestId ?? null,
    state: response?.state ?? null,
    code: input.error?.code ?? null,
    taskRevision: response?.taskRevision ?? null,
    expectedTaskRevision: input.fence.expectedTaskRevision,
    sourceRunId: input.fence.sourceRunId,
  };
}

function slots(input: RecoveryProjectionInput, response: RecoveryRequestResponse | null): readonly RecoveryActionSlot[] {
  void input;
  void response;
  const readOnlyReason = "历史恢复记录仅供查阅，不会创建或确认新的执行。";
  return [
    { kind: "retry", label: "重试", available: false, unavailableReason: readOnlyReason },
    { kind: "replan", label: "调整方案", available: false, unavailableReason: readOnlyReason },
    { kind: "confirm", label: "确认调整方案", available: false, unavailableReason: readOnlyReason },
  ];
}

function baseDisplay(failure: RecoveryFailureContext, unavailableReason?: string | null): RecoveryDisplay {
  return {
    title: "恢复信息暂不可用",
    reason: failure.reason ?? unavailableReason ?? UNAVAILABLE,
    failedStep: failure.failedStep ?? UNAVAILABLE,
    retainedFacts: failure.retainedWork ? [failure.retainedWork] : [UNAVAILABLE],
    retryEffect: "重试会从当前失败步骤重新安排执行。",
    replanEffect: "调整方案会保留已完成的研究内容，等待你确认后继续。",
    nextStep: unavailableReason ?? "正在确认最新恢复状态。",
    successorFact: null,
  };
}

function factsForDisplay(response: RecoveryRequestResponse, failure: RecoveryFailureContext): string[] {
  const facts = [`已保留 ${response.preservedFacts.preservedArtifactCount} 项研究产出。`];
  if (failure.retainedWork) facts.unshift(failure.retainedWork);
  return facts;
}

export function projectRecoveryUi(input: RecoveryProjectionInput): RecoveryUiModel {
  const response = isRecoveryRequestResponse(input.response) && recoveryFenceMatches(input.response, input.fence)
    ? input.response
    : null;
  const base = baseDisplay(input.failure, input.unavailableReason);
  const error = input.error;

  if (input.responseUnknown || error?.responseUnknown) {
    return {
      state: "external_result_unknown",
      display: { ...base, title: "恢复结果尚未确认", nextStep: "请重新核对，系统会继续确认原操作结果。" },
      actionSlots: slots(input, response),
      authoritativeRefreshRequired: true,
      technical: technical(input, response),
    };
  }
  if (error) {
    return {
      state: "failed",
      display: { ...base, title: "恢复操作未完成", reason: error.code ? ERROR_MESSAGES[error.code] : "恢复操作暂时无法完成，请稍后重新核对。", nextStep: error.code === "idempotency_payload_conflict" ? "修改操作后重新提交，系统不会重复执行。" : "请重新核对当前任务状态。" },
      actionSlots: slots(input, response),
      authoritativeRefreshRequired: false,
      technical: technical(input, response),
    };
  }
  if (input.phase === "creating" || input.phase === "confirming") {
    return {
      state: "creating",
      display: { ...base, title: "正在提交恢复操作", nextStep: "正在确认恢复操作是否已生效。" },
      actionSlots: slots({ ...input, eligibleForCreate: false }, response),
      authoritativeRefreshRequired: false,
      technical: technical(input, response),
    };
  }
  if (input.phase === "reconciling") {
    return {
      state: "reconciling",
      display: { ...base, title: "正在核对恢复状态", nextStep: "确认最新状态后再继续。" },
      actionSlots: slots({ ...input, eligibleForCreate: false }, response),
      authoritativeRefreshRequired: true,
      technical: technical(input, response),
    };
  }
  if (!response) {
    return { state: "unavailable", display: base, actionSlots: slots(input, null), authoritativeRefreshRequired: false, technical: technical(input, null) };
  }
  if (response.recoveryKind === "replan" && response.state === "requested" && response.allowedActions[0] === "confirm") {
    return {
      state: "awaiting_user_confirmation",
      display: { ...base, title: "历史恢复记录", retainedFacts: factsForDisplay(response, input.failure), nextStep: "此记录仅供查阅；新的执行由当前正式流程创建。" },
      actionSlots: slots(input, response),
      authoritativeRefreshRequired: false,
      technical: technical(input, response),
    };
  }
  if (response.state === "confirmed") {
    return {
      state: "confirmed",
      display: {
        ...base,
        title: "恢复已确认",
        retainedFacts: factsForDisplay(response, input.failure),
        nextStep: response.successorRunId ? "已进入后续执行，系统将继续更新进展。" : "等待后续执行开始。",
        successorFact: response.successorRunId ? "已进入后续执行。" : null,
      },
      actionSlots: slots({ ...input, eligibleForCreate: false }, response),
      authoritativeRefreshRequired: false,
      technical: technical(input, response),
    };
  }
  return {
    state: "failed",
    display: { ...base, title: "恢复操作已结束", nextStep: "请重新核对当前任务状态。" },
    actionSlots: slots(input, response),
    authoritativeRefreshRequired: false,
    technical: technical(input, response),
  };
}

export interface DeferredRecoveryContext {
  taskId: string;
  expectedTaskRevision: number;
  /** The authoritative Run visible when the supplement was submitted, if any. */
  submittedSourceRunId: string | null;
  /** The one authoritative Run this context has been allowed to attach to. */
  boundSourceRunId: string | null;
  commandIds: string[];
}

export interface DeferredRecoveryContextStore {
  version: 1;
  contexts: DeferredRecoveryContext[];
}

function validDeferredContext(value: unknown): value is DeferredRecoveryContext {
  if (!value || typeof value !== "object") return false;
  const context = value as Partial<DeferredRecoveryContext>;
  return nonEmptyText(context.taskId)
    && validRevision(context.expectedTaskRevision)
    && (context.submittedSourceRunId === null || nonEmptyText(context.submittedSourceRunId))
    && (context.boundSourceRunId === null || nonEmptyText(context.boundSourceRunId))
    && Array.isArray(context.commandIds)
    && context.commandIds.every((commandId) => nonEmptyText(commandId));
}

export function normalizeDeferredRecoveryContextStore(value: unknown): DeferredRecoveryContextStore {
  if (!value || typeof value !== "object") return { version: 1, contexts: [] };
  const store = value as Partial<DeferredRecoveryContextStore>;
  if (store.version !== 1 || !Array.isArray(store.contexts)) return { version: 1, contexts: [] };
  return {
    version: 1,
    contexts: store.contexts
      .filter(validDeferredContext)
      .map((context) => ({
        ...context,
        commandIds: Array.from(new Set(context.commandIds)),
      })),
  };
}

export function deferredRecoveryContextsForTask(
  store: DeferredRecoveryContextStore,
  taskId: string,
  authoritativeTaskRevision: number,
): DeferredRecoveryContext[] {
  return store.contexts.filter((context) => (
    context.taskId === taskId && context.expectedTaskRevision <= authoritativeTaskRevision
  ));
}

export function rememberDeferredRecoveryContext(input: {
  store: DeferredRecoveryContextStore;
  taskId: string;
  expectedTaskRevision: number;
  submittedSourceRunId: string | null;
  commandId: string;
}): DeferredRecoveryContextStore {
  const { store, taskId, expectedTaskRevision, submittedSourceRunId, commandId } = input;
  if (!nonEmptyText(taskId) || !validRevision(expectedTaskRevision) || !nonEmptyText(commandId)) return store;
  const index = store.contexts.findIndex((context) => (
    context.taskId === taskId
    && context.expectedTaskRevision === expectedTaskRevision
    && context.submittedSourceRunId === submittedSourceRunId
  ));
  const context: DeferredRecoveryContext = index >= 0
    ? {
      ...store.contexts[index],
      commandIds: Array.from(new Set([...store.contexts[index].commandIds, commandId])),
    }
    : {
      taskId,
      expectedTaskRevision,
      submittedSourceRunId,
      boundSourceRunId: submittedSourceRunId,
      commandIds: [commandId],
    };
  const contexts = [...store.contexts];
  if (index >= 0) contexts[index] = context;
  else contexts.push(context);
  return { version: 1, contexts };
}

export function bindDeferredRecoveryContextToFence(input: {
  context: DeferredRecoveryContext;
  fence: RecoveryFence;
}): { context: DeferredRecoveryContext | null; commandIds: string[] } {
  const { context, fence } = input;
  if (
    context.taskId !== fence.taskId
    || fence.expectedTaskRevision < context.expectedTaskRevision
    || !nonEmptyText(fence.sourceRunId)
  ) {
    return { context: null, commandIds: [] };
  }
  const expectedRunId = context.boundSourceRunId ?? context.submittedSourceRunId;
  if (expectedRunId && expectedRunId !== fence.sourceRunId) return { context: null, commandIds: [] };
  const bound = context.boundSourceRunId === fence.sourceRunId
    ? context
    : { ...context, boundSourceRunId: fence.sourceRunId };
  return { context: bound, commandIds: Array.from(new Set(context.commandIds)) };
}

export function replaceDeferredRecoveryContext(
  store: DeferredRecoveryContextStore,
  context: DeferredRecoveryContext,
): DeferredRecoveryContextStore {
  const contexts = store.contexts.map((candidate) => (
    candidate.taskId === context.taskId
    && candidate.expectedTaskRevision === context.expectedTaskRevision
    && candidate.submittedSourceRunId === context.submittedSourceRunId
      ? context
      : candidate
  ));
  return { version: 1, contexts };
}

export function removeDeferredRecoveryContext(
  store: DeferredRecoveryContextStore,
  context: DeferredRecoveryContext,
): DeferredRecoveryContextStore {
  return {
    version: 1,
    contexts: store.contexts.filter((candidate) => !(
      candidate.taskId === context.taskId
      && candidate.expectedTaskRevision === context.expectedTaskRevision
      && candidate.submittedSourceRunId === context.submittedSourceRunId
    )),
  };
}

/**
 * A context submitted before a Run exists can bind only when this page observes
 * that exact task/revision transition from no Run to one authoritative Run.
 */
export function bindDeferredRecoveryContext(input: {
  context: DeferredRecoveryContext | null;
  fence: RecoveryFence;
  sourceRunArrived: boolean;
}): string[] {
  const { context, fence, sourceRunArrived } = input;
  if (!context || !sourceRunArrived || context.expectedTaskRevision !== fence.expectedTaskRevision) return [];
  return bindDeferredRecoveryContextToFence({ context, fence }).commandIds;
}

/** A same-task revision refresh must not strand a confirmed UI submission. */
export function isCurrentControllerSubmissionTask(input: {
  submittedTaskId: string;
  submissionGeneration: number;
  currentTaskId: string | null;
  currentSubmissionGeneration: number;
}): boolean {
  return input.submittedTaskId === input.currentTaskId
    && input.submissionGeneration === input.currentSubmissionGeneration;
}
