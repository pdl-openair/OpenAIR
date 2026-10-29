/**
 * Pure ownership and scope rules for the Workbench center artifact view.
 * The current execution position is deliberately not part of a pinned view.
 */

import { formatPresentationRank } from "./artifactPresentationOrder.mjs";

export function artifactsForExplicitScope({ scope, substageArtifacts, stageArtifacts, taskArtifacts }) {
  if (scope === "substage") return substageArtifacts;
  if (scope === "task") return taskArtifacts;
  return stageArtifacts;
}

export function resolveArtifactLocation({ artifact, stages }) {
  if (!artifact) return { kind: "unlocatable", reason: "artifact_not_found" };
  const stage = stages.find((candidate) => candidate.stage === artifact.relatedStage);
  if (!stage) return { kind: "unlocatable", reason: "stage_not_found" };

  if (artifact.subStageInstanceId) {
    const exact = stage.subStages.find((substage) => substage.id === artifact.subStageInstanceId);
    if (!exact) return { kind: "unlocatable", reason: "substage_instance_not_found" };
    return {
      kind: "located",
      stageKey: stage.stage,
      substageInstanceId: exact.id,
      evidence: "substage_instance_id",
    };
  }

  const refMatches = stage.subStages.filter((substage) =>
    Array.isArray(substage.artifactRefs) && substage.artifactRefs.includes(artifact.id),
  );
  if (refMatches.length === 1) {
    return {
      kind: "located",
      stageKey: stage.stage,
      substageInstanceId: refMatches[0].id,
      evidence: "substage_artifact_ref",
    };
  }
  if (refMatches.length > 1) return { kind: "unlocatable", reason: "ambiguous_substage_refs" };
  return { kind: "unlocatable", reason: "missing_substage_evidence" };
}

/**
 * Without an explicit focus or deep link, default to the most synthesized
 * visible Artifact (报告 → 表格 → 图 → 原始数据) instead of the first minted
 * record — the mint order usually leads with the raw result JSON. Formats are
 * optional: without them the historical first-id behavior is kept.
 */
export function chooseFocusedArtifactId({
  focusedArtifactId,
  deepLinkedArtifactId,
  visibleArtifactIds,
  formatsById = null,
}) {
  if (focusedArtifactId && visibleArtifactIds.includes(focusedArtifactId)) return focusedArtifactId;
  if (deepLinkedArtifactId && visibleArtifactIds.includes(deepLinkedArtifactId)) return deepLinkedArtifactId;
  if (formatsById) {
    let bestId = null;
    let bestRank = Number.POSITIVE_INFINITY;
    for (const id of visibleArtifactIds) {
      const format = typeof formatsById.get === "function"
        ? formatsById.get(id)
        : formatsById[id];
      const rank = formatPresentationRank(format);
      if (rank < bestRank) {
        bestId = id;
        bestRank = rank;
      }
    }
    if (bestId !== null) return bestId;
  }
  return visibleArtifactIds[0] ?? null;
}

export function shouldAutoFollowCurrentArtifact({
  ownership,
  latestArtifactId,
  focusedArtifactId,
  deepLinkedArtifactId,
  location,
}) {
  return ownership === "following_current"
    && Boolean(latestArtifactId)
    && !deepLinkedArtifactId
    && latestArtifactId !== focusedArtifactId
    && location?.kind === "located";
}

export function buildArtifactViewNotices({ ownership, latestArtifactId, reviewableArtifactId, focusedArtifactId }) {
  const newCurrentArtifactId = ownership === "pinned"
    && latestArtifactId
    && latestArtifactId !== focusedArtifactId
    ? latestArtifactId
    : null;
  const pendingReviewArtifactId = reviewableArtifactId
    && reviewableArtifactId !== focusedArtifactId
    && reviewableArtifactId !== newCurrentArtifactId
    ? reviewableArtifactId
    : null;
  return { newCurrentArtifactId, pendingReviewArtifactId };
}

export function artifactScopePresentation({ scope, stageLabel, substageLabel }) {
  if (scope === "task") {
    return {
      title: "全部任务产物",
      path: "全部任务产物",
      explanation: "正在查看本项目的全部产物。",
      returnLabel: substageLabel ? `返回 ${substageLabel}` : null,
    };
  }
  if (scope === "stage") {
    return {
      title: `${stageLabel} · 本阶段产物`,
      path: `${stageLabel} / 本阶段全部节点产物`,
      explanation: `正在查看“${stageLabel}”下多个研究节点的产物。`,
      returnLabel: substageLabel ? `返回 ${substageLabel}` : null,
    };
  }
  return {
    title: substageLabel ?? stageLabel,
    path: substageLabel ? `${stageLabel} / ${substageLabel}` : `${stageLabel} / 未定位节点`,
    explanation: substageLabel
      ? `正在查看“${substageLabel}”节点的产物。`
      : "当前没有可证明归属的研究节点。",
    returnLabel: null,
  };
}

export function reconcileArtifactViewSelection({ selection, snapshot, initialSelection }) {
  if (!selection || selection.taskId !== snapshot.taskId) return { ...initialSelection };

  const stageExists = snapshot.stageKeys.includes(selection.stageKey);
  const stageKey = stageExists ? selection.stageKey : initialSelection.stageKey;
  const legalSubstages = snapshot.substageIdsByStage[stageKey] ?? [];
  const selectedSubstageId = selection.selectedSubstageId && legalSubstages.includes(selection.selectedSubstageId)
    ? selection.selectedSubstageId
    : null;
  const focusedArtifactId = selection.focusedArtifactId
    && snapshot.artifactIds.includes(selection.focusedArtifactId)
    ? selection.focusedArtifactId
    : null;

  return {
    ...selection,
    revision: snapshot.revision,
    researchDesignKey: snapshot.researchDesignKey,
    stageKey,
    selectedSubstageId,
    focusedArtifactId,
  };
}
