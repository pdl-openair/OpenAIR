import literatureMd from "@fixtures/recorded_runs/cs_research_ope_001/literature.md?raw";
import proposalMd from "@fixtures/recorded_runs/cs_research_ope_001/proposal-v3.md?raw";
import experimentDesignMd from "@fixtures/recorded_runs/cs_research_ope_001/experiment_design-v2.md?raw";
import taskPlanMd from "@fixtures/recorded_runs/cs_research_ope_001/task_plan.md?raw";
import summaryMd from "@fixtures/recorded_runs/cs_research_ope_001/summary.md?raw";

/** fixture `artifacts.json` 中 path 字段 → 构建时载入的正文（不修改 fixture） */
export const CS_RESEARCH_OPE_CONTENT_BY_PATH: Record<string, string> = {
  "literature.md": literatureMd,
  "proposal-v3.md": proposalMd,
  "experiment_design-v2.md": experimentDesignMd,
  "task_plan.md": taskPlanMd,
  "summary.md": summaryMd,
};

export function fixtureContentForPath(path: string | undefined): string | null {
  if (!path) return null;
  return CS_RESEARCH_OPE_CONTENT_BY_PATH[path] ?? null;
}
