const NON_USER_VISIBILITIES = new Set([
  "audit",
  "developer",
  "diagnostic",
  "hidden",
  "internal",
]);

import { formatPresentationRank } from "../../lib/artifactPresentationOrder.mjs";

function text(value) {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function artifactVisibility(artifact) {
  return (
    text(artifact.artifactVisibility) ??
    text(artifact.visibility) ??
    text(artifact.audience)
  );
}

function belongsToTask(artifact, taskId) {
  const artifactTaskId = text(artifact.taskId);
  return artifactTaskId == null || artifactTaskId === taskId;
}

function isUserVisibleArtifact(artifact) {
  const visibility = artifactVisibility(artifact)?.toLowerCase();
  return visibility == null || !NON_USER_VISIBILITIES.has(visibility);
}

/**
 * Project the complete canonical Artifact set for one Substage.
 *
 * Substage refs retain their authored order as the tie-break; sibling outputs
 * are then stable-sorted into reader-facing presentation order (报告 → 表格 →
 * 图 → 原始数据) so the first button — and the default focus that follows it —
 * is the most synthesized view, not the raw result JSON. Explicit instance
 * ownership adds records that have already been persisted but whose ref
 * projection arrived later, without replacing earlier refs or leaking
 * non-user records.
 */
export function projectSubstageArtifactFooter({
  taskId,
  substageInstanceId,
  artifactRefs,
  artifacts,
}) {
  if (!text(taskId) || !text(substageInstanceId) || !Array.isArray(artifacts))
    return [];

  const byId = new Map();
  for (const artifact of artifacts) {
    if (!artifact || typeof artifact !== "object") continue;
    const id = text(artifact.id);
    const title = text(artifact.title);
    if (
      !id ||
      !title ||
      byId.has(id) ||
      !belongsToTask(artifact, taskId) ||
      !isUserVisibleArtifact(artifact)
    )
      continue;
    byId.set(id, { ...artifact, title });
  }

  const orderedIds = [];
  const seen = new Set();
  for (const ref of Array.isArray(artifactRefs) ? artifactRefs : []) {
    const id = text(ref);
    if (!id || seen.has(id) || !byId.has(id)) continue;
    const artifact = byId.get(id);
    const explicitSubstageId = text(artifact.subStageInstanceId);
    if (explicitSubstageId && explicitSubstageId !== substageInstanceId)
      continue;
    seen.add(id);
    orderedIds.push(id);
  }
  for (const artifact of byId.values()) {
    if (
      text(artifact.subStageInstanceId) !== substageInstanceId ||
      seen.has(artifact.id)
    )
      continue;
    seen.add(artifact.id);
    orderedIds.push(artifact.id);
  }

  return orderedIds
    .map((id, index) => {
      const artifact = byId.get(id);
      return {
        item: {
          id,
          title: artifact.title,
          format: text(artifact.format),
          artifactState: text(artifact.artifactState),
        },
        index,
      };
    })
    .sort(
      (left, right) =>
        formatPresentationRank(left.item.format) -
          formatPresentationRank(right.item.format) ||
        left.index - right.index,
    )
    .map((row) => row.item);
}

/**
 * Reconcile controlled Footer selection from durable scope identity.
 * An explicit valid deep link wins; otherwise a same-scope selection survives
 * refreshes and appended Artifacts. Cross-task/Substage state is discarded.
 */
export function reconcileSubstageArtifactFooterSelection({
  selection,
  taskId,
  substageInstanceId,
  artifactIds,
  deepLinkedArtifactId,
}) {
  const visibleIds = new Set(Array.isArray(artifactIds) ? artifactIds : []);
  const deepLink = text(deepLinkedArtifactId);
  const sameScope =
    selection &&
    selection.taskId === taskId &&
    selection.substageInstanceId === substageInstanceId;
  const retained = sameScope ? text(selection.artifactId) : null;
  const artifactId =
    deepLink && visibleIds.has(deepLink)
      ? deepLink
      : retained && visibleIds.has(retained)
        ? retained
        : (artifactIds?.[0] ?? null);

  return { taskId, substageInstanceId, artifactId };
}

export function buildSubstageArtifactDeepLink({
  pathname,
  search,
  artifactId,
}) {
  const params = new URLSearchParams(typeof search === "string" ? search : "");
  const id = text(artifactId);
  if (id) params.set("artifact", id);
  else params.delete("artifact");
  const query = params.toString();
  return `${text(pathname) ?? "/workbench"}${query ? `?${query}` : ""}`;
}
