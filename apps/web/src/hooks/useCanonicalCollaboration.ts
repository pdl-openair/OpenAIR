import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CanonicalCollaborationApiError,
  fetchCanonicalCollaboration,
  type CanonicalCollaborationApiErrorKind,
} from "../api/canonicalCollaboration";
import {
  buildCanonicalCollaborationView,
  type CanonicalCollaborationSnapshot,
  type CanonicalCollaborationView,
} from "../model/canonicalCollaborationView";

export type CanonicalCollaborationLoadStatus = "idle" | "loading" | "ready" | "error";

export interface CanonicalCollaborationLoadError {
  kind: CanonicalCollaborationApiErrorKind;
  message: string;
  status: number | null;
}

export interface UseCanonicalCollaborationResult {
  status: CanonicalCollaborationLoadStatus;
  snapshot: CanonicalCollaborationSnapshot | null;
  view: CanonicalCollaborationView | null;
  error: CanonicalCollaborationLoadError | null;
  refresh: () => void;
}

export function useCanonicalCollaboration(
  taskId: string | null | undefined,
  enabled = true,
  expectedRevision?: number | null,
): UseCanonicalCollaborationResult {
  const [snapshot, setSnapshot] = useState<CanonicalCollaborationSnapshot | null>(null);
  const [status, setStatus] = useState<CanonicalCollaborationLoadStatus>("idle");
  const [error, setError] = useState<CanonicalCollaborationLoadError | null>(null);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const generationRef = useRef(0);

  const refresh = useCallback(() => setRefreshVersion((value) => value + 1), []);

  useEffect(() => {
    const normalizedTaskId = taskId?.trim() ?? "";
    const generation = ++generationRef.current;
    if (!enabled || !normalizedTaskId) {
      setSnapshot(null);
      setError(null);
      setStatus("idle");
      return;
    }

    const controller = new AbortController();
    setSnapshot(null);
    setError(null);
    setStatus("loading");

    void fetchCanonicalCollaboration(normalizedTaskId, {
      signal: controller.signal,
      expectedRevision,
    })
      .then((nextSnapshot) => {
        if (generationRef.current !== generation) return;
        setSnapshot(nextSnapshot);
        setError(null);
        setStatus("ready");
      })
      .catch((caught: unknown) => {
        if (generationRef.current !== generation) return;
        if (caught instanceof CanonicalCollaborationApiError && caught.kind === "aborted") return;
        const normalized = caught instanceof CanonicalCollaborationApiError
          ? caught
          : new CanonicalCollaborationApiError(
            "unavailable",
            "暂时无法读取协作进展，请稍后重试。",
          );
        setSnapshot(null);
        setError({ kind: normalized.kind, message: normalized.message, status: normalized.status });
        setStatus("error");
      });

    return () => controller.abort();
  }, [enabled, expectedRevision, refreshVersion, taskId]);

  const view = useMemo(
    () => (snapshot ? buildCanonicalCollaborationView(snapshot) : null),
    [snapshot],
  );

  return { status, snapshot, view, error, refresh };
}
