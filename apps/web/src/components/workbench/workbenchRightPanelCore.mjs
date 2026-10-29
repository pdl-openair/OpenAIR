const ARTIFACT_STATE_ORDER = {
  usable: 0,
  needs_review: 1,
  needs_input: 2,
  insufficient_materials: 3,
  partial_success: 4,
  rejected_or_needs_revision: 5,
  failed: 6,
  unknown: 7,
};
const UNSAFE_PRODUCT_TEXT = /(?:provider\s*[:=]|model\s*[:=]|prompt\s*[:=]|token\s*[:=]|tool\s*(?:args|arguments)\s*[:=]|traceback|stack\s*trace|authorization\s*[:=]|bearer\s+[a-z0-9._~+/=-]{8,}|\b(?:planner|worker|claim|provider|runtime|canonical|orchestration|workunit|executiondecision|dispatch|receipt|recovery|controller|sse|projection|transport|lease)\b|\borchestrat(?:ion|or)\b|\blifecycle\b|\brevision\b|\bheartbeat\b|execution[\s_-]*decision|work[\s_-]*unit|\bcommand\b|\breconcile\b|\bfence\b|run[\s_-]*id|task[\s_-]*id|\bworkspace\b|transport[\s_-]*uncertain|dependency[\s_-]*preflight|artifact[\s_-]*scope|\btaskId\s*=|\brevision\s*=|服务端|后端|进度投影|权威投影|运行时|调用栈|请求标识|追踪标识|数据库|回执|恢复生命周期|任务\s*(?:ID|Id|id))/i;
const INTERNAL_REFERENCE = /\bsdr\.[a-z0-9_.-]+\b|\bskill(?:\s*id|[_-]id)?\b|\bdefinition[_-]?key\b|\binternal[_-]?(?:artifact|ref)(?:[_-]?id)?\b/i;

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function stringValue(value) {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function safeRightPanelText(value, fallback) {
  const normalized = stringValue(value);
  return normalized && !UNSAFE_PRODUCT_TEXT.test(normalized) && !INTERNAL_REFERENCE.test(normalized) ? normalized : fallback;
}

const FALSE_NO_BUSINESS_ARTIFACT_MESSAGE = /没有形成可用业务产物/;
const COLLABORATION_SUMMARY_WHEN_ARTIFACT = "研究产物已形成，可查看相关产物。";
const COLLABORATION_SUMMARY_NEUTRAL = "当前研究进展待确认。";
const COLLABORATION_REASON_NEUTRAL = "当前步骤尚未继续，系统正在确认原因。";
const REVIEW_STATUS_FALLBACK = "复核结论将以最新任务状态为准。";
// Assignment / JSON-key shapes only — bare scientific uses of these words stay visible.
const INTERNAL_EXECUTION_ASSIGNMENT = /\b(?:configuration|adapter|trace|skill[_-]?id)\s*=\s*\S+/i;
const INTERNAL_EXECUTION_JSON_KEY = /"(?:configuration|adapter|trace|skill[_-]?id)"\s*:/;
const INTERNAL_EXECUTION_BARE_MARKERS = [
  /\bconfiguration\b/i,
  /\badapter\b/i,
  /\btrace\b/i,
  /\bskill[_-]?id\b/i,
];

/**
 * Canonical Artifact truth for collaboration presentation.
 * Payload artifactRef alone never proves a user-visible Artifact exists.
 */
export function collaborationCanonicalArtifactExists(artifactRefs, visibleArtifactIds) {
  const visible = new Set(
    (Array.isArray(visibleArtifactIds) ? visibleArtifactIds : [])
      .map((id) => stringValue(id))
      .filter(Boolean),
  );
  if (!visible.size) return false;
  return (Array.isArray(artifactRefs) ? artifactRefs : []).some((id) => {
    const normalized = stringValue(id);
    return Boolean(normalized && visible.has(normalized));
  });
}

/**
 * Detect obvious internal/debug dumps without single-keyword false positives.
 * Scientific copy such as "We compare adapter configuration strategies." stays allowed.
 */
export function looksLikeInternalExecutionDetail(value) {
  const normalized = stringValue(value);
  if (!normalized) return false;
  if (INTERNAL_EXECUTION_ASSIGNMENT.test(normalized) || INTERNAL_EXECUTION_JSON_KEY.test(normalized)) {
    return true;
  }
  const bareHits = INTERNAL_EXECUTION_BARE_MARKERS.filter((pattern) => pattern.test(normalized)).length;
  return bareHits >= 3;
}

function collaborationNarrativeNeedsReplacement(normalized, { suppressFalseNoBusiness }) {
  if (suppressFalseNoBusiness && FALSE_NO_BUSINESS_ARTIFACT_MESSAGE.test(normalized)) return true;
  if (looksLikeInternalExecutionDetail(normalized)) return true;
  if (INTERNAL_REFERENCE.test(normalized)) return true;
  if (UNSAFE_PRODUCT_TEXT.test(normalized)) return true;
  return false;
}

/**
 * Result-summary presentation authority: Artifact existence is decided only by
 * canonical visible Artifact ids; the sanitizer only chooses safe copy.
 */
export function presentCollaborationResultSummary(summary, { canonicalArtifactExists = false } = {}) {
  const normalized = stringValue(summary);
  if (normalized === REVIEW_STATUS_FALLBACK) return null;
  if (!normalized) {
    return canonicalArtifactExists ? COLLABORATION_SUMMARY_WHEN_ARTIFACT : null;
  }
  if (collaborationNarrativeNeedsReplacement(normalized, {
    suppressFalseNoBusiness: canonicalArtifactExists,
  })) {
    return canonicalArtifactExists ? COLLABORATION_SUMMARY_WHEN_ARTIFACT : COLLABORATION_SUMMARY_NEUTRAL;
  }
  return normalized;
}

/**
 * Action-reason presentation authority — same Artifact truth / sanitizer split.
 */
export function presentCollaborationActionReason(reason, { canonicalArtifactExists = false } = {}) {
  const normalized = stringValue(reason);
  if (normalized === REVIEW_STATUS_FALLBACK) return null;
  if (!normalized) {
    return canonicalArtifactExists ? COLLABORATION_SUMMARY_WHEN_ARTIFACT : COLLABORATION_REASON_NEUTRAL;
  }
  if (collaborationNarrativeNeedsReplacement(normalized, {
    suppressFalseNoBusiness: canonicalArtifactExists,
  })) {
    return canonicalArtifactExists ? COLLABORATION_SUMMARY_WHEN_ARTIFACT : COLLABORATION_REASON_NEUTRAL;
  }
  return normalized;
}

const USER_ARTIFACT_TYPE_LABELS = {
  research_question_refinement: "研究问题凝练",
  key_literature_positioning: "关键文献定位",
  validation_design: "验证方案设计",
};

function userArtifactTitle(rawTitle, artifactType) {
  const normalizedTitle = stringValue(rawTitle)?.toLowerCase() ?? "";
  const normalizedType = stringValue(artifactType)?.toLowerCase() ?? "";
  if (normalizedTitle && normalizedTitle === normalizedType && USER_ARTIFACT_TYPE_LABELS[normalizedType]) {
    return USER_ARTIFACT_TYPE_LABELS[normalizedType];
  }
  return safeRightPanelText(rawTitle, USER_ARTIFACT_TYPE_LABELS[normalizedType] ?? "研究产物");
}

const SOURCE_MODE_LABELS = {
  live: "研究结果",
  replay: "历史结果",
  demo: "演示内容",
  none: "来源未提供",
  inline: "当前项目产物",
  external: "",
};

function artifactSourceMode(artifact, taskResultSource, apiConnected) {
  const artifactMode = stringValue(artifact.sourceMode) ?? stringValue(artifact.resultSource);
  // The legacy model represents a canonical API demo artifact as "fixture".
  // Only an API-backed task-level demo fact may translate that value to demo;
  // local fixture fallbacks remain excluded.
  if (artifactMode === "fixture" && apiConnected && taskResultSource === "demo") return "demo";
  return artifactMode ?? taskResultSource ?? "none";
}

function taskMatches(taskId, artifact) {
  const artifactTaskId = stringValue(artifact.taskId) ?? stringValue(artifact.sourceTaskId);
  return !artifactTaskId || artifactTaskId === taskId;
}

function canonicalProjectionRow(value) {
  const projection = isRecord(value) ? value : null;
  const badge = isRecord(projection?.badge) ? projection.badge : null;
  const gate = isRecord(projection?.gate) ? projection.gate : null;
  const state = stringValue(projection?.state) ?? "unknown";
  const visibleAllowedActions = Array.isArray(projection?.visibleAllowedActions)
    ? projection.visibleAllowedActions.filter((action) => typeof action === "string")
    : [];
  return {
    state,
    stateLabel: stringValue(badge?.label) ?? "状态待确认",
    stateTone: stringValue(badge?.color) ?? "default",
    countBucket: stringValue(projection?.countBucket) ?? "unknown",
    isReviewable: gate?.isReviewable === true,
    canConfirmPass: gate?.canConfirmPass === true,
    canRequestRevision: gate?.canRequestRevision === true,
    canUseDownstream: gate?.canUseDownstream === true,
    visibleAllowedActions,
  };
}

export function projectRightPanelArtifacts(taskId, projectedArtifacts, taskResultSource = "none", apiConnected = false) {
  if (!taskId || !Array.isArray(projectedArtifacts)) return [];
  const byId = new Map();
  for (const projectedArtifact of projectedArtifacts) {
    if (!isRecord(projectedArtifact)) continue;
    const artifact = isRecord(projectedArtifact.artifact) ? projectedArtifact.artifact : null;
    if (!artifact) continue;
    if (!isRecord(artifact)) continue;
    const id = stringValue(artifact.id);
    const rawTitle = stringValue(artifact.title);
    if (!id || !rawTitle || !taskMatches(taskId, artifact)) continue;
    const title = userArtifactTitle(rawTitle, artifact.artifactType);
    const canonical = canonicalProjectionRow(projectedArtifact.projection);
    const sourceMode = artifactSourceMode(artifact, taskResultSource, apiConnected);
    if (sourceMode === "fixture") continue;
    byId.set(id, {
      id,
      title,
      sourceMode,
      sourceModeLabel: SOURCE_MODE_LABELS[sourceMode] ?? "",
      ...canonical,
      order: ARTIFACT_STATE_ORDER[canonical.state] ?? ARTIFACT_STATE_ORDER.unknown,
    });
  }
  return [...byId.values()].sort((left, right) => left.order - right.order || left.title.localeCompare(right.title, "zh-CN"));
}

const TASK_RELOAD_SCOPES = new Set(["task", "collaboration", "artifact", "review", "recovery", "allowed_actions"]);
const CONTROLLER_RELOAD_SCOPES = new Set(["controller_command", "controller_receipt", "receipt"]);

export function authoritativeReloadPlan(scopes) {
  const values = new Set(Array.isArray(scopes) ? scopes.filter((value) => typeof value === "string") : []);
  return {
    collaboration: values.has("collaboration"),
    task: [...values].some((scope) => TASK_RELOAD_SCOPES.has(scope)),
    controllerCommand: [...values].some((scope) => CONTROLLER_RELOAD_SCOPES.has(scope)),
  };
}

const NON_USER_RESOURCE_VISIBILITIES = new Set(["audit", "internal", "hidden"]);
const RESOURCE_STATUS_PRESENTATION = {
  queued: { label: "已安排", tone: "default" },
  running: { label: "使用中", tone: "processing" },
  executing: { label: "使用中", tone: "processing" },
  completed: { label: "已完成", tone: "success" },
  succeeded: { label: "已完成", tone: "success" },
  failed: { label: "未完成", tone: "error" },
  blocked: { label: "受阻", tone: "warning" },
};

export function projectRightPanelResources(calls) {
  if (!Array.isArray(calls)) return [];
  const rows = [];
  for (const call of calls) {
    if (!isRecord(call)) continue;
    const id = stringValue(call.id);
    const sourceMode = stringValue(call.sourceMode) ?? stringValue(call.runMode);
    const visibility = stringValue(call.callVisibility);
    const status = stringValue(call.status);
    if (!id || sourceMode !== "live" || status === "not_started") continue;
    if (visibility && NON_USER_RESOURCE_VISIBILITIES.has(visibility)) continue;
    const narrative = isRecord(call.userFacingNarrative) ? call.userFacingNarrative : null;
    const summary = safeRightPanelText(narrative?.summary ?? call.resultSummary, "已使用研究资源");
    const presentation = RESOURCE_STATUS_PRESENTATION[status] ?? { label: "已记录", tone: "default" };
    rows.push({ id, summary, statusLabel: presentation.label, statusTone: presentation.tone });
  }
  return rows;
}
