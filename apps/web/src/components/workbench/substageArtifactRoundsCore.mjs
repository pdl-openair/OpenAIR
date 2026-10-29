/**
 * Round grouping for the Substage artifact footer.
 *
 * A repeatable substage re-executes one round per execution decision (e.g.
 * PVD feedback rounds); one round may mint several attempt artifacts. The
 * grouping key is the WorkUnit's executionDecisionId — never a timestamp gap
 * heuristic — and rounds are ordered by their latest WorkUnit finish time.
 */

function text(value) {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

/**
 * Group footer artifact ids by execution round.
 *
 * Returns `null` when the substage has fewer than two decided rounds (single
 * round stays flat, matching the historical footer) or when the work-unit
 * facts are missing. Otherwise returns ordered groups:
 *   [{ key, roundIndex|null, finishedAt, artifactIds }]
 * Unmatched artifacts (no resolvable WorkUnit/decision) form a leading
 * `roundIndex: null` group so nothing is dropped from the selector.
 */
export function groupSubstageArtifactsByRound({
  workUnits,
  substageInstanceId,
  artifactIds,
}) {
  const ids = /** @type {string[]} */ (Array.isArray(artifactIds)
    ? artifactIds.map((id) => text(id)).filter(Boolean)
    : []);
  if (ids.length === 0 || !Array.isArray(workUnits)) return null;

  const substageUnits = workUnits.filter(
    (workUnit) =>
      workUnit &&
      typeof workUnit === "object" &&
      text(workUnit.subStageInstanceId) === substageInstanceId,
  );
  if (substageUnits.length === 0) return null;

  const decisionByArtifact = new Map();
  const finishedAtByDecision = new Map();
  for (const workUnit of substageUnits) {
    const decisionId = text(workUnit.executionDecisionId);
    if (!decisionId) continue;
    for (const ref of Array.isArray(workUnit.outputArtifactRefs)
      ? workUnit.outputArtifactRefs
      : []) {
      const artifactId = text(ref);
      if (artifactId) decisionByArtifact.set(artifactId, decisionId);
    }
    const finishedAt = text(workUnit.finishedAt) ?? text(workUnit.dispatchedAt);
    const known = finishedAtByDecision.get(decisionId);
    if (!known || (finishedAt && finishedAt > known)) {
      finishedAtByDecision.set(decisionId, finishedAt ?? known ?? null);
    }
  }
  if (finishedAtByDecision.size < 2) return null;

  const grouped = new Map();
  const unassigned = [];
  for (const artifactId of ids) {
    const decisionId = decisionByArtifact.get(artifactId);
    if (!decisionId) {
      unassigned.push(artifactId);
      continue;
    }
    const rows = grouped.get(decisionId) ?? [];
    rows.push(artifactId);
    grouped.set(decisionId, rows);
  }
  if (grouped.size < 2) return null;

  const orderedDecisions = [...grouped.keys()].sort((left, right) => {
    const leftTime = finishedAtByDecision.get(left) ?? "";
    const rightTime = finishedAtByDecision.get(right) ?? "";
    return leftTime.localeCompare(rightTime) || left.localeCompare(right);
  });

  const groups = [];
  if (unassigned.length > 0) {
    groups.push({ key: null, roundIndex: null, finishedAt: null, artifactIds: unassigned });
  }
  orderedDecisions.forEach((decisionId, index) => {
    groups.push({
      key: decisionId,
      roundIndex: index + 1,
      finishedAt: finishedAtByDecision.get(decisionId),
      artifactIds: grouped.get(decisionId),
    });
  });
  return groups;
}

/** "2026-10-14T13:32:36.316531Z" → "10-14 21:32" (viewer-local, best effort). */
export function formatRoundTime(isoTime) {
  const value = text(isoTime);
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const pad = (part) => String(part).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
