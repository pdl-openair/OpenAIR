/**
 * Fail-closed extraction of pending decisions from collaboration snapshots.
 * Tolerates missing nested `collaboration`, flat top-level lists, and malformed rows.
 */

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function stringValue(value, fallback = "") {
  return typeof value === "string" ? value : fallback;
}

function normalizeDecision(raw) {
  if (!isRecord(raw)) return null;
  const gateId = stringValue(raw.gateId).trim();
  if (!gateId) return null;
  const allowedActions = Array.isArray(raw.allowedActions)
    ? raw.allowedActions.filter((action) => typeof action === "string" && action)
    : [];
  return {
    gateId,
    kind: stringValue(raw.kind, "confirmation"),
    reason: stringValue(raw.reason, "需要你确认后继续"),
    artifactRefs: Array.isArray(raw.artifactRefs) ? raw.artifactRefs : [],
    createdAt: typeof raw.createdAt === "string" ? raw.createdAt : null,
    allowedActions,
  };
}

function decisionsFromList(value) {
  if (!Array.isArray(value)) return [];
  const out = [];
  for (const item of value) {
    const normalized = normalizeDecision(item);
    if (normalized) out.push(normalized);
  }
  return out;
}

/**
 * @param {unknown} projection
 * @returns {Array<{gateId: string, kind: string, reason: string, artifactRefs: unknown[], createdAt: string | null, allowedActions: string[]}>}
 */
export function pendingDecisionsFromProjection(projection) {
  if (!isRecord(projection)) return [];
  const nested = isRecord(projection.collaboration)
    ? projection.collaboration.pendingDecisions
    : undefined;
  const nestedList = decisionsFromList(nested);
  if (nestedList.length > 0 || Array.isArray(nested)) return nestedList;
  // Flat / canonical-shaped snapshots may put the list at the top level.
  return decisionsFromList(projection.pendingDecisions);
}
