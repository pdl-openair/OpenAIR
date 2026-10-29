/**
 * Pure Workbench screen-view core (no React / HTTP).
 * Left, top, middle, and right panes must all read one Task revision.
 */

export const CANONICAL_STAGE_ORDER = [
  "problem_modeling",
  "solution_design",
  "execution_validation",
  "insight_generation",
];

/** Stable backend stageKey -> user-facing label. Never derive from titles/plans/artifacts. */
export const CANONICAL_STAGE_LABELS = {
  problem_modeling: "问题建模",
  solution_design: "方案设计",
  execution_validation: "执行验证",
  insight_generation: "结果总结",
};

/** Known substageKey identity -> stageKey. Not title/agent guessing. */
export const SUBSTAGE_KEY_TO_STAGE = {
  question_refinement: "problem_modeling",
  research_question_refinement: "problem_modeling",
};

const STATUS_RANK = {
  failed: 100,
  external_result_unknown: 90,
  needs_input: 80,
  stopped: 70,
  cancelled: 70,
  running: 60,
  queued: 55,
  reviewing: 50,
  blocked_recoverable: 45,
  insufficient_evidence: 40,
  completed: 20,
  skipped: 20,
  not_started: 0,
  planned: 0,
};

export function coerceStartedStageStatus(row) {
  const raw = row.projectionStatus ?? row.status;
  if (row.statusReasonCode === "external_result_unknown") return "external_result_unknown";
  if (raw === "needs_input") return "needs_input";
  if (raw === "failed" || raw === "cancelled") return raw === "cancelled" ? "stopped" : "failed";
  if (raw === "external_result_unknown") return "external_result_unknown";
  if (raw === "skipped") return "skipped";
  const started =
    (row.workUnitIds?.length ?? 0) > 0
    || (row.completedCount ?? 0) > 0
    || ["running", "queued", "reviewing", "blocked_recoverable", "insufficient_evidence", "completed", "skipped"].includes(raw);
  if (started && (raw === "not_started" || raw === "planned")) return "running";
  if (raw === "planned") return "not_started";
  return raw;
}

export function aggregateSubstageStatus(substages = []) {
  let best = "not_started";
  for (const sub of substages) {
    const status = coerceStartedStageStatus({
      status: sub.status ?? "not_started",
      projectionStatus: sub.projectionStatus ?? sub.status ?? "not_started",
      statusReasonCode: sub.statusReasonCode ?? null,
      workUnitIds: sub.workUnitIds ?? (sub.started ? ["started"] : []),
    });
    const normalized = status === "cancelled" ? "stopped" : status;
    if ((STATUS_RANK[normalized] ?? 0) > (STATUS_RANK[best] ?? 0)) best = normalized;
  }
  return best;
}

const STATUS_LABEL = {
  running: "执行中",
  executing: "执行中",
  in_progress: "执行中",
  preparing: "准备中",
  assigned: "准备中",
  scheduled: "准备中",
  failed: "已失败",
  needs_input: "待用户补充",
  awaiting_user_confirmation: "等待你确认",
  external_result_unknown: "结果待核查",
  research_design_incomplete: "研究方案未形成",
  blocked: "执行受阻",
  stopped: "已停止",
  stopped_by_user: "已停止",
  cancelled: "已停止",
  completed: "已完成",
  succeeded: "已完成",
  skipped: "已跳过",
  not_started: "未开始",
  draft: "草案",
  awaiting_confirmation: "待确认",
  projection_unavailable: "进度暂不可确认",
  blocked_recoverable: "执行受阻",
  insufficient_evidence: "证据不足",
  insufficient_materials: "材料不足",
  reviewing: "核查中",
  system_reviewing: "核查中",
  queued: "准备中",
  planned: "已规划",
};

const SUBSTAGE_LABELS = {
  question_refinement: "研究问题凝练",
  research_question_refinement: "研究问题凝练",
  key_literature_positioning: "关键文献定位",
  result_reliability_audit: "独立结果审核",
};

function isTechnicalSubstageLabel(value) {
  return /^sdr:/i.test(value)
    || /^[a-z0-9]+(?:[_:-][a-z0-9]+)+$/i.test(value);
}

export function durableSubstageBaseLabel(substage) {
  // Canonical ResearchDesign instances carry their semantic name in the
  // instance fields; keep those authoritative before older display labels.
  const canonicalTitle = [substage?.title, substage?.name, substage?.objective]
    .find((value) => typeof value === "string" && value.trim() && !isTechnicalSubstageLabel(value.trim()));
  if (canonicalTitle) return canonicalTitle.trim();
  const authored = typeof substage?.label === "string" ? substage.label.trim() : "";
  if (authored && !isTechnicalSubstageLabel(authored)) return authored;
  const definition = String(substage?.substageDefinitionId ?? substage?.subStage ?? "").replace(/^sdr:/i, "");
  return SUBSTAGE_LABELS[definition] ?? "研究步骤";
}

export function durableSubstageDisplayLabel(substage, instanceSequence, duplicateCount = 1) {
  const baseLabel = durableSubstageBaseLabel(substage);
  return duplicateCount > 1 ? `${baseLabel} ${instanceSequence}` : baseLabel;
}

const AGENT_LABELS = {
  research_question_refinement: "研究问题凝练",
  key_literature_positioning: "关键文献定位",
  result_reliability_audit: "独立结果审核",
  orchestrator: "研究协调",
};

const INTERNAL_ROLE_PATTERN = /\b(?:planner|worker|claim|provider|runtime|canonical|orchestration|workunit|executiondecision|dispatch|receipt|recovery|controller|sse|projection|transport|lease)\b|\borchestrat(?:ion|or)\b|\blifecycle\b|\brevision\b|\bheartbeat\b|execution[\s_-]*decision|work[\s_-]*unit|\bcommand\b|\breconcile\b|\bfence\b|run[\s_-]*id|task[\s_-]*id|\bworkspace\b|transport[\s_-]*uncertain|dependency[\s_-]*preflight|artifact[\s_-]*scope|\btaskId\s*=|\brevision\s*=|服务端|后端|进度投影|权威投影|运行时|调用栈|请求标识|追踪标识|数据库|回执|恢复生命周期|任务\s*(?:ID|Id|id)/i;
const USER_FACING_FALLBACK = {
  collaborationTitle: "研究协作任务",
  collaborationDetail: "正在处理当前研究任务。",
  systemDetail: "研究步骤暂时无法执行。",
};

function userFacingCollaborationText(value, fallback) {
  const text = typeof value === "string" ? value.trim() : "";
  return text && !INTERNAL_ROLE_PATTERN.test(text) ? text : fallback;
}

const INTERACTION_LABELS = {
  handoff_requested: "请求交接",
  handoff_completed: "完成交接",
  challenge_raised: "提出质疑",
  response_provided: "回应质疑",
  review_submitted: "提交独立审核",
  acceptance_recorded: "审核通过",
  rejection_recorded: "审核未通过",
};

function agentDisplayLabel(node) {
  const authored = typeof node.agentName === "string" ? node.agentName.trim() : "";
  if (authored && !INTERNAL_ROLE_PATTERN.test(authored)) return authored;
  const agentId = typeof node.agentId === "string" ? node.agentId.trim() : "";
  if (AGENT_LABELS[agentId] && !INTERNAL_ROLE_PATTERN.test(AGENT_LABELS[agentId])) return AGENT_LABELS[agentId];
  const title = typeof node.title === "string" ? node.title.trim() : "";
  if (title && !INTERNAL_ROLE_PATTERN.test(title)) return title;
  return USER_FACING_FALLBACK.collaborationTitle;
}

function interactionDisplayLabel(event) {
  const eventType = typeof event.interactionType === "string" ? event.interactionType : "";
  return INTERACTION_LABELS[eventType] ?? "协作更新";
}

function interactionDisplayStatus(event) {
  const decision = event?.verdict?.decision;
  if (decision === "approved") return "completed";
  if (decision === "rejected") return "failed";
  if (decision === "insufficient_evidence") return "needs_input";
  return "completed";
}

function lifecycleDisplayStatus(event) {
  const payload = event?.payload ?? {};
  const raw = String(payload.status ?? payload.phase ?? "running");
  if (["scheduled", "assigned", "preparing"].includes(raw)) return "preparing";
  if (["started", "executing", "running"].includes(raw)) return "running";
  if (["persisted", "completed", "usable", "approved"].includes(raw)) return "completed";
  if (["unknown", "external_result_unknown"].includes(raw)) return "external_result_unknown";
  if (["stopped", "stopped_by_user", "cancelled"].includes(raw)) return "stopped";
  if (["blocked", "needs_input"].includes(raw)) return "needs_input";
  if (["failed", "rejected"].includes(raw)) return "failed";
  return raw;
}

function lifecycleDisplayTitle(event) {
  const payload = event?.payload ?? {};
  if (event.type === "orchestrator_lifecycle") return "研究协调";
  if (event.type === "mandatory_review_lifecycle") return "结果复核";
  if (event.type === "skill_lifecycle") return "研究技能执行";
  if (event.type === "handoff_completed") return "协作交接";
  if (event.type === "artifact_persisted") return userFacingCollaborationText(payload.title, "研究产物已保存");
  if (event.type === "execution_terminal") return "本轮执行结果";
  return agentDisplayLabel(payload);
}

function lifecycleDisplayDetail(event) {
  const payload = event?.payload ?? {};
  return userFacingCollaborationText(payload.summary || payload.title, USER_FACING_FALLBACK.systemDetail);
}

const WORK_UNIT_LIFECYCLE_TYPES = new Set([
  "agent_lifecycle",
  "mandatory_review_lifecycle",
]);

function lifecyclePhaseRank(event) {
  const phase = String(event?.payload?.phase ?? "");
  if (["completed", "persisted", "usable", "approved", "failed", "rejected", "blocked", "stopped"].includes(phase)) {
    return 30;
  }
  if (["started", "executing", "running"].includes(phase)) return 20;
  if (["scheduled", "assigned", "preparing"].includes(phase)) return 10;
  return 0;
}

function lifecycleRecency(event, index) {
  const occurredAt = event?.occurredAt ?? event?.emittedAt ?? event?.updatedAt;
  const timestamp = typeof occurredAt === "string" ? Date.parse(occurredAt) : Number.NaN;
  return {
    timestamp: Number.isFinite(timestamp) ? timestamp : -1,
    sequence: Number.isSafeInteger(event?.sequence) ? event.sequence : -1,
    phase: lifecyclePhaseRank(event),
    index,
  };
}

function isNewerLifecycle(candidate, current) {
  if (candidate.timestamp !== current.timestamp) return candidate.timestamp > current.timestamp;
  if (candidate.sequence !== current.sequence) return candidate.sequence > current.sequence;
  if (candidate.phase !== current.phase) return candidate.phase > current.phase;
  return candidate.index > current.index;
}

function lifecycleIdentity(event, index) {
  const payload = event?.payload ?? {};
  if (WORK_UNIT_LIFECYCLE_TYPES.has(event?.type) && payload.workUnitId) {
    return `work-unit:${payload.workUnitId}`;
  }
  if (event?.type === "orchestrator_lifecycle" && payload.runId) {
    return `orchestrator:${payload.runId}`;
  }
  if (event?.type === "skill_lifecycle") {
    if (payload.callId) return `skill:${payload.callId}`;
    if (payload.workUnitId && payload.skillId) return `skill:${payload.workUnitId}:${payload.skillId}`;
  }
  if (event?.type === "handoff_completed") {
    if (payload.handoffId) return `handoff:${payload.handoffId}`;
    if (payload.fromWorkUnitId || payload.toWorkUnitId) {
      return `handoff:${payload.fromWorkUnitId ?? ""}:${payload.toWorkUnitId ?? ""}`;
    }
  }
  if (event?.type === "artifact_persisted" && payload.artifactId) {
    return `artifact:${payload.artifactId}`;
  }
  if (event?.type === "execution_terminal" && payload.runId) {
    return `execution:${payload.runId}`;
  }
  return String(event?.id ?? `lifecycle-${index}`);
}

function lifecycleStatusRow(event, index, id = lifecycleIdentity(event, index)) {
  return {
    id,
    kind: event.type ?? "lifecycle",
    title: lifecycleDisplayTitle(event),
    detail: lifecycleDisplayDetail(event),
    workUnitId: event?.payload?.workUnitId ?? null,
    targetWorkUnitId: event?.payload?.toWorkUnitId ?? null,
    agentId: event?.payload?.agentId ?? null,
    occurredAt: event.occurredAt ?? event.emittedAt ?? null,
    status: lifecycleDisplayStatus(event),
  };
}

function projectCollaborationStatuses(model) {
  const workUnits = new Map();
  const workUnitOrder = [];
  (model.collaborationNodes ?? []).forEach((node, index) => {
    const id = String(node.workUnitId ?? node.agentId ?? `node-${index}`);
    const identity = node.workUnitId ? `work-unit:${node.workUnitId}` : `node:${id}`;
    if (!workUnits.has(identity)) workUnitOrder.push(identity);
    workUnits.set(identity, {
      id,
      kind: node.kind ?? "agent",
      title: agentDisplayLabel(node),
      detail: userFacingCollaborationText(node.objective, USER_FACING_FALLBACK.collaborationDetail),
      workUnitId: node.workUnitId ?? null,
      agentId: node.agentId ?? null,
      occurredAt: node.completedAt ?? node.startedAt ?? null,
      status: node.status ?? model.executionDisplayStatus,
    });
  });

  const interactionRows = new Map();
  (model.collaborationEvents ?? []).forEach((event, index) => {
    const id = String(event.interactionId ?? `interaction-${index}`);
    interactionRows.set(id, {
      id,
      kind: event.interactionType ?? "interaction",
      title: interactionDisplayLabel(event),
      detail: userFacingCollaborationText(event.summary, USER_FACING_FALLBACK.collaborationDetail),
      workUnitId: event.sourceWorkUnitId ?? null,
      targetWorkUnitId: event.targetWorkUnitId ?? null,
      occurredAt: event.createdAt ?? null,
      status: interactionDisplayStatus(event),
    });
  });

  const latestLifecycle = new Map();
  (model.lifecycleEvents ?? []).forEach((event, index) => {
    const identity = lifecycleIdentity(event, index);
    const candidate = { event, index, recency: lifecycleRecency(event, index) };
    const current = latestLifecycle.get(identity);
    if (!current || isNewerLifecycle(candidate.recency, current.recency)) {
      latestLifecycle.set(identity, candidate);
    }
  });

  const distinctLifecycleRows = [];
  for (const [identity, { event, index }] of latestLifecycle) {
    const lifecycleRow = lifecycleStatusRow(event, index, identity);
    if (identity.startsWith("work-unit:")) {
      if (!workUnits.has(identity)) workUnitOrder.push(identity);
      const current = workUnits.get(identity);
      workUnits.set(identity, {
        ...current,
        ...lifecycleRow,
        id: current?.id ?? String(lifecycleRow.workUnitId),
        title: current?.title ?? lifecycleRow.title,
        detail: lifecycleRow.detail ?? current?.detail ?? null,
        kind: event.type === "mandatory_review_lifecycle"
          ? "mandatory_review"
          : (current?.kind ?? "agent"),
      });
      continue;
    }
    distinctLifecycleRows.push(lifecycleRow);
  }

  return [
    ...workUnitOrder.map((identity) => workUnits.get(identity)).filter(Boolean),
    ...interactionRows.values(),
    ...distinctLifecycleRows,
  ];
}

export function userVisibleStatusLabel(status) {
  return STATUS_LABEL[status] ?? "状态待确认";
}

export function canonicalStageLabel(stageKey) {
  return CANONICAL_STAGE_LABELS[stageKey] ?? stageKey;
}

export function resolveSubstageStageKey(substageKey, stageKey) {
  if (stageKey && CANONICAL_STAGE_ORDER.includes(stageKey)) return stageKey;
  if (substageKey && SUBSTAGE_KEY_TO_STAGE[substageKey]) return SUBSTAGE_KEY_TO_STAGE[substageKey];
  if (stageKey) return stageKey;
  return null;
}

/**
 * Roll up stage status from durable stage row + substages.
 * A started/failed/needs_input/unknown substage must never leave the parent as 未开始.
 */
/**
 * Explicit JS/TS boundary for durable stage rollup.
 * @param {{
 *   stageStatus?: string | null,
 *   statusReasonCode?: string | null,
 *   completedCount?: number,
 *   totalCount?: number,
 *   substages?: Array<{
 *     status?: string | null,
 *     projectionStatus?: string | null,
 *     statusReasonCode?: string | null,
 *     workUnitIds?: string[],
 *     started?: boolean
 *   }>
 * }} input
 * @returns {string}
 */
export function rollupDurableStageStatus({
  stageStatus,
  statusReasonCode = null,
  completedCount = 0,
  totalCount = 0,
  substages = [],
}) {
  const subStatuses = substages.map((sub) => coerceStartedStageStatus({
    status: sub.status ?? "not_started",
    projectionStatus: sub.projectionStatus ?? sub.status ?? "not_started",
    statusReasonCode: sub.statusReasonCode ?? null,
    workUnitIds: sub.workUnitIds ?? (sub.started ? ["started"] : []),
  }));
  let best = coerceStartedStageStatus({
    status: stageStatus ?? "not_started",
    projectionStatus: stageStatus ?? "not_started",
    statusReasonCode,
    completedCount,
    totalCount,
    workUnitIds: substages.flatMap((sub) => sub.workUnitIds ?? []),
  });
  for (const status of subStatuses) {
    const normalized = status === "cancelled" ? "stopped" : status;
    if ((STATUS_RANK[normalized] ?? 0) > (STATUS_RANK[best] ?? 0)) best = normalized;
  }
  return best;
}

export function collaborationMatchesTaskRevision(collaboration, taskRevision) {
  if (!collaboration || typeof taskRevision !== "number") return false;
  if (typeof collaboration.revision !== "number") return false;
  return collaboration.revision === taskRevision;
}

export function projectVisibleDurableResearch(
  projection,
  designInstances = new Map(),
  scopeToDesign = designInstances.size > 0,
) {
  const sourceSubstages = Array.isArray(projection?.substageProgress) ? projection.substageProgress : [];
  const scopedSubstages = scopeToDesign
    ? sourceSubstages.filter((sub) => designInstances.has(sub.substageInstanceId))
    : [...sourceSubstages];
  const sourceOrder = new Map(scopedSubstages.map((sub, index) => [sub.substageInstanceId, index]));
  const substagesByStage = new Map();

  for (const sub of scopedSubstages) {
    const designInstance = designInstances.get(sub.substageInstanceId);
    const substageDefinitionId = sub.substageDefinitionId ?? designInstance?.substageDefinitionId;
    const stageKey = resolveSubstageStageKey(
      substageDefinitionId ?? sub.substageInstanceId,
      sub.stageKey ?? designInstance?.stageKey,
    );
    if (!stageKey) continue;
    const list = substagesByStage.get(stageKey) ?? [];
    list.push({
      ...sub,
      substageDefinitionId,
      stageKey,
      objective: sub.objective ?? designInstance?.objective ?? null,
      sequence: sub.sequence ?? designInstance?.sequenceHint ?? null,
    });
    substagesByStage.set(stageKey, list);
  }

  const sortSubstages = (rows) => [...rows].sort((left, right) => {
    const leftSequence = typeof left.sequence === "number"
      ? left.sequence
      : (sourceOrder.get(left.substageInstanceId) ?? 0);
    const rightSequence = typeof right.sequence === "number"
      ? right.sequence
      : (sourceOrder.get(right.substageInstanceId) ?? 0);
    return leftSequence - rightSequence || left.substageInstanceId.localeCompare(right.substageInstanceId);
  });
  const visibleStageKeys = CANONICAL_STAGE_ORDER.filter((stageKey) => substagesByStage.has(stageKey));
  const visibleStageKeySet = new Set(visibleStageKeys);
  const stageProgress = visibleStageKeys.map((stageKey) => {
    const substages = sortSubstages(substagesByStage.get(stageKey) ?? []);
    const normalizedStatuses = substages.map((sub) => coerceStartedStageStatus({
      status: sub.status,
      projectionStatus: sub.status,
      statusReasonCode: sub.statusReasonCode ?? null,
      workUnitIds: sub.workUnitIds ?? [],
    }));
    const completedCount = normalizedStatuses.filter((status) => status === "completed" || status === "skipped").length;
    const statusReasonCode = substages.find((sub) => sub.statusReasonCode)?.statusReasonCode ?? null;
    return {
      stageKey,
      status: aggregateSubstageStatus(substages.map((sub) => ({
        status: sub.status,
        projectionStatus: sub.status,
        statusReasonCode: sub.statusReasonCode ?? null,
        workUnitIds: sub.workUnitIds ?? [],
      }))),
      substageInstanceRefs: substages.map((sub) => sub.substageInstanceId),
      completedCount,
      totalCount: substages.length,
      artifactRefs: [...new Set(substages.flatMap((sub) => sub.artifactRefs ?? []))],
      statusReasonCode,
    };
  });

  return {
    ...projection,
    substageProgress: visibleStageKeys.flatMap((stageKey) => sortSubstages(substagesByStage.get(stageKey) ?? []).map((sub) => ({
      ...sub,
      label: durableSubstageBaseLabel(sub),
    }))),
    stageProgress,
    taskProgress: {
      ...(projection?.taskProgress ?? {}),
      activeStageKeys: (projection?.taskProgress?.activeStageKeys ?? [])
        .filter((stageKey) => visibleStageKeySet.has(stageKey)),
    },
  };
}

/** A declared-absent plan keeps the four stages dimmed. Partial test models omit the keys. */
function unformedPlanShell(model) {
  if (!model || !Object.prototype.hasOwnProperty.call(model, "creationPlan")) return false;
  if (!Object.prototype.hasOwnProperty.call(model, "researchDesign")) return false;
  if (model.creationPlan || model.researchDesign) return false;
  return model.taskStatus === "draft" || model.taskStatus === "failed";
}

function paneFromModel(model) {
  const revision = model.progressRevision ?? model.revision;
  const isLive = model.progressTrust?.kind === "canonical";
  const sourceStages = model.stages ?? [];
  const shell = unformedPlanShell(model);
  const byKey = new Map(sourceStages.map((stage) => [stage.stage, stage]));
  const visibleLiveStageKeys = new Set(
    sourceStages
      .filter((stage) => (stage?.subStages ?? []).length > 0)
      .map((stage) => stage.stage)
      .filter((stageKey) => CANONICAL_STAGE_ORDER.includes(stageKey)),
  );
  const orderedKeys = shell
    ? CANONICAL_STAGE_ORDER.slice()
    : isLive
      ? CANONICAL_STAGE_ORDER.filter((stageKey) => visibleLiveStageKeys.has(stageKey))
      : sourceStages.map((stage) => stage.stage).filter(Boolean);

  const stageStatuses = orderedKeys.map((stageKey) => {
    if (shell) {
      return {
        stageKey,
        status: "not_started",
        label: userVisibleStatusLabel("not_started"),
        planState: undefined,
        substages: [],
      };
    }
    const stage = byKey.get(stageKey);
    const substages = (stage?.subStages ?? []).map((sub) => ({
      id: sub.id,
      status: sub.status,
      projectionStatus: sub.projectionStatus ?? sub.status,
      statusReasonCode: sub.statusReasonCode ?? null,
      workUnitIds: sub.workUnitIds
        ?? (sub.projectionStatus && !["not_started", "planned"].includes(sub.projectionStatus) ? ["started"] : []),
      substageKey: sub.subStage ?? sub.id,
      label: sub.label,
      instanceSequence: sub.instanceSequence,
      objective: sub.objective ?? null,
    }));
    const planState = isLive ? "planned" : undefined;
    const status = isLive
      ? aggregateSubstageStatus(substages)
      : (stage?.projectionStatus ?? stage?.status ?? "not_started");
    return {
      stageKey,
      status,
      label: userVisibleStatusLabel(status),
      planState,
      substages: substages.map((sub) => {
        const subStatus = coerceStartedStageStatus({
          status: sub.status ?? "not_started",
          projectionStatus: sub.projectionStatus ?? sub.status ?? "not_started",
          statusReasonCode: sub.statusReasonCode ?? null,
          workUnitIds: sub.workUnitIds ?? [],
        });
        return {
          id: sub.id,
          substageKey: sub.substageKey,
          label: sub.label || sub.substageKey,
          instanceSequence: sub.instanceSequence,
          objective: sub.objective,
          status: subStatus,
          statusLabel: userVisibleStatusLabel(subStatus),
        };
      }),
    };
  });

  return {
    revision,
    executionDisplayStatus: model.executionDisplayStatus,
    stageStatuses,
    collaborationStatuses: projectCollaborationStatuses(model),
  };
}

/** Ordinary-user CTAs come only from workbench_user_actions_v1. Never invent next actions here. */
function failureNextActions(_model) {
  return [];
}

export function buildWorkbenchScreenView(model, collaboration) {
  const isLiveResearch = model.progressTrust?.kind === "canonical";
  const pane = paneFromModel(model);
  const matchedCollaboration = collaborationMatchesTaskRevision(collaboration, pane.revision)
    ? collaboration
    : null;
  const leftStages = pane.stageStatuses.map((stage) => ({
    stageKey: stage.stageKey,
    status: stage.status,
    statusLabel: stage.label,
    label: isLiveResearch ? canonicalStageLabel(stage.stageKey) : (canonicalStageLabel(stage.stageKey) || stage.stageKey),
    completedCount: undefined,
    totalCount: undefined,
    substages: stage.substages,
    planState: stage.planState,
  }));
  const middlePath = leftStages.map(({ stageKey, status, statusLabel, label, substages, planState }) => ({
    stageKey,
    status,
    statusLabel,
    label,
    substages,
    planState,
  }));
  const rightCollaboration = pane.collaborationStatuses.map((node) => ({
    id: node.id,
    status: node.status,
    label: userVisibleStatusLabel(node.status),
    title: node.title,
    detail: node.detail,
    kind: node.kind,
    workUnitId: node.workUnitId,
    targetWorkUnitId: node.targetWorkUnitId,
    agentId: node.agentId,
    occurredAt: node.occurredAt,
  }));
  const visibleSubstages = leftStages
    .flatMap((stage) => (stage.substages ?? []).map((sub) => ({ ...sub, stageKey: stage.stageKey, stageLabel: stage.label })));
  const activeSubstages = visibleSubstages
    .filter((sub) => ["running", "failed", "needs_input", "external_result_unknown", "stopped"].includes(sub.status));
  const activeSubstage = activeSubstages[0] ?? null;
  const stageCounts = visibleSubstages.length > 0
    ? {
      completed: visibleSubstages.filter((sub) => ["completed", "skipped"].includes(sub.status)).length,
      total: visibleSubstages.length,
    }
    : null;

  return {
    taskId: model.taskId,
    revision: pane.revision,
    progressTrust: model.progressTrust,
    isLiveResearch,
    executionDisplayStatus: model.executionDisplayStatus,
    executionStatusLabel: userVisibleStatusLabel(model.executionDisplayStatus),
    stageCounts: isLiveResearch ? stageCounts : (model.stageCounts ?? null),
    pane,
    leftStages,
    middlePath,
    rightCollaboration,
    activeSubstages,
    activeSubstage,
    matchedCollaboration,
    failureNextActions: isLiveResearch || model.progressTrust?.kind === "invalid"
      ? failureNextActions(model)
      : [],
    forbidLegacyRetry: isLiveResearch || model.progressTrust?.kind === "invalid",
    legacyCompatibilityNote: model.progressTrust?.kind === "legacy"
      ? "当前任务使用兼容展示；部分进度信息可能不完整。"
      : model.progressTrust?.kind === "invalid"
        ? "暂时无法确认最新进度，已避免展示可能过期的成功状态。"
        : null,
  };
}

export function assertScreenPanesShareRevision(screen) {
  const { revision, executionDisplayStatus } = screen.pane;
  if (screen.revision !== revision) return false;
  if (screen.executionDisplayStatus !== executionDisplayStatus) return false;
  if (screen.leftStages.some((stage, i) => stage.status !== screen.middlePath[i]?.status)) return false;
  if (screen.rightCollaboration.some((node) => typeof node.status !== "string")) return false;
  return true;
}

export function projectScreenToPanes(screen) {
  return {
    left: {
      revision: screen.revision,
      executionDisplayStatus: screen.executionDisplayStatus,
      stages: screen.leftStages.map((stage) => ({
        stageKey: stage.stageKey,
        status: stage.status,
        label: stage.label,
        planState: stage.planState,
        substages: stage.substages ?? [],
      })),
    },
    middle: {
      revision: screen.revision,
      executionDisplayStatus: screen.executionDisplayStatus,
      path: screen.middlePath.map((step) => ({
        stageKey: step.stageKey,
        status: step.status,
        label: step.label,
        planState: step.planState,
        substages: step.substages ?? [],
      })),
    },
    right: {
      revision: screen.revision,
      executionDisplayStatus: screen.executionDisplayStatus,
      collaboration: screen.rightCollaboration.map((node) => ({
        id: node.id,
        status: node.status,
        label: node.label,
        title: node.title,
        detail: node.detail,
        kind: node.kind,
      })),
    },
    top: {
      revision: screen.revision,
      executionDisplayStatus: screen.executionDisplayStatus,
      label: screen.executionStatusLabel,
    },
  };
}

export function panesShareSingleRevision(panes) {
  const revisions = [panes.left.revision, panes.middle.revision, panes.right.revision, panes.top.revision];
  if (revisions.some((revision) => revision !== revisions[0])) return false;
  const statuses = [
    panes.left.executionDisplayStatus,
    panes.middle.executionDisplayStatus,
    panes.right.executionDisplayStatus,
    panes.top.executionDisplayStatus,
  ];
  if (statuses.some((status) => status !== statuses[0])) return false;
  return panes.left.stages.every((stage, index) => stage.status === panes.middle.path[index]?.status);
}

export function stageStatusAfterExternalFailure(projectionStatus, statusReasonCode, workUnitIds = ["wu-1"]) {
  return coerceStartedStageStatus({
    status: "not_started",
    projectionStatus,
    statusReasonCode,
    workUnitIds,
  });
}

export function mapScreenStatusToNav(status) {
  if (status === "completed" || status === "skipped") return "completed";
  if (status === "running" || status === "queued" || status === "reviewing") return "running";
  if (status === "blocked" || status === "blocked_recoverable") {
    // System-internal recoverable block — never user confirmation ("pending").
    return "blocked";
  }
  if (
    status === "needs_input"
    || status === "insufficient_evidence"
    || status === "insufficient_materials"
    || status === "external_result_unknown"
  ) {
    return "pending";
  }
  if (
    status === "failed"
    || status === "cancelled"
    || status === "stopped"
    || status === "projection_unavailable"
  ) {
    return "failed";
  }
  return "not_started";
}

export function progressStepsFromScreen(screen, resolveStage) {
  return screen.leftStages.map((stage) => {
    const meta = typeof resolveStage === "function"
      ? resolveStage(stage.stageKey)
      : { id: stage.stageKey, label: stage.label };
    return {
      id: meta.id,
      stageKey: stage.stageKey,
      label: stage.label || meta.label,
      status: mapScreenStatusToNav(stage.status),
      projectionStatus: stage.status,
      substages: stage.substages ?? [],
    };
  });
}

export function phaseAggFromScreen(screen, stageKeyToPhase) {
  const out = {};
  for (const stage of screen.leftStages) {
    const phase = stageKeyToPhase(stage.stageKey);
    out[phase] = mapScreenStatusToNav(stage.status);
  }
  return out;
}

/** True when any pane still shows 未开始 while a started terminal status exists for that stage. */
export function hasStartedVsNotStartedContradiction(screen) {
  return screen.leftStages.some((stage) => {
    const startedSub = (stage.substages ?? []).some((sub) =>
      ["running", "failed", "needs_input", "external_result_unknown", "stopped", "completed", "skipped"].includes(sub.status),
    );
    return startedSub && stage.status === "not_started";
  });
}
