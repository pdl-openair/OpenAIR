import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ControllerCommand } from "../api/client";
import {
  WorkbenchActionApiError,
  discoverControllerCommandReceipt,
  fetchControllerCommandReceipt,
  submitSupplementCommand,
  type WorkbenchActionOperationState,
} from "../api/workbenchUserActions";

export interface PersistedControllerCommandIntent {
  taskId: string;
  text: string;
  idempotencyKey: string;
  commandId: string | null;
}

export interface ControllerCommandReceiptState {
  operationState: WorkbenchActionOperationState;
  intent: PersistedControllerCommandIntent | null;
  receipt: ControllerCommand | null;
  error: string | null;
}

const EMPTY_STATE: ControllerCommandReceiptState = {
  operationState: "idle",
  intent: null,
  receipt: null,
  error: null,
};

function storageKey(taskId: string): string {
  return `openair.controller-command-intent.v2:${encodeURIComponent(taskId)}`;
}

function storage(): Storage | null {
  return typeof window === "undefined" ? null : window.localStorage;
}

function validIntent(value: unknown, taskId: string): value is PersistedControllerCommandIntent {
  if (!value || typeof value !== "object") return false;
  const row = value as Partial<PersistedControllerCommandIntent>;
  return row.taskId === taskId
    && typeof row.text === "string"
    && row.text.trim().length > 0
    && typeof row.idempotencyKey === "string"
    && row.idempotencyKey.trim().length > 0
    && (row.commandId === null || typeof row.commandId === "string");
}

export function readControllerCommandIntent(taskId: string): PersistedControllerCommandIntent | null {
  try {
    const raw = storage()?.getItem(storageKey(taskId));
    if (!raw) return null;
    const value: unknown = JSON.parse(raw);
    return validIntent(value, taskId) ? value : null;
  } catch {
    return null;
  }
}

export function writeControllerCommandIntent(intent: PersistedControllerCommandIntent): void {
  try {
    storage()?.setItem(storageKey(intent.taskId), JSON.stringify(intent));
  } catch {
    // The request remains safe in memory when persistent browser storage is unavailable.
  }
}

export function clearControllerCommandIntent(taskId: string): void {
  try {
    storage()?.removeItem(storageKey(taskId));
  } catch {
    // A known failure is still terminal in memory when storage is unavailable.
  }
}

function stateForReceipt(receipt: ControllerCommand): WorkbenchActionOperationState {
  if (receipt.state === "received") return "pending";
  if (receipt.state === "applied") return "confirmed";
  if (receipt.state === "rejected") return "rejected";
  return "failed";
}

function isUnresolved(state: WorkbenchActionOperationState): boolean {
  return state === "submitting" || state === "response_unknown" || state === "pending";
}

export interface UseControllerCommandReceiptsResult extends ControllerCommandReceiptState {
  busy: boolean;
  submit(text: string): Promise<ControllerCommand | null>;
  reconcile(): Promise<ControllerCommand | null>;
}

export function useControllerCommandReceipts(
  taskId: string,
  enabled: boolean,
): UseControllerCommandReceiptsResult {
  const [state, setState] = useState<ControllerCommandReceiptState>(EMPTY_STATE);
  const stateRef = useRef<ControllerCommandReceiptState>(EMPTY_STATE);
  const generationRef = useRef(0);
  const inFlightRef = useRef(false);

  const applyState = useCallback((next: ControllerCommandReceiptState) => {
    stateRef.current = next;
    setState(next);
  }, []);

  const readAuthority = useCallback(async (
    intent: PersistedControllerCommandIntent,
    generation: number,
  ): Promise<ControllerCommand | null> => {
    try {
      const receipt = intent.commandId
        ? await fetchControllerCommandReceipt(taskId, intent.commandId)
        : await discoverControllerCommandReceipt(taskId, intent.idempotencyKey);
      if (generationRef.current !== generation) return null;
      const nextIntent = { ...intent, commandId: receipt.commandId };
      writeControllerCommandIntent(nextIntent);
      applyState({ operationState: stateForReceipt(receipt), intent: nextIntent, receipt, error: null });
      return receipt;
    } catch (error) {
      if (generationRef.current !== generation) return null;
      const unknown = error instanceof WorkbenchActionApiError
        && (error.responseUnknown || error.status === 404);
      applyState({
        operationState: unknown ? "response_unknown" : "failed",
        intent,
        receipt: null,
        error: unknown ? "提交结果尚未确认，请继续从服务端核对。" : "暂时无法读取服务端回执。",
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
      const receipt = await submitSupplementCommand(taskId, normalized, intent.idempotencyKey);
      if (generationRef.current !== generation) return null;
      const nextIntent = { ...intent, commandId: receipt.commandId };
      writeControllerCommandIntent(nextIntent);
      applyState({ operationState: "pending", intent: nextIntent, receipt, error: null });
      return await readAuthority(nextIntent, generation);
    } catch (error) {
      if (generationRef.current !== generation) return null;
      const responseUnknown = error instanceof WorkbenchActionApiError && error.responseUnknown;
      if (!responseUnknown) {
        clearControllerCommandIntent(taskId);
        applyState({
          operationState: "failed",
          intent: null,
          receipt: null,
          error: error instanceof Error ? error.message : "补充信息未提交。",
        });
        return null;
      }
      applyState({
        operationState: "response_unknown",
        intent,
        receipt: null,
        error: "提交响应未送达，正在使用原幂等键核对服务端。",
      });
      return await readAuthority(intent, generation);
    } finally {
      if (generationRef.current === generation) inFlightRef.current = false;
    }
  }, [applyState, enabled, readAuthority, reconcile, taskId]);

  useEffect(() => {
    generationRef.current += 1;
    inFlightRef.current = false;
    const intent = enabled ? readControllerCommandIntent(taskId) : null;
    applyState(intent
      ? { operationState: "pending", intent, receipt: null, error: null }
      : EMPTY_STATE);
  }, [applyState, enabled, taskId]);

  useEffect(() => {
    if (!enabled || !state.intent) return;
    void reconcile();
  }, [enabled, reconcile, state.intent?.commandId, state.intent?.idempotencyKey]);

  useEffect(() => {
    if (!enabled || (state.operationState !== "pending" && state.operationState !== "response_unknown")) return;
    const timer = window.setInterval(() => void reconcile(), 1500);
    return () => window.clearInterval(timer);
  }, [enabled, reconcile, state.operationState]);

  return useMemo(() => ({
    ...state,
    busy: isUnresolved(state.operationState),
    submit,
    reconcile,
  }), [reconcile, state, submit]);
}
