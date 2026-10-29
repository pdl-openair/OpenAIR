export type TaskMessageAction = "message" | "retry" | "replan" | "confirm";

export interface TaskMessageIdempotencyManager {
  getOrCreate(taskId: string, action: TaskMessageAction, operationScope?: string): string;
  forgetTask(taskId: string): void;
}

export interface TaskMessageIdempotencyOptions {
  createToken?: () => string;
}

export interface PendingAuthoritativeRecoveryRefresh {
  idempotencyKey: string;
  state: "same_key_replay_pending_authoritative_refresh";
  authoritativeRefreshRequired: true;
}

export interface RecoveryRequestCacheEntry {
  taskId: string;
  expectedTaskRevision: number;
  sourceRunId: string;
  recoveryRequestId: string | null;
  request: {
    sourceRunId: string;
    recoveryKind: "retry" | "replan";
    expectedTaskRevision: number;
    messageIds: string[];
    idempotencyKey: string;
    planFingerprint?: string;
  };
}

function recoveryStorageKey(taskId: string, expectedTaskRevision: number, sourceRunId: string): string {
  return `openair.recovery-request.v1:${encodeURIComponent(taskId)}:${expectedTaskRevision}:${encodeURIComponent(sourceRunId)}`;
}

function controllerMessageStorageKey(taskId: string, expectedTaskRevision: number, sourceRunId: string): string {
  return `openair.recovery-controller-message.v1:${encodeURIComponent(taskId)}:${expectedTaskRevision}:${encodeURIComponent(sourceRunId)}`;
}

function storage(): Storage | null {
  return typeof window !== "undefined" ? window.localStorage : null;
}

function validCacheEntry(value: unknown, taskId: string, expectedTaskRevision: number, sourceRunId: string): value is RecoveryRequestCacheEntry {
  if (!value || typeof value !== "object") return false;
  const entry = value as Partial<RecoveryRequestCacheEntry>;
  const request = entry.request;
  if (!request) return false;
  return entry.taskId === taskId
    && entry.expectedTaskRevision === expectedTaskRevision
    && entry.sourceRunId === sourceRunId
    && (entry.recoveryRequestId === null || typeof entry.recoveryRequestId === "string")
    && request.sourceRunId === sourceRunId
    && request.expectedTaskRevision === expectedTaskRevision
    && (request.recoveryKind === "retry" || request.recoveryKind === "replan")
    && Array.isArray(request.messageIds)
    && request.messageIds.length > 0
    && request.messageIds.every((id) => typeof id === "string" && id.trim().length > 0)
    && typeof request.idempotencyKey === "string"
    && request.idempotencyKey.trim().length > 0;
}

export function readRecoveryRequestCache(
  taskId: string,
  expectedTaskRevision: number,
  sourceRunId: string,
): RecoveryRequestCacheEntry | null {
  try {
    const raw = storage()?.getItem(recoveryStorageKey(taskId, expectedTaskRevision, sourceRunId));
    if (!raw) return null;
    const value: unknown = JSON.parse(raw);
    return validCacheEntry(value, taskId, expectedTaskRevision, sourceRunId) ? value : null;
  } catch {
    return null;
  }
}

export function writeRecoveryRequestCache(entry: RecoveryRequestCacheEntry): void {
  try {
    storage()?.setItem(
      recoveryStorageKey(entry.taskId, entry.expectedTaskRevision, entry.sourceRunId),
      JSON.stringify(entry),
    );
  } catch {
    // The recovery flow remains usable during a storage-disabled browser session.
  }
}

export function rememberRecoveryControllerMessage(
  taskId: string,
  expectedTaskRevision: number,
  sourceRunId: string,
  commandId: string,
): void {
  if (!commandId.trim()) return;
  try {
    const key = controllerMessageStorageKey(taskId, expectedTaskRevision, sourceRunId);
    const existing = readRecoveryControllerMessages(taskId, expectedTaskRevision, sourceRunId);
    storage()?.setItem(key, JSON.stringify(Array.from(new Set([...existing, commandId]))));
  } catch {
    // No fabricated message id is used when browser storage is unavailable.
  }
}

export function readRecoveryControllerMessages(
  taskId: string,
  expectedTaskRevision: number,
  sourceRunId: string,
): string[] {
  try {
    const raw = storage()?.getItem(controllerMessageStorageKey(taskId, expectedTaskRevision, sourceRunId));
    const value: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(value)
      ? Array.from(new Set(value.filter((id): id is string => typeof id === "string" && id.trim().length > 0)))
      : [];
  } catch {
    return [];
  }
}

function defaultToken(): string {
  const randomPart = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID().replaceAll("-", "")
    : Math.random().toString(36).slice(2);
  return randomPart || "local";
}

function validTaskId(taskId: string): boolean {
  return taskId.trim().length > 0;
}

/**
 * Keys are opaque and scoped by the caller's task id inside this manager. They
 * never accept message text, errors, or payload data, so those values cannot
 * become part of a key.
 */
export function createTaskMessageIdempotencyManager(
  options: TaskMessageIdempotencyOptions = {},
): TaskMessageIdempotencyManager {
  const createToken = options.createToken ?? defaultToken;
  const taskNamespaces = new Map<string, string>();
  const keys = new Map<string, string>();

  return {
    getOrCreate(taskId, action, operationScope = "default") {
      if (!validTaskId(taskId)) {
        throw new Error("生成幂等键需要有效的任务标识。");
      }
      const namespace = taskNamespaces.get(taskId) ?? `task-${createToken()}`;
      taskNamespaces.set(taskId, namespace);
      const lookup = `${taskId}\u0000${action}\u0000${operationScope}`;
      const existing = keys.get(lookup);
      if (existing) return existing;

      const key = `openair-${namespace}-${action}-${createToken()}`;
      keys.set(lookup, key);
      return key;
    },
    forgetTask(taskId) {
      taskNamespaces.delete(taskId);
      for (const key of keys.keys()) {
        if (key.startsWith(`${taskId}\u0000`)) keys.delete(key);
      }
    },
  };
}

/**
 * Records only the future replay/read state shape. It does not send, replay,
 * or read anything, and accepts no message body or other sensitive payload.
 */
export function preparePendingAuthoritativeRecoveryRefresh(
  manager: TaskMessageIdempotencyManager,
  taskId: string,
  action: TaskMessageAction,
): PendingAuthoritativeRecoveryRefresh {
  return {
    idempotencyKey: manager.getOrCreate(taskId, action),
    state: "same_key_replay_pending_authoritative_refresh",
    authoritativeRefreshRequired: true,
  };
}
