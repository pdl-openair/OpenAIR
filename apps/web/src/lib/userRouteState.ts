const CURRENT_TASK_KEY = "openair.userRoute.currentTaskId";
const CURRENT_ARTIFACT_KEY = "openair.userRoute.currentArtifactId";
const RESEARCH_START_PENDING_KEY = "openair.researchStart.pending.v2";
const LEGACY_RESEARCH_START_PENDING_KEY = "openair.researchStart.pending.v1";
export const RESEARCH_START_PENDING_EVENT = "openair:research-start-pending";

export interface ResearchStartThinkingStep {
  kind: "stage" | "agent";
  key?: string;
  label?: string;
  detail?: string;
  role?: string;
  position?: string;
  text?: string;
}

export interface ResearchStartPendingState {
  status: "pending" | "accepted" | "running" | "needs_clarification" | "needs_action" | "failed";
  startedAt: number;
  input: string;
  clientRequestId: string;
  intentFingerprint: string;
  sourcePage?: "home" | "board" | "starter_plaza" | "sample";
  presetQuery?: string;
  message?: string;
  sessionId?: string;
  /** Reserved by the accepted response for this exact session. */
  reservedTaskId?: string | null;
  /** Legacy alias retained only for reading old browser state. */
  taskId?: string | null;
  missingInfo?: string[];
  suggestedNextAction?: string;
  steps?: ResearchStartThinkingStep[];
}

export interface BeginResearchStartInput {
  input: string;
  sourcePage: "home" | "board" | "starter_plaza" | "sample";
  presetQuery?: string;
  clientRequestId: string;
  startedAt?: number;
}

export function rememberCurrentTask(taskId: string): void {
  try {
    const normalizedTaskId = taskId.trim();
    if (!normalizedTaskId) return;
    const previousTaskId = sessionStorage.getItem(CURRENT_TASK_KEY);
    if (previousTaskId !== normalizedTaskId) {
      sessionStorage.removeItem(CURRENT_ARTIFACT_KEY);
    }
    sessionStorage.setItem(CURRENT_TASK_KEY, normalizedTaskId);
  } catch {
    // Direct task-id routes remain usable when storage is unavailable.
  }
}

export function readCurrentTask(): string | undefined {
  try {
    return sessionStorage.getItem(CURRENT_TASK_KEY) || undefined;
  } catch {
    return undefined;
  }
}

export function rememberCurrentArtifact(artifactId: string | null | undefined): void {
  try {
    if (artifactId) sessionStorage.setItem(CURRENT_ARTIFACT_KEY, artifactId);
    else sessionStorage.removeItem(CURRENT_ARTIFACT_KEY);
  } catch {
    // Ignore storage failures.
  }
}

export function readCurrentArtifact(): string | undefined {
  try {
    return sessionStorage.getItem(CURRENT_ARTIFACT_KEY) || undefined;
  } catch {
    return undefined;
  }
}

function emitResearchStartPendingChanged(): void {
  try {
    window.dispatchEvent(new CustomEvent(RESEARCH_START_PENDING_EVENT));
  } catch {
    // Ignore environments without DOM events.
  }
}

/** Stable, non-secret identity used only to decide whether a browser retry is the same user intent. */
export function researchStartIntentFingerprint(
  input: string,
  sourcePage: BeginResearchStartInput["sourcePage"],
  presetQuery?: string,
): string {
  const normalized = `${sourcePage}\n${presetQuery?.trim() ?? ""}\n${input.trim().replace(/\s+/g, " ")}`;
  let hash = 0x811c9dc5;
  for (let index = 0; index < normalized.length; index += 1) {
    hash ^= normalized.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return `v1-${(hash >>> 0).toString(16).padStart(8, "0")}`;
}

export function rememberResearchStartPending(state: ResearchStartPendingState): void {
  try {
    sessionStorage.setItem(RESEARCH_START_PENDING_KEY, JSON.stringify(state));
    sessionStorage.removeItem(LEGACY_RESEARCH_START_PENDING_KEY);
    emitResearchStartPendingChanged();
  } catch {
    // The request still proceeds; response identity remains server-enforced.
  }
}

/**
 * Only in-flight starts may reuse clientRequestId for idempotent retry.
 * `accepted` must mint a new key — otherwise delete-then-restart with the same
 * query reuses a key already reserved with a different durable fingerprint
 * (e.g. after sessionId was written on accept), which surfaces as
 * fingerprint_conflict / "client request key is already reserved".
 */
export function canReuseResearchStartClientRequest(
  status: ResearchStartPendingState["status"],
): boolean {
  return status === "pending"
    || status === "running"
    || status === "needs_clarification"
    || status === "needs_action";
}

export function beginResearchStartPending(input: BeginResearchStartInput): ResearchStartPendingState {
  const state: ResearchStartPendingState = {
    status: "pending",
    startedAt: input.startedAt ?? Date.now(),
    input: input.input,
    sourcePage: input.sourcePage,
    presetQuery: input.presetQuery,
    clientRequestId: input.clientRequestId,
    intentFingerprint: researchStartIntentFingerprint(input.input, input.sourcePage, input.presetQuery),
    reservedTaskId: null,
    taskId: null,
  };
  rememberResearchStartPending(state);
  return state;
}

export function rememberResearchStartAccepted(input: {
  clientRequestId: string;
  sessionId: string;
  reservedTaskId: string;
}): ResearchStartPendingState | null {
  const current = readResearchStartPending();
  if (!current || current.clientRequestId !== input.clientRequestId) return null;
  const next: ResearchStartPendingState = {
    ...current,
    status: "accepted",
    sessionId: input.sessionId,
    reservedTaskId: input.reservedTaskId,
    taskId: input.reservedTaskId,
  };
  rememberResearchStartPending(next);
  return next;
}

export function readResearchStartPending(): ResearchStartPendingState | null {
  try {
    const raw = sessionStorage.getItem(RESEARCH_START_PENDING_KEY)
      ?? sessionStorage.getItem(LEGACY_RESEARCH_START_PENDING_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<ResearchStartPendingState>;
    if (
      parsed.status !== "pending"
      && parsed.status !== "accepted"
      && parsed.status !== "running"
      && parsed.status !== "needs_clarification"
      && parsed.status !== "needs_action"
      && parsed.status !== "failed"
    ) return null;
    if (typeof parsed.startedAt !== "number" || typeof parsed.input !== "string") return null;

    const sourcePage = parsed.sourcePage === "board"
      || parsed.sourcePage === "starter_plaza"
      || parsed.sourcePage === "sample"
      ? parsed.sourcePage
      : "home";
    const clientRequestId = typeof parsed.clientRequestId === "string" && parsed.clientRequestId.trim()
      ? parsed.clientRequestId
      : "";
    const intentFingerprint = typeof parsed.intentFingerprint === "string" && parsed.intentFingerprint.trim()
      ? parsed.intentFingerprint
      : researchStartIntentFingerprint(parsed.input, sourcePage, parsed.presetQuery);
    const reservedTaskId = typeof parsed.reservedTaskId === "string"
      ? parsed.reservedTaskId
      : typeof parsed.taskId === "string" ? parsed.taskId : null;

    return {
      status: parsed.status,
      startedAt: parsed.startedAt,
      input: parsed.input,
      clientRequestId,
      intentFingerprint,
      sourcePage,
      presetQuery: typeof parsed.presetQuery === "string" ? parsed.presetQuery : undefined,
      message: typeof parsed.message === "string" ? parsed.message : undefined,
      sessionId: typeof parsed.sessionId === "string" ? parsed.sessionId : undefined,
      reservedTaskId,
      taskId: reservedTaskId,
      missingInfo: Array.isArray(parsed.missingInfo)
        ? parsed.missingInfo.filter((item): item is string => typeof item === "string")
        : undefined,
      suggestedNextAction: typeof parsed.suggestedNextAction === "string" ? parsed.suggestedNextAction : undefined,
      steps: Array.isArray(parsed.steps)
        ? parsed.steps.filter(
            (item): item is ResearchStartThinkingStep =>
              !!item && typeof item === "object" && (item.kind === "stage" || item.kind === "agent"),
          )
        : undefined,
    };
  } catch {
    return null;
  }
}

export function readResearchStartPendingForSession(sessionId: string | undefined): ResearchStartPendingState | null {
  if (!sessionId) return null;
  const pending = readResearchStartPending();
  return pending?.sessionId === sessionId ? pending : null;
}

export function clearResearchStartPending(): void {
  try {
    sessionStorage.removeItem(RESEARCH_START_PENDING_KEY);
    sessionStorage.removeItem(LEGACY_RESEARCH_START_PENDING_KEY);
    emitResearchStartPendingChanged();
  } catch {
    // Ignore storage failures.
  }
}

/** Drop pending start identity when its reserved task is deleted. */
export function clearResearchStartPendingForTask(taskId: string): void {
  const pending = readResearchStartPending();
  const reserved = pending?.reservedTaskId ?? pending?.taskId;
  if (reserved && reserved === taskId) clearResearchStartPending();
}
