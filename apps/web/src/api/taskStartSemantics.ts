export type CanonicalTaskStartState =
  | "accepted"
  | "running"
  | "committed"
  | "blocked"
  | "failed"
  | "not_committed"
  | "unknown";

export type CanonicalStartResponsibility = "SYSTEM" | "USER" | "NONE" | "TERMINAL";

/** Current Task authority. It intentionally has no request identity. */
export type CanonicalTaskStartStatus = {
  taskId: string;
  state: CanonicalTaskStartState;
  taskRevision: number;
  startAllowed: boolean;
  responsibility: CanonicalStartResponsibility;
  retryable: boolean;
  reasonSummary?: string | null;
  blockerRevision?: number | null;
  executionDecisionId?: string | null;
  blockingReasonCode?: string | null;
  failureReasonCode?: string | null;
};

/** One Start request receipt. It can never decide current Task startability. */
export type StartRequestReceipt = {
  taskId: string;
  selectionRequestId: string;
  state: CanonicalTaskStartState;
  taskRevision?: number | null;
  executionDecisionId?: string | null;
  blockingReasonCode?: string | null;
  failureReasonCode?: string | null;
};

export type TaskStartStatusFence = {
  taskId: string;
  selectionRequestId: string;
  taskRevision: number | null;
  generation: number;
};

export type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export function durableStartIntentStorageKey(taskId: string): string {
  return `openair.durable-start-intent.v1:${taskId}`;
}

function browserStorage(): StorageLike | null {
  return typeof window === "undefined" ? null : window.localStorage;
}

export function readDurableStartIntent(
  taskId: string | undefined,
  storage: StorageLike | null = browserStorage(),
): string | null {
  if (!taskId || !storage) return null;
  return storage.getItem(durableStartIntentStorageKey(taskId));
}

export function rememberDurableStartIntent(
  taskId: string,
  selectionRequestId: string,
  storage: StorageLike | null = browserStorage(),
): void {
  storage?.setItem(durableStartIntentStorageKey(taskId), selectionRequestId);
}

export function settleDurableStartIntent(
  taskId: string,
  selectionRequestId: string,
  state: CanonicalTaskStartState,
  storage: StorageLike | null = browserStorage(),
): void {
  if (!storage || ["accepted", "running", "unknown"].includes(state)) return;
  const key = durableStartIntentStorageKey(taskId);
  if (storage.getItem(key) === selectionRequestId) storage.removeItem(key);
}

export function isTaskStartStatusFenceCurrent(
  fence: TaskStartStatusFence,
  current: {
    taskId: string | null;
    selectionRequestId: string | null;
    taskRevision: number | null;
    generation: number;
  },
): boolean {
  return fence.taskId === current.taskId
    && fence.selectionRequestId === current.selectionRequestId
    && fence.taskRevision === current.taskRevision
    && fence.generation === current.generation;
}

export type AutomaticResearchStartObservation =
  | { state: "started"; blockingReasonCode: null }
  | { state: "blocked"; blockingReasonCode: string | null }
  | { state: "explicit_start_required"; blockingReasonCode: null };

export function observeAutomaticResearchStart(
  taskStatus: string | null | undefined,
  substages: readonly { status?: string | null; statusReasonCode?: string | null }[] | null | undefined,
): AutomaticResearchStartObservation {
  if (taskStatus === "running") {
    return { state: "started", blockingReasonCode: null };
  }
  if (taskStatus === "awaiting_confirmation") {
    const blocker = substages?.find((row) => (
      row.status === "needs_input"
      || row.status === "blocked_recoverable"
      || row.status === "insufficient_evidence"
    ));
    return { state: "blocked", blockingReasonCode: blocker?.statusReasonCode ?? null };
  }
  return { state: "explicit_start_required", blockingReasonCode: null };
}

const START_BLOCKER_MESSAGES: Record<string, string> = {
  required_input_missing: "研究还没有开始，因为开始前还缺少必要信息。需要先补充这些信息才能继续。",
  authorization_required: "研究还没有开始，因为开始前还缺少必要确认。需要先完成确认才能继续。",
  budget_expansion_required: "研究还没有开始，因为当前研究范围需要额外确认。完成确认后才能继续。",
  policy_prohibited: "研究还没有开始，因为当前方案不符合已确认的研究边界。需要先调整研究边界。",
  runtime_unavailable: "研究还没有开始，因为当前执行能力暂不可用。请稍后刷新项目查看状态。",
  llm_runtime_unavailable: "研究还没有开始，因为当前执行能力暂不可用。请稍后刷新项目查看状态。",
  limited_coverage: "研究还没有开始，因为当前方案无法安全覆盖这一步。需要先调整研究方案。",
  provided_materials_only: "研究还没有开始，因为现有材料不足以安全执行这一步。需要先补充材料。",
  dependency_instance_missing: "研究还没有开始，因为前置研究信息不完整。请刷新项目查看最新状态。",
  dependency_terminal_unsatisfied: "研究还没有开始，因为前置研究步骤没有成功完成。需要先处理该步骤。",
  dependency_not_satisfied: "研究还没有开始，因为前置研究条件尚未满足。需要先完成前置步骤。",
  canonical_workflow_lineage_mismatch: "研究还没有开始，因为当前研究方案与已确认的执行流程不一致。需要重新核对研究方案。",
  canonical_skill_composition_not_frozen: "研究还没有开始，因为当前方案所需能力尚未准备完成。请稍后刷新项目查看状态。",
  canonical_skill_composition_unavailable: "研究还没有开始，因为当前方案所需能力暂不可用。请稍后刷新项目查看状态。",
  needs_input: "研究还没有开始，因为开始前还需要补充信息。补充完成后才能继续。",
};

export function taskStartStatusMessage(
  state: CanonicalTaskStartState,
  blockingReasonCode?: string | null,
): string {
  if (state === "committed") {
    return "研究已经进入执行准备。";
  }
  if (state === "accepted") {
    return "开始请求已接收，系统正在安排已确认方案中的研究工作。";
  }
  if (state === "running") {
    return "系统正在准备任务资料并分发研究工作。";
  }
  if (state === "blocked") {
    return START_BLOCKER_MESSAGES[blockingReasonCode ?? ""]
      ?? "研究还没有开始，因为开始条件尚未满足。需要先解决当前阻塞条件。";
  }
  if (state === "not_committed") {
    return "研究还没有开始，因为刚才的启动请求没有提交成功。请重新开始研究。";
  }
  if (state === "failed") {
    return "系统未能完成启动准备，已保留请求记录。请刷新后重试。";
  }
  return "开始结果暂无法确认，系统不会自动重试。请刷新查看状态。";
}
