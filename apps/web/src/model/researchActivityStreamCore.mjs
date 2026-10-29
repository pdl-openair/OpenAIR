const UNSAFE_KEY = /(?:chain.?of.?thought|reasoning|hidden.?token|prompt|system.?prompt|developer.?prompt|authorization|auth.?header|api.?key|secret|password|access.?token|refresh.?token|tool.?arguments?|shell.?output|stack.?trace|raw.?request|raw.?response)/i;
const UNSAFE_VALUE = /(?:bearer\s+[a-z0-9._~+/=-]{8,}|sk-[a-z0-9_-]{12,}|-----BEGIN [A-Z ]+PRIVATE KEY-----)/i;
const DISPLAY_EVENT_TYPES = new Set(["research_progress", "research_tool_summary", "meaningful_progress"]);

function record(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function assertSafeResearchActivity(value) {
  if (!record(value)) throw new TypeError("Research activity must be an object");
  for (const [key, child] of Object.entries(value)) {
    if (UNSAFE_KEY.test(key)) throw new TypeError(`Unsafe research activity field: ${key}`);
    if (typeof child === "string" && UNSAFE_VALUE.test(child)) {
      throw new TypeError("Unsafe research activity value");
    }
    if (record(child)) assertSafeResearchActivity(child);
    if (Array.isArray(child)) {
      child.forEach((entry) => {
        if (record(entry)) assertSafeResearchActivity(entry);
        if (typeof entry === "string" && UNSAFE_VALUE.test(entry)) {
          throw new TypeError("Unsafe research activity value");
        }
      });
    }
  }
  return value;
}

export function createResearchActivityState(bindingKey = "") {
  return {
    bindingKey,
    lastEventId: null,
    primaryActivities: [],
    collapsedDetails: [],
    invalidateScopes: [],
    seenEventIds: [],
  };
}

function scopesFrom(event) {
  const scopes = event.invalidateScopes;
  return Array.isArray(scopes) ? scopes.filter((value) => typeof value === "string") : [];
}

function stringList(value) {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.filter((item) => typeof item === "string" && item.trim()).map((item) => item.trim()))];
}

function integerList(value) {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.filter((item) => Number.isInteger(item) && item >= 1))];
}

export function filterResearchActivityArtifactRefs(rows = [], visibleArtifactIds = []) {
  const visible = new Set(stringList(visibleArtifactIds));
  return rows.map((row) => ({
    ...row,
    artifactRefs: stringList(row?.artifactRefs).filter((artifactId) => visible.has(artifactId)),
  }));
}

function activityFrom(event) {
  assertSafeResearchActivity(event);
  return {
    eventId: typeof event.eventId === "string" ? event.eventId : null,
    taskId: typeof event.taskId === "string" ? event.taskId : null,
    sequence: Number.isInteger(event.sequence) ? event.sequence : null,
    revision: Number.isInteger(event.revision) ? event.revision : null,
    eventType: typeof event.eventType === "string" ? event.eventType : "research_progress",
    presentationLevel: event.presentationLevel === "detail" ? "detail" : "primary",
    workUnitId: typeof event.workUnitId === "string" ? event.workUnitId : null,
    headline: typeof event.headline === "string" ? event.headline : "研究进展已更新",
    summary: typeof event.summary === "string" ? event.summary : null,
    occurredAt: typeof event.occurredAt === "string" ? event.occurredAt : null,
    startedAt: typeof event.startedAt === "string" ? event.startedAt : null,
    aggregateCount: Number.isInteger(event.aggregateCount) ? event.aggregateCount : null,
    activityKey: typeof event.activityKey === "string" ? event.activityKey : null,
    phase: typeof event.phase === "string" ? event.phase : null,
    artifactRefs: stringList(event.artifactRefs),
    resultRefs: stringList(event.resultRefs),
    scientificStateVersions: integerList(event.scientificStateVersions),
  };
}

export function distinctResearchActivities(rows = []) {
  const seen = new Set();
  const retained = [];
  for (let index = rows.length - 1; index >= 0; index -= 1) {
    const row = rows[index];
    if (!row) continue;
    const actionKey = typeof row.activityKey === "string" ? row.activityKey.trim() : "";
    const workUnitId = typeof row.workUnitId === "string" ? row.workUnitId.trim() : "";
    const eventId = typeof row.eventId === "string" ? row.eventId.trim() : "";
    const planningPhase = /^(planning-attempt:|research-design:)/.test(actionKey)
      ? `:${row.phase ?? ""}` : "";
    const identity = actionKey
      ? `action:${workUnitId || "unscoped"}:${actionKey}${planningPhase}`
      : eventId
        ? `event:${eventId}`
        : "";
    if (identity && seen.has(identity)) continue;
    if (identity) seen.add(identity);
    retained.push(row);
  }
  return retained.reverse();
}



const WORK_LIFECYCLE_PHASES = new Set([
  "scheduled", "queued", "started", "running", "reviewing",
  "completed", "succeeded", "failed", "blocked", "stopped", "stopped_by_user",
]);

const ACTION_TERMINAL_PHASES = new Set(["completed", "succeeded", "failed", "blocked", "stopped", "stopped_by_user"]);

const CANONICAL_TERMINAL_STATUSES = new Set([
  "completed", "succeeded", "failed", "blocked", "blocked_recoverable",
  "external_result_unknown", "cancelled", "stopped", "stopped_by_user",
]);

function canonicalRevision(currentState) {
  return Number.isInteger(currentState?.revision) ? currentState.revision : null;
}

function canonicalWorkUnitMap(currentState) {
  const rows = Array.isArray(currentState?.workUnits) ? currentState.workUnits : [];
  const result = new Map();
  for (const row of rows) {
    const workUnitId = typeof row?.workUnitId === "string" ? row.workUnitId.trim() : "";
    const status = typeof row?.status === "string" ? row.status.trim() : "";
    if (!workUnitId || !status) continue;
    result.set(workUnitId, {
      workUnitId,
      status,
      startedAt: typeof row.startedAt === "string" ? row.startedAt : null,
      finishedAt: typeof row.finishedAt === "string" ? row.finishedAt : null,
    });
  }
  return result;
}

function canonicalTaskState(currentState) {
  const status = typeof currentState?.taskStatus === "string" ? currentState.taskStatus.trim() : "";
  return {
    status: status || null,
    finishedAt: typeof currentState?.taskFinishedAt === "string" ? currentState.taskFinishedAt : null,
  };
}

function phaseMatchesCanonicalStatus(phase, status) {
  if (!phase || !status) return false;
  if (["completed", "succeeded"].includes(status)) return ["completed", "succeeded"].includes(phase);
  if (status === "failed") return phase === "failed";
  if (["blocked", "blocked_recoverable", "external_result_unknown"].includes(status)) return phase === "blocked";
  if (status === "cancelled" || status === "stopped") return phase === "stopped";
  if (status === "stopped_by_user") return phase === "stopped_by_user";
  return phase === status;
}

function mergeIdentity(row) {
  const activityKey = typeof row?.activityKey === "string" ? row.activityKey.trim() : "";
  const workUnitId = typeof row?.workUnitId === "string" ? row.workUnitId.trim() : "";
  if (
    row?.presentationLevel !== "detail"
    && workUnitId
    && typeof row?.phase === "string"
    && WORK_LIFECYCLE_PHASES.has(row.phase)
  ) {
    return `work-phase:${workUnitId}:${row.phase}`;
  }
  if (/^(planning-attempt:|research-design:)/.test(activityKey)) {
    return `planning:${activityKey}:${row.phase ?? ""}`;
  }
  if (activityKey) return `activity:${workUnitId || "unscoped"}:${activityKey}`;
  const eventId = typeof row?.eventId === "string" ? row.eventId.trim() : "";
  return eventId ? `event:${eventId}` : null;
}

function parsedTime(value) {
  if (typeof value !== "string" || !value.trim()) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function liveRowCoveredByCanonicalSnapshot(row, currentState, workUnits) {
  const snapshotRevision = canonicalRevision(currentState);
  const lifecycleRow = row.presentationLevel !== "detail"
    && typeof row.phase === "string"
    && WORK_LIFECYCLE_PHASES.has(row.phase);
  if (
    lifecycleRow
    && snapshotRevision !== null
    && row.revision !== null
    && row.revision <= snapshotRevision
  ) {
    // Lifecycle replay at or behind the Task snapshot is already represented by
    // canonical current state. Meaningful historical facts are not discarded
    // merely because they share an older revision.
    return true;
  }

  if (
    row.presentationLevel === "detail"
    || !row.workUnitId
    || !ACTION_TERMINAL_PHASES.has(row.phase)
  ) return false;

  const canonical = workUnits.get(row.workUnitId);
  if (!canonical || !CANONICAL_TERMINAL_STATUSES.has(canonical.status)) return false;
  if (phaseMatchesCanonicalStatus(row.phase, canonical.status)) return false;

  const eventTime = parsedTime(row.occurredAt);
  const canonicalFinishedAt = parsedTime(canonical.finishedAt);
  // Without a revision, only reject a contradictory terminal when the existing
  // canonical finish timestamp proves that the live row is older. Otherwise it
  // may remain as non-current history, but it still cannot become Authority.
  return eventTime !== null && canonicalFinishedAt !== null && eventTime <= canonicalFinishedAt;
}

/**
 * Merge a Task-GET durable bootstrap with later SSE rows.
 *
 * The fourth argument is the canonical current snapshot. It is deliberately
 * separate from activity history: SSE/history may describe what happened, but
 * never decide what the WorkUnit or Task is now. Replay events already covered
 * by the canonical revision are ignored. If revision evidence is unavailable,
 * a contradictory older terminal is ignored only when canonical timestamps
 * prove it stale; otherwise it is retained as non-current history.
 */
export function mergeResearchActivityHistory(
  durableActivities = [],
  livePrimaryActivities = [],
  liveCollapsedDetails = [],
  canonicalCurrentState = null,
) {
  const durable = durableActivities.map((row) => activityFrom(row));
  const live = [...livePrimaryActivities, ...liveCollapsedDetails].map((row) => activityFrom(row));
  const workUnits = canonicalWorkUnitMap(canonicalCurrentState);
  const positions = new Map();
  const origins = [];
  const merged = [];

  for (const row of durable) {
    const identity = mergeIdentity(row);
    if (identity && positions.has(identity)) {
      merged[positions.get(identity)] = row;
      origins[positions.get(identity)] = "durable";
      continue;
    }
    if (identity) positions.set(identity, merged.length);
    merged.push(row);
    origins.push("durable");
  }

  for (const row of live) {
    const planningHistory = row.activityKey?.startsWith("planning-attempt:")
      || row.activityKey?.startsWith("planning-thought:");
    if (!planningHistory && liveRowCoveredByCanonicalSnapshot(row, canonicalCurrentState, workUnits)) continue;
    const identity = mergeIdentity(row);
    if (identity && positions.has(identity)) {
      const position = positions.get(identity);
      // Durable bootstrap is the stable history for the current snapshot. A
      // live row may replace the same presentation fact only when its revision
      // is provably newer than that snapshot. Missing revision is not guessed.
      const previous = merged[position];
      const thinking = row.activityKey?.startsWith("planning-thought:");
      const growsThought = thinking && previous.phase === "forming"
        && typeof row.summary === "string"
        && row.summary.startsWith(previous.summary ?? "");
      if (thinking && previous.phase === "formed") continue;
      if (origins[position] === "durable") {
        const snapshotRevision = canonicalRevision(canonicalCurrentState);
        const isProvablyNewer = snapshotRevision !== null
          && row.revision !== null
          && row.revision > snapshotRevision;
        if (!isProvablyNewer && !growsThought) continue;
      }
      merged[position] = thinking
        ? { ...row, eventId: previous.eventId, occurredAt: previous.occurredAt }
        : row;
      origins[position] = "live";
      continue;
    }
    if (identity) positions.set(identity, merged.length);
    merged.push(row);
    origins.push("live");
  }

  return {
    primaryActivities: merged.filter((row) => row.presentationLevel !== "detail"),
    collapsedDetails: merged.filter((row) => row.presentationLevel === "detail"),
  };
}

export function reduceResearchActivityEvent(state, event, bindingKey = state.bindingKey) {
  const current = state.bindingKey === bindingKey ? state : createResearchActivityState(bindingKey);
  if (!DISPLAY_EVENT_TYPES.has(event?.eventType)) return current;
  const activity = activityFrom(event);
  if (!activity.eventId || current.seenEventIds.includes(activity.eventId)) return current;

  const collection = activity.presentationLevel === "detail" ? "collapsedDetails" : "primaryActivities";
  let nextActivity = activity;
  if (collection === "collapsedDetails" && activity.workUnitId && activity.activityKey) {
    const previous = [...current.collapsedDetails].reverse().find((item) => (
      item.workUnitId === activity.workUnitId && item.activityKey === activity.activityKey
    ));
    const startedAt = previous?.startedAt
      ?? (["scheduled", "queued", "started", "running"].includes(previous?.phase) ? previous?.occurredAt : null)
      ?? (["scheduled", "queued", "started", "running"].includes(activity.phase) ? activity.occurredAt : null);
    nextActivity = { ...activity, startedAt: startedAt ?? activity.startedAt ?? null };
  }
  let next = [...current[collection], nextActivity];
  if (collection === "collapsedDetails" && activity.workUnitId && activity.activityKey) {
    // Replace only lifecycle churn for the same capability call. Distinct calls
    // and distinct research facts in the same WorkUnit remain append-only.
    next = next.filter((item, index, rows) => {
      if (item.workUnitId !== activity.workUnitId || item.activityKey !== activity.activityKey) return true;
      return index === rows.length - 1;
    });
  }
  return {
    ...current,
    bindingKey,
    lastEventId: activity.eventId,
    [collection]: next,
    seenEventIds: [...current.seenEventIds, activity.eventId],
    invalidateScopes: [...new Set([...current.invalidateScopes, ...scopesFrom(event)])],
  };
}


function compareActivityOrder(left, right) {
  const leftTime = left.occurredAt ? Date.parse(left.occurredAt) : Number.NaN;
  const rightTime = right.occurredAt ? Date.parse(right.occurredAt) : Number.NaN;
  if (Number.isFinite(leftTime) && Number.isFinite(rightTime) && leftTime !== rightTime) {
    return leftTime - rightTime;
  }
  // Sequence is only a transport-local tie breaker. It is never compared ahead
  // of fact time and never used to infer current state.
  if (left.sequence !== null && right.sequence !== null && left.sequence !== right.sequence) {
    return left.sequence - right.sequence;
  }
  if (left.sequence !== null && right.sequence === null) return -1;
  if (right.sequence !== null && left.sequence === null) return 1;
  return String(left.eventId ?? "").localeCompare(String(right.eventId ?? ""));
}

function isHighValueProcessActivity(row, terminalActivity) {
  if (!row || row === terminalActivity) return false;
  // Detail rows have already been compacted by capability-call activityKey, so
  // each retained row represents one real research operation rather than its
  // scheduled/started/completed lifecycle chatter.
  if (row.presentationLevel === "detail") return true;
  // Primary meaningful_progress facts describe durable user-relevant changes
  // such as artifacts, review outcomes, handoffs, waits, or recovery-relevant
  // execution facts. Keep all of them; do not truncate by an arbitrary count.
  return row.eventType === "meaningful_progress";
}

function validTime(value) {
  if (typeof value !== "string" || !value.trim()) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? { text: value, ms: parsed } : null;
}

function timeBounds(rows) {
  const times = rows.map((row) => validTime(row?.occurredAt)).filter(Boolean);
  if (!times.length) return { startedAt: null, latestOccurredAt: null };
  const first = times.reduce((left, right) => left.ms <= right.ms ? left : right);
  const last = times.reduce((left, right) => left.ms >= right.ms ? left : right);
  return { startedAt: first.text, latestOccurredAt: last.text };
}

/**
 * Fold append-only research facts into a user-facing research activity view.
 * This is presentation only: replay cursors, eventId dedupe, and the retained
 * canonical activity arrays are intentionally left untouched.
 *
 * The fold is semantic, not count based. Every retained capability-call
 * summary and meaningful durable progress fact remains visible; only lifecycle
 * bookkeeping is excluded from the research-process narrative.
 */
export function foldResearchActivityPresentation(
  primaryActivities = [],
  collapsedDetails = [],
  canonicalCurrentState = null,
) {
  const rows = [...primaryActivities, ...collapsedDetails].sort(compareActivityOrder);
  const byWorkUnit = new Map();
  const unscoped = [];
  const currentWorkUnits = canonicalWorkUnitMap(canonicalCurrentState);

  for (const row of rows) {
    if (!row?.workUnitId) {
      unscoped.push(row);
      continue;
    }
    const current = byWorkUnit.get(row.workUnitId) ?? [];
    current.push(row);
    byWorkUnit.set(row.workUnitId, current);
  }

  const blocks = [...byWorkUnit.entries()].map(([workUnitId, items]) => {
    const canonical = currentWorkUnits.get(workUnitId) ?? null;
    const currentStatus = canonical?.status ?? null;
    const currentIsTerminal = currentStatus !== null && CANONICAL_TERMINAL_STATUSES.has(currentStatus);
    const terminalActivity = currentIsTerminal
      ? [...items].reverse().find((row) => (
        row?.presentationLevel !== "detail"
        && ACTION_TERMINAL_PHASES.has(row?.phase)
        && phaseMatchesCanonicalStatus(row.phase, currentStatus)
      )) ?? null
      : null;
    const processActivities = items.filter((row) => isHighValueProcessActivity(row, terminalActivity));
    const artifactRefs = [...new Set(items.flatMap((row) => Array.isArray(row?.artifactRefs) ? row.artifactRefs : []))];
    const resultRefs = [...new Set(items.flatMap((row) => Array.isArray(row?.resultRefs) ? row.resultRefs : []))];
    const scientificStateVersions = [...new Set(items.flatMap((row) => Array.isArray(row?.scientificStateVersions) ? row.scientificStateVersions : []))];
    const bounds = timeBounds(items);
    return {
      workUnitId,
      rows: items,
      processActivities,
      currentStatus,
      terminalActivity,
      artifactRefs,
      resultRefs,
      scientificStateVersions,
      startedAt: canonical?.startedAt ?? bounds.startedAt,
      endedAt: currentIsTerminal ? canonical?.finishedAt ?? null : null,
      latestOccurredAt: bounds.latestOccurredAt,
    };
  });

  const visibleUnscoped = unscoped.filter((row) => (
    row?.presentationLevel === "detail" || row?.eventType === "meaningful_progress"
  ));
  const streamBounds = timeBounds(rows);
  const task = canonicalTaskState(canonicalCurrentState);
  const taskIsTerminal = task.status !== null && ["completed", "failed", "cancelled", "stopped", "stopped_by_user"].includes(task.status);

  return {
    blocks,
    unscoped: visibleUnscoped,
    startedAt: streamBounds.startedAt,
    endedAt: taskIsTerminal ? task.finishedAt : null,
    latestOccurredAt: streamBounds.latestOccurredAt,
  };
}

const LIVE_STEP_STATUSES = new Set(["scheduled", "queued", "started", "running", "reviewing"]);
const OPEN_STEP_STATUSES = new Set([
  "failed", "blocked", "blocked_recoverable", "external_result_unknown", "needs_input",
]);

function stepStatusRank(status) {
  if (LIVE_STEP_STATUSES.has(status)) return 4;
  if (OPEN_STEP_STATUSES.has(status)) return 3;
  if (status === "completed" || status === "succeeded") return 2;
  if (status) return 1;
  return 0;
}

function stepTimeMs(value) {
  const parsed = Date.parse(typeof value === "string" ? value : "");
  return Number.isFinite(parsed) ? parsed : 0;
}

/**
 * One user-facing research step card per objective. Internal retries that mint
 * new WorkUnits must not repeat the same goal as a second hero block; their
 * process facts nest under the current step instead.
 */
export function coalesceResearchActivityBlocks(blocks = [], objectiveByWorkUnit = new Map(), goalByWorkUnit = new Map()) {
  if (!Array.isArray(blocks) || blocks.length <= 1) return Array.isArray(blocks) ? blocks : [];
  const groups = new Map();
  for (const block of blocks) {
    if (!block) continue;
    const objective = String(
      objectiveByWorkUnit instanceof Map
        ? (objectiveByWorkUnit.get(block.workUnitId) ?? "")
        : (objectiveByWorkUnit?.[block.workUnitId] ?? ""),
    ).trim();
    const key = goalByWorkUnit.get(block.workUnitId) || objective || `work-unit:${block.workUnitId}`;
    const bucket = groups.get(key) ?? [];
    bucket.push(block);
    groups.set(key, bucket);
  }

  return [...groups.values()].map((group) => {
    if (group.length === 1) return group[0];
    const ordered = [...group].sort((left, right) => {
      const rankDelta = stepStatusRank(right.currentStatus) - stepStatusRank(left.currentStatus);
      if (rankDelta) return rankDelta;
      return Math.max(stepTimeMs(right.startedAt), stepTimeMs(right.latestOccurredAt))
        - Math.max(stepTimeMs(left.startedAt), stepTimeMs(left.latestOccurredAt));
    });
    const primary = ordered[0];
    const chronological = [...ordered].sort((left, right) => (
      Math.max(stepTimeMs(left.startedAt), stepTimeMs(left.latestOccurredAt))
      - Math.max(stepTimeMs(right.startedAt), stepTimeMs(right.latestOccurredAt))
    ));
    const processActivities = [];
    const seen = new Set();
    for (const block of chronological) {
      for (const row of block.processActivities ?? []) {
        const identity = row?.eventId
          || `${row?.activityKey ?? ""}:${row?.occurredAt ?? ""}:${row?.headline ?? ""}`;
        if (!identity || seen.has(identity)) continue;
        seen.add(identity);
        processActivities.push(row);
      }
    }
    processActivities.sort(compareActivityOrder);
    const startedAt = chronological
      .map((block) => block.startedAt)
      .filter(Boolean)
      .sort((left, right) => stepTimeMs(left) - stepTimeMs(right))[0] ?? primary.startedAt;
    const latestOccurredAt = chronological
      .map((block) => block.latestOccurredAt)
      .filter(Boolean)
      .sort((left, right) => stepTimeMs(right) - stepTimeMs(left))[0] ?? primary.latestOccurredAt;
    return {
      ...primary,
      rows: chronological.flatMap((block) => block.rows ?? []),
      processActivities,
      artifactRefs: [...new Set(ordered.flatMap((block) => block.artifactRefs ?? []))],
      resultRefs: [...new Set(ordered.flatMap((block) => block.resultRefs ?? []))],
      scientificStateVersions: [...new Set(ordered.flatMap((block) => block.scientificStateVersions ?? []))],
      startedAt,
      latestOccurredAt,
    };
  }).sort((left, right) => stepTimeMs(left.startedAt) - stepTimeMs(right.startedAt));
}


/** Legacy Workflow contexts refined one registered responsibility, not separate goals.
 * Freeform objectives without that assembly marker keep their formal instance identity.
 */
export function researchWorkUnitContexts(substages = []) {
  const groups = new Map();
  for (const substage of substages) {
    const objective = String(substage.objective || substage.label || "").trim();
    const marker = "。当前项目科研重点：";
    const index = objective.indexOf(marker);
    const legacy = index >= 0 && substage.substageDefinitionId;
    const goalKey = legacy
      ? `workflow:${substage.substageDefinitionId}:${objective.slice(0, index)}`
      : `instance:${substage.substageInstanceId}`;
    const group = groups.get(goalKey) ?? { goalKey, objectives: [], workUnitIds: [] };
    if (objective && !group.objectives.includes(objective)) group.objectives.push(objective);
    group.workUnitIds.push(...(substage.workUnitIds ?? []));
    groups.set(goalKey, group);
  }
  return [...groups.values()].flatMap((group) => {
    const first = group.objectives[0] || "";
    const marker = "。当前项目科研重点：";
    const index = first.indexOf(marker);
    const objective = group.objectives.length > 1 && index >= 0
      ? first.slice(0, index) + marker + group.objectives.map((value) =>
          value.slice(value.indexOf(marker) + marker.length).replace(/。+$/, "")
        ).join("；") + "。"
      : first;
    return [...new Set(group.workUnitIds)].map((workUnitId) => ({
      workUnitId, objective, goalKey: group.goalKey,
    }));
  });
}
