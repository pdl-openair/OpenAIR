import type {
  Artifact,
  ArtifactEvidenceRef,
  ArtifactVersion,
  ArtifactVersionSummary,
  MaterialManifest,
  CreationStatus,
  ResultSource,
  ResultProtocol,
  StageCatalog,
  StageStatus,
  SubStage,
  Task,
  TaskExecutionMode,
  TaskPlan,
  TaskPlanUpdate,
  TaskStage,
  TaskStatus,
} from "@openair/shared";
import type { ReleaseGatedStarter } from "@openair/shared";
import { ApiError, apiErrorFromResponse, buildAuthHeaders, getApiBaseUrl, parseJson, parseVoid } from "./http";

import { fetchTaskListPage } from "./taskList";
export { fetchTaskListPage, type TaskListPage } from "./taskList";
export { getApiBaseUrl };

export type ControllerCommand = {
  commandId: string;
  messageId?: string | null;
  taskId: string;
  kind: "supplement_input";
  state: "received" | "applied" | "rejected" | "failed";
  userMessage: string;
  submittedTaskRevision: number;
  appliedExecutionDecisionId: string | null;
  appliedWorkUnitId: string | null;
  terminalReasonCode: string | null;
  submittedAt: string;
  updatedAt: string;
};

export async function submitControllerSupplement(
  taskId: string,
  text: string,
  idempotencyKey: string,
  expectedTaskRevision: number,
): Promise<ControllerCommand> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/controller-commands`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json", "Idempotency-Key": idempotencyKey }),
    body: JSON.stringify({ kind: "supplement_input", text, expectedTaskRevision }),
  });
  return parseJson<ControllerCommand>(res);
}

export type RecoveryKind = "retry" | "replan";
export type RecoveryRequestState = "requested" | "confirmed" | "cancelled";
export type RecoveryErrorCode =
  | "recovery_not_found"
  | "recovery_stale"
  | "recovery_mutex_conflict"
  | "recovery_terminal"
  | "recovery_preflight_failed"
  | "recovery_successor_exists"
  | "idempotency_payload_conflict"
  | "recovery_requires_postgres"
  | "legacy_recovery_read_only";

export interface RecoveryPreservedFacts {
  sourceRunState: string;
  sourceRunNumber: number;
  preservedArtifactCount: number;
}

export interface RecoveryRequestResponse {
  recoveryRequestId: string;
  taskId: string;
  sourceRunId: string;
  recoveryKind: RecoveryKind;
  state: RecoveryRequestState;
  expectedTaskRevision: number;
  taskRevision: number;
  successorRunId: string | null;
  replayed: boolean;
  allowedActions: Array<"confirm">;
  preservedFacts: RecoveryPreservedFacts;
}

export class RecoveryApiError extends Error {
  readonly status: number | null;
  readonly code: RecoveryErrorCode | null;
  readonly responseUnknown: boolean;

  constructor(message: string, options: { status?: number | null; code?: RecoveryErrorCode | null; responseUnknown?: boolean } = {}) {
    super(message);
    this.name = "RecoveryApiError";
    this.status = options.status ?? null;
    this.code = options.code ?? null;
    this.responseUnknown = options.responseUnknown ?? false;
  }
}

function isRecoveryErrorCode(value: unknown): value is RecoveryErrorCode {
  return value === "recovery_not_found"
    || value === "recovery_stale"
    || value === "recovery_mutex_conflict"
    || value === "recovery_terminal"
    || value === "recovery_preflight_failed"
    || value === "recovery_successor_exists"
    || value === "idempotency_payload_conflict"
    || value === "recovery_requires_postgres"
    || value === "legacy_recovery_read_only";
}

function recoveryErrorFromResponse(status: number, text: string): RecoveryApiError {
  let code: RecoveryErrorCode | null = null;
  try {
    const body: unknown = JSON.parse(text);
    if (isRecord(body) && isRecord(body.detail) && isRecoveryErrorCode(body.detail.code)) {
      code = body.detail.code;
    }
  } catch {
    // A non-JSON error body cannot be promoted into a recovery success state.
  }
  return new RecoveryApiError(text || `HTTP ${status}`, { status, code });
}

async function parseRecoveryResponse(res: Response): Promise<RecoveryRequestResponse> {
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw recoveryErrorFromResponse(res.status, text);
  }
  try {
    return await res.json() as RecoveryRequestResponse;
  } catch {
    throw new RecoveryApiError("Recovery response is missing.", { responseUnknown: true });
  }
}

async function recoveryFetch(url: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch {
    throw new RecoveryApiError("Recovery response is unavailable.", { responseUnknown: true });
  }
}

export async function fetchRecoveryRequest(taskId: string, recoveryRequestId: string): Promise<RecoveryRequestResponse> {
  const res = await recoveryFetch(
    `${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/recovery-requests/${encodeURIComponent(recoveryRequestId)}`,
    { headers: buildAuthHeaders() },
  );
  return parseRecoveryResponse(res);
}

export async function discoverRecoveryRequest(
  taskId: string,
  sourceRunId: string,
  expectedTaskRevision: number,
): Promise<RecoveryRequestResponse> {
  const params = new URLSearchParams({
    sourceRunId,
    expectedTaskRevision: String(expectedTaskRevision),
  });
  const res = await recoveryFetch(
    `${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/recovery-requests?${params.toString()}`,
    { headers: buildAuthHeaders() },
  );
  return parseRecoveryResponse(res);
}

export interface RuntimeVersion {
  startedAt?: string | null;
  buildTime?: string | null;
  gitCommit?: string | null;
  gitCommitShort?: string | null;
  gitBranch?: string | null;
}

export interface HealthResponse {
  status: string;
  runtimeVersion?: RuntimeVersion | null;
}

export async function fetchHealth(): Promise<HealthResponse> {
  const res = await fetch(`${getApiBaseUrl()}/health`, {
    headers: buildAuthHeaders(),
  });
  return parseJson<HealthResponse>(res);
}

export async function fetchTasks(opts?: {
  limit?: number;
  offset?: number;
}): Promise<Task[]> {
  const page = await fetchTaskListPage(opts);
  return page.tasks;
}

export type TaskTitleOption = {
  taskId: string;
  title: string;
};

/** Title-only task picker rows (no payload). Used by artifact library. */
export async function fetchTaskTitleOptions(opts?: {
  limit?: number;
  offset?: number;
}): Promise<{ tasks: TaskTitleOption[]; total: number }> {
  const params = new URLSearchParams();
  params.set("limit", String(opts?.limit ?? 50));
  if (opts?.offset != null && opts.offset > 0) params.set("offset", String(opts.offset));
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/options?${params.toString()}`, {
    headers: buildAuthHeaders(),
  });
  const data = await parseJson<unknown>(res);
  if (!isRecord(data) || !Array.isArray(data.tasks)) {
    throw new ApiError(
      "Task title options response contract is invalid.",
      200,
      "response_contract_invalid",
      data,
    );
  }
  return {
    tasks: data.tasks
      .filter(isRecord)
      .map((row) => ({
        taskId: String(row.taskId ?? ""),
        title: String(row.title ?? "").trim() || "未命名项目",
      }))
      .filter((row) => row.taskId),
    total: typeof data.total === "number" ? data.total : 0,
  };
}

export async function fetchTask(taskId: string): Promise<Task | null> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}`, {
    headers: buildAuthHeaders(),
  });
  if (res.status === 404) {
    // Discard body — never surface protected task detail on a miss.
    await res.text().catch(() => "");
    return null;
  }
  return parseJson<Task>(res);
}

export async function fetchTaskTokenUsage(taskId: string): Promise<{ totalTokens: number }> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/token-usage`, {
    headers: buildAuthHeaders(),
  });
  return parseJson<{ totalTokens: number }>(res);
}

/** Live work-unit execution progress (transient workspace projection, not an artifact). */
export type WorkUnitProgress = {
  exists: boolean;
  content: string | null;
  updatedAt: string | null;
};

export async function fetchWorkUnitProgress(
  taskId: string,
  workUnitId: string,
  signal?: AbortSignal,
): Promise<WorkUnitProgress | null> {
  const res = await fetch(
    `${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/workunits/${encodeURIComponent(workUnitId)}/progress`,
    { headers: buildAuthHeaders(), signal },
  );
  if (res.status === 404) {
    await res.text().catch(() => "");
    return null;
  }
  return parseJson<WorkUnitProgress>(res);
}

/** T07 durable progress projection carried on TaskOut / SSE snapshot payload. */
export type DurableSubstageProgress = {
  substageInstanceId: string;
  instanceRevision: number;
  substageDefinitionId?: string | null;
  stageKey: string | null;
  label: string;
  /** Optional server-authored instance objective; never derived from a plan on the client. */
  objective?: string | null;
  /** Optional durable ordering key. Array order remains the compatibility fallback. */
  sequence?: number | null;
  status: string;
  statusReasonCode?: string | null;
  activeExecutionDecisionId?: string | null;
  workUnitIds: string[];
  artifactRefs: string[];
  affected?: boolean;
};

export type DurableStageProgress = {
  stageKey: string;
  status: string;
  substageInstanceRefs: string[];
  completedCount: number;
  totalCount: number;
  artifactRefs: string[];
  statusReasonCode?: string | null;
};

export type DurableWorkbenchProjection = {
  collaborationNodes: Array<{
    workUnitId?: string | null;
    agentId?: string | null;
    agentName?: string | null;
    title?: string | null;
    objective?: string | null;
    kind?: string | null;
    stageKey?: string | null;
    substageKey?: string | null;
    startedAt?: string | null;
    completedAt?: string | null;
    status?: string | null;
  }>;
  collaborationEvents: Array<{
    interactionId?: string | null;
    interactionType?: string | null;
    sourceWorkUnitId?: string | null;
    targetWorkUnitId?: string | null;
    summary?: string | null;
    createdAt?: string | null;
    verdict?: { decision?: string | null } | null;
  }>;
  toolResourceRecords: Array<{ workUnitId?: string | null; kind?: string | null }>;
  artifactRefs: string[];
};

export type DurableResearchActivity = {
  eventId: string;
  taskId?: string | null;
  sequence: number | null;
  revision?: number | null;
  eventType: string;
  presentationLevel: "primary" | "detail";
  workUnitId: string | null;
  headline: string;
  summary: string | null;
  occurredAt: string | null;
  startedAt?: string | null;
  aggregateCount: number | null;
  activityKey?: string | null;
  phase?: string | null;
  artifactRefs: string[];
  resultRefs: string[];
  scientificStateVersions: number[];
};

export type DurableProgressProjection = {
  revision: number;
  substageProgress: DurableSubstageProgress[];
  stageProgress: DurableStageProgress[];
  taskProgress: {
    status: string;
    activeStageKeys: string[];
    awaitingConfirmationRefs: string[];
  };
  workbenchProjection: DurableWorkbenchProjection;
  researchProcessHistory?: DurableResearchActivity[];
};

export type TaskWithProgressProjection = Task & {
  progressProjection?: DurableProgressProjection | null;
};

export type DurableTaskEventPayload = {
  revision: number;
  runId?: string | null;
  projection: DurableProgressProjection;
};

export type TaskEvent = {
  id: string;
  type:
    | "snapshot"
    | "heartbeat"
    | "task_created"
    | "task_updated"
    | "task_deleted"
    | "artifact_created"
    | "agent_message_appended"
    | "confirmation_resolved"
    | "capability_call_started"
    | "capability_call_updated"
    | "orchestrator_lifecycle"
    | "agent_lifecycle"
    | "skill_lifecycle"
    | "mandatory_review_lifecycle"
    | "handoff_completed"
    | "artifact_persisted"
    | "execution_terminal";
  taskId: string;
  runId?: string | null;
  scope?: {
    taskId: string;
    runId: string | null;
    ownerId: string;
    workspaceId: string;
  };
  revision?: number | null;
  sequence?: number | null;
  updatedAt?: string | null;
  emittedAt?: string | null;
  occurredAt?: string | null;
  payload?: Record<string, unknown>;
};
const TASK_EVENT_TYPES = new Set<TaskEvent["type"]>([
  "snapshot",
  "heartbeat",
  "task_created",
  "task_updated",
  "task_deleted",
  "artifact_created",
  "agent_message_appended",
  "confirmation_resolved",
  "capability_call_started",
  "capability_call_updated",
  "orchestrator_lifecycle",
  "agent_lifecycle",
  "skill_lifecycle",
  "mandatory_review_lifecycle",
  "handoff_completed",
  "artifact_persisted",
  "execution_terminal",
]);

function taskEventType(value: string): TaskEvent["type"] {
  return TASK_EVENT_TYPES.has(value as TaskEvent["type"])
    ? value as TaskEvent["type"]
    : "task_updated";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function optionalText(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function textList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.map(optionalText).filter((item): item is string => item !== null))];
}

function positiveIntegerList(value: unknown): number[] {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.filter((item): item is number => (
    typeof item === "number" && Number.isInteger(item) && item >= 1
  )))];
}

function parseResearchProcessHistory(value: unknown): DurableResearchActivity[] {
  if (!Array.isArray(value)) return [];
  const rows: DurableResearchActivity[] = [];
  for (const item of value) {
    if (!isRecord(item)) continue;
    const eventId = optionalText(item.eventId);
    const eventType = optionalText(item.eventType);
    const headline = optionalText(item.headline);
    if (!eventId || !eventType || !headline) continue;
    rows.push({
      eventId,
      taskId: optionalText(item.taskId),
      sequence: typeof item.sequence === "number" && Number.isInteger(item.sequence)
        ? item.sequence
        : null,
      revision: typeof item.revision === "number" && Number.isInteger(item.revision)
        ? item.revision
        : null,
      eventType,
      presentationLevel: item.presentationLevel === "detail" ? "detail" : "primary",
      workUnitId: optionalText(item.workUnitId),
      headline,
      summary: optionalText(item.summary),
      occurredAt: optionalText(item.occurredAt),
      startedAt: optionalText(item.startedAt),
      aggregateCount: typeof item.aggregateCount === "number" && Number.isInteger(item.aggregateCount)
        ? item.aggregateCount
        : null,
      activityKey: optionalText(item.activityKey),
      phase: optionalText(item.phase),
      artifactRefs: textList(item.artifactRefs),
      resultRefs: textList(item.resultRefs),
      scientificStateVersions: positiveIntegerList(item.scientificStateVersions),
    });
  }
  return rows;
}

function parseCollaborationEvents(value: unknown): DurableWorkbenchProjection["collaborationEvents"] {
  if (!Array.isArray(value)) return [];
  return value.filter(isRecord).map((event) => {
    const verdict = isRecord(event.verdict)
      ? { decision: optionalText(event.verdict.decision) }
      : null;
    return {
      interactionId: optionalText(event.interactionId),
      interactionType: optionalText(event.interactionType),
      sourceWorkUnitId: optionalText(event.sourceWorkUnitId),
      targetWorkUnitId: optionalText(event.targetWorkUnitId),
      summary: optionalText(event.summary),
      createdAt: optionalText(event.createdAt),
      verdict,
    };
  });
}

/** Parse backend progressProjection; never invent fields. Returns null when absent/malformed. */
export function parseDurableProgressProjection(raw: unknown): DurableProgressProjection | null {
  if (!isRecord(raw)) return null;
  if (typeof raw.revision !== "number" || !Number.isFinite(raw.revision)) return null;
  if (!Array.isArray(raw.substageProgress) || !Array.isArray(raw.stageProgress)) return null;
  if (!isRecord(raw.taskProgress)) return null;
  const taskProgress = raw.taskProgress;
  if (typeof taskProgress.status !== "string") return null;
  if (!isRecord(raw.workbenchProjection)) return null;
  const workbench = raw.workbenchProjection;
  return {
    revision: raw.revision,
    substageProgress: raw.substageProgress as DurableSubstageProgress[],
    stageProgress: raw.stageProgress as DurableStageProgress[],
    taskProgress: {
      status: taskProgress.status,
      activeStageKeys: Array.isArray(taskProgress.activeStageKeys)
        ? (taskProgress.activeStageKeys as string[])
        : [],
      awaitingConfirmationRefs: Array.isArray(taskProgress.awaitingConfirmationRefs)
        ? (taskProgress.awaitingConfirmationRefs as string[])
        : [],
    },
    workbenchProjection: {
      collaborationNodes: Array.isArray(workbench.collaborationNodes)
        ? (workbench.collaborationNodes as DurableWorkbenchProjection["collaborationNodes"])
        : [],
      collaborationEvents: parseCollaborationEvents(workbench.collaborationEvents),
      toolResourceRecords: Array.isArray(workbench.toolResourceRecords)
        ? (workbench.toolResourceRecords as DurableWorkbenchProjection["toolResourceRecords"])
        : [],
      artifactRefs: Array.isArray(workbench.artifactRefs) ? (workbench.artifactRefs as string[]) : [],
    },
    researchProcessHistory: parseResearchProcessHistory(raw.researchProcessHistory),
  };
}

/** Accept only same-or-newer durable revision; stale SSE must not overwrite newer UI. */
export function shouldAcceptProgressRevision(appliedRevision: number, incomingRevision: number): boolean {
  return incomingRevision >= appliedRevision;
}

export function readTaskProgressProjection(task: Task | TaskWithProgressProjection): DurableProgressProjection | null {
  return parseDurableProgressProjection((task as TaskWithProgressProjection).progressProjection);
}

/** SSE durable payload is `{ revision, projection }`; legacy opens with `{ reason }`. */
export function readDurableProgressFromTaskEvent(event: TaskEvent): DurableTaskEventPayload | null {
  const payload = event.payload;
  if (!isRecord(payload) || typeof payload.revision !== "number") return null;
  const projection = parseDurableProgressProjection(payload.projection);
  if (!projection) return null;
  if (projection.revision !== payload.revision) return null;
  return { revision: payload.revision, projection };
}

/** True when the projection contains research work a user could inspect or confirm. */
export function projectionHasUserFacingResearchWork(
  projection: DurableProgressProjection,
): boolean {
  if (projection.taskProgress.awaitingConfirmationRefs.length > 0) return true;
  if (projection.substageProgress.length > 0) return true;
  if ((projection.workbenchProjection.artifactRefs ?? []).length > 0) return true;
  return projection.stageProgress.some(
    (stage) =>
      (stage.totalCount ?? 0) > 0
      || (stage.completedCount ?? 0) > 0
      || (stage.substageInstanceRefs?.length ?? 0) > 0,
  );
}

/**
 * Fail-visible user status from a trusted projection.
 * Surfaces running / failed / needs_input / external_result_unknown / stopped explicitly.
 * Empty shells must not pretend there is a result to review or a plan to confirm.
 */
export function durableExecutionDisplayStatus(projection: DurableProgressProjection): string {
  const reasonUnknown = projection.substageProgress.some(
    (row) => row.statusReasonCode === "external_result_unknown",
  );
  if (reasonUnknown) return "external_result_unknown";
  const status = projection.taskProgress.status;
  if (status === "cancelled") return "stopped";
  const hasResearchWork = projectionHasUserFacingResearchWork(projection);
  if (
    (status === "external_result_unknown" || status === "awaiting_confirmation")
    && !hasResearchWork
  ) {
    return "research_design_incomplete";
  }
  if (
    status === "awaiting_confirmation"
    && (projection.taskProgress.awaitingConfirmationRefs.length > 0
      || projection.substageProgress.some((row) => row.status === "needs_input")
      || projection.stageProgress.some((row) => row.status === "needs_input"))
  ) {
    return "needs_input";
  }
  return status;
}

export function durableStageCounts(projection: DurableProgressProjection): { completed: number; total: number } {
  return projection.stageProgress.reduce(
    (acc, stage) => ({
      completed: acc.completed + stage.completedCount,
      total: acc.total + stage.totalCount,
    }),
    { completed: 0, total: 0 },
  );
}

/**
 * After an external/durable action has started, never display not_started.
 * Prefer explicit failed / external_result_unknown / needs_input / running.
 */
export { coerceStartedStageStatus } from "../model/workbenchScreenCore.mjs";

export async function subscribeTaskEvents(
  taskId: string,
  onEvent: (event: TaskEvent) => void,
  signal?: AbortSignal,
  lastEventId?: string | null,
): Promise<void> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/events`, {
    headers: buildAuthHeaders({
      Accept: "text/event-stream, application/x-ndjson",
      ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}),
    }),
    signal,
  });
  if (!res.ok) throw await apiErrorFromResponse(res);
  if (!res.body) {
    throw new Error("Task event stream is not available");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const isSse = (res.headers.get("content-type") ?? "").includes("text/event-stream");
  let sseEventName = "";
  let sseEventId = "";
  let sseDataLines: string[] = [];

  const emitSseEvent = () => {
    if (!sseDataLines.length) {
      sseEventName = "";
      sseEventId = "";
      return;
    }
    const parsed = JSON.parse(sseDataLines.join("\n")) as TaskEvent;
    if ((parsed.id && sseEventId && parsed.id !== sseEventId)
        || (parsed.type && sseEventName && parsed.type !== sseEventName)) {
      sseEventName = "";
      sseEventId = "";
      sseDataLines = [];
      return;
    }
    onEvent({
      ...parsed,
      id: parsed.id || sseEventId,
      type: parsed.type || taskEventType(sseEventName),
    });
    sseEventName = "";
    sseEventId = "";
    sseDataLines = [];
  };

  const processLine = (rawLine: string) => {
    const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
    if (!isSse) {
      const trimmed = line.trim();
      if (trimmed) onEvent(JSON.parse(trimmed) as TaskEvent);
      return;
    }
    if (!line) {
      emitSseEvent();
      return;
    }
    if (line.startsWith(":")) return;
    const separator = line.indexOf(":");
    const field = separator >= 0 ? line.slice(0, separator) : line;
    const value = separator >= 0 ? line.slice(separator + 1).replace(/^ /, "") : "";
    if (field === "id") sseEventId = value;
    else if (field === "event") sseEventName = value;
    else if (field === "data") sseDataLines.push(value);
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      processLine(line);
    }
  }

  if (buffer) processLine(buffer);
  if (isSse) emitSseEvent();
}

export interface ArtifactDisplayProjection {
  kind: "historical_draft_display";
  label: string;
  sourceArtifactId: string;
  sourceArtifactIds: string[];
  originalStatus: "unfinalized";
  content: string;
}

export interface ArtifactContentResponse {
  displayProjection?: ArtifactDisplayProjection | null;
  artifactId: string;
  path: string;
  format?: string | null;
  sourceMode: "live" | "replay" | "external" | "inline";
  content: string;
  evidenceRefs?: ArtifactEvidenceRef[];
  linkedMaterialIds?: string[];
}

export async function fetchArtifactContent(taskId: string, artifactId: string): Promise<ArtifactContentResponse | null> {
  const res = await fetch(
    `${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(artifactId)}/content`,
    { headers: buildAuthHeaders() },
  );
  if (res.status === 404) return null;
  return parseJson<ArtifactContentResponse>(res);
}

export interface SkillPack {
  key: string;
  label: string;
  description?: string;
  when_to_use?: string;
}

export async function fetchSkillPacks(): Promise<SkillPack[]> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/meta/packs`, {
    headers: buildAuthHeaders(),
  });
  return parseJson<SkillPack[]>(res);
}

export async function fetchPackStageCatalog(pack: string): Promise<StageCatalog[]> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/meta/packs/${encodeURIComponent(pack)}/stage-catalog`, {
    headers: buildAuthHeaders(),
  });
  return parseJson<StageCatalog[]>(res);
}

export interface CreateTaskInput {
  title?: string;
  goalSummary?: string;
  domain?: string;
  skillPack?: string;
  currentStage?: TaskStage;
  status?: TaskStatus;
  resultSource?: ResultSource;
  executionMode?: TaskExecutionMode;
  creationStatus?: CreationStatus;
  creationPlan?: TaskPlan;
  stages?: TaskStage[];
  ownerId?: string;
  workspaceId?: string;
  createdBy?: string;
}

export async function createTask(body: CreateTaskInput = {}): Promise<Task> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  return parseJson<Task>(res);
}

export async function createDraftTask(body: CreateTaskInput): Promise<Task> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/draft`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  return parseJson<Task>(res);
}

export interface ResearchStartSubmitInput {
  input: string;
  sourcePage?: "home" | "board" | "starter_plaza" | "sample";
  selectedHint?: string;
  presetQuery?: string;
  clientRequestId: string;
  sessionId?: string;
  requestedWorkflow?:
    | "opentrajectory_research_workflow"
    | "pvd_process_optimization_workflow"
    | "cs_paper_reproduction_workflow"
    | "materials_full_discovery_workflow";
  executionMode?: TaskExecutionMode;
  createClarificationDraft?: boolean;
}

export interface KeyLiteratureReading {
  rank: number;
  sourceRef: string;
  title: string;
  authors: string[];
  year?: number | null;
  venue?: string | null;
  url?: string | null;
  doi?: string | null;
  arxivId?: string | null;
  sourceName: string;
  sourceMode: string;
  sourceModeLabel: string;
  role: string;
  whyRead: string;
  matchedSignals: string[];
  limitations: string[];
  confidence: number;
  provenance: Record<string, unknown>;
}

export interface KeyLiteraturePositioningPanel {
  status: "ready" | "partial" | "needs_clarification" | "no_reliable_candidates";
  querySummary: {
    originalQuestion: string;
    normalizedQuery: string;
    topK: number;
    retrievalResultCount: number;
    dedupedResultCount: number;
    relevantCandidateCount: number;
  };
  searchStrategy: {
    normalizedQuery: string;
    queryVariants: string[];
    coreTerms: string[];
    queryPhrases: string[];
    sourcePriority: string[];
    topK?: number | null;
    boundary: string;
  };
  keyReadings: KeyLiteratureReading[];
  candidateReadings: KeyLiteratureReading[];
  coverage: {
    coveredAspects: string[];
    missingAspects: string[];
  };
  sourceAttempts: Array<{
    sourceName: string;
    query: string;
    status: string;
    retrievedAt: string;
    resultCount: number;
    error?: string | null;
  }>;
  nextQuestions: string[];
  userFacingSummary: string;
}

export interface UserVisibleStartPanel {
  kind?: "generic" | "key_literature_positioning";
  userFacingSummary: string;
  recommendedFirstAction: string;
  suggestedNextAction: string;
  missingInfo: string[];
  safetyNotes: string[];
  needsConfirmation: boolean;
  allowImmediateExecution: boolean;
  keyLiterature?: KeyLiteraturePositioningPanel | null;
}

export interface ResearchStartAcceptedResponse {
  sessionId: string;
  requestId: string;
  status: "queued" | "leased" | "succeeded" | "failed" | "unknown";
  reservedTaskId: string;
  reservedArtifactId: string;
}

export async function submitResearchStart(input: ResearchStartSubmitInput): Promise<ResearchStartAcceptedResponse> {
  const res = await fetch(`${getApiBaseUrl()}/api/research-start/submit`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      input: input.input,
      sourcePage: input.sourcePage ?? "home",
      selectedHint: input.selectedHint,
      presetQuery: input.presetQuery,
      requestedWorkflow: input.requestedWorkflow,
      clientRequestId: input.clientRequestId,
      sessionId: input.sessionId,
      executionMode: input.executionMode,
      createClarificationDraft: input.createClarificationDraft,
    }),
  });
  return parseJson<ResearchStartAcceptedResponse>(res);
}

export type ResearchStartStreamEvent =
  | ({ type: "accepted" } & ResearchStartAcceptedResponse)
  | { type: "error"; message: string; status?: number };

export interface ResearchStartSessionStatus {
  sessionId: string;
  status: "queued" | "running" | "worker_unavailable" | "completed" | "failed" | "external_result_unknown";
  currentPhase?: "understanding" | "organizing" | "planning";
  reservedTaskId?: string | null;
  clientRequestId?: string | null;
  queueState?: string | null;
  reasonCode?: string | null;
  recoveryReason?: "still_queued" | "queued_stale" | "worker_unavailable" | "external_result_unknown" | null;
  retryAfter?: number | null;
  allowedActions?: string[];
  planningProgress?: {
    code: string;
    message: string;
    elapsedSeconds: number;
    startedAt?: string | null;
    updatedAt?: string | null;
    attemptOrdinal?: number | null;
    repairRound?: number;
    thoughts?: Array<{
      key: string;
      state: "forming" | "formed";
      text: string;
    }> | null;
    keyResults?: {
      planningMode?: "lightweight" | "partial" | "sdr" | null;
      substageCount: number;
      researchQuestions: string[];
      objectives: string[];
      targetOutputs: string[];
      expectedArtifactTypes: string[];
      planningRationale?: string | null;
    } | null;
  } | null;
  result?: {
    outcome: string;
    sessionId: string;
    taskId?: string | null;
  } | null;
  userMessage?: string | null;
}

export async function fetchResearchStartSession(sessionId: string): Promise<ResearchStartSessionStatus> {
  const res = await fetch(`${getApiBaseUrl()}/api/research-start/sessions/${encodeURIComponent(sessionId)}`, {
    headers: buildAuthHeaders(),
  });
  return parseJson<ResearchStartSessionStatus>(res);
}

/**
 * Streaming transport for durable acceptance. The stream closes immediately
 * after its single accepted event; execution is owned by the durable worker.
 */
export async function submitResearchStartStream(
  input: ResearchStartSubmitInput,
  onEvent: (event: ResearchStartStreamEvent) => void,
): Promise<ResearchStartAcceptedResponse> {
  const res = await fetch(`${getApiBaseUrl()}/api/research-start/submit/stream`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      input: input.input,
      sourcePage: input.sourcePage ?? "home",
      selectedHint: input.selectedHint,
      presetQuery: input.presetQuery,
      requestedWorkflow: input.requestedWorkflow,
      clientRequestId: input.clientRequestId,
      sessionId: input.sessionId,
      executionMode: input.executionMode,
      createClarificationDraft: input.createClarificationDraft,
    }),
  });
  if (!res.ok) {
    throw await apiErrorFromResponse(res);
  }
  if (!res.body) {
    // Fallback: environments without streaming bodies fall back to the JSON route.
    return submitResearchStart(input);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalResult: ResearchStartAcceptedResponse | null = null;

  const handleLine = (line: string) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    const event = JSON.parse(trimmed) as ResearchStartStreamEvent;
    onEvent(event);
    if (event.type === "error") {
      throw Object.assign(new Error("启动判断未完成，请检查输入后重试。"), {
        code: "research_start_rejected",
        status: event.status,
      });
    }
    if (event.type === "accepted") {
      finalResult = event;
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) handleLine(line);
  }
  if (buffer.trim()) handleLine(buffer);

  if (!finalResult) {
    throw Object.assign(new Error("启动响应尚未确认。"), { code: "response_unknown" });
  }
  return finalResult;
}

export interface StarterUseInput {
  title?: string;
  goalSummary?: string;
  inputArtifactIds?: string[];
}

export interface StarterUseResponse {
  starter: ReleaseGatedStarter;
  outcome: "workflow_draft" | "prep_check" | "gap_state" | "requires_existing_inputs" | "literature_artifacts" | "real_task";
  task?: Task | null;
  artifact?: Artifact | null;
  state?: {
    kind?: string;
    missingInputs?: string[];
    artifactExpectation?: string[];
    failureState?: {
      state?: string;
      reason?: string;
      impact?: string;
      nextAction?: string;
      retryPolicy?: string;
      partialOutputStatus?: string;
    };
    failureBoundary?: string;
    artifactShell?: string;
    readyToConfirm?: boolean;
    attemptedSourceApis?: unknown[];
    attemptedQueries?: string[];
    retrievalCount?: number;
    dedupedCount?: number;
    relevantCandidateCount?: number;
    topKRelevantCount?: number;
    relevanceGate?: Record<string, unknown>;
    partialArtifacts?: unknown[];
  } | null;
  nextAction: string;
}

export async function fetchStarters(): Promise<ReleaseGatedStarter[]> {
  const res = await fetch(`${getApiBaseUrl()}/api/starters`, {
    headers: buildAuthHeaders(),
  });
  const data = await parseJson<{ starters?: ReleaseGatedStarter[] }>(res);
  return data.starters ?? [];
}

export async function useStarter(starterId: string, body: StarterUseInput = {}): Promise<StarterUseResponse> {
  const res = await fetch(`${getApiBaseUrl()}/api/starters/${encodeURIComponent(starterId)}/use`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  return parseJson<StarterUseResponse>(res);
}

export async function patchTaskStatus(taskId: string, status: TaskStatus): Promise<Task> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/status`, {
    method: "PATCH",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ status }),
  });
  return parseJson<Task>(res);
}

export async function renameTask(taskId: string, title: string): Promise<Task> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}`, {
    method: "PATCH",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ title }),
  });
  return parseJson<Task>(res);
}

export async function deleteTask(taskId: string): Promise<void> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}`, {
    method: "DELETE",
    headers: buildAuthHeaders(),
  });
  return parseVoid(res);
}

export interface CloneTaskInput {
  /** Override the cloned title; defaults to `${source.title}（副本）` server-side. */
  title?: string;
  /** Reuse every source artifact (content-addressed, zero-copy). `false` = spec-only template. */
  includeArtifacts?: boolean;
}

export async function cloneTask(taskId: string, body: CloneTaskInput = {}): Promise<Task> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/clone`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  return parseJson<Task>(res);
}

async function postTaskAction(taskId: string, action: string): Promise<Task> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/${action}`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
  });
  return parseJson<Task>(res);
}

export interface ResearchStartConfirmationPayload {
  researchGoalConfirmed: boolean;
  existingMaterialsConfirmed: boolean;
  missingMaterialsDecision?: "supplement" | "accept_limitations" | "modify_goal" | "pause" | string | null;
  nonCitableBoundariesAccepted: boolean;
  nextStepLimitationsConfirmed: boolean;
  acceptedLimitations?: string[];
  notes?: string | null;
}

export async function confirmTaskPlan(taskId: string, confirmation: ResearchStartConfirmationPayload): Promise<Task> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/confirm-plan`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(confirmation),
  });
  return parseJson<Task>(res);
}

export interface ResearchDesignRead {
  designId: string;
  version: number;
  status: "draft" | "confirmed" | "superseded" | "cancelled";
  coverage: Record<string, unknown> | null;
  revision: number;
}

export async function confirmResearchDesign(
  taskId: string,
  input: {
    designId: string;
    version: number;
    confirmationId: string;
    decision: "approved" | "rejected" | "request_revision";
    expectedTaskRevision: number;
  },
): Promise<ResearchDesignRead> {
  const res = await fetch(
    `${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/research-design/confirm-plan`,
    {
      method: "POST",
      headers: buildAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(input),
    },
  );
  return parseJson<ResearchDesignRead>(res);
}

export async function recordResearchStartDecision(taskId: string, decision: ResearchStartConfirmationPayload): Promise<Task> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/research-start-decision`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(decision),
  });
  return parseJson<Task>(res);
}

export function reviseTaskPlan(taskId: string): Promise<Task> {
  return postTaskAction(taskId, "revise-plan");
}

export type ModelJobAccepted = {
  jobId: string;
  resourceId: string;
  jobKind: string;
  state: "accepted";
};

export type ModelJobStatus = {
  jobId: string;
  jobKind: string;
  resourceKind: "task" | "agent_session";
  resourceId: string;
  state: "accepted" | "running" | "succeeded" | "blocked" | "failed";
  result?: Record<string, unknown> | null;
  errorCode?: string | null;
};

export async function fetchModelJobStatus(
  resourceKind: "task" | "agent_session",
  resourceId: string,
  jobId: string,
): Promise<ModelJobStatus> {
  const res = await fetch(
    `${getApiBaseUrl()}/api/model-jobs/${resourceKind}/${encodeURIComponent(resourceId)}/${encodeURIComponent(jobId)}`,
    { headers: buildAuthHeaders() },
  );
  return parseJson<ModelJobStatus>(res);
}

export async function waitForModelJob(
  resourceKind: "task" | "agent_session",
  resourceId: string,
  jobId: string,
): Promise<ModelJobStatus> {
  for (;;) {
    const status = await fetchModelJobStatus(resourceKind, resourceId, jobId);
    if (!["accepted", "running"].includes(status.state)) {
      if (status.state !== "succeeded") {
        throw new Error(status.errorCode || `model job ${status.state}`);
      }
      return status;
    }
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
}

export async function updateTaskPlan(taskId: string, body: TaskPlanUpdate): Promise<Task> {
  const requestId = crypto.randomUUID();
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/update-plan`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json", "Idempotency-Key": requestId }),
    body: JSON.stringify(body),
  });
  const result = await parseJson<Task | ModelJobAccepted>(res);
  if (!("jobId" in result)) return result;
  await waitForModelJob("task", taskId, result.jobId);
  const updated = await fetchTask(taskId);
  if (!updated) throw new Error("task unavailable after model job");
  return updated;
}

export async function agentReviseResearchDesign(
  taskId: string,
  body: { instruction: string; expectedRevision: number },
): Promise<Task> {
  const requestId = crypto.randomUUID();
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/research-design/agent-revision`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json", "Idempotency-Key": requestId }),
    body: JSON.stringify(body),
  });
  const result = await parseJson<ModelJobAccepted>(res);
  await waitForModelJob("task", taskId, result.jobId);
  const updated = await fetchTask(taskId);
  if (!updated) throw new Error("task unavailable after model job");
  return updated;
}

export async function updateTaskExecutionMode(taskId: string, executionMode: TaskExecutionMode): Promise<Task> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/execution-mode`, {
    method: "PATCH",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ executionMode }),
  });
  return parseJson<Task>(res);
}

export type TaskExecutionPolicy = {
  taskId: string;
  currentMode: TaskExecutionMode;
  pendingMode: TaskExecutionMode | null;
  policyVersion: number;
  pendingPolicyVersion: number | null;
  changedBy: string;
  changedAt: string;
  appliedAt: string;
};

export async function fetchTaskExecutionPolicy(taskId: string): Promise<TaskExecutionPolicy> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/execution-policy`, {
    headers: buildAuthHeaders(),
  });
  return parseJson<TaskExecutionPolicy>(res);
}

export async function updateTaskExecutionPolicy(taskId: string, executionMode: TaskExecutionMode): Promise<TaskExecutionPolicy> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/execution-policy`, {
    method: "PATCH",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ executionMode }),
  });
  return parseJson<TaskExecutionPolicy>(res);
}

export function cancelTaskDraft(taskId: string): Promise<Task> {
  return postTaskAction(taskId, "cancel-draft");
}

export type DurableStartResponse =
  | {
      taskId: string;
      selectionRequestId: string;
      state: "accepted" | "leased" | "succeeded" | "blocked" | "failed";
      taskRevision: number;
    }
  | {
      run: { runId: string; state: string; runNumber: number; latestEventSequence: number };
      replayed: boolean;
    }
  | {
      decision: Record<string, unknown>;
      revision: number;
      replayed: boolean;
    };

export type ResearchExecutionStopResponse = {
  taskId: string;
  status: string;
  revision: number;
  suppressed: true;
};

export type ResearchExecutionResumeResponse = {
  receiptId: string;
  taskId: string;
  status: "running";
  revision: number;
  resumed: true;
  resumedDispatchCount: number;
  continuationStarted: boolean;
  continuationAccepted: boolean;
  selectionRequestId: string | null;
};

export type DurableRunProjection = {
  runId: string;
  taskId: string;
  state: string;
  latestEventSequence: number;
  capabilityCallId: string | null;
};

export type DurableRunEvent = {
  sequence: number;
  type: string;
  payload: Record<string, unknown>;
  occurredAt: string;
};

export type CollaborationReference = {
  id: string;
  title?: string | null;
};

export type CollaborationSkillProjection = {
  skillId: string;
  agentId: string;
  agentName: string;
  agentKind: "professional" | "general" | "support" | "neutral";
  knownAgent: boolean;
  status: string;
  purpose: string;
  summary: string;
  startedAt: string | null;
  completedAt: string | null;
  outputArtifactRefs: CollaborationReference[];
};

export type CollaborationHandoffProjection = {
  fromAgentId: string;
  fromAgentName: string;
  toAgentId: string;
  toAgentName: string;
  summary: string;
  artifactRefs: CollaborationReference[];
  occurredAt: string | null;
};

export type CollaborationAgentProjection = {
  sequence: number;
  agentId: string;
  agentName: string;
  agentKind: "professional" | "general" | "support" | "neutral";
  knownAgent: boolean;
  visibility: "main" | "detail";
  isMember: boolean;
  title: string;
  objective: string;
  status: string;
  summary: string;
  stageKey: string | null;
  substageKey: string | null;
  assignedAt: string | null;
  startedAt: string | null;
  completedAt: string | null;
  skillCalls: CollaborationSkillProjection[];
  inputArtifactRefs: CollaborationReference[];
  outputArtifactRefs: CollaborationReference[];
  handoff: CollaborationHandoffProjection | null;
  supportingActivities: Array<{
    agentName: string;
    title: string;
    status: string;
    summary: string;
    skillCalls: CollaborationSkillProjection[];
  }>;
};

export type CollaborationDecisionProjection = {
  gateId: string;
  kind: string;
  reason: string;
  artifactRefs: CollaborationReference[];
  createdAt: string | null;
  allowedActions: CollaborationDecisionAction[];
};

export type CollaborationDecisionAction = "start_revision_run" | "continue_run" | "terminate_run";

export type CollaborationDecisionResult = {
  gateId: string;
  taskId: string;
  runId: string;
  state: "resolved" | "cancelled";
  action: CollaborationDecisionAction;
  successorRunId: string | null;
  taskStatus: Task["status"];
  userMessage: string;
};

export type CollaborationProgressStageProjection = {
  sequence: number;
  stageKey: string | null;
  substageKey: string | null;
  agentId: string;
  status: string;
};

export type CollaborationResourceProjection = {
  id?: string;
  title?: string;
  name?: string;
  label?: string;
};

export type CollaborationArtifactProjection = {
  artifactId: string;
  title: string;
  kind: string;
  format: string;
  status: string;
  confirmable: boolean;
  provenance: "live_confirmed_run";
  createdAt: string | null;
};

export type CollaborationFailureProjection = {
  failedStep: string;
  externalAction: string;
  resultTrustAndRetention: string;
  safeNextStep: string;
};

// Kept only for the legacy, now-unmounted collaboration renderer while the
// Workbench reads the four authoritative projection blocks below.
export type CollaborationEntryProjection = {
  id: string;
  sequence: number;
  kind: string;
  occurredAt: string;
  status: string;
  visibility: "main" | "detail";
  agentId: string;
  title: string;
  summary: string;
  workUnitId?: string | null;
  decisionSummary?: string | null;
  inputMemoryRefs: CollaborationReference[];
  inputArtifactRefs: CollaborationReference[];
  outputArtifactRefs: CollaborationReference[];
  skillCalls: Array<{
    callId: string;
    skillId: string;
    status: string;
    summary?: string | null;
    startedAt?: string | null;
    endedAt?: string | null;
    outputArtifactRefs: CollaborationReference[];
  }>;
  handoff?: (CollaborationHandoffProjection & { memoryRefs: CollaborationReference[] }) | null;
};

export type CollaborationProjection = {
  taskId: string;
  /** Task revision this snapshot is bound to. Required for live-research screen merge. */
  revision: number;
  status: string;
  statusLabel: string;
  failure?: CollaborationFailureProjection;
  collaboration: {
    actual: CollaborationAgentProjection[];
    handoffs: CollaborationHandoffProjection[];
    needsUserDecision: boolean;
    pendingDecisions: CollaborationDecisionProjection[];
  };
  progress: { status: string; stages: CollaborationProgressStageProjection[] };
  resources: {
    skills: CollaborationSkillProjection[];
    materials: CollaborationResourceProjection[];
    sources: CollaborationResourceProjection[];
    files: CollaborationResourceProjection[];
  };
  artifacts: { items: CollaborationArtifactProjection[]; confirmableCount: number };
};

export async function fetchTaskCollaboration(taskId: string, signal?: AbortSignal): Promise<CollaborationProjection> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/collaboration`, {
    headers: buildAuthHeaders(),
    signal,
  });
  return parseJson<CollaborationProjection>(res);
}

export async function decideTaskCollaborationGate(
  taskId: string,
  gateId: string,
  action: CollaborationDecisionAction,
  signal?: AbortSignal,
): Promise<CollaborationDecisionResult> {
  const res = await fetch(
    `${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/collaboration/decisions/${encodeURIComponent(gateId)}`,
    {
      method: "POST",
      headers: buildAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ action }),
      signal,
    },
  );
  return parseJson<CollaborationDecisionResult>(res);
}

export async function fetchCurrentTaskExecutionRun(taskId: string): Promise<DurableRunProjection | null> {
  const res = await fetch(`${getApiBaseUrl()}/api/execution-runs/task/${encodeURIComponent(taskId)}`, {
    headers: buildAuthHeaders(),
  });
  const payload = await parseJson<{ run: DurableRunProjection | null }>(res);
  return payload.run;
}

export async function fetchExecutionRunEvents(runId: string): Promise<DurableRunEvent[]> {
  const res = await fetch(
    `${getApiBaseUrl()}/api/execution-runs/${encodeURIComponent(runId)}/events?afterSequence=0`,
    { headers: buildAuthHeaders() },
  );
  const payload = await parseJson<{ events: DurableRunEvent[] }>(res);
  return payload.events;
}

export type StartProductAction = "initial_start" | "continue_research";

export async function startTask(
  taskId: string,
  expectedTaskRevision: number,
  idempotencyKey: string,
  productAction: StartProductAction,
): Promise<DurableStartResponse> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/start`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json", "Idempotency-Key": idempotencyKey }),
    body: JSON.stringify({ expectedTaskRevision, selectionRequestId: idempotencyKey, productAction }),
  });
  return parseJson<DurableStartResponse>(res);
}

export async function stopTaskRun(taskId: string, expectedTaskRevision: number): Promise<Task | ResearchExecutionStopResponse> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/stop-run`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ expectedTaskRevision }),
  });
  return parseJson<Task | ResearchExecutionStopResponse>(res);
}

export async function resumeTaskRun(taskId: string, expectedTaskRevision: number): Promise<ResearchExecutionResumeResponse> {
  const res = await fetch(`${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/resume-run`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ expectedTaskRevision }),
  });
  return parseJson<ResearchExecutionResumeResponse>(res);
}

export function retryTaskRun(taskId: string): Promise<Task> {
  return postTaskAction(taskId, "retry");
}

export function retryTaskStage(taskId: string, stageKey: TaskStage): Promise<Task> {
  return postTaskAction(taskId, `stages/${encodeURIComponent(stageKey)}/retry`);
}

export interface StageResultInput {
  label?: string;
  summary?: string;
  risks?: string;
  nextSteps?: string;
  entryCondition?: string;
  completionCondition?: string;
  lockedReason?: string;
  primaryAction?: string;
  canSkip?: boolean;
  canReview?: boolean;
  canLock?: boolean;
  status?: StageStatus;
  artifactRefs?: string[];
  subStages?: SubStage[];
}

export async function saveStageResult(
  taskId: string,
  stageKey: TaskStage,
  body: StageResultInput,
): Promise<Task> {
  const res = await fetch(
    `${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/stages/${encodeURIComponent(stageKey)}/result`,
    {
      method: "POST",
      headers: buildAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    },
  );
  return parseJson<Task>(res);
}

export async function fetchSubstageSkills(
  taskId: string,
  substageKey: string,
): Promise<{ skills: string[]; labels: Record<string, string> }> {
  const res = await fetch(
    `${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/substage-skills?substage_key=${encodeURIComponent(substageKey)}`,
    { headers: buildAuthHeaders() },
  );
  return parseJson<{ substageKey: string; skills: string[]; labels: Record<string, string> }>(res);
}

export async function advanceSubstage(
  taskId: string,
  substageKey?: string | null,
): Promise<{
  completed: boolean;
  hasNext: boolean;
  requiresConfirmation?: boolean;
  stageKey?: string;
  nextStageKey?: string;
  substageKey?: string;
  nextSubstageKey?: string;
  substageLabel?: string;
  nextSubstageLabel?: string;
}> {
  const params = substageKey ? `?substage_key=${encodeURIComponent(substageKey)}` : "";
  const res = await fetch(
    `${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/advance-substage${params}`,
    { method: "POST", headers: buildAuthHeaders() },
  );
  return parseJson(res);
}

export interface FileTreeNode {
  name: string;
  type: "file" | "directory";
  size?: number;
  children?: FileTreeNode[];
}

export async function fetchArtifactTree(taskId: string, artifactId: string): Promise<FileTreeNode | null> {
  const res = await fetch(
    `${getApiBaseUrl()}/api/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(artifactId)}/tree`,
    { headers: buildAuthHeaders() },
  );
  if (res.status === 404) return null;
  return parseJson<FileTreeNode>(res);
}

// ---------------------------------------------------------------------------
// Artifact library (产物库, Artifact-01) — artifact-scoped /api/artifacts
// ---------------------------------------------------------------------------

export interface CatalogArtifact {
  id: string;
  taskId?: string | null;
  ownerId: string;
  workspaceId: string;
  createdBy: string;
  kind: string;
  title: string;
  summary?: string | null;
  createdAt: string;
  format?: string | null;
  relatedStage?: TaskStage | null;
  stage?: TaskStage | null;
  relatedSkillId?: string | null;
  subStageInstanceId?: string | null;
  substage?: string | null;
  sourceCapabilityCallId?: string | null;
  artifactState?: string | null;
  validatorResult?: unknown | null;
  resultProtocol?: ResultProtocol | null;
  artifactQuality?: "standard" | "degraded" | null;
  warnings?: string[];
  humanGate?: unknown | null;
  artifactType?: string | null;
  artifactVisibility?: string | null;
  audience?: string | null;
  storageType?: string | null;
  origin?: string | null;
  externalProvider?: string | null;
  externalUri?: string | null;
  contentSize?: number | null;
  reusedFrom?: Record<string, string> | null;
  sourceTaskId: string;
  sourceTaskTitle?: string | null;
  linkedMaterialIds?: string[];
  materialManifest?: MaterialManifest[];
  memoryRefs?: string[];
}

export interface ArtifactListResponse {
  artifacts: CatalogArtifact[];
  total: number;
  page: number;
  pageSize: number;
}

export interface ArtifactListParams {
  kind?: string;
  origin?: string;
  sourceTaskId?: string;
  relatedStage?: string;
  format?: string;
  q?: string;
  page?: number;
  pageSize?: number;
  searchInline?: boolean;
}

export async function fetchArtifacts(params: ArtifactListParams = {}): Promise<ArtifactListResponse> {
  const search = new URLSearchParams();
  if (params.kind) search.set("kind", params.kind);
  if (params.origin) search.set("origin", params.origin);
  if (params.sourceTaskId) search.set("sourceTaskId", params.sourceTaskId);
  if (params.relatedStage) search.set("relatedStage", params.relatedStage);
  if (params.format) search.set("format", params.format);
  if (params.q) search.set("q", params.q);
  if (params.page) search.set("page", String(params.page));
  if (params.pageSize) search.set("pageSize", String(params.pageSize));
  if (params.searchInline) search.set("searchInline", "true");
  const qs = search.toString();
  const res = await fetch(`${getApiBaseUrl()}/api/artifacts${qs ? `?${qs}` : ""}`, {
    headers: buildAuthHeaders(),
  });
  return parseJson<ArtifactListResponse>(res);
}

export async function fetchArtifactDetail(artifactId: string): Promise<CatalogArtifact | null> {
  const res = await fetch(`${getApiBaseUrl()}/api/artifacts/${encodeURIComponent(artifactId)}`, {
    headers: buildAuthHeaders(),
  });
  if (res.status === 404) return null;
  return parseJson<CatalogArtifact>(res);
}

export async function fetchArtifactContentById(artifactId: string): Promise<ArtifactContentResponse | null> {
  const res = await fetch(`${getApiBaseUrl()}/api/artifacts/${encodeURIComponent(artifactId)}/content`, {
    headers: buildAuthHeaders(),
  });
  if (res.status === 404) return null;
  return parseJson<ArtifactContentResponse>(res);
}

/** 下载产物：external blob 后端返回 presigned GET url（直接 window.open）；
 * inline/local 返回文件流（鉴权 fetch blob + objectURL，JWT 下不能把 Bearer 放进 <a href>）。 */
export async function reviewArtifact(
  artifactId: string,
  decision: "approved" | "rejected" | "needs_revision",
  note?: string,
): Promise<CatalogArtifact> {
  const res = await fetch(`${getApiBaseUrl()}/api/artifacts/${encodeURIComponent(artifactId)}/review`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ decision, note }),
  });
  return parseJson<CatalogArtifact>(res);
}

export async function confirmArtifactScopeExpansion(
  artifactId: string,
  note?: string,
): Promise<CatalogArtifact> {
  const res = await fetch(`${getApiBaseUrl()}/api/artifacts/${encodeURIComponent(artifactId)}/confirm-scope-expansion`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ note, source: "user" }),
  });
  return parseJson<CatalogArtifact>(res);
}

export async function downloadArtifact(artifactId: string): Promise<void> {
  const res = await fetch(`${getApiBaseUrl()}/api/artifacts/${encodeURIComponent(artifactId)}/download`, {
    headers: buildAuthHeaders(),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `下载失败：HTTP ${res.status}`);
  }
  const contentType = res.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    const data = (await res.json()) as { url?: string };
    if (data.url) {
      window.open(data.url, "_blank");
      return;
    }
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  const disposition = res.headers.get("content-disposition") ?? "";
  const match = /filename="?([^";]+)"?/i.exec(disposition);
  a.download = match?.[1] ?? artifactId;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** 用户上传产物（multipart）。不要手设 Content-Type —— 浏览器自动带 multipart boundary。 */
export async function uploadArtifact(
  targetTaskId: string,
  file: File,
  opts: { kind?: string; title?: string; currentTaskId?: string; sourceTaskId?: string; ownershipConfirmed?: boolean } = {},
): Promise<Artifact> {
  const form = new FormData();
  form.append("file", file);
  if (targetTaskId) form.append("targetTaskId", targetTaskId);
  if (opts.currentTaskId) form.append("currentTaskId", opts.currentTaskId);
  if (opts.sourceTaskId) form.append("sourceTaskId", opts.sourceTaskId);
  if (opts.ownershipConfirmed) form.append("ownershipConfirmed", "true");
  if (opts.kind) form.append("kind", opts.kind);
  if (opts.title) form.append("title", opts.title);
  const res = await fetch(`${getApiBaseUrl()}/api/artifacts/upload`, {
    method: "POST",
    headers: buildAuthHeaders(),
    body: form,
  });
  if (!res.ok) {
    if (res.status === 413) throw new Error("文件过大，超过上传上限");
    const text = await res.text().catch(() => "");
    throw new Error(text || `上传失败：HTTP ${res.status}`);
  }
  return parseJson<Artifact>(res);
}

/** 删除产物（仅 owner 可删；external blob 后端会顺带清理 MinIO 对象）。 */
export async function deleteArtifact(artifactId: string): Promise<void> {
  const res = await fetch(`${getApiBaseUrl()}/api/artifacts/${encodeURIComponent(artifactId)}`, {
    method: "DELETE",
    headers: buildAuthHeaders(),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `删除失败：HTTP ${res.status}`);
  }
}

export interface ReuseArtifactResponse {
  artifact: Artifact;
  sourceArtifactId: string;
  sourceTaskId: string;
}

export async function reuseArtifact(
  artifactId: string,
  targetTaskId: string,
  opts: { title?: string; summary?: string } = {},
): Promise<ReuseArtifactResponse> {
  const res = await fetch(`${getApiBaseUrl()}/api/artifacts/${encodeURIComponent(artifactId)}/reuse`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ targetTaskId, ...opts }),
  });
  return parseJson<ReuseArtifactResponse>(res);
}

// Artifact-03 来源链 --------------------------------------------------------

export interface ArtifactRelation {
  relationType: string;
  direction: "incoming" | "outgoing";
  artifactId: string;
  title?: string | null;
  kind?: string | null;
  capabilityCallId?: string | null;
  createdAt?: string | null;
}

export async function fetchArtifactRelations(
  artifactId: string,
  params: { direction?: "incoming" | "outgoing"; relationType?: string } = {},
): Promise<ArtifactRelation[]> {
  const search = new URLSearchParams();
  if (params.direction) search.set("direction", params.direction);
  if (params.relationType) search.set("relationType", params.relationType);
  const qs = search.toString();
  const res = await fetch(
    `${getApiBaseUrl()}/api/artifacts/${encodeURIComponent(artifactId)}/relations${qs ? `?${qs}` : ""}`,
    { headers: buildAuthHeaders() },
  );
  if (res.status === 404) return [];
  return parseJson<{ relations: ArtifactRelation[] }>(res).then((r) => r.relations);
}

// Artifact 版本管理 (issue #302) ------------------------------------------------

export type { ArtifactVersion, ArtifactVersionSource, ArtifactVersionSummary } from "@openair/shared";

export interface ArtifactVersionsResponse {
  artifactId: string;
  revision?: number | null;
  currentVersionId?: string | null;
  versions: ArtifactVersionSummary[];
}

/** 版本历史时间线（不含正文）。 */
export async function fetchArtifactVersions(artifactId: string): Promise<ArtifactVersionsResponse | null> {
  const res = await fetch(`${getApiBaseUrl()}/api/artifacts/${encodeURIComponent(artifactId)}/versions`, {
    headers: buildAuthHeaders(),
  });
  if (res.status === 404) return null;
  return parseJson<ArtifactVersionsResponse>(res);
}

/** 单个版本详情（含正文快照，用于查看历史版本）。 */
export async function fetchArtifactVersion(artifactId: string, versionId: string): Promise<ArtifactVersion | null> {
  const res = await fetch(
    `${getApiBaseUrl()}/api/artifacts/${encodeURIComponent(artifactId)}/versions/${encodeURIComponent(versionId)}`,
    { headers: buildAuthHeaders() },
  );
  if (res.status === 404) return null;
  return parseJson<ArtifactVersion>(res);
}

/** 读取某个历史版本的正文（local/external 大文件也能读，按版本独立解析）。 */
export async function fetchArtifactVersionContent(
  artifactId: string,
  versionId: string,
): Promise<string | null> {
  const res = await fetch(
    `${getApiBaseUrl()}/api/artifacts/${encodeURIComponent(artifactId)}/versions/${encodeURIComponent(versionId)}/content`,
    { headers: buildAuthHeaders() },
  );
  if (res.status === 404) return null;
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `读取版本正文失败：HTTP ${res.status}`);
  }
  const body = await parseJson<{ content?: string }>(res);
  return body?.content ?? null;
}

/** 在线编辑 Markdown/文本 → 新建 user_edit 版本（原版本保留）。 */
export async function editArtifactContent(
  artifactId: string,
  content: string,
  changeReason: string | undefined,
  expectedRevision: number,
): Promise<CatalogArtifact> {
  const res = await fetch(`${getApiBaseUrl()}/api/artifacts/${encodeURIComponent(artifactId)}/content`, {
    method: "PATCH",
    headers: buildAuthHeaders({
      "Content-Type": "application/json",
      "If-Match-Revision": String(expectedRevision),
    }),
    body: JSON.stringify({ content, changeReason: changeReason ?? null }),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `编辑失败：HTTP ${res.status}`);
  }
  return parseJson<CatalogArtifact>(res);
}

/** 退回修改 + 触发 Agent 重生成（记录 revision_requested，后续产出折叠为 agent_regenerate 版本）。 */
export async function regenerateArtifact(
  artifactId: string,
  feedback?: string,
  skillId?: string,
): Promise<CatalogArtifact> {
  const res = await fetch(`${getApiBaseUrl()}/api/artifacts/${encodeURIComponent(artifactId)}/regenerate`, {
    method: "POST",
    headers: buildAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ feedback: feedback ?? null, skillId: skillId ?? null }),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `重新生成失败：HTTP ${res.status}`);
  }
  return parseJson<CatalogArtifact>(res);
}

/** 恢复历史版本 → 新建 restore 版本（不覆盖历史）。 */
export async function restoreArtifactVersion(
  artifactId: string,
  versionId: string,
  expectedRevision: number,
): Promise<CatalogArtifact> {
  const res = await fetch(
    `${getApiBaseUrl()}/api/artifacts/${encodeURIComponent(artifactId)}/versions/${encodeURIComponent(versionId)}/restore`,
    {
      method: "POST",
      headers: buildAuthHeaders({ "If-Match-Revision": String(expectedRevision) }),
    },
  );
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `恢复失败：HTTP ${res.status}`);
  }
  return parseJson<CatalogArtifact>(res);
}
