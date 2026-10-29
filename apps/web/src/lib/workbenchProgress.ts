import { STAGE_LABELS } from "@openair/shared";
import {
  PAGE_PHASE_ORDER,
  underlyingStageForPagePhase,
  type PagePhaseId,
  type PhaseNavStatus,
} from "./pagePhases";
import type { WorkbenchTaskModel } from "../model/workbenchTask";

export interface WorkbenchProgressStep {
  id: PagePhaseId;
  label: string;
  status: PhaseNavStatus;
  /** Durable projection status from the unique screen view (same revision). */
  projectionStatus?: string;
}

const DEFAULT_PROGRESS_LABELS: Record<PagePhaseId, string> = {
  problem: STAGE_LABELS.problem_modeling,
  method: STAGE_LABELS.solution_design,
  experiment: STAGE_LABELS.execution_validation,
  report: STAGE_LABELS.insight_generation,
};

export function buildProgressSteps(task: WorkbenchTaskModel): WorkbenchProgressStep[] {
  const stageByPhase = new Map(task.stages.map((stage) => [stage.pagePhase, stage]));

  const phases = task.stages.length > 0
    ? task.stages.map((stage) => stage.pagePhase)
    : PAGE_PHASE_ORDER;

  return phases.map((phase) => {
    const stage = stageByPhase.get(phase);
    const label = DEFAULT_PROGRESS_LABELS[phase] ?? STAGE_LABELS[underlyingStageForPagePhase(phase)];

    return {
      id: phase,
      label,
      status: stage?.status ?? "not_started",
    };
  });
}

export function progressStatusLabel(status: PhaseNavStatus): string {
  if (status === "completed") return "已完成";
  if (status === "running") return "进行中";
  if (status === "blocked") return "执行受阻";
  // Generic pending is not a confirmation gate.
  if (status === "pending") return "待处理";
  if (status === "failed") return "执行失败";
  return "未开始";
}

/** User-facing left-nav copy. Only genuine awaiting_confirmation may show 待确认. */
export function flowProjectionStatusLabel(
  projectionStatus: string | undefined,
  navStatus: PhaseNavStatus,
): string {
  if (projectionStatus === "awaiting_confirmation") return "待确认";
  if (projectionStatus === "research_design_incomplete") return "研究方案未形成";
  if (projectionStatus === "needs_input") return "待用户补充";
  if (projectionStatus === "blocked_recoverable" || projectionStatus === "blocked") return "执行受阻";
  if (projectionStatus === "insufficient_evidence") return "证据不足";
  if (projectionStatus === "insufficient_materials") return "材料不足";
  if (projectionStatus === "external_result_unknown") return "结果待核查";
  if (projectionStatus === "failed") return "已失败";
  if (projectionStatus === "stopped" || projectionStatus === "cancelled") return "已停止";
  if (projectionStatus === "running") return "执行中";
  if (projectionStatus === "skipped") return "已跳过";
  if (projectionStatus === "completed") return "已完成";
  return progressStatusLabel(navStatus);
}
