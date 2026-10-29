import { useEffect, useState } from "react";
import {
  fetchResearchStartDisplayContext,
  type ResearchStartDisplayContext,
} from "../api/researchStartDisplayContext";

export function useResearchStartDisplayContext(
  taskId: string | undefined,
  planningSessionId: string | null,
  reloadDisplayMetadata?: () => void,
) {
  const [startContext, setStartContext] = useState<{
    taskId: string;
    requestedSessionId: string | null;
    status: "ready" | "unavailable";
    value: ResearchStartDisplayContext | null;
  } | null>(null);
  const visibleContext =
    startContext &&
    startContext.taskId === taskId &&
    (!planningSessionId ||
      startContext.requestedSessionId === planningSessionId)
      ? startContext
      : null;
  useEffect(() => {
    if (!taskId) return;
    let cancelled = false;
    let timer: number | null = null;
    const requestedTaskId = taskId;
    const deadline = Date.now() + 30_000;
    const readContext = async () => {
      try {
        const value = await fetchResearchStartDisplayContext(
          requestedTaskId,
          planningSessionId,
        );
        if (cancelled || value.taskId !== requestedTaskId) return;
        setStartContext({
          taskId: requestedTaskId,
          requestedSessionId: planningSessionId,
          status: "ready",
          value,
        });
        if (value.titlePending && Date.now() < deadline) {
          timer = window.setTimeout(() => void readContext(), 1_500);
        } else if (value.displayTitle) {
          reloadDisplayMetadata?.();
        }
      } catch {
        if (cancelled) return;
        setStartContext((previous) => ({
          taskId: requestedTaskId,
          requestedSessionId: planningSessionId,
          status: "unavailable",
          value:
            previous?.taskId === requestedTaskId &&
            (!planningSessionId ||
              previous.value?.sessionId === planningSessionId)
              ? previous.value
              : null,
        }));
        if (Date.now() < deadline) {
          timer = window.setTimeout(() => void readContext(), 1_500);
        }
      }
    };
    void readContext();
    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [taskId, planningSessionId, reloadDisplayMetadata]);
  return visibleContext;
}
