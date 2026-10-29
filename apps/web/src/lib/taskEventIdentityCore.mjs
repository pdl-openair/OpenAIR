export const DURABLE_LIFECYCLE_EVENT_TYPES = new Set([
  "orchestrator_lifecycle",
  "agent_lifecycle",
  "skill_lifecycle",
  "mandatory_review_lifecycle",
  "handoff_completed",
  "artifact_persisted",
  "execution_terminal",
]);

function text(value) {
  return typeof value === "string" && value.length > 0 ? value : null;
}

/**
 * Classify one SSE event against the authoritative Task GET snapshot.
 * Future revisions may request a GET refresh, but are not consumed and must
 * not advance Last-Event-ID until that GET establishes the new facts.
 */
export function classifyTaskEvent(event, expected, activeRunId) {
  if (!event || typeof event !== "object" || !expected) return { accepted: false, reason: "malformed" };
  const scope = event.scope;
  const runId = text(event.runId);
  if (
    !scope || typeof scope !== "object"
    || event.taskId !== expected.taskId
    || scope.taskId !== expected.taskId
    || scope.ownerId !== expected.ownerId
    || scope.workspaceId !== expected.workspaceId
    || (text(scope.runId) ?? null) !== runId
  ) {
    return { accepted: false, reason: "scope_mismatch" };
  }
  if (!Number.isInteger(event.revision)) return { accepted: false, reason: "missing_revision" };
  if (event.payload && typeof event.payload === "object") {
    if (Number.isInteger(event.payload.revision) && event.payload.revision !== event.revision) {
      return { accepted: false, reason: "payload_revision_mismatch" };
    }
    if ("runId" in event.payload && (text(event.payload.runId) ?? null) !== runId) {
      return { accepted: false, reason: "payload_run_mismatch" };
    }
  }
  if (event.revision < expected.revision) return { accepted: false, reason: "stale_revision" };
  if (event.revision > expected.revision) return { accepted: false, reason: "future_revision", refresh: true };

  const requiresRun = DURABLE_LIFECYCLE_EVENT_TYPES.has(event.type);
  if (requiresRun && !runId) return { accepted: false, reason: "missing_run" };
  if (runId && !text(event.id)?.includes(`:run:${runId}:`)) {
    return { accepted: false, reason: "event_id_run_mismatch" };
  }
  if (event.type === "snapshot") {
    return { accepted: true, reason: "accepted", runId };
  }
  if ((requiresRun || event.type === "task_updated" || event.type === "heartbeat")
      && (activeRunId ?? null) !== runId) {
    return { accepted: false, reason: "run_mismatch" };
  }
  return { accepted: true, reason: "accepted", runId: activeRunId ?? runId };
}

export function lifecycleBucketKey(taskId, revision, runId) {
  return `${taskId}\u0000${revision}\u0000${runId}`;
}
