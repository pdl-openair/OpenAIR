import type { TaskStage } from "./task";

export const TASK_STAGE_ORDER = [
  "problem_modeling",
  "solution_design",
  "execution_validation",
  "insight_generation",
] as const satisfies readonly TaskStage[];

export const TASK_STAGE_DISPLAY_NAMES_ZH: Record<TaskStage, string> = {
  problem_modeling: "问题建模",
  solution_design: "方案设计",
  execution_validation: "执行验证",
  insight_generation: "结果总结",
};

export function stageDisplayName(stage: TaskStage): string {
  return TASK_STAGE_DISPLAY_NAMES_ZH[stage];
}
