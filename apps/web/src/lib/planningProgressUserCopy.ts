/**
 * Single user-facing outlet for research-start / planning progress codes.
 * Research-user language only; no Worker / queue / infra wording.
 */
export const PLANNING_PROGRESS_USER_COPY: Readonly<Record<string, string>> = {
  still_queued: "研究方案还在准备中。",
  worker_assigned: "研究方案正在准备中。",
  organizing_intent: "正在整理研究目标、研究范围、约束和相关工作。",
  planning_design: "正在组织研究方案。",
  compiling_design: "研究方案正在形成并核对完整性。",
  repairing_design: "研究方案正在重新整理并核对。",
  queued_stale: "准备时间比通常更长，你可以先离开，稍后再回来查看。",
  worker_unavailable: "研究方案还在准备中。",
  design_ready: "研究方案已形成，可以查看并确认。",
  failed: "任务准备未完成，请返回研究起点检查输入后重试。",
  external_result_unknown: "结果尚无法确认。可以继续检查当前研究，系统不会另建任务。",
};

export function planningProgressUserCopy(
  code: string | null | undefined,
  fallback: string,
): string {
  const key = code?.trim().toLowerCase() ?? "";
  if (key && PLANNING_PROGRESS_USER_COPY[key]) return PLANNING_PROGRESS_USER_COPY[key];
  return fallback;
}
