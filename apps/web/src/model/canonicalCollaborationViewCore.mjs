const SCHEMA_VERSION = "canonical_collaboration_v1";
const LIFECYCLE_KIND = "canonical_v1_2";
const REVISION_FENCE_KIND = "research_task_v1_2_revision";

const TASK_STATUSES = new Set([
  "draft",
  "not_started",
  "running",
  "awaiting_confirmation",
  "completed",
  "failed",
  "cancelled",
]);
const DECISION_STATUSES = new Set(["selected", "dispatched", "closed"]);
const DECISION_OUTCOMES = new Set([
  "succeeded",
  "partial",
  "progressed",
  "needs_input",
  "rejected",
  "blocked",
  "failed",
  "cancelled",
]);
const ROLES = new Set(["执行工作", "复核工作", "支持工作"]);
const WORK_UNIT_STATUSES = new Set([
  "queued",
  "running",
  "reviewing",
  "completed",
  "failed",
  "blocked",
  "cancelled",
  "external_result_unknown",
]);
const WAIT_KINDS = new Set([
  "dependency",
  "system_review",
  "user_action",
  "runtime_unavailable",
  "external_result_unknown",
  "command_pending",
  "blocked",
]);
const ARTIFACT_STATES = new Set([
  "usable",
  "needs_review",
  "needs_input",
  "insufficient_materials",
  "failed",
  "partial_success",
  "rejected_or_needs_revision",
]);
const REVIEW_DECISIONS = new Set(["approved", "rejected", "insufficient_evidence"]);
const HANDOFF_STATES = new Set(["requested", "completed"]);
const ACTION_ITEM_KINDS = new Set([
  "provide_input",
  "artifact_review_required",
  "external_result_unknown",
  "explicit_retry_ready",
  "supplement_pending",
  "supplement_rejected",
]);
const ALLOWED_ACTIONS = new Set([
  "supplement_input",
  "explicit_retry",
  "stop",
]);

export class CanonicalCollaborationContractError extends Error {
  constructor(kind, message) {
    super(message);
    this.name = "CanonicalCollaborationContractError";
    this.kind = kind;
  }
}

function contractError(kind, path, message) {
  throw new CanonicalCollaborationContractError(kind, `${path}: ${message}`);
}

function record(value, path) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    contractError("invalid_contract", path, "应为对象");
  }
  return value;
}

function stringValue(value, path, { allowEmpty = false } = {}) {
  if (typeof value !== "string") contractError("invalid_contract", path, "应为字符串");
  const normalized = value.trim();
  if (!allowEmpty && normalized.length === 0) contractError("invalid_contract", path, "不能为空");
  return normalized;
}

function optionalString(value, path) {
  if (value === null || value === undefined) return null;
  return stringValue(value, path, { allowEmpty: true });
}

function integerValue(value, path, minimum = 0) {
  if (!Number.isInteger(value) || value < minimum) {
    contractError("invalid_contract", path, `应为不小于 ${minimum} 的整数`);
  }
  return value;
}

function enumValue(value, allowed, path) {
  const parsed = stringValue(value, path);
  if (!allowed.has(parsed)) contractError("invalid_contract", path, `未知值 ${parsed}`);
  return parsed;
}

function arrayValue(value, path) {
  if (!Array.isArray(value)) contractError("invalid_contract", path, "应为数组");
  return value;
}

function stringArray(value, path) {
  return arrayValue(value, path).map((entry, index) => stringValue(entry, `${path}[${index}]`));
}

function nullableRecord(value, path) {
  if (value === null || value === undefined) return null;
  return record(value, path);
}

function uniqueBy(rows, key, path) {
  const seen = new Set();
  for (const row of rows) {
    const value = row[key];
    if (seen.has(value)) contractError("invalid_contract", path, `重复标识 ${value}`);
    seen.add(value);
  }
}

function ensureRefs(refs, known, path) {
  for (const ref of refs) {
    if (!known.has(ref)) contractError("invalid_contract", path, "引用了当前执行决策之外的对象");
  }
}

function parseDecision(value) {
  const row = nullableRecord(value, "executionDecision");
  if (row === null) return null;
  return {
    executionDecisionId: stringValue(row.executionDecisionId, "executionDecision.executionDecisionId"),
    decisionSequence: integerValue(row.decisionSequence, "executionDecision.decisionSequence", 1),
    status: enumValue(row.status, DECISION_STATUSES, "executionDecision.status"),
    outcome: row.outcome === null || row.outcome === undefined
      ? null
      : enumValue(row.outcome, DECISION_OUTCOMES, "executionDecision.outcome"),
    researchDesignVersion: integerValue(row.researchDesignVersion, "executionDecision.researchDesignVersion", 1),
    substageInstanceId: stringValue(row.substageInstanceId, "executionDecision.substageInstanceId"),
    substageInstanceRevision: integerValue(
      row.substageInstanceRevision,
      "executionDecision.substageInstanceRevision",
      1,
    ),
    decidedAt: stringValue(row.decidedAt, "executionDecision.decidedAt"),
    closedAt: optionalString(row.closedAt, "executionDecision.closedAt"),
  };
}

function parseWaiting(value, path) {
  const row = nullableRecord(value, path);
  if (row === null) return null;
  return {
    kind: enumValue(row.kind, WAIT_KINDS, `${path}.kind`),
    reason: stringValue(row.reason, `${path}.reason`),
    reasonCode: stringValue(row.reasonCode, `${path}.reasonCode`),
    blockingWorkUnitIds: stringArray(row.blockingWorkUnitIds ?? [], `${path}.blockingWorkUnitIds`),
  };
}

function parseWorkUnit(value, index) {
  const path = `workUnits[${index}]`;
  const row = record(value, path);
  return {
    workUnitId: stringValue(row.workUnitId, `${path}.workUnitId`),
    assignmentId: stringValue(row.assignmentId, `${path}.assignmentId`),
    role: enumValue(row.role, ROLES, `${path}.role`),
    objective: stringValue(row.objective, `${path}.objective`, { allowEmpty: true }),
    responsibleActorId: stringValue(row.responsibleActorId, `${path}.responsibleActorId`),
    responsibleActorType: stringValue(row.responsibleActorType, `${path}.responsibleActorType`, { allowEmpty: true }),
    status: enumValue(row.status, WORK_UNIT_STATUSES, `${path}.status`),
    parallelGroupId: optionalString(row.parallelGroupId, `${path}.parallelGroupId`),
    dependencyWorkUnitIds: stringArray(row.dependencyWorkUnitIds ?? [], `${path}.dependencyWorkUnitIds`),
    waiting: parseWaiting(row.waiting, `${path}.waiting`),
    reviewerWorkUnitIds: stringArray(row.reviewerWorkUnitIds ?? [], `${path}.reviewerWorkUnitIds`),
    inputArtifactRefs: stringArray(row.inputArtifactRefs ?? [], `${path}.inputArtifactRefs`),
    outputArtifactRefs: stringArray(row.outputArtifactRefs ?? [], `${path}.outputArtifactRefs`),
    needsReviewArtifactRefs: stringArray(row.needsReviewArtifactRefs ?? [], `${path}.needsReviewArtifactRefs`),
    failureReasonCode: optionalString(row.failureReasonCode, `${path}.failureReasonCode`),
    dispatchedAt: stringValue(row.dispatchedAt, `${path}.dispatchedAt`),
    finishedAt: optionalString(row.finishedAt, `${path}.finishedAt`),
  };
}

function parseArtifact(value, index) {
  const path = `artifacts[${index}]`;
  const row = record(value, path);
  return {
    artifactId: stringValue(row.artifactId, `${path}.artifactId`),
    workUnitId: stringValue(row.workUnitId, `${path}.workUnitId`),
    artifactType: stringValue(row.artifactType, `${path}.artifactType`, { allowEmpty: true }),
    artifactState: enumValue(row.artifactState, ARTIFACT_STATES, `${path}.artifactState`),
    version: integerValue(row.version, `${path}.version`, 1),
    sourceCapabilityCallId: optionalString(row.sourceCapabilityCallId, `${path}.sourceCapabilityCallId`),
  };
}

function parseVerdict(value, path) {
  const row = nullableRecord(value, path);
  if (row === null) return null;
  return {
    decision: enumValue(row.decision, REVIEW_DECISIONS, `${path}.decision`),
    rationaleCode: stringValue(row.rationaleCode, `${path}.rationaleCode`),
  };
}

function parseHandoff(value, index) {
  const path = `handoffs[${index}]`;
  const row = record(value, path);
  return {
    handoffId: stringValue(row.handoffId, `${path}.handoffId`),
    state: enumValue(row.state, HANDOFF_STATES, `${path}.state`),
    fromWorkUnitId: stringValue(row.fromWorkUnitId, `${path}.fromWorkUnitId`),
    toWorkUnitId: stringValue(row.toWorkUnitId, `${path}.toWorkUnitId`),
    summary: stringValue(row.summary, `${path}.summary`, { allowEmpty: true }),
    artifactRefs: stringArray(row.artifactRefs ?? [], `${path}.artifactRefs`),
    verdict: parseVerdict(row.verdict, `${path}.verdict`),
    occurredAt: stringValue(row.occurredAt, `${path}.occurredAt`),
  };
}

function parseReview(value, index) {
  const path = `reviews[${index}]`;
  const row = record(value, path);
  return {
    reviewId: stringValue(row.reviewId, `${path}.reviewId`),
    reviewerWorkUnitId: stringValue(row.reviewerWorkUnitId, `${path}.reviewerWorkUnitId`),
    targetWorkUnitId: optionalString(row.targetWorkUnitId, `${path}.targetWorkUnitId`),
    artifactRefs: stringArray(row.artifactRefs ?? [], `${path}.artifactRefs`),
    decision: enumValue(row.decision, REVIEW_DECISIONS, `${path}.decision`),
    rationaleCode: stringValue(row.rationaleCode, `${path}.rationaleCode`),
    submittedAt: stringValue(row.submittedAt, `${path}.submittedAt`),
  };
}

function parseAllowedActions(value, path) {
  return arrayValue(value ?? [], path).map((entry, index) =>
    enumValue(entry, ALLOWED_ACTIONS, `${path}[${index}]`),
  );
}

function parseActionItem(value, index) {
  const path = `userActionItems[${index}]`;
  const row = record(value, path);
  return {
    itemId: stringValue(row.itemId, `${path}.itemId`),
    kind: enumValue(row.kind, ACTION_ITEM_KINDS, `${path}.kind`),
    reason: stringValue(row.reason, `${path}.reason`),
    workUnitId: optionalString(row.workUnitId, `${path}.workUnitId`),
    artifactRefs: stringArray(row.artifactRefs ?? [], `${path}.artifactRefs`),
    allowedActions: parseAllowedActions(row.allowedActions, `${path}.allowedActions`),
  };
}

export function parseCanonicalCollaborationPayload(input, options = {}) {
  const root = record(input, "response");
  if (root.schemaVersion === undefined) {
    const looksLegacy = "entries" in root || ("status" in root && !("lifecycleKind" in root));
    contractError(
      looksLegacy ? "legacy_shape" : "unknown_schema",
      "schemaVersion",
      looksLegacy ? "收到 legacy collaboration 结构" : "缺少 canonical schemaVersion",
    );
  }
  const schemaVersion = stringValue(root.schemaVersion, "schemaVersion");
  if (schemaVersion !== SCHEMA_VERSION) {
    contractError("unknown_schema", "schemaVersion", `不支持 ${schemaVersion}`);
  }
  const lifecycleKind = stringValue(root.lifecycleKind, "lifecycleKind");
  if (lifecycleKind !== LIFECYCLE_KIND) {
    contractError("lifecycle_mismatch", "lifecycleKind", `只接受 ${LIFECYCLE_KIND}`);
  }

  const revision = integerValue(root.revision, "revision", 0);
  const revisionFence = record(root.revisionFence, "revisionFence");
  const revisionFenceKind = stringValue(revisionFence.kind, "revisionFence.kind");
  if (revisionFenceKind !== REVISION_FENCE_KIND) {
    contractError("invalid_contract", "revisionFence.kind", `应为 ${REVISION_FENCE_KIND}`);
  }
  const fencedRevision = integerValue(revisionFence.revision, "revisionFence.revision", 0);
  if (fencedRevision !== revision) {
    contractError("revision_mismatch", "revisionFence.revision", "与响应 revision 不一致");
  }
  if (Number.isInteger(options.expectedRevision) && options.expectedRevision !== revision) {
    contractError(
      "revision_mismatch",
      "revision",
      `任务当前 revision=${options.expectedRevision}，协作快照 revision=${revision}`,
    );
  }

  const executionDecision = parseDecision(root.executionDecision);
  const sourceIdentity = optionalString(root.sourceIdentity, "sourceIdentity");
  if (executionDecision === null && sourceIdentity !== null) {
    contractError("source_identity_mismatch", "sourceIdentity", "无 ExecutionDecision 时必须为空");
  }
  if (executionDecision !== null && sourceIdentity !== executionDecision.executionDecisionId) {
    contractError(
      "source_identity_mismatch",
      "sourceIdentity",
      "必须等于当前 executionDecisionId",
    );
  }

  const workUnits = arrayValue(root.workUnits ?? [], "workUnits").map(parseWorkUnit);
  const artifacts = arrayValue(root.artifacts ?? [], "artifacts").map(parseArtifact);
  const handoffs = arrayValue(root.handoffs ?? [], "handoffs").map(parseHandoff);
  const reviews = arrayValue(root.reviews ?? [], "reviews").map(parseReview);
  const userActionItems = arrayValue(root.userActionItems ?? [], "userActionItems").map(parseActionItem);
  const allowedActions = parseAllowedActions(root.allowedActions, "allowedActions");

  if (executionDecision === null && workUnits.length > 0) {
    contractError("source_identity_mismatch", "workUnits", "无 ExecutionDecision 时不得包含 WorkUnit");
  }

  uniqueBy(workUnits, "workUnitId", "workUnits");
  uniqueBy(artifacts, "artifactId", "artifacts");
  uniqueBy(handoffs, "handoffId", "handoffs");
  uniqueBy(reviews, "reviewId", "reviews");
  uniqueBy(userActionItems, "itemId", "userActionItems");

  const workUnitIds = new Set(workUnits.map((row) => row.workUnitId));
  const artifactIds = new Set(artifacts.map((row) => row.artifactId));
  for (const unit of workUnits) {
    ensureRefs(unit.dependencyWorkUnitIds, workUnitIds, "workUnits.dependencyWorkUnitIds");
    ensureRefs(unit.reviewerWorkUnitIds, workUnitIds, "workUnits.reviewerWorkUnitIds");
    ensureRefs(unit.outputArtifactRefs, artifactIds, "workUnits.outputArtifactRefs");
    ensureRefs(unit.needsReviewArtifactRefs, artifactIds, "workUnits.needsReviewArtifactRefs");
    if (unit.waiting) ensureRefs(unit.waiting.blockingWorkUnitIds, workUnitIds, "workUnits.waiting.blockingWorkUnitIds");
  }
  for (const artifact of artifacts) ensureRefs([artifact.workUnitId], workUnitIds, "artifacts.workUnitId");
  for (const handoff of handoffs) {
    ensureRefs([handoff.fromWorkUnitId, handoff.toWorkUnitId], workUnitIds, "handoffs.workUnitIds");
    ensureRefs(handoff.artifactRefs, artifactIds, "handoffs.artifactRefs");
  }
  for (const review of reviews) {
    ensureRefs([review.reviewerWorkUnitId], workUnitIds, "reviews.reviewerWorkUnitId");
    if (review.targetWorkUnitId) ensureRefs([review.targetWorkUnitId], workUnitIds, "reviews.targetWorkUnitId");
    ensureRefs(review.artifactRefs, artifactIds, "reviews.artifactRefs");
  }
  for (const item of userActionItems) {
    if (item.workUnitId) ensureRefs([item.workUnitId], workUnitIds, "userActionItems.workUnitId");
    ensureRefs(item.artifactRefs, artifactIds, "userActionItems.artifactRefs");
  }

  return {
    schemaVersion,
    lifecycleKind,
    taskId: stringValue(root.taskId, "taskId"),
    taskStatus: enumValue(root.taskStatus, TASK_STATUSES, "taskStatus"),
    revision,
    revisionFence: { kind: revisionFenceKind, revision: fencedRevision },
    sourceIdentity,
    executionDecision,
    workUnits,
    handoffs,
    reviews,
    artifacts,
    userActionItems,
    allowedActions,
  };
}

const STATUS_LABELS = {
  queued: "待开始",
  running: "正在执行",
  reviewing: "正在复核",
  completed: "已完成",
  failed: "执行失败",
  blocked: "已阻塞",
  cancelled: "已取消",
  external_result_unknown: "结果待核对",
};

const ARTIFACT_STATE_LABELS = {
  usable: "可使用",
  needs_review: "待复核",
  needs_input: "待补充",
  insufficient_materials: "材料不足",
  failed: "形成失败",
  partial_success: "部分形成",
  rejected_or_needs_revision: "需修改",
};

const REVIEW_LABELS = {
  approved: "复核通过",
  rejected: "复核未通过",
  insufficient_evidence: "证据不足",
};

const ACTION_KIND_LABELS = {
  provide_input: "补充研究材料",
  artifact_review_required: "复核当前产物",
  external_result_unknown: "核对外部执行结果",
  explicit_retry_ready: "重试",
  supplement_pending: "补充已提交",
  supplement_rejected: "补充未生效",
};

const ACTION_LABELS = {
  supplement_input: "补充材料",
  explicit_retry: "重试",
  stop: "停止执行",
};

const INTERNAL_PRESENTATION_TEXT = /\bcanonical\b|\borchestrat(?:ion|or)\b|\bplanner\b|\bworker\b|\bclaim\b|\bruntime\b|\bprovider\b|\breceipt\b|\brecovery\b|\blifecycle\b|\brevision\b|\bheartbeat\b|\bsse\b|execution[\s_-]*decision|work[\s_-]*unit|\bcontroller\b|\bcommand\b|\breconcile\b|\bfence\b|run[\s_-]*id|task[\s_-]*id|workspace|dispatch|transport[\s_-]*uncertain|dependency[\s_-]*preflight|artifact[\s_-]*scope|运行时|服务端|后端|数据库|回执|恢复生命周期|进度投影|任务\s*(?:ID|Id|id)/i;

function presentationText(value, fallback) {
  const text = typeof value === "string" ? value.trim() : "";
  return text && !INTERNAL_PRESENTATION_TEXT.test(text) ? text : fallback;
}

const ARTIFACT_TYPE_LABELS = {
  research_report: "研究报告",
  analysis_report: "分析报告",
  evidence_table: "证据表",
  source_table: "来源表",
  literature_review: "文献综述",
  dataset: "数据集",
  code: "代码产物",
  chart: "图表",
  summary: "研究摘要",
};

function displayArtifactType(value) {
  const normalized = value.trim().toLowerCase();
  if (ARTIFACT_TYPE_LABELS[normalized]) return ARTIFACT_TYPE_LABELS[normalized];
  if (normalized.includes("report")) return "研究报告";
  if (normalized.includes("review")) return "复核产物";
  if (normalized.includes("evidence") || normalized.includes("source")) return "证据材料";
  if (normalized.includes("table") || normalized.includes("csv")) return "数据表";
  if (normalized.includes("code") || normalized.includes("script")) return "代码产物";
  if (normalized.includes("chart") || normalized.includes("plot")) return "图表";
  if (normalized.includes("summary") || normalized.includes("note")) return "研究摘要";
  return "研究产物";
}

function localKey(prefix, index) {
  return `${prefix}-${index + 1}`;
}

function displayTime(value) {
  return value || null;
}

export function buildCanonicalCollaborationView(snapshot) {
  const unitMeta = new Map();
  snapshot.workUnits.forEach((unit, index) => {
    unitMeta.set(unit.workUnitId, {
      key: localKey("work", index),
      objective: presentationText(unit.objective, `${unit.role}正在处理当前任务`),
      role: unit.role,
    });
  });

  const artifactMeta = new Map();
  snapshot.artifacts.forEach((artifact, index) => {
    artifactMeta.set(artifact.artifactId, {
      key: localKey("artifact", index),
      label: displayArtifactType(artifact.artifactType),
      state: artifact.artifactState,
      stateLabel: ARTIFACT_STATE_LABELS[artifact.artifactState],
      version: artifact.version,
    });
  });

  const groupRows = new Map();
  snapshot.workUnits.forEach((unit) => {
    const groupKey = unit.parallelGroupId ? `parallel:${unit.parallelGroupId}` : `single:${unit.workUnitId}`;
    const rows = groupRows.get(groupKey) ?? [];
    rows.push(unit);
    groupRows.set(groupKey, rows);
  });

  let parallelIndex = 0;
  const currentWork = Array.from(groupRows.entries()).map(([groupKey, units], groupIndex) => {
    const isParallel = groupKey.startsWith("parallel:") && units.length > 1;
    if (isParallel) parallelIndex += 1;
    return {
      key: localKey("group", groupIndex),
      title: isParallel ? `并行工作 ${parallelIndex}` : "独立工作",
      isParallel,
      items: units.map((unit) => {
        const meta = unitMeta.get(unit.workUnitId);
        return {
          key: meta.key,
          role: unit.role,
          objective: meta.objective,
          status: unit.status,
          statusLabel: STATUS_LABELS[unit.status],
          dispatchedAt: displayTime(unit.dispatchedAt),
          finishedAt: displayTime(unit.finishedAt),
          artifacts: unit.outputArtifactRefs
            .map((ref) => artifactMeta.get(ref))
            .filter(Boolean),
          needsReviewCount: unit.needsReviewArtifactRefs.length,
        };
      }),
    };
  });

  const waitingAndDependencies = snapshot.workUnits
    .filter((unit) => unit.waiting !== null || unit.dependencyWorkUnitIds.length > 0)
    .map((unit, index) => {
      const meta = unitMeta.get(unit.workUnitId);
      const dependencyIds = unit.waiting?.blockingWorkUnitIds.length
        ? unit.waiting.blockingWorkUnitIds
        : unit.dependencyWorkUnitIds;
      return {
        key: localKey("waiting", index),
        workKey: meta.key,
        role: unit.role,
        objective: meta.objective,
        reason: presentationText(unit.waiting?.reason, "需要等待相关研究步骤完成。"),
        kind: unit.waiting?.kind ?? "dependency",
        dependencies: dependencyIds
          .map((ref) => unitMeta.get(ref))
          .filter(Boolean)
          .map((dependency) => ({
            key: dependency.key,
            label: dependency.objective,
            role: dependency.role,
          })),
        reviewers: unit.reviewerWorkUnitIds
          .map((ref) => unitMeta.get(ref))
          .filter(Boolean)
          .map((reviewer) => ({
            key: reviewer.key,
            label: reviewer.objective,
            role: reviewer.role,
          })),
      };
    });

  const reviewAndHandoffs = [
    ...snapshot.handoffs.map((handoff) => {
      const from = unitMeta.get(handoff.fromWorkUnitId);
      const to = unitMeta.get(handoff.toWorkUnitId);
      return {
        type: "handoff",
        occurredAt: handoff.occurredAt,
        state: handoff.state,
        payload: {
          summary: presentationText(handoff.summary, "相关工作已完成交接。"),
          stateLabel: handoff.state === "completed" ? "交接已完成" : "已发起交接",
          from: { key: from.key, label: from.objective, role: from.role },
          to: { key: to.key, label: to.objective, role: to.role },
          artifacts: handoff.artifactRefs.map((ref) => artifactMeta.get(ref)).filter(Boolean),
          verdictLabel: handoff.verdict ? REVIEW_LABELS[handoff.verdict.decision] : null,
        },
      };
    }),
    ...snapshot.reviews.map((review) => {
      const reviewer = unitMeta.get(review.reviewerWorkUnitId);
      const target = review.targetWorkUnitId ? unitMeta.get(review.targetWorkUnitId) : null;
      return {
        type: "review",
        occurredAt: review.submittedAt,
        state: review.decision,
        payload: {
          reviewer: { key: reviewer.key, label: reviewer.objective, role: reviewer.role },
          target: target ? { key: target.key, label: target.objective, role: target.role } : null,
          decisionLabel: REVIEW_LABELS[review.decision],
          artifacts: review.artifactRefs.map((ref) => artifactMeta.get(ref)).filter(Boolean),
        },
      };
    }),
  ]
    .sort((left, right) => left.occurredAt.localeCompare(right.occurredAt))
    .map((entry, index) => ({ ...entry, key: localKey("review-event", index) }));

  const userActionItems = snapshot.userActionItems.map((item, index) => {
    const work = item.workUnitId ? unitMeta.get(item.workUnitId) : null;
    return {
      key: localKey("action-item", index),
      kind: item.kind,
      title: ACTION_KIND_LABELS[item.kind],
      reason: presentationText(item.reason, "当前需要你处理一项研究任务。"),
      work: work ? { key: work.key, label: work.objective, role: work.role } : null,
      artifacts: item.artifactRefs.map((ref) => artifactMeta.get(ref)).filter(Boolean),
      allowedActions: item.allowedActions.map((action) => ({
        action,
        label: ACTION_LABELS[action],
      })),
    };
  });

  return {
    schemaVersion: snapshot.schemaVersion,
    lifecycleKind: snapshot.lifecycleKind,
    taskId: snapshot.taskId,
    taskStatus: snapshot.taskStatus,
    revision: snapshot.revision,
    hasExecutionDecision: snapshot.executionDecision !== null,
    currentWork,
    waitingAndDependencies,
    reviewAndHandoffs,
    userActionItems,
    allowedActions: [...snapshot.allowedActions],
  };
}

export function decodeCanonicalCollaborationView(input, options = {}) {
  return buildCanonicalCollaborationView(parseCanonicalCollaborationPayload(input, options));
}
