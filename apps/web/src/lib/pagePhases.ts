import { getStageLabels, type UiLanguage } from "@openair/shared";

/** Stable page phase IDs used for color/route compatibility; labels/order come from the task workflow. */
export type PagePhaseId = "problem" | "method" | "experiment" | "report";

export const PAGE_PHASE_ORDER: PagePhaseId[] = ["problem", "method", "experiment", "report"];

const ZH_PAGE_PHASE_LABEL: Record<PagePhaseId, string> = {
  problem: "问题",
  method: "方法",
  experiment: "实验",
  report: "报告",
};

const EN_PAGE_PHASE_LABEL: Record<PagePhaseId, string> = {
  problem: "Problem",
  method: "Method",
  experiment: "Experiment",
  report: "Report",
};

export function getPagePhaseLabels(language: UiLanguage): Record<PagePhaseId, string> {
  return language === "en-US" ? EN_PAGE_PHASE_LABEL : ZH_PAGE_PHASE_LABEL;
}

/** 默认中文标签常量（供无需语言切换的场景直接索引） */
export const PAGE_PHASE_LABEL: Record<PagePhaseId, string> = ZH_PAGE_PHASE_LABEL;

export function pagePhaseLabel(phase: PagePhaseId, language: UiLanguage): string {
  return getPagePhaseLabels(language)[phase];
}

export const UNDERLYING_TO_PAGE = {
  problem_modeling: "problem",
  solution_design: "method",
  execution_validation: "experiment",
  insight_generation: "report",
} as const;

export const UNDERLYING_STAGE_ORDER = [
  "problem_modeling",
  "solution_design",
  "execution_validation",
  "insight_generation",
] as const;

export function stageLabelForPagePhase(phase: PagePhaseId, language: UiLanguage): string {
  const stageLabels = getStageLabels(language);
  const stageKey = underlyingStageForPagePhase(phase);
  return stageLabels[stageKey];
}

export function pagePhaseForUnderlying(stage: keyof typeof UNDERLYING_TO_PAGE): PagePhaseId {
  return UNDERLYING_TO_PAGE[stage];
}

export function underlyingStageForPagePhase(phase: PagePhaseId): keyof typeof UNDERLYING_TO_PAGE {
  return UNDERLYING_STAGE_ORDER.find((s) => UNDERLYING_TO_PAGE[s] === phase) ?? "problem_modeling";
}

export function underlyingStagesInPagePhase(phase: PagePhaseId): (keyof typeof UNDERLYING_TO_PAGE)[] {
  return [underlyingStageForPagePhase(phase)];
}

export type PhaseNavStatus = "completed" | "running" | "pending" | "blocked" | "failed" | "not_started";

export function aggregatePhaseStatus(statuses: PhaseNavStatus[]): PhaseNavStatus {
  if (statuses.some((s) => s === "failed")) return "failed";
  if (statuses.some((s) => s === "running")) return "running";
  if (statuses.some((s) => s === "blocked")) return "blocked";
  if (statuses.some((s) => s === "pending")) return "pending";
  if (statuses.length > 0 && statuses.every((s) => s === "completed")) return "completed";
  return "not_started";
}

export function mapStageStatusToNav(s: string): PhaseNavStatus {
  switch (s) {
    case "completed":
    case "skipped":
      return "completed";
    case "running":
    case "queued":
    case "reviewing":
      return "running";
    case "blocked":
    case "blocked_recoverable":
      // System-internal recoverable block — never user confirmation.
      return "blocked";
    case "needs_input":
    case "insufficient_materials":
    case "insufficient_evidence":
    case "external_result_unknown":
      return "pending";
    case "failed":
    case "cancelled":
    case "stopped":
    case "projection_unavailable":
      return "failed";
    case "not_started":
    case "planned":
      return "not_started";
    default:
      return "not_started";
  }
}

export function mapFixtureStageStatus(s: string): PhaseNavStatus {
  return mapStageStatusToNav(s);
}
