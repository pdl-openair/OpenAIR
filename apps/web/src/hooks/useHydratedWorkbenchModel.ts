import { useCallback, useLayoutEffect, useRef, useState } from "react";
import { ApiError } from "../api/http";
import {
  artifactBodyKey,
  loadArtifactBody,
  readArtifactBody,
} from "../lib/artifactBodyCache";
import {
  hydrateArtifactContent,
  type WorkbenchTaskModel,
} from "../model/workbenchTask";
import type { useWorkbenchTaskModel } from "./useWorkbenchTaskModel";

type Failure = "not_found" | "transient";
export function useHydratedModel(
  loadState: ReturnType<typeof useWorkbenchTaskModel>,
) {
  const sourceModel = loadState.status === "ready" ? loadState.model : null;
  const [hydratedModel, setHydratedModel] = useState<WorkbenchTaskModel | null>(
    sourceModel,
  );
  const [loadingArtifactIds, setLoading] = useState<Set<string>>(
    () => new Set(),
  );
  const [failures, setFailures] = useState<Map<string, Failure>>(
    () => new Map(),
  );
  const inFlight = useRef(new Set<string>());
  const failed = useRef(new Map<string, Failure>());
  const current = useRef({ loadState, hydratedModel });
  useLayoutEffect(() => {
    current.current = { loadState, hydratedModel };
  }, [loadState, hydratedModel]);

  const [projectedSource, setProjectedSource] = useState<
    WorkbenchTaskModel | null | undefined
  >(undefined);
  if (projectedSource !== sourceModel) {
    setProjectedSource(sourceModel);
    setHydratedModel(() => {
      if (!sourceModel) return null;
      return {
        ...sourceModel,
        artifacts: sourceModel.artifacts.map((artifact) => {
          if (artifact.content != null) return artifact;
          const key = artifactBodyKey(sourceModel.taskId, artifact);
          const cached = readArtifactBody(key);
          if (cached)
            return {
              ...artifact,
              content: cached.content,
              displayProjection: cached.displayProjection,
              format: cached.format || artifact.format,
              sourceMode: cached.sourceMode,
              evidenceRefs: cached.evidenceRefs ?? [],
            };
          return artifact;
        }),
      };
    });
  }

  const hydrate = useCallback(
    async (
      taskId: string,
      artifactId: string,
      options?: { force?: boolean },
    ) => {
      const state = current.current.loadState;
      if (
        state.status !== "ready" ||
        state.source !== "api" ||
        state.model.taskId !== taskId
      )
        return;
      const base =
        current.current.hydratedModel?.taskId === taskId
          ? current.current.hydratedModel
          : state.model;
      const artifact = base.artifacts.find((item) => item.id === artifactId);
      if (!artifact || (!options?.force && artifact.content != null)) return;
      const key = artifactBodyKey(taskId, artifact);
      if (
        inFlight.current.has(key) ||
        (!options?.force && failed.current.has(key))
      )
        return;
      inFlight.current.add(key);
      failed.current.delete(key);
      setFailures((previous) => {
        const next = new Map(previous);
        next.delete(key);
        return next;
      });
      setLoading((previous) => new Set(previous).add(key));
      const stillCurrent = () => {
        const latest = current.current.loadState;
        if (latest.status !== "ready" || latest.model.taskId !== taskId)
          return false;
        const item = latest.model.artifacts.find(
          (row) => row.id === artifactId,
        );
        return !!item && artifactBodyKey(taskId, item) === key;
      };
      try {
        const payload = await loadArtifactBody(
          taskId,
          artifact,
          options?.force,
        );
        if (!stillCurrent()) return;
        if (!payload) {
          failed.current.set(key, "not_found");
          setFailures((previous) => new Map(previous).set(key, "not_found"));
          return;
        }
        setHydratedModel((previous) =>
          previous?.taskId === taskId
            ? hydrateArtifactContent(
                previous,
                artifactId,
                payload.content,
                payload.sourceMode,
                payload.evidenceRefs ?? [],
                payload.format,
                payload.displayProjection,
              )
            : previous,
        );
      } catch (error) {
        if (!stillCurrent()) return;
        const reason =
          error instanceof ApiError && error.status === 404
            ? "not_found"
            : "transient";
        failed.current.set(key, reason);
        setFailures((previous) => new Map(previous).set(key, reason));
      } finally {
        inFlight.current.delete(key);
        setLoading((previous) => {
          const next = new Set(previous);
          next.delete(key);
          return next;
        });
      }
    },
    [],
  );
  const retryArtifact = useCallback(
    (taskId: string, artifactId: string) => {
      void hydrate(taskId, artifactId, { force: true });
    },
    [hydrate],
  );
  const getArtifactFailureReason = useCallback(
    (artifactId: string) => {
      const artifact = sourceModel?.artifacts.find(
        (item) => item.id === artifactId,
      );
      return artifact && sourceModel
        ? failures.get(artifactBodyKey(sourceModel.taskId, artifact))
        : undefined;
    },
    [failures, sourceModel],
  );
  const visibleLoading = new Set(
    (sourceModel?.artifacts ?? [])
      .filter((artifact) =>
        loadingArtifactIds.has(artifactBodyKey(sourceModel!.taskId, artifact)),
      )
      .map((artifact) => artifact.id),
  );
  return {
    hydratedModel,
    hydrate,
    retryArtifact,
    loadingArtifactIds: visibleLoading,
    getArtifactFailureReason,
    replaceModel: setHydratedModel,
  };
}
