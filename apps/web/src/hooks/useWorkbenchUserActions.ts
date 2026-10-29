import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  submitControllerSupplement,
  type ControllerCommand,
} from "../api/client";
import { ApiError } from "../api/http";
import {
  WorkbenchActionApiError,
  discoverControllerCommandReceipt,
  fetchControllerCommandReceipt,
  fetchWorkbenchUserActions,
  requestCanonicalResume,
  requestCanonicalStop,
  retryCanonicalExternalResult,
  type WorkbenchActionOperationState,
  type WorkbenchUserActionItem,
  type WorkbenchUserActionsSnapshot,
} from "../api/workbenchUserActions";
import {
  clearControllerCommandIntent,
  readControllerCommandIntent,
  writeControllerCommandIntent,
  type ControllerCommandReceiptState,
  type PersistedControllerCommandIntent,
} from "./useControllerCommandReceipts";
import { getWorkbenchTaskRequestCoordinator } from "../lib/workbenchTaskRequestCoordinator";
import {
  currentWorkbenchActionItem, currentWorkbenchActionSnapshot,
  hasWorkbenchTaskAction,
} from "../lib/workbenchInterruption.mjs";

export interface WorkbenchActionState {
  state: WorkbenchActionOperationState;
  error: string | null;
}

interface CoordinatedControllerCommandResult extends ControllerCommandReceiptState {
  busy: boolean;
  submit(text: string): Promise<ControllerCommand | null>;
  reconcile(): Promise<ControllerCommand | null>;
}

export interface WorkbenchUserActionsController {
  snapshot: WorkbenchUserActionsSnapshot | null;
  items: WorkbenchUserActionItem[];
  lifecycleKind: "canonical_v1_2" | "legacy_durable_run" | null;
  loading: boolean;
  error: string | null;
  operationState(item: WorkbenchUserActionItem, action: string): WorkbenchActionState;
  submitSupplement(text: string): Promise<ControllerCommand | null>;
  explicitRetry(item: WorkbenchUserActionItem, confirmed: boolean): Promise<void>;
  stop(): Promise<void>;
  resume(): Promise<boolean>;
  refresh(): Promise<WorkbenchUserActionsSnapshot | null>;
  controller: CoordinatedControllerCommandResult;
}

interface UseWorkbenchUserActionsOptions {
  taskId: string;
  enabled: boolean;
  revision: number | null;
  onAuthoritativeRefresh?: () => void | Promise<void>;
}

interface PersistedActionIntent {
  taskId: string;
  workUnitId: string;
  action: "explicit_retry";
  revision: number;
  commandRequestId: string;
}

function actionKey(item: WorkbenchUserActionItem, action: string): string {
  return `${item.itemId}\u0000${action}`;
}

function intentStorageKey(intent: Omit<PersistedActionIntent, "commandRequestId">): string {
  return `openair.workbench-action-intent.v1:${encodeURIComponent(intent.taskId)}:${intent.revision}:${encodeURIComponent(intent.workUnitId)}:${intent.action}`;
}

function readOrCreateIntent(
  taskId: string,
  workUnitId: string,
  action: PersistedActionIntent["action"],
  revision: number,
): PersistedActionIntent {
  const base = { taskId, workUnitId, action, revision };
  const key = intentStorageKey(base);
  try {
    const raw = window.localStorage.getItem(key);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<PersistedActionIntent>;
      if (
        parsed.taskId === taskId
        && parsed.workUnitId === workUnitId
        && parsed.action === action
        && parsed.revision === revision
        && typeof parsed.commandRequestId === "string"
        && parsed.commandRequestId.trim()
      ) {
        return parsed as PersistedActionIntent;
      }
    }
  } catch {
    // Continue with an in-memory-safe new request identity.
  }
  const intent = { ...base, commandRequestId: crypto.randomUUID() };
  try {
    window.localStorage.setItem(key, JSON.stringify(intent));
  } catch {
    // The server still fences the request by revision and command fingerprint.
  }
  return intent;
}

const EMPTY_CONTROLLER_STATE: ControllerCommandReceiptState = {
  operationState: "idle",
  intent: null,
  receipt: null,
  error: null,
};

function controllerStateForReceipt(receipt: ControllerCommand): WorkbenchActionOperationState {
  if (receipt.state === "received") return "pending";
  if (receipt.state === "applied") return "confirmed";
  if (receipt.state === "rejected") return "rejected";
  return "failed";
}

function controllerStateIsBusy(state: WorkbenchActionOperationState): boolean {
  return state === "submitting" || state === "response_unknown" || state === "pending";
}

function controllerFailureMessage(error: unknown): string {
  if (error instanceof ApiError) return error.userMessage;
  if (error instanceof WorkbenchActionApiError) return error.message;
  return "暂时无法完成补充信息操作。";
}

function workbenchActionFailureMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return error.userMessage;
  if (error instanceof WorkbenchActionApiError) return error.message;
  return fallback;
}

function responseMayBeUnknown(error: unknown): boolean {
  return error instanceof TypeError
    || (error instanceof ApiError && error.status >= 500)
    || (error instanceof WorkbenchActionApiError && error.responseUnknown);
}

function useCoordinatedControllerCommandReceipts(
  taskId: string,
  enabled: boolean,
  revision: number | null,
): CoordinatedControllerCommandResult {
  const [state, setState] = useState<ControllerCommandReceiptState>(EMPTY_CONTROLLER_STATE);
  const stateRef = useRef<ControllerCommandReceiptState>(EMPTY_CONTROLLER_STATE);
  const generationRef = useRef(0);
  const inFlightRef = useRef(false);
  const renderTaskIdRef = useRef(taskId);
  if (renderTaskIdRef.current !== taskId) {
    renderTaskIdRef.current = taskId;
    generationRef.current += 1;
    inFlightRef.current = false;
    stateRef.current = EMPTY_CONTROLLER_STATE;
  }

  const applyState = useCallback((next: ControllerCommandReceiptState) => {
    stateRef.current = next;
    setState(next);
  }, []);

  const readAuthority = useCallback(async (
    intent: PersistedControllerCommandIntent,
    generation: number,
  ): Promise<ControllerCommand | null> => {
    const coordinator = getWorkbenchTaskRequestCoordinator("workbench-page", "canonical", taskId);
    try {
      const receipt = await coordinator.request(
        `controller-command-receipt:${intent.commandId ?? intent.idempotencyKey}`,
        () => intent.commandId
          ? fetchControllerCommandReceipt(taskId, intent.commandId)
          : discoverControllerCommandReceipt(taskId, intent.idempotencyKey),
      ) as ControllerCommand | undefined;
      if (!receipt || generationRef.current !== generation) return null;
      const nextIntent = { ...intent, commandId: receipt.commandId };
      writeControllerCommandIntent(nextIntent);
      applyState({
        operationState: controllerStateForReceipt(receipt),
        intent: nextIntent,
        receipt,
        error: null,
      });
      return receipt;
    } catch (error) {
      if (generationRef.current !== generation) return null;
      const notFound = error instanceof WorkbenchActionApiError && error.status === 404;
      const responseUnknown = error instanceof WorkbenchActionApiError && error.responseUnknown;
      const rateLimited = error instanceof ApiError && error.status === 429;
      const serverResponseUnknown = error instanceof ApiError && error.status >= 500;
      const coordinatorLimited = typeof error === "object"
        && error !== null
        && "code" in error
        && (error as { code?: unknown }).code === "rate_limited";
      const unresolved = notFound || responseUnknown || rateLimited || serverResponseUnknown || coordinatorLimited;
      applyState({
        operationState: unresolved ? "response_unknown" : "failed",
        intent,
        receipt: null,
        error: unresolved
          ? "提交结果尚未确认，正在按当前任务回执继续核对。"
          : controllerFailureMessage(error),
      });
      return null;
    }
  }, [applyState, taskId]);

  const reconcile = useCallback(async (): Promise<ControllerCommand | null> => {
    const current = stateRef.current;
    const intent = readControllerCommandIntent(taskId) ?? current.intent;
    if (!enabled || !intent || inFlightRef.current) return current.receipt;
    const generation = generationRef.current;
    inFlightRef.current = true;
    applyState({
      ...current,
      intent,
      operationState: current.operationState === "submitting" ? "submitting" : "pending",
      error: null,
    });
    try {
      return await readAuthority(intent, generation);
    } finally {
      if (generationRef.current === generation) inFlightRef.current = false;
    }
  }, [applyState, enabled, readAuthority, taskId]);

  const submit = useCallback(async (text: string): Promise<ControllerCommand | null> => {
    const normalized = text.trim();
    if (!enabled || !normalized || inFlightRef.current) return null;
    const current = stateRef.current;
    const persisted = readControllerCommandIntent(taskId);
    if (persisted) {
      const matchingTerminalReceipt = current.intent?.idempotencyKey === persisted.idempotencyKey
        && current.receipt?.commandId === persisted.commandId
        && current.receipt.state !== "received";
      if (!matchingTerminalReceipt) {
        const recovered = await reconcile();
        if (!recovered || recovered.state === "received") return recovered;
      }
    }
    if (revision == null) {
      applyState({
        operationState: "failed",
        intent: null,
        receipt: null,
        error: "当前任务版本尚未确认，请刷新后再提交。",
      });
      return null;
    }
    const generation = generationRef.current;
    const intent: PersistedControllerCommandIntent = {
      taskId,
      text: normalized,
      idempotencyKey: crypto.randomUUID(),
      commandId: null,
    };
    writeControllerCommandIntent(intent);
    inFlightRef.current = true;
    applyState({ operationState: "submitting", intent, receipt: null, error: null });
    try {
      const coordinator = getWorkbenchTaskRequestCoordinator("workbench-page", "canonical", taskId);
      const receipt = await coordinator.request(
        "action:controller-supplement",
        () => submitControllerSupplement(taskId, normalized, intent.idempotencyKey, revision),
      ) as ControllerCommand | undefined;
      if (!receipt || generationRef.current !== generation) return null;
      const nextIntent = { ...intent, commandId: receipt.commandId };
      writeControllerCommandIntent(nextIntent);
      applyState({
        operationState: controllerStateForReceipt(receipt),
        intent: nextIntent,
        receipt,
        error: null,
      });
      return receipt.state === "received" ? readAuthority(nextIntent, generation) : receipt;
    } catch (error) {
      if (generationRef.current !== generation) return null;
      if (responseMayBeUnknown(error)) {
        applyState({
          operationState: "response_unknown",
          intent,
          receipt: null,
          error: "提交结果尚未确认，正在使用原提交标识核对服务端。",
        });
        return readAuthority(intent, generation);
      }
      clearControllerCommandIntent(taskId);
      applyState({
        operationState: "failed",
        intent: null,
        receipt: null,
        error: controllerFailureMessage(error),
      });
      return null;
    } finally {
      if (generationRef.current === generation) inFlightRef.current = false;
    }
  }, [applyState, enabled, readAuthority, reconcile, revision, taskId]);

  useEffect(() => {
    generationRef.current += 1;
    inFlightRef.current = false;
    const intent = enabled ? readControllerCommandIntent(taskId) : null;
    applyState(intent
      ? { operationState: "pending", intent, receipt: null, error: null }
      : EMPTY_CONTROLLER_STATE);
  }, [applyState, enabled, taskId]);

  useEffect(() => {
    if (!enabled || !state.intent) return;
    void reconcile();
  }, [enabled, reconcile, state.intent?.commandId, state.intent?.idempotencyKey]);

  useEffect(() => {
    if (!enabled || (state.operationState !== "pending" && state.operationState !== "response_unknown")) return;
    let disposed = false;
    let timer: number | null = null;
    const schedule = () => {
      if (disposed || document.visibilityState === "hidden") return;
      const coordinator = getWorkbenchTaskRequestCoordinator("workbench-page", "canonical", taskId);
      const delay = Math.max(3_000, coordinator.backoffUntil - Date.now());
      timer = window.setTimeout(async () => {
        await reconcile();
        schedule();
      }, delay);
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") {
        if (timer !== null) window.clearTimeout(timer);
        timer = null;
        return;
      }
      if (timer === null) {
        void reconcile();
        schedule();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    schedule();
    return () => {
      disposed = true;
      if (timer !== null) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [enabled, reconcile, state.operationState, taskId]);

  const visibleState = stateRef.current;
  return useMemo(() => ({
    ...visibleState,
    busy: controllerStateIsBusy(visibleState.operationState),
    submit,
    reconcile,
  }), [reconcile, state, submit, visibleState]);
}

export function useWorkbenchUserActions({
  taskId,
  enabled,
  revision,
  onAuthoritativeRefresh,
}: UseWorkbenchUserActionsOptions): WorkbenchUserActionsController {
  const [snapshot, setSnapshot] = useState<WorkbenchUserActionsSnapshot | null>(null);
  const [loadingTaskId, setLoadingTaskId] = useState<string | null>(null);
  const [errorState, setErrorState] = useState<{ taskId: string; message: string } | null>(null);
  const [operations, setOperations] = useState<Record<string, WorkbenchActionState>>({});
  const [operationsTaskId, setOperationsTaskId] = useState(taskId);
  const generationRef = useRef(0);
  const inFlightRef = useRef(new Set<string>());
  const pollingRef = useRef(false);
  const renderTaskIdRef = useRef(taskId);
  if (renderTaskIdRef.current !== taskId) {
    renderTaskIdRef.current = taskId;
    generationRef.current += 1;
    inFlightRef.current.clear();
    pollingRef.current = false;
  }
  const renderRevisionRef = useRef(revision);
  renderRevisionRef.current = revision;
  const currentSnapshot = currentWorkbenchActionSnapshot(snapshot, taskId, revision) as WorkbenchUserActionsSnapshot | null;
  const operationsForTask = operationsTaskId === taskId ? operations : {};
  const isCurrentGeneration = useCallback((boundTaskId: string, generation: number) => (
    renderTaskIdRef.current === boundTaskId && generationRef.current === generation
  ), []);
  const controller = useCoordinatedControllerCommandReceipts(
    taskId,
    enabled,
    revision,
  );

  const refresh = useCallback(async (): Promise<WorkbenchUserActionsSnapshot | null> => {
    if (!enabled || !taskId) return null;
    const generation = generationRef.current;
    setLoadingTaskId(taskId);
    try {
      const coordinator = getWorkbenchTaskRequestCoordinator("workbench-page", "canonical", taskId);
      const next = await coordinator.request(
        "user-actions",
        () => fetchWorkbenchUserActions(taskId),
      ) as WorkbenchUserActionsSnapshot | undefined;
      if (!next || generationRef.current !== generation) return null;
      setSnapshot(next);
      setErrorState(null);
      return next;
    } catch (requestError) {
      if (generationRef.current !== generation) return null;
      setSnapshot(null);
      setErrorState({ taskId, message: workbenchActionFailureMessage(requestError, "暂时无法读取可执行操作。") });
      return null;
    } finally {
      if (generationRef.current === generation) setLoadingTaskId(null);
    }
  }, [enabled, taskId]);

  const refreshAll = useCallback(async (): Promise<WorkbenchUserActionsSnapshot | null> => {
    await onAuthoritativeRefresh?.();
    return refresh();
  }, [onAuthoritativeRefresh, refresh]);

  const setOperation = useCallback((key: string, state: WorkbenchActionOperationState, operationError: string | null = null) => {
    setOperations((current) => ({ ...current, [key]: { state, error: operationError } }));
  }, []);

  const setOperationForGeneration = useCallback((
    boundTaskId: string,
    generation: number,
    key: string,
    state: WorkbenchActionOperationState,
    operationError: string | null = null,
  ) => {
    if (!isCurrentGeneration(boundTaskId, generation)) return false;
    setOperation(key, state, operationError);
    return true;
  }, [isCurrentGeneration, setOperation]);

  const explicitRetry = useCallback(async (item: WorkbenchUserActionItem, confirmed: boolean): Promise<void> => {
    const boundTaskId = taskId;
    const generation = generationRef.current;
    const authorizedItem = currentWorkbenchActionItem(currentSnapshot, boundTaskId, renderRevisionRef.current, item, "explicit_retry") as WorkbenchUserActionItem | null;
    const context = authorizedItem?.commandContexts.explicit_retry;
    const currentRevision = currentSnapshot?.revision;
    const key = actionKey(item, "explicit_retry");
    const inFlightKey = `${boundTaskId}\u0000${key}`;
    if (
      !confirmed
      || !context
      || currentSnapshot?.lifecycleKind !== "canonical_v1_2"
      || !item.allowedActions.includes("explicit_retry")
      || currentRevision == null
      || inFlightRef.current.has(inFlightKey)
    ) return;
    inFlightRef.current.add(inFlightKey);
    const intent = readOrCreateIntent(boundTaskId, context.workUnitId, "explicit_retry", currentRevision);
    setOperationForGeneration(boundTaskId, generation, key, "submitting");
    try {
      const coordinator = getWorkbenchTaskRequestCoordinator("workbench-page", "canonical", boundTaskId);
      const receipt = await coordinator.request(
        `action:${key}`,
        () => retryCanonicalExternalResult(context, currentRevision, intent.commandRequestId),
      );
      if (!isCurrentGeneration(boundTaskId, generation)) return;
      setOperationForGeneration(boundTaskId, generation, key, "pending");
      const next = await refreshAll();
      if (!isCurrentGeneration(boundTaskId, generation)) return;
      const stillAllowed = next?.items.some(
        (row) => row.workUnitId === context.workUnitId && row.allowedActions.includes("explicit_retry"),
      );
      setOperationForGeneration(boundTaskId, generation, key, receipt && next && !stillAllowed ? "confirmed" : "pending");
    } catch (requestError) {
      if (!isCurrentGeneration(boundTaskId, generation)) return;
      const unknown = responseMayBeUnknown(requestError);
      setOperationForGeneration(boundTaskId, generation, key, unknown ? "response_unknown" : "failed", workbenchActionFailureMessage(requestError, "重新执行失败。"));
      // A disappearing action is not proof that this request took effect.
      await refreshAll().catch(() => null);
    } finally {
      inFlightRef.current.delete(inFlightKey);
    }
  }, [isCurrentGeneration, refreshAll, revision, setOperationForGeneration, currentSnapshot, taskId]);

  const stop = useCallback(async (): Promise<void> => {
    const boundTaskId = taskId;
    const generation = generationRef.current;
    const currentRevision = currentSnapshot?.revision;
    const key = "task\u0000stop";
    const inFlightKey = `${boundTaskId}\u0000${key}`;
    if (
      currentSnapshot?.lifecycleKind !== "canonical_v1_2"
      || !hasWorkbenchTaskAction(currentSnapshot, boundTaskId, renderRevisionRef.current, "stop")
      || currentRevision == null
      || inFlightRef.current.has(inFlightKey)
    ) return;
    inFlightRef.current.add(inFlightKey);
    setOperationForGeneration(boundTaskId, generation, key, "submitting");
    try {
      const coordinator = getWorkbenchTaskRequestCoordinator("workbench-page", "canonical", boundTaskId);
      const receipt = await coordinator.request(
        `action:${key}`,
        () => requestCanonicalStop(boundTaskId, currentRevision),
      );
      if (!isCurrentGeneration(boundTaskId, generation)) return;
      const next = await refreshAll();
      if (!isCurrentGeneration(boundTaskId, generation)) return;
      const stopCommitted = Boolean(
        receipt
        && "suppressed" in receipt
        && receipt.status === "stopped"
        && next?.executionStopped,
      );
      setOperationForGeneration(boundTaskId, generation, key, stopCommitted ? "confirmed" : "pending");
    } catch (requestError) {
      if (!isCurrentGeneration(boundTaskId, generation)) return;
      setOperationForGeneration(
        boundTaskId,
        generation,
        key,
        responseMayBeUnknown(requestError) ? "response_unknown" : "failed",
        workbenchActionFailureMessage(requestError, "停止失败。"),
      );
      const next = await refreshAll().catch(() => null);
      if (!isCurrentGeneration(boundTaskId, generation)) return;
      if (next?.executionStopped) setOperationForGeneration(boundTaskId, generation, key, "confirmed");
    } finally {
      inFlightRef.current.delete(inFlightKey);
    }
  }, [isCurrentGeneration, refreshAll, revision, setOperationForGeneration, currentSnapshot, taskId]);

  const resume = useCallback(async (): Promise<boolean> => {
    const boundTaskId = taskId;
    const generation = generationRef.current;
    const currentRevision = currentSnapshot?.revision;
    const key = "task\u0000resume";
    const inFlightKey = `${boundTaskId}\u0000${key}`;
    if (
      currentSnapshot?.lifecycleKind !== "canonical_v1_2"
      || !currentSnapshot.executionStopped
      || !hasWorkbenchTaskAction(currentSnapshot, boundTaskId, renderRevisionRef.current, "continue_research")
      || currentRevision == null
      || inFlightRef.current.has(inFlightKey)
    ) return false;
    inFlightRef.current.add(inFlightKey);
    setOperationForGeneration(boundTaskId, generation, key, "submitting");
    try {
      const coordinator = getWorkbenchTaskRequestCoordinator("workbench-page", "canonical", boundTaskId);
      const receipt = await coordinator.request(
        `action:${key}`,
        () => requestCanonicalResume(boundTaskId, currentRevision),
      );
      if (!isCurrentGeneration(boundTaskId, generation)) return false;
      const next = await refreshAll();
      if (!isCurrentGeneration(boundTaskId, generation)) return false;
      const resumeCommitted = Boolean(receipt?.status === "running" && next && !next.executionStopped);
      setOperationForGeneration(boundTaskId, generation, key, resumeCommitted ? "confirmed" : "pending");
      return resumeCommitted;
    } catch (requestError) {
      if (!isCurrentGeneration(boundTaskId, generation)) return false;
      setOperationForGeneration(
        boundTaskId,
        generation,
        key,
        responseMayBeUnknown(requestError) ? "response_unknown" : "failed",
        workbenchActionFailureMessage(requestError, "继续研究失败。"),
      );
      const next = await refreshAll().catch(() => null);
      if (!isCurrentGeneration(boundTaskId, generation)) return false;
      if (next && !next.executionStopped) {
        setOperationForGeneration(boundTaskId, generation, key, "confirmed");
        return true;
      }
      return false;
    } finally {
      inFlightRef.current.delete(inFlightKey);
    }
  }, [isCurrentGeneration, refreshAll, revision, setOperationForGeneration, currentSnapshot, taskId]);

  const pollUnresolvedOperations = useCallback(async (): Promise<void> => {
    if (!enabled || pollingRef.current) return;
    const boundTaskId = taskId;
    const generation = generationRef.current;
    const unresolved = Object.entries(operationsForTask).filter(([, operation]) =>
      operation.state === "pending" || operation.state === "response_unknown",
    );
    if (!unresolved.length) return;
    pollingRef.current = true;
    let authorityChanged = false;
    try {
      const next = await refresh();
      if (!isCurrentGeneration(boundTaskId, generation)) return;
      // Revision changes refresh research facts; absence of an action cannot
      // confirm a lost or rejected recovery response.
      if (next && next.revision !== renderRevisionRef.current) authorityChanged = true;
      const stopOperation = operationsForTask["task\u0000stop"];
      if (stopOperation && (stopOperation.state === "pending" || stopOperation.state === "response_unknown")) {
        if (next?.executionStopped) {
          setOperation("task\u0000stop", "confirmed");
          authorityChanged = true;
        }
      }
      const resumeOperation = operationsForTask["task\u0000resume"];
      if (resumeOperation && (resumeOperation.state === "pending" || resumeOperation.state === "response_unknown")) {
        if (next && !next.executionStopped) {
          setOperation("task\u0000resume", "confirmed");
          authorityChanged = true;
        }
      }
      if (authorityChanged) await onAuthoritativeRefresh?.();
    } finally {
      if (isCurrentGeneration(boundTaskId, generation)) pollingRef.current = false;
    }
  }, [enabled, isCurrentGeneration, onAuthoritativeRefresh, operationsForTask, refresh, setOperation, taskId]);

  useEffect(() => {
    const hasUnresolved = Object.values(operationsForTask).some((operation) =>
      operation.state === "pending" || operation.state === "response_unknown",
    );
    if (!enabled || !hasUnresolved || !taskId) return;
    let disposed = false;
    let timer: number | null = null;
    const coordinator = getWorkbenchTaskRequestCoordinator("workbench-page", "canonical", taskId);
    const clearTimer = () => {
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
    };
    const schedule = () => {
      clearTimer();
      if (disposed || document.visibilityState === "hidden") return;
      const delay = Math.max(3_000, coordinator.backoffUntil - Date.now());
      timer = window.setTimeout(async () => {
        timer = null;
        await pollUnresolvedOperations();
        schedule();
      }, delay);
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") {
        clearTimer();
      } else {
        void pollUnresolvedOperations();
        schedule();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    schedule();
    return () => {
      disposed = true;
      clearTimer();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [enabled, operationsForTask, pollUnresolvedOperations, taskId]);

  useEffect(() => {
    if (!enabled || !taskId) return;
    const coordinator = getWorkbenchTaskRequestCoordinator("workbench-page", "canonical", taskId);
    return coordinator.acquire();
  }, [enabled, taskId]);

  useEffect(() => {
    generationRef.current += 1;
    inFlightRef.current.clear();
    pollingRef.current = false;
    setSnapshot(null);
    setOperations({});
    setOperationsTaskId(taskId);
    setErrorState(null);
    setLoadingTaskId(null);
  }, [enabled, taskId]);

  useEffect(() => {
    if (enabled && taskId) void refresh();
  }, [enabled, refresh, revision, taskId]);

  const submitSupplement = useCallback(async (text: string): Promise<ControllerCommand | null> => {
    // Do not pre-block the researcher. Server accepts or rejects the command.
    return controller.submit(text);
  }, [controller.submit]);

  const visibleSnapshot = currentSnapshot;
  const visibleOperations = operationsForTask;
  const visibleError = errorState?.taskId === taskId ? errorState.message : null;
  const loading = loadingTaskId === taskId;

  return useMemo(() => ({
    snapshot: visibleSnapshot,
    items: visibleSnapshot?.items ?? [],
    lifecycleKind: visibleSnapshot?.lifecycleKind ?? null,
    loading,
    error: visibleError,
    operationState(item: WorkbenchUserActionItem, action: string): WorkbenchActionState {
      return visibleOperations[actionKey(item, action)] ?? { state: "idle", error: null };
    },
    submitSupplement,
    explicitRetry,
    stop,
    resume,
    refresh,
    controller: { ...controller, submit: submitSupplement },
  }), [controller, submitSupplement, explicitRetry, loading, refresh, resume, stop, visibleError, visibleOperations, visibleSnapshot]);
}
