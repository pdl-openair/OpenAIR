import { useEffect } from "react";
import {
  fetchResearchStartSession,
  type ResearchStartSessionStatus,
} from "../../api/client";
import { ApiError } from "../../api/http";
import { getWorkbenchTaskRequestCoordinator } from "../../lib/workbenchTaskRequestCoordinator";

export type PlanningSessionAccessError =
  "unauthorized" | "forbidden" | "notFound" | null;

function planningSessionRecheckDelay(
  snapshot: ResearchStartSessionStatus,
): number {
  if (snapshot.status === "queued" || snapshot.status === "worker_unavailable")
    return 5_000;
  if (snapshot.status === "external_result_unknown") return 20_000;
  return 2_500;
}

export function ResearchStartPlanningSync({
  sessionId,
  taskId,
  onCompleted,
  onStatusChange,
  refreshTaskWhileMissing = false,
  recheckSignal = 0,
}: {
  sessionId: string | null;
  taskId: string;
  onCompleted: () => void;
  onStatusChange: (
    /** `undefined` keeps the last good snapshot (transient read blips). */
    snapshot: ResearchStartSessionStatus | null | undefined,
    readUnavailable: boolean,
    accessError: PlanningSessionAccessError,
  ) => void;
  refreshTaskWhileMissing?: boolean;
  /** Increment to force an immediate session re-read (workbench “再检查”). */
  recheckSignal?: number;
}) {
  useEffect(() => {
    if (!sessionId || !taskId) {
      onStatusChange(null, false, null);
      return undefined;
    }
    // Do not clear the last good snapshot on effect restart — wiping it makes
    // elapsed / progress flash empty during every silent task recheck.
    const coordinator = getWorkbenchTaskRequestCoordinator(
      "workbench-page",
      "canonical",
      taskId,
    );
    const resource = `research-start-session:${sessionId}`;
    const release = coordinator.acquire();
    let disposed = false;
    let completionReported = false;

    const reconcile = async () => {
      if (disposed) return;
      try {
        const next = (await coordinator.request(resource, () =>
          fetchResearchStartSession(sessionId),
        )) as ResearchStartSessionStatus | undefined;
        if (disposed || !next || next.sessionId !== sessionId) return;
        if (next.reservedTaskId && next.reservedTaskId !== taskId) {
          onStatusChange(null, false, "notFound");
          return;
        }
        onStatusChange(next, false, null);
        if (refreshTaskWhileMissing) onCompleted();
        if (next.status === "completed" && !completionReported) {
          completionReported = true;
          if (!refreshTaskWhileMissing) {
            onCompleted();
            return;
          }
        }
        if (next.status === "failed") {
          if (!refreshTaskWhileMissing && !completionReported) onCompleted();
          return;
        }
        coordinator.schedule(
          resource,
          reconcile,
          planningSessionRecheckDelay(next),
        );
      } catch (error) {
        if (disposed) return;
        if ((error as { code?: string })?.code === "page_hidden") {
          coordinator.schedule(resource, reconcile, 0);
          return;
        }
        if (
          error instanceof ApiError &&
          (error.status === 401 || error.status === 403 || error.status === 404)
        ) {
          onStatusChange(
            null,
            false,
            error.status === 401
              ? "unauthorized"
              : error.status === 403
                ? "forbidden"
                : "notFound",
          );
          return;
        }
        // Keep the last good snapshot; only mark the read as temporarily unavailable.
        onStatusChange(undefined, true, null);
        coordinator.schedule(resource, reconcile, 2_500);
      }
    };

    coordinator.schedule(resource, reconcile, 0);
    return () => {
      disposed = true;
      coordinator.cancelSchedule(resource);
      coordinator.cancel(resource);
      release();
    };
  }, [
    onCompleted,
    onStatusChange,
    recheckSignal,
    refreshTaskWhileMissing,
    sessionId,
    taskId,
  ]);

  return null;
}
