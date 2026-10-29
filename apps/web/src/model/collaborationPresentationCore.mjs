function text(value) {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function stringList(value) {
  return Array.isArray(value)
    ? [...new Set(value.map(text).filter(Boolean))]
    : [];
}

function timestampMs(value) {
  const normalized = text(value);
  if (!normalized) return null;
  const parsed = Date.parse(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function stableFactOrder(left, right) {
  const leftTime = timestampMs(left.occurredAt);
  const rightTime = timestampMs(right.occurredAt);
  if (leftTime !== null && rightTime !== null && leftTime !== rightTime) return leftTime - rightTime;
  if (leftTime !== null && rightTime === null) return -1;
  if (leftTime === null && rightTime !== null) return 1;
  return String(left.id).localeCompare(String(right.id));
}

function overlap(left, right) {
  if (!left?.length || !right?.length) return false;
  const rightSet = new Set(right);
  return left.some((value) => rightSet.has(value));
}

function firstOccurredAt(rows) {
  const candidates = (Array.isArray(rows) ? rows : [])
    .map((row) => ({ value: text(row?.occurredAt), ms: timestampMs(row?.occurredAt) }))
    .filter((row) => row.value && row.ms !== null)
    .sort((left, right) => left.ms - right.ms);
  return candidates[0]?.value ?? null;
}

function researchNodes(researchFacts) {
  return (Array.isArray(researchFacts) ? researchFacts : [])
    .filter((fact) => text(fact?.id))
    .map((fact) => ({
      id: text(fact.id),
      kind: "research_activity",
      occurredAt: text(fact.occurredAt) ?? firstOccurredAt(fact.rows),
      workUnitId: text(fact.workUnitId),
      artifactRefs: stringList(fact.artifactRefs),
      source: fact,
      related: [],
    }));
}

function messageNode(message) {
  const id = text(message?.id);
  const role = text(message?.role);
  const at = text(message?.at);
  if (!id || !at) return null;
  if (role === "user") {
    return {
      id: `message:${id}`,
      kind: "user_intervention",
      occurredAt: at,
      workUnitId: null,
      artifactRefs: stringList(message.artifactRefs),
      capabilityCallId: text(message.capabilityCallId),
      source: message,
      related: [],
    };
  }
  if (role === "assistant") {
    const messageType = text(message?.messageType) ?? "";
    const summary = text(message?.userFacingNarrative?.summary);
    const body = text(message?.text);
    const dispatch = messageType === "research_dispatch_result" && summary;
    const chat = messageType === "chat"
      || messageType === "chat_error"
      || messageType.startsWith("runtime_gateway_");
    if (!dispatch && !(chat && body)) return null;
    return {
      id: `message:${id}`,
      kind: "research_result",
      occurredAt: at,
      workUnitId: null,
      artifactRefs: stringList(message.artifactRefs),
      capabilityCallId: text(message.capabilityCallId),
      source: message,
      related: [],
    };
  }
  return null;
}

function actionNode(action) {
  const id = text(action?.itemId);
  if (!id) return null;
  const workUnitId = text(action.workUnitId);
  const publicAction = {
    itemId: action.itemId,
    kind: action.kind,
    title: action.title,
    reason: action.reason,
    workUnitId: action.workUnitId,
    objective: text(action.objective),
    artifactRefs: stringList(action.artifactRefs),
    allowedActions: action.allowedActions,
    commandContexts: action.commandContexts,
  };
  return {
    id: `action:${id}`,
    kind: "action_required",
    occurredAt: null,
    workUnitId,
    artifactRefs: [],
    source: publicAction,
    related: [],
  };
}

function artifactNode(artifact) {
  const id = text(artifact?.id);
  if (!id) return null;
  return {
    id: `artifact:${id}`,
    kind: "artifact",
    occurredAt: text(artifact.createdAt),
    workUnitId: text(artifact.workUnitId),
    artifactRefs: [id],
    capabilityCallId: text(artifact.sourceCapabilityCallId),
    source: artifact,
    related: [],
  };
}

function controlNode(control) {
  const id = text(control?.id);
  const kind = text(control?.kind);
  if (!id || !kind) return null;
  return {
    id: `control:${id}`,
    kind: "control",
    controlKind: kind,
    occurredAt: text(control.occurredAt),
    workUnitId: text(control.workUnitId),
    artifactRefs: stringList(control.artifactRefs),
    source: Object.prototype.hasOwnProperty.call(control, "source") ? control.source : control,
    related: [],
  };
}

function uniqueExplicitResearchTarget(research, fact) {
  const workUnitId = text(fact.workUnitId);
  if (workUnitId) {
    const byWorkUnit = research.filter((node) => node.workUnitId === workUnitId);
    if (byWorkUnit.length === 1) return byWorkUnit[0];
    if (byWorkUnit.length > 1) return null;
  }
  const refs = stringList(fact.artifactRefs);
  if (refs.length) {
    const byArtifact = research.filter((node) => overlap(node.artifactRefs, refs));
    if (byArtifact.length === 1) return byArtifact[0];
  }
  return null;
}

function uniqueExplicitResultTarget(results, artifact) {
  const artifactId = text(artifact?.source?.id);
  const capabilityCallId = text(artifact?.capabilityCallId);
  const matches = results.filter((node) => {
    if (artifactId && node.artifactRefs.includes(artifactId)) return true;
    return Boolean(capabilityCallId && node.capabilityCallId === capabilityCallId);
  });
  return matches.length === 1 ? matches[0] : null;
}

/**
 * Pure presentation composition over already-public facts.
 *
 * It never creates canonical facts and never infers relations from text, time
 * proximity, array position, or render order. A related fact may grow an
 * existing research node only through explicit workUnit/artifact identity.
 */
/**
 * Explicit JS/TS boundary for the canonical collaboration presentation inputs.
 * @param {{
 *   researchFacts?: unknown[],
 *   messages?: unknown[],
 *   actions?: unknown[],
 *   artifacts?: unknown[],
 *   controls?: unknown[]
 * }} input
 * @returns {any[]}
 */
export function composeCollaborationPresentation({
  researchFacts = [],
  messages = [],
  actions = [],
  artifacts = [],
  controls = [],
} = {}) {
  const research = researchNodes(researchFacts);
  const topLevel = [...research];

  const messageNodes = messages.map(messageNode).filter(Boolean);
  const resultNodes = messageNodes.filter((node) => node.kind === "research_result");
  for (const node of messageNodes) {
    const target = uniqueExplicitResearchTarget(research, node);
    if (target) target.related.push(node);
    else topLevel.push(node);
  }

  for (const action of actions.map(actionNode).filter(Boolean)) {
    const target = uniqueExplicitResearchTarget(research, action);
    if (target) target.related.push(action);
    else topLevel.push(action);
  }

  for (const artifact of artifacts.map(artifactNode).filter(Boolean)) {
    const researchTarget = uniqueExplicitResearchTarget(research, artifact);
    if (researchTarget) {
      researchTarget.related.push(artifact);
      continue;
    }
    const resultTarget = uniqueExplicitResultTarget(resultNodes, artifact);
    if (resultTarget) resultTarget.related.push(artifact);
    else topLevel.push(artifact);
  }

  for (const control of controls.map(controlNode).filter(Boolean)) {
    const target = uniqueExplicitResearchTarget(research, control);
    if (target) target.related.push(control);
    else topLevel.push(control);
  }

  for (const node of topLevel) {
    node.related.sort(stableFactOrder);
  }
  topLevel.sort(stableFactOrder);
  return topLevel;
}
