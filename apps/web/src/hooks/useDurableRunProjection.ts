import { useCallback, useEffect, useState } from "react";
import {
  fetchCurrentTaskExecutionRun,
  fetchExecutionRunEvents,
  type DurableRunEvent,
  type DurableRunProjection,
} from "../api/client";
import { getWorkbenchTaskRequestCoordinator } from "../lib/workbenchTaskRequestCoordinator";

type DurableProjectionState = {
  run: DurableRunProjection | null;
  events: DurableRunEvent[];
  refresh: () => void;
};

export function useDurableRunProjection(taskId: string | undefined, enabled: boolean): DurableProjectionState {
  const [request, setRequest] = useState(0);
  const [run, setRun] = useState<DurableRunProjection | null>(null);
  const [events, setEvents] = useState<DurableRunEvent[]>([]);
  const refresh = useCallback(() => setRequest((value) => value + 1), []);

  useEffect(() => {
    if (!taskId || !enabled) {
      setRun(null);
      setEvents([]);
      return;
    }

    let cancelled = false;
    const coordinator = getWorkbenchTaskRequestCoordinator("workbench-page", "canonical", taskId);
    const releaseCoordinator = coordinator.acquire();
    const load = async () => {
      try {
        const projection = await coordinator.request("durable-run", async () => {
          const current = await fetchCurrentTaskExecutionRun(taskId);
          const currentEvents = current ? await fetchExecutionRunEvents(current.runId) : [];
          return { current, currentEvents };
        }) as { current: DurableRunProjection | null; currentEvents: DurableRunEvent[] } | undefined;
        if (!cancelled && projection) {
          setRun(projection.current);
          setEvents(projection.currentEvents);
        }
      } catch {
        // Keep the task snapshot usable while this read-only projection is unavailable.
      }
    };

    void load();
    return () => {
      cancelled = true;
      releaseCoordinator();
    };
  }, [taskId, enabled, request]);

  const visibleRun = run?.taskId === taskId ? run : null;
  return { run: visibleRun, events: visibleRun ? events : [], refresh };
}
