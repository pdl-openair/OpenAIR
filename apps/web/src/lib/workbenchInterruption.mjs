import { safeRightPanelText } from "../components/workbench/workbenchRightPanelCore.mjs";

/**
 * A read-only view of the same-revision canonical progress projection.
 * Neither this view nor Artifact presentation grants a recovery action.
 * @typedef {{substageInstanceId: string, label: string, status: string}} InterruptionStep
 * @typedef {{
 *   revision: number,
 *   kind: "system_failure"|"user_input"|"external_dependency"|"external_result_unknown"|"user_stop"|"execution_cancelled",
 *   summary: string, impact: string,
 *   responsibility: "system"|"user"|"external", responsibilityLabel: string, userAction: string,
 *   affectedSteps: InterruptionStep[], completedSteps: InterruptionStep[],
 *   retainedResults: {artifacts: Array<{artifactId: string, artifactState: string|null, version: string|number|null}>, resultCount: number, summary: string, finalResultStatus: string|null},
 *   controlFact: {kind: "user_stop", summary: string, occurredAt: string|null}|null,
 *   diagnostics: Array<{reasonCode: string|null, workUnitId: string|null, substageInstanceId: string|null}>
 * }} ResearchInterruption
 */

const KINDS = new Set(["system_failure", "user_input", "external_dependency", "external_result_unknown", "user_stop", "execution_cancelled"]);
/** Align with left-nav progress semantics; never invent a second operator story. */
const STATUS_LABELS = {
  system_failure: "执行受阻",
  user_input: "需要补充信息",
  external_dependency: "等待外部条件",
  external_result_unknown: "结果待核对",
  user_stop: "研究已停止",
  execution_cancelled: "执行已取消",
};
const INTERNAL_DETAIL = /(?:[a-z]:[\\/]|\/(?:workspace|srv|app|home|tmp|var)\/|\b(?:[A-Za-z]*Error|[A-Za-z]*Exception)\s*:|research-config\.ya?ml|configuration\s*=|adapter\s*=)/i;
const record = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value) => typeof value === "string" && value.trim().length > 0;
const nullableText = (value) => value === null || typeof value === "string";
const step = (value) => record(value) && text(value.substageInstanceId) && text(value.label) && text(value.status);

/** @param {unknown} value @param {number} revision @returns {ResearchInterruption|null} */
export function parseResearchInterruption(value, revision) {
  if (!record(value) || !Number.isSafeInteger(revision) || value.revision !== revision || !KINDS.has(value.kind)
    || !["system", "user", "external"].includes(value.responsibility)
    || ![value.summary, value.impact, value.responsibilityLabel, value.userAction].every(text)
    || !Array.isArray(value.affectedSteps) || !value.affectedSteps.every(step)
    || !Array.isArray(value.completedSteps) || !value.completedSteps.every(step)
    || !record(value.retainedResults) || !Array.isArray(value.retainedResults.artifacts)
    || !value.retainedResults.artifacts.every((artifact) => record(artifact) && text(artifact.artifactId)
      && nullableText(artifact.artifactState) && (artifact.version === null || typeof artifact.version === "string" || Number.isSafeInteger(artifact.version)))
    || !Number.isSafeInteger(value.retainedResults.resultCount) || value.retainedResults.resultCount < 0
    || !text(value.retainedResults.summary) || !nullableText(value.retainedResults.finalResultStatus)
    || !(value.controlFact === null || (record(value.controlFact) && value.controlFact.kind === "user_stop"
      && text(value.controlFact.summary) && nullableText(value.controlFact.occurredAt)))
    || !Array.isArray(value.diagnostics) || !value.diagnostics.every((diagnostic) => record(diagnostic)
      && nullableText(diagnostic.reasonCode) && nullableText(diagnostic.workUnitId) && nullableText(diagnostic.substageInstanceId))) return null;
  return /** @type {ResearchInterruption} */ (value);
}

export function interruptionText(value, fallback) {
  return typeof value === "string" && !INTERNAL_DETAIL.test(value) ? safeRightPanelText(value, fallback) : fallback;
}

/** Presentation only from formal progressProjection.interruption. No second whoActsNow story. */
export function researchInterruptionPresentation(model) {
  const fact = parseResearchInterruption(model?.interruption, model?.revision);
  if (!fact) return null;
  const awaitingUser = fact.kind === "user_input";
  const stopped = fact.kind === "user_stop" || fact.kind === "execution_cancelled";
  return {
    kind: fact.kind,
    responsibility: fact.responsibility,
    presentationMode: awaitingUser ? "awaiting_user" : stopped ? "stopped" : "blocked",
    statusLabel: STATUS_LABELS[fact.kind],
    tone: stopped ? "info" : "warning",
    briefStatus: interruptionText(fact.summary, STATUS_LABELS[fact.kind]),
    summary: interruptionText(fact.summary, "当前研究暂未继续，具体原因正在确认。"),
    impact: interruptionText(fact.impact, "当前步骤及其后续判断暂未完成。"),
    responsibilityLabel: interruptionText(fact.responsibilityLabel, "处理方正在确认"),
    userAction: interruptionText(fact.userAction, "可用操作确认后会显示在协作区。"),
    completedSteps: fact.completedSteps.map((item) => ({ ...item, label: interruptionText(item.label, "已完成的研究步骤") })),
    affectedSteps: fact.affectedSteps.map((item) => ({ ...item, label: interruptionText(item.label, "尚未完成的研究步骤") })),
    retainedSummary: interruptionText(fact.retainedResults.summary, "已有研究产物继续保留，各自的核查状态保持不变。"),
    finalResultStatus: fact.retainedResults.finalResultStatus,
    controlSummary: fact.controlFact ? interruptionText(fact.controlFact.summary, "你已停止未完成的执行；已有内容仍保留。") : null,
    // Never surface internal diagnostics to ordinary users.
    diagnostics: [],
  };
}

/** Only revision/target validation. The server snapshot remains the action Authority. */
export function currentWorkbenchActionSnapshot(snapshot, taskId, revision) {
  return record(snapshot) && snapshot.schemaVersion === "workbench_user_actions_v1"
    && snapshot.taskId === taskId && Number.isSafeInteger(revision) && snapshot.revision === revision
    && Array.isArray(snapshot.allowedActions) && Array.isArray(snapshot.items)
    ? snapshot : null;
}

export function hasWorkbenchTaskAction(snapshot, taskId, revision, action) {
  return Boolean(currentWorkbenchActionSnapshot(snapshot, taskId, revision)?.allowedActions.includes(action));
}

export function currentWorkbenchActionItem(snapshot, taskId, revision, item, action) {
  const current = currentWorkbenchActionSnapshot(snapshot, taskId, revision);
  if (!current || !record(item)) return null;
  const matches = current.items.filter((candidate) => candidate.itemId === item.itemId && candidate.workUnitId === item.workUnitId);
  if (matches.length !== 1 || !Array.isArray(matches[0].allowedActions) || !matches[0].allowedActions.includes(action)) return null;
  return matches[0];
}

export function canSubmitWorkbenchSupplement(snapshot, taskId, revision) {
  return hasWorkbenchTaskAction(snapshot, taskId, revision, "supplement_input");
}

export const WORKBENCH_ACTIONS_UNCONFIRMED = "当前可用操作尚未确认，暂不能发送。未提交的内容会保留。";
export const WORKBENCH_SUPPLEMENT_UNAVAILABLE = "当前暂不能发送补充，请稍候。";
