export function userFacingTaskTitle(title: string | null | undefined): string {
  const value = (title ?? "").trim();
  if (!value) return "";
  return value
    .replace(/研究方向调研\s*Workflow/g, "调研一个研究方向")
    .replace(/来源整理与证据准备\s*Workflow/g, "整理来源与证据")
    .replace(/工作计划\s*Agent/g, "研究计划智能体")
    .replace(/\s*Workflow\s*:/g, "：")
    .replace(/\s*Agent\s*:/g, "：")
    .replace(/\bWorkflow\b/g, "")
    .replace(/\bAgent\b/g, "智能体")
    .replace(/\s+/g, " ")
    .trim();
}
