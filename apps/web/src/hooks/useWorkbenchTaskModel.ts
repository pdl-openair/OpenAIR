import { useCallback, useEffect, useRef, useState } from "react";
import type { UiLanguage } from "@openair/shared";
import {
  readDurableProgressFromTaskEvent,
  shouldAcceptProgressRevision,
  subscribeTaskEvents,
  type TaskEvent,
} from "../api/client";
import { loadTaskForPages, type TaskDataSource } from "../lib/loadTask";
import {
  DURABLE_LIFECYCLE_EVENT_TYPES,
  classifyTaskEvent,
  lifecycleBucketKey,
} from "../lib/taskEventIdentity";
import type { WorkbenchTaskModel } from "../model/workbenchTask";
import { TASK_LOAD_EN, TASK_LOAD_ZH, classifyTaskLoadFailure, userFacingTaskLoadError } from "../lib/taskLoadError.mjs";
import { getWorkbenchTaskRequestCoordinator } from "../lib/workbenchTaskRequestCoordinator";
import { ApiError } from "../api/http";
import { rememberCurrentTask } from "../lib/userRouteState";

type LoadState =
  | { status: "loading"; reload: () => void }
  | {
      status: "ready";
      model: WorkbenchTaskModel;
      source: TaskDataSource;
      reload: () => void;
      isRefreshing: boolean;
      progressStreamStale: boolean;
    }
  | { status: "error"; message: string; errorKind: "unauthorized" | "forbidden" | "notFound" | "conflict" | "precondition" | "rateLimited" | "serviceUnavailable" | "loadFailed"; reload: () => void };

const WORKBENCH_POLL_INTERVAL_MS = 2_000;
const PERSISTENT_PROGRESS_STALE_MS = 30_000;
function appendLifecycleEvent(events: TaskEvent[], event: TaskEvent): TaskEvent[] {
  const existingIndex = events.findIndex((item) => item.id === event.id);
  const next = existingIndex >= 0
    ? events.map((item, index) => index === existingIndex ? event : item)
    : [...events, event];
  return next
    .sort((left, right) => (left.sequence ?? 0) - (right.sequence ?? 0) || left.id.localeCompare(right.id))
    .slice(-100);
}

export function useWorkbenchTaskModel(taskId: string | undefined, language: UiLanguage = "zh-CN"): LoadState & {
  silentReload: (forceMissing?: boolean) => void;
} {
  const [refreshRequest, setRefreshRequest] = useState({ seq: 0, silent: false, forceMissing: false });
  const reload = useCallback(
    () => setRefreshRequest((prev) => ({ seq: prev.seq + 1, silent: false, forceMissing: false })),
    [],
  );
  const silentReload = useCallback(
    (forceMissing = false) =>
      setRefreshRequest((prev) => ({ seq: prev.seq + 1, silent: true, forceMissing })),
    [],
  );
  const [state, setState] = useState<LoadState>({ status: "loading", reload });
  // Applied durable projection revision — stale SSE/fetch must not overwrite newer UI.
  const appliedRevisionRef = useRef(0);
  const activeRunIdRef = useRef<string | null>(null);
  const lifecycleEventsByRunRef = useRef(new Map<string, TaskEvent[]>());
  // The effect below reloads when `taskId` changes, but effects run after commit.
  // Without this guard the component would render one frame with the PREVIOUS
  // task's state (e.g. a stale "缺少任务 ID" error) right after `/workbench/current`
  // swaps to a freshly created task id — surfacing a transient red error on the
  // waiting page. Reset to loading synchronously during render when the requested
  // task id changes so the intermediate frame shows a spinner, not stale state.
  const [settledTaskId, setSettledTaskId] = useState<string | undefined>(taskId);
  if (taskId !== settledTaskId) {
    setSettledTaskId(taskId);
    appliedRevisionRef.current = 0;
    activeRunIdRef.current = null;
    lifecycleEventsByRunRef.current.clear();
    setState(
      taskId
        ? { status: "loading", reload }
        : { status: "error", message: "未找到要打开的项目", errorKind: "loadFailed", reload },
    );
  }

  useEffect(() => {
    if (!taskId) return;
    const coordinator = getWorkbenchTaskRequestCoordinator("workbench-page", "canonical", taskId);
    return coordinator.acquire();
  }, [taskId]);

  useEffect(() => {
    if (!taskId) {
      setState({ status: "error", message: "未找到要打开的项目", errorKind: "loadFailed", reload });
      return;
    }

    let cancelled = false;
    const coordinator = getWorkbenchTaskRequestCoordinator("workbench-page", "canonical", taskId);

    async function run(): Promise<void> {
      setState((prev) => {
        if (prev.status === "ready") {
          return {
            ...prev,
            reload,
            isRefreshing: refreshRequest.silent ? false : true,
          };
        }
        // A silent recheck must not replace the current screen with a full-page loader.
        if (refreshRequest.silent) return prev;
        return { status: "loading", reload };
      });

      const nextTaskId = taskId;
      if (!nextTaskId) {
        return;
      }
      let loaded;
      try {
        loaded = await coordinator.request(
          "task-model",
          () =>
            loadTaskForPages(nextTaskId, language, { allowFixtureFallback: false }),
          { allowWhenHidden: !refreshRequest.silent },
        ) as Awaited<ReturnType<typeof loadTaskForPages>>;
      } catch (err) {
        if (cancelled) return;
        const errorKind = classifyTaskLoadFailure(err);
        const message = userFacingTaskLoadError(err, language);
        setState((prev) => {
          if (prev.status === "ready" && refreshRequest.silent && !refreshRequest.forceMissing) {
            return {
              ...prev,
              reload,
              isRefreshing: false,
            };
          }
          return {
            status: "error",
            message,
            errorKind,
            reload,
          };
        });
        return;
      }
      if (cancelled) return;
      if (loaded) {
        if (loaded.source === "api") rememberCurrentTask(loaded.task.taskId);
        const previouslyAppliedRevision = appliedRevisionRef.current;
        const incomingRevision = loaded.model.progressRevision ?? loaded.model.revision ?? 0;
        if (
          refreshRequest.silent
          && loaded.source === "api"
          && loaded.model.progressTrust.kind !== "legacy"
          && !shouldAcceptProgressRevision(appliedRevisionRef.current, incomingRevision)
        ) {
          // Stale fetch after a newer durable revision — keep current UI.
          setState((prev) =>
            prev.status === "ready"
              ? { ...prev, reload, isRefreshing: false }
              : prev,
          );
          return;
        }
        if (loaded.source === "api") {
          appliedRevisionRef.current = Math.max(appliedRevisionRef.current, incomingRevision);
        }
        if (incomingRevision > previouslyAppliedRevision) {
          activeRunIdRef.current = null;
        }
        const lifecycleEvents = activeRunIdRef.current
          ? lifecycleEventsByRunRef.current.get(
              lifecycleBucketKey(nextTaskId, incomingRevision, activeRunIdRef.current),
            ) ?? []
          : [];
        setState((prev) => ({
          status: "ready",
          model: { ...loaded.model, lifecycleEvents },
          source: loaded.source,
          reload,
          isRefreshing: false,
          progressStreamStale: Boolean(
            refreshRequest.silent
            && incomingRevision <= previouslyAppliedRevision
            && prev.status === "ready"
            && prev.progressStreamStale
          ),
        }));
        return;
      }

      // null return means authoritative 404 (distinct from auth/scope which throws)
      const message = language === "en-US" ? TASK_LOAD_EN.notFound : TASK_LOAD_ZH.notFound;

      setState((prev) => {
        if (prev.status === "ready" && refreshRequest.silent && !refreshRequest.forceMissing) {
          return {
            ...prev,
            reload,
            isRefreshing: false,
          };
        }
        return {
          status: "error",
          message,
          errorKind: "notFound",
          reload,
        };
      });
    }

    void run();
    return () => {
      cancelled = true;
    };
  }, [taskId, refreshRequest, reload, language]);

  const streamModelRef = useRef<WorkbenchTaskModel | null>(null);
  if (state.status === "ready" && state.source === "api") {
    streamModelRef.current = state.model;
  }

  useEffect(() => {
    if (!taskId) return;
    if (state.status !== "ready" || state.source !== "api") return;

    const coordinator = getWorkbenchTaskRequestCoordinator("workbench-page", "canonical", taskId);
    let reloadTimer: number | null = null;
    let stopped = false;
    const interruptionKind = state.model.interruption?.kind ?? null;
    const stoppedByUser =
      interruptionKind === "user_stop" || interruptionKind === "execution_cancelled";
    const executionIsRunning = !stoppedByUser && (
      state.model.taskStatus === "running"
      || state.model.executionDisplayStatus === "running"
    );
    const streamClosedByTaskStatus = state.model.taskStatus === "completed"
      || state.model.taskStatus === "failed"
      || state.model.taskStatus === "cancelled"
      || stoppedByUser;

    if (streamClosedByTaskStatus) {
      coordinator.markManagedStreamTerminal("task-event-stream");
      setState((previous) => previous.status === "ready"
        ? previous.progressStreamStale
          ? { ...previous, progressStreamStale: false }
          : previous
        : previous);
      return;
    }

    const scheduleReload = () => {
      if (reloadTimer !== null) window.clearTimeout(reloadTimer);
      reloadTimer = window.setTimeout(() => {
        reloadTimer = null;
        silentReload();
      }, 300);
    };

    const handleEvent = (event: TaskEvent) => {
      if (stopped) return;
      const currentModel = streamModelRef.current;
      if (!currentModel || currentModel.taskId !== taskId) return;
      const authoritativeRevision = currentModel.progressRevision ?? currentModel.revision;
      const classification = classifyTaskEvent(
        event,
        {
          taskId,
          ownerId: currentModel.ownerId ?? "",
          workspaceId: currentModel.workspaceId ?? "",
          revision: authoritativeRevision,
        },
        activeRunIdRef.current,
      );
      if (classification.refresh) {
        scheduleReload();
        return;
      }
      if (!classification.accepted) return;
      if (event.type === "snapshot") activeRunIdRef.current = classification.runId ?? null;
      if (event.type === "heartbeat") return;
      coordinator.touchManagedStream("task-event-stream");
      if (
        DURABLE_LIFECYCLE_EVENT_TYPES.has(event.type)
        && typeof event.revision === "number"
        && typeof event.runId === "string"
      ) {
        const bucket = lifecycleBucketKey(taskId, event.revision, event.runId);
        const revisionEvents = appendLifecycleEvent(
          lifecycleEventsByRunRef.current.get(bucket) ?? [],
          event,
        );
        lifecycleEventsByRunRef.current.set(bucket, revisionEvents);
        setState((prev) => {
          if (
            prev.status !== "ready"
            || (prev.model.progressRevision ?? prev.model.revision) !== event.revision
            || activeRunIdRef.current !== event.runId
          ) return prev;
          return { ...prev, model: { ...prev.model, lifecycleEvents: revisionEvents } };
        });
      }
      if (event.type === "task_deleted") {
        if (reloadTimer !== null) {
          window.clearTimeout(reloadTimer);
          reloadTimer = null;
        }
        silentReload(true);
        return;
      }
      const durable = readDurableProgressFromTaskEvent(event);
      if (durable) {
        if (!shouldAcceptProgressRevision(appliedRevisionRef.current, durable.revision)) return;
        appliedRevisionRef.current = Math.max(appliedRevisionRef.current, durable.revision);
      }
      scheduleReload();
    };

    const unsubscribe = coordinator.subscribeManagedStream(
      "task-event-stream",
      ({ signal, onEvent, lastEventId }: {
        signal: AbortSignal;
        onEvent: (event: unknown) => void;
        lastEventId: string | null;
      }) => subscribeTaskEvents(
        taskId,
        onEvent as (event: TaskEvent) => void,
        signal,
        lastEventId,
      ),
      handleEvent as (event: unknown) => void,
      {
        staleAfterMs: executionIsRunning ? PERSISTENT_PROGRESS_STALE_MS : 0,
        fallbackPollIntervalMs: WORKBENCH_POLL_INTERVAL_MS,
        onStaleChange: (stale: boolean) => {
          if (stopped) return;
          setState((prev) => prev.status === "ready"
            ? { ...prev, progressStreamStale: stale }
            : prev);
        },
        onFallbackPoll: () => {
          if (!stopped) silentReload();
        },
        onError: (err: unknown) => {
          if (stopped || (err as { name?: string })?.name === "AbortError") return;
          if (err instanceof ApiError && (err.status === 401 || err.status === 403)) return;
          console.warn("Task event stream closed", err);
        },
      },
    );
    coordinator.touchManagedStream("task-event-stream");

    return () => {
      stopped = true;
      unsubscribe();
      if (reloadTimer !== null) window.clearTimeout(reloadTimer);
    };
  }, [
    taskId,
    silentReload,
    state.status,
    state.status === "ready" ? state.source : undefined,
    state.status === "ready" ? state.model.taskStatus : undefined,
    state.status === "ready" ? state.model.executionDisplayStatus : undefined,
    state.status === "ready" ? state.model.interruption?.kind : undefined,
  ]);

  return { ...state, silentReload };
}
