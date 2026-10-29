import { useCallback, useEffect, useState } from "react";
import {
  fetchTaskCollaboration,
  type CollaborationProjection,
} from "../api/client";
import { collaborationMatchesTaskRevision } from "../model/workbenchScreenView";
import { getWorkbenchTaskRequestCoordinator } from "../lib/workbenchTaskRequestCoordinator";

type CollaborationProjectionState = {
  /**
   * Auxiliary legacy snapshot used only for user-decision/recovery controls.
   * Golden-path Workbench presentation comes from Task durable progress + SSE + Artifacts.
   */
  projection: CollaborationProjection | null;
  loading: boolean;
  unavailable: boolean;
  refresh: () => void;
};

/**
 * Auxiliary collaboration read for legacy decision/recovery controls only.
 *
 * Do not use this hook as a Workbench presentation authority. The normal research
 * surface is built from the Task GET's durable progress projection, while research
 * SSE only invalidates those authoritative reads. Keeping this read auxiliary avoids
 * an independent collaboration revision becoming a Golden Path rendering gate.
 */
export function useCollaborationProjection(
  taskId: string | undefined,
  enabled: boolean,
  expectedRevision?: number | null,
): CollaborationProjectionState {
  const [request, setRequest] = useState(0);
  const [projection, setProjection] = useState<CollaborationProjection | null>(null);
  const [loading, setLoading] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [boundTaskId, setBoundTaskId] = useState<string | null>(null);
  const refresh = useCallback(() => setRequest((value) => value + 1), []);

  useEffect(() => {
    if (!taskId || !enabled) {
      setProjection(null);
      setLoading(false);
      setUnavailable(false);
      setBoundTaskId(null);
      return;
    }

    let cancelled = false;
    const coordinator = getWorkbenchTaskRequestCoordinator("workbench-page", "canonical", taskId);
    const releaseCoordinator = coordinator.acquire();
    const revisionAtStart = expectedRevision;
    setBoundTaskId(taskId);

    const loadAuxiliary = async () => {
      setLoading(true);
      try {
        const next = await coordinator.fetchCollaboration(
          (signal: AbortSignal) => fetchTaskCollaboration(taskId, signal),
        ) as CollaborationProjection | undefined;
        if (!next || cancelled) return;
        if (typeof revisionAtStart === "number" && !collaborationMatchesTaskRevision(next, revisionAtStart)) {
          setProjection(null);
          setUnavailable(false);
          return;
        }
        setProjection(next);
        setUnavailable(false);
      } catch (error) {
        if (!cancelled && (error as { name?: string })?.name !== "AbortError") {
          setUnavailable(true);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void loadAuxiliary();
    return () => {
      cancelled = true;
      releaseCoordinator();
    };
  }, [taskId, enabled, request, expectedRevision]);

  useEffect(() => {
    if (typeof expectedRevision !== "number") return;
    setProjection((current) => {
      if (!current) return current;
      return current.revision === expectedRevision ? current : null;
    });
  }, [expectedRevision]);

  const projectionMatchesTask = projection?.taskId === taskId;
  const bindingMatchesTask = boundTaskId === taskId;

  return {
    projection: projectionMatchesTask ? projection : null,
    loading: bindingMatchesTask ? loading : Boolean(taskId && enabled),
    unavailable: bindingMatchesTask ? unavailable : false,
    refresh,
  };
}
