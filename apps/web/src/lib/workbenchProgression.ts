import type { TaskStage } from "@openair/shared";
import type { WorkbenchStageRow, WorkbenchTaskModel } from "../model/workbenchTask";
import type { WorkbenchUserActionsSnapshot } from "../api/workbenchUserActions";
import { hasWorkbenchTaskAction, researchInterruptionPresentation } from "./workbenchInterruption.mjs";
import { projectWorkbenchArtifactState } from "./workbenchUi";

export interface WorkbenchProgressionView {
  conclusion: string;
  availability: string;
  evidenceState: string;
  executionState: string;
  blockerReason: string | null;
  trustBoundary: string;
  keyGap: string;
  nextAction: string;
  canStop: boolean;
  canRetryTask: boolean;
  canRetryStage: boolean;
  stopDisabledReason: string | null;
  retryTaskDisabledReason: string | null;
  retryStageDisabledReason: string | null;
  retryStageKey: TaskStage;
  primaryActionLabel: string;
  secondaryActionLabel: string;
}

function compact(value?: string | null): string | null {
  const text = (value ?? "").replace(/\s+/g, " ").trim();
  return text ? text.length > 96 ? text.slice(0, 94) + "…" : text : null;
}

/** Progress copy consumes canonical interruption facts; only the action snapshot permits controls. */
export function buildWorkbenchProgressionView(
  model: WorkbenchTaskModel,
  stage?: WorkbenchStageRow,
  actions?: WorkbenchUserActionsSnapshot | null,
): WorkbenchProgressionView {
  const current = stage ?? model.stages.find((row) => row.stage === model.currentUnderlyingStage) ?? model.stages[0] ?? null;
  const interruption = researchInterruptionPresentation(model);
  const stageIds = new Set(current?.artifactRefs ?? []);
  const stageArtifacts = current ? model.artifacts.filter((artifact) => stageIds.has(artifact.id) || artifact.relatedStage === current.stage) : [];
  const states = stageArtifacts.map(projectWorkbenchArtifactState);
  const usable = states.filter((row) => row.state === "usable").length;
  const review = states.filter((row) => row.state === "needs_review").length;
  const evidenceState = stageArtifacts.length
    ? stageArtifacts.length + " 个阶段产物保留，其中 " + usable + " 个可用、" + review + " 个待复核；其余以各自产物状态为准。"
    : "当前阶段尚无可查看产物；其他阶段已形成的内容继续保留。";
  const running = model.capabilityCalls.filter((call) => call.status === "running").length;
  const completed = model.capabilityCalls.filter((call) => call.status === "completed").length;
  const executionState = interruption?.impact ?? (running ? running + " 个步骤正在执行。" : completed ? completed + " 个执行记录已完成，成果仍须按核查状态使用。" : "以当前阶段进度和已有产物为准。");
  let conclusion = interruption?.summary ?? compact(current?.summary) ?? "当前阶段尚未形成可采用结论。";
  let availability = interruption?.statusLabel ?? "当前阶段进度可查看。";
  let nextAction = interruption?.userAction ?? "查看已有研究产物和当前可用操作。";
  if (!interruption) {
    if (model.taskStatus === "draft") {
      conclusion = "已生成研究起点草案，尚未进入正式执行。";
      availability = "需要先确认研究方案。";
      nextAction = "检查并确认研究方案。";
    } else if (model.taskStatus === "not_started") {
      availability = "任务已创建，尚未开始执行。";
      nextAction = "确认启动条件后，使用开始研究操作。";
    } else if (model.taskStatus === "completed") {
      availability = "研究执行已完成。";
      nextAction = "查看最终结果及其依据。";
    } else if (model.taskStatus === "running") {
      availability = "当前研究正在推进。";
      nextAction = "等待当前步骤更新；可用操作以协作区为准。";
    }
  }
  const canStop = hasWorkbenchTaskAction(actions, model.taskId, model.revision, "stop");
  return {
    conclusion, availability, evidenceState, executionState,
    blockerReason: interruption?.impact ?? null,
    trustBoundary: interruption?.retainedSummary ?? evidenceState,
    keyGap: interruption?.impact ?? evidenceState,
    nextAction, canStop,
    // A whole-task/stage legacy retry cannot stand in for a scoped server action.
    canRetryTask: false, canRetryStage: false,
    stopDisabledReason: canStop ? null : "当前未确认可执行的停止操作。",
    retryTaskDisabledReason: "恢复操作须先确认目标和安全条件；可用操作会在协作区显示。",
    retryStageDisabledReason: "恢复操作须先确认目标和安全条件；可用操作会在协作区显示。",
    retryStageKey: current?.stage ?? model.currentUnderlyingStage,
    primaryActionLabel: "查看当前操作", secondaryActionLabel: "查看已有成果",
  };
}
