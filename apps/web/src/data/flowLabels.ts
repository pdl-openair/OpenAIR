/**
 * 流程/领域展示名：由数据字段映射，避免在组件里散落 if/else。
 * 新增领域时在映射表补充即可。
 */
import type { UiLanguage } from "@openair/shared";

const DOMAIN_FLOW_ZH: Record<string, string> = {
  cs_research: "计算科研任务",
  research_task: "通用科研任务",
  materials: "材料科研任务",
};

const DOMAIN_FLOW_EN: Record<string, string> = {
  cs_research: "Computational research task",
  research_task: "General research task",
  materials: "Materials research task",
};

// Pack-keyed labels — mirror schemas/packs.yaml. Keep short for the 168px board
// column. Used when the task carries an explicit skillPack (selected at create
// time); domain is the legacy fallback.
const PACK_FLOW_ZH: Record<string, string> = {
  sdr: "通用科研任务",
  materials: "材料科研任务",
  ope: "策略评估研究任务",
  opentrajectory: "证据追踪研究任务",
};

const PACK_FLOW_EN: Record<string, string> = {
  sdr: "General research task",
  materials: "Materials research task",
  ope: "Policy evaluation research task",
  opentrajectory: "Evidence tracing research task",
};

export function flowLabelForDomain(domain: string | undefined, language: UiLanguage = "zh-CN"): string {
  const dict = language === "en-US" ? DOMAIN_FLOW_EN : DOMAIN_FLOW_ZH;
  if (!domain) return language === "en-US" ? "Research task" : "科研任务";
  return dict[domain] ?? (language === "en-US" ? "Research task" : "科研任务");
}

/**
 * Prefer skillPack (user-selected at task creation) over domain. Falls back to
 * domain for legacy tasks without skillPack. Unknown packs fall back to a
 * generic user-facing label rather than exposing internal ids.
 */
export function flowLabelForTask(
  skillPack: string | undefined,
  domain: string | undefined,
  language: UiLanguage = "zh-CN",
): string {
  if (skillPack) {
    const dict = language === "en-US" ? PACK_FLOW_EN : PACK_FLOW_ZH;
    if (dict[skillPack]) return dict[skillPack];
    return language === "en-US" ? "Research task" : "科研任务";
  }
  return flowLabelForDomain(domain, language);
}
