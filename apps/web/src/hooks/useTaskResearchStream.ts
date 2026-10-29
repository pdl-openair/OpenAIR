import { useEffect, useRef, useState } from "react";
import {
  subscribeTaskResearchEvents,
  type ResearchStreamEvent,
} from "../api/taskResearchStream";
import {
  assertSafeResearchActivity,
  createResearchActivityState,
  reduceResearchActivityEvent,
} from "../model/researchActivityStreamCore.mjs";
import { getWorkbenchTaskRequestCoordinator } from "../lib/workbenchTaskRequestCoordinator";
import { ApiError } from "../api/http";

const invalidationDebounceMs = 140;
const disconnectedAfterAttempts = 5;
const recoveredNoticeMs = 2500;

type ResearchActivityState = ReturnType<typeof createResearchActivityState>;

export type ResearchStreamConnectionState =
  | "connecting"
  | "live"
  | "reconnecting"
  | "disconnected"
  | "reset_required";

function activityEvent(event: ResearchStreamEvent) {
  const payload = event.payload ?? {};
  return {
    eventId: event.id ?? null,
    taskId: event.taskId,
    sequence: event.sequence ?? null,
    eventType: event.type,
    presentationLevel: payload.presentationLevel === "detail" ? "detail" : "primary",
    workUnitId: typeof payload.workUnitId === "string" ? payload.workUnitId : null,
    headline: typeof payload.headline === "string" ? payload.headline : "研究进展已更新",
    summary: typeof payload.summary === "string" ? payload.summary : null,
    occurredAt: event.occurredAt ?? event.emittedAt ?? null,
    aggregateCount: typeof payload.aggregateCount === "number" ? payload.aggregateCount : null,
    activityKey: typeof payload.activityKey === "string" ? payload.activityKey : null,
    phase: typeof payload.phase === "string" ? payload.phase : null,
    artifactRefs: Array.isArray(payload.artifactRefs) ? payload.artifactRefs : [],
    invalidateScopes: Array.isArray(payload.invalidateScopes) ? payload.invalidateScopes : [],
  };
}

function retryDelay(attempt: number, serverRetryMs: number): number {
  const exponential = Math.min(15000, 1000 * 2 ** Math.min(attempt, 4));
  return Math.max(serverRetryMs, exponential);
}

export function useTaskResearchStream(
  taskId: string,
  enabled: boolean,
  onInvalidate?: (scopes: string[]) => void,
) {
  const generationRef = useRef(0);
  const [state, setState] = useState<ResearchActivityState>(() => createResearchActivityState(taskId));
  const [connectionState, setConnectionState] = useState<ResearchStreamConnectionState>("connecting");
  const [connectionNotice, setConnectionNotice] = useState<string | null>(null);

  useEffect(() => {
    const generation = ++generationRef.current;
    const bindingKey = taskId;
    const lifecycleController = new AbortController();
    const controller = lifecycleController;
    let invalidationTimer: number | null = null;
    let reconnectTimer: number | null = null;
    let recoveredTimer: number | null = null;
    let pendingScopes = new Set<string>();
    let lastEventId: string | null = null;
    let retryMs = 3000;
    let attempt = 0;
    let openedOnce = false;
    let connectInFlight = false;

    setState((current) => (
      current.bindingKey === taskId || current.bindingKey.startsWith(`${taskId}:`)
        ? current
        : createResearchActivityState(bindingKey)
    ));
    setConnectionNotice(null);
    setConnectionState(enabled && taskId ? "connecting" : "disconnected");
    if (!enabled || !taskId) return () => controller.abort();
    const coordinator = getWorkbenchTaskRequestCoordinator("workbench-page", "canonical", taskId);
    const releaseCoordinator = coordinator.acquire();

    // consume-invalidations batches state.invalidateScopes before authoritative GET refreshes.
    const consumeInvalidations = () => {
      invalidationTimer = null;
      const scopes = [...pendingScopes];
      pendingScopes = new Set();
      if (generationRef.current === generation && scopes.length) onInvalidate?.(scopes);
    };

    const queueInvalidations = (scopes: string[]) => {
      scopes.forEach((scope) => pendingScopes.add(scope));
      if (invalidationTimer === null) {
        invalidationTimer = window.setTimeout(consumeInvalidations, invalidationDebounceMs);
      }
    };

    const handleEvent = (event: ResearchStreamEvent) => {
      if (generationRef.current !== generation || event.taskId !== taskId) return;
      // Keepalives and unrecognized internal frames never reach presentation state or invalidation.
      if (
        event.type === "heartbeat"
        || event.type.startsWith("internal_")
        || (event.payload as { internal?: unknown; visibility?: unknown } | undefined)?.internal === true
        || (event.payload as { visibility?: unknown } | undefined)?.visibility === "internal"
      ) return;
      if (event.type === "stream_reset") {
        // A server reset requests replay/reconciliation. It must not erase the
        // append-only presentation history already visible to the researcher.
        // The next reconnect continues from the last known cursor and lets the
        // server replay missing events with eventId dedupe.
        setConnectionState("reset_required");
        queueInvalidations(event.payload?.invalidateScopes ?? ["task", "collaboration", "artifact", "review", "recovery"]);
        return;
      }
      if (![
        "research_progress",
        "research_tool_summary",
        "meaningful_progress",
      ].includes(event.type)) return;
      const activity = activityEvent(event);
      if (!event.id) return;
      try {
        assertSafeResearchActivity(activity);
      } catch {
        return;
      }
      setState((current) => reduceResearchActivityEvent(current, activity, bindingKey));
      lastEventId = event.id;
      queueInvalidations(activity.invalidateScopes);
      setConnectionState("live");
    };

    const connect = async () => {
      if (lifecycleController.signal.aborted || generationRef.current !== generation) return;
      if (connectInFlight) return;
      if (document.visibilityState === "hidden") {
        setConnectionState("disconnected");
        return;
      }
      connectInFlight = true;
      setConnectionState(attempt === 0 ? "connecting" : attempt >= disconnectedAfterAttempts ? "disconnected" : "reconnecting");
      try {
        await coordinator.request(
          "research-event-stream",
          (signal: AbortSignal) => subscribeTaskResearchEvents(
            taskId,
            {
              onOpen: () => {
                if (generationRef.current !== generation) return;
                setConnectionState("live");
                if (openedOnce) {
                  setConnectionNotice("连接已恢复");
                  if (recoveredTimer !== null) window.clearTimeout(recoveredTimer);
                  recoveredTimer = window.setTimeout(() => setConnectionNotice(null), recoveredNoticeMs);
                }
                openedOnce = true;
                attempt = 0;
              },
              onEvent: handleEvent,
              onRetry: (value) => { retryMs = value; },
            },
            signal,
            lastEventId,
          ),
        );
        if (!lifecycleController.signal.aborted) throw new Error("research stream closed");
      } catch (error) {
        if (lifecycleController.signal.aborted || (error as { name?: string })?.name === "AbortError") return;
        if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
          setConnectionState("disconnected");
          return;
        }
        attempt += 1;
        setConnectionState(attempt >= disconnectedAfterAttempts ? "disconnected" : "reconnecting");
        const sharedRetryMs = Math.max(0, coordinator.backoffUntil - Date.now());
        if (reconnectTimer === null) {
          const scheduledGeneration = generation;
          reconnectTimer = window.setTimeout(() => {
            reconnectTimer = null;
            if (generationRef.current !== scheduledGeneration || lifecycleController.signal.aborted) return;
            void connect();
          }, retryDelay(attempt, Math.max(retryMs, sharedRetryMs)));
        }
      } finally {
        connectInFlight = false;
      }
    };

    const handleVisibility = () => {
      if (document.visibilityState === "hidden") {
        coordinator.cancel("research-event-stream");
        if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
        reconnectTimer = null;
        setConnectionState("disconnected");
      } else if (!lifecycleController.signal.aborted) {
        if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
        reconnectTimer = null;
        void connect();
      }
    };
    document.addEventListener("visibilitychange", handleVisibility);
    void connect();
    return () => {
      controller.abort();
      coordinator.cancel("research-event-stream");
      releaseCoordinator();
      document.removeEventListener("visibilitychange", handleVisibility);
      if (invalidationTimer !== null) window.clearTimeout(invalidationTimer);
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      if (recoveredTimer !== null) window.clearTimeout(recoveredTimer);
    };
  }, [enabled, onInvalidate, taskId]);

  const stateMatchesTask = state.bindingKey === taskId || state.bindingKey.startsWith(`${taskId}:`);
  const visibleState = stateMatchesTask ? state : createResearchActivityState(taskId);
  const visibleConnectionState = !enabled || !taskId
    ? "disconnected"
    : stateMatchesTask ? connectionState : "connecting";

  return {
    ...visibleState,
    connectionState: visibleConnectionState,
    connectionNotice: stateMatchesTask ? connectionNotice : null,
  };
}
