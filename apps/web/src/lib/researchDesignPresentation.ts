import {
  STAGE_LABELS,
  type TaskPlan,
  type TaskPlanStage,
  type WorkflowSubStage,
} from "@openair/shared";
import { labelForSubStage } from "../data/recordedRunAdapter";

const TECHNICAL_ID = /^[a-z0-9]+(?:[._][a-z0-9]+)+$/i;
const PLANNED_OUTPUT =
  /^(?:[a-z][a-z0-9_]*\.)+([a-z][a-z0-9_]*)\.planned_output$/i;
const CANONICAL_FINAL_OBJECTIVE_EN =
  "Assemble the completed research into the canonical final result.";
const CANONICAL_FINAL_OBJECTIVE_ZH = "汇总已完成的研究结果，形成最终研究报告";

export function looksTechnicalIdentifier(value: string): boolean {
  return TECHNICAL_ID.test(value.trim());
}

export function researchDraftText(value?: string | null): string {
  const normalized = value?.trim() ?? "";
  if (!normalized) return "";
  if (normalized === CANONICAL_FINAL_OBJECTIVE_EN)
    return CANONICAL_FINAL_OBJECTIVE_ZH;
  if (looksTechnicalIdentifier(normalized)) return "";
  return normalized;
}

export function researchDraftNodeKey(substage: WorkflowSubStage): string {
  return (substage.substage ?? "").trim();
}

export function researchDraftNodeTitle(substage: WorkflowSubStage): string {
  const key = researchDraftNodeKey(substage);
  const label = researchDraftText(substage.label);
  const registry = key ? labelForSubStage(key, "zh-CN") : "";
  // Preserve concise instance/catalog labels before consulting the shared dictionary.
  if (
    label &&
    label !== key &&
    /[\u3400-\u9fff]/.test(label) &&
    label.length <= 24
  ) {
    return label;
  }
  if (registry && registry !== key && !looksTechnicalIdentifier(registry))
    return registry;
  if (
    label &&
    label !== key &&
    (label.length <= 24 || !researchDraftText(substage.action))
  ) {
    return label;
  }
  return "研究工作";
}

export function researchDraftNodeAction(substage: WorkflowSubStage): string {
  const key = researchDraftNodeKey(substage);
  const action = researchDraftText(substage.action);
  if (action) {
    if (
      key === "report_generation" &&
      /canonical final result/i.test(substage.action ?? "")
    ) {
      return CANONICAL_FINAL_OBJECTIVE_ZH;
    }
    return action;
  }
  const label = researchDraftText(substage.label);
  const title = researchDraftNodeTitle(substage);
  if (label && label !== title) return label;
  return "";
}

/** Execution navigation retains the work type alongside its instance objective. */
export function researchWorkProgressTitle(substage: {
  subStage?: string;
  label: string;
  objective?: string | null;
}): string {
  const definition = substage.subStage ?? "";
  const key = definition.split(":").at(-1) ?? "";
  const objective = researchDraftText(substage.objective);
  const scoped = definition ? labelForSubStage(definition, "zh-CN") : "";
  const registry =
    scoped && scoped !== definition
      ? scoped
      : key
        ? labelForSubStage(key, "zh-CN")
        : "";
  const title =
    registry && registry !== key
      ? registry
      : researchDraftNodeTitle({
          substage: key,
          label:
            objective && substage.label.trim() === objective
              ? key
              : substage.label,
          action: objective,
        });
  const action = objective || researchDraftText(substage.label);
  return action && action !== title ? `${title}：${action}` : title;
}

export function researchDraftStageTitle(stage: TaskPlanStage): string {
  return researchDraftText(stage.title) || STAGE_LABELS[stage.stageKey] || "";
}

export function researchDraftExpectedOutput(value?: string | null): string {
  const normalized = value?.trim() ?? "";
  if (!normalized) return "";
  const planned = PLANNED_OUTPUT.exec(normalized);
  if (planned) {
    const key = planned[1];
    const title = labelForSubStage(key, "zh-CN");
    if (title && title !== key && !looksTechnicalIdentifier(title)) {
      return `${title}（计划产物）`;
    }
    return "计划研究产物";
  }
  if (
    looksTechnicalIdentifier(normalized) ||
    /\.planned_output$/i.test(normalized)
  ) {
    return "";
  }
  return researchDraftText(normalized);
}

/** Hide stage descriptions that merely concatenate the same work objectives. */
export function researchDraftStageDescription(
  stage: TaskPlanStage,
  workActions: string[],
): string {
  const description = researchDraftText(stage.description);
  if (!description) return "";
  const normalizedActions = workActions
    .map((item) => item.trim())
    .filter(Boolean);
  if (normalizedActions.length === 0) return description;
  if (normalizedActions.length === 1 && description === normalizedActions[0])
    return "";
  if (
    description.includes(" / ") &&
    normalizedActions.every((action) => description.includes(action))
  ) {
    return "";
  }
  return description;
}

/** Prefer a concise goal-derived title when the stored title is a mechanical truncation. */
export function researchDesignDisplayTitle(
  title: string | null | undefined,
  goalSummary: string | null | undefined,
): string {
  const current = (title ?? "").trim();
  const goal = (goalSummary ?? "").trim();
  const looksTruncated =
    !current ||
    /^研究一下/.test(current) ||
    /^我想研究一下/.test(current) ||
    (current.length <= 10 && goal.length > current.length + 20);
  if (!looksTruncated || !goal) return current;

  const firstClause =
    goal
      .split(/[。！？\n]/)
      .map((part) => part.trim())
      .find(Boolean) ?? goal;
  const cleaned = firstClause
    .replace(/^我想研究一下[，,]?/, "")
    .replace(/^帮我/, "")
    .replace(/^请/, "")
    .trim();
  if (!cleaned) return current;
  return cleaned.length > 36 ? `${cleaned.slice(0, 36)}…` : cleaned;
}

export interface ResearchDesignOverview {
  primaryResearchQuestions: string[];
  auxiliaryResearchDimensions: string[];
  targetOutputs: string[];
  evidenceBoundaries: string[];
  riskNotes: string[];
  researchPath: string[];
}

function researchOverviewList(values?: string[] | null): string[] {
  return Array.from(
    new Set((values ?? []).map((value) => value.trim()).filter(Boolean)),
  );
}

/** Read-only, rebuildable high-value projection of the Formal ResearchDesign. */
export function researchDesignOverview(plan: TaskPlan): ResearchDesignOverview {
  const understanding = plan.understanding;
  const researchPath = (plan.stages ?? []).flatMap((stage) => {
    const work = Array.from(
      new Set(
        (stage.substages ?? [])
          .map((substage) => {
            const key = researchDraftNodeKey(substage);
            const title = researchDraftNodeTitle(substage);
            const mechanicalTitle = key
              .replace(/[._-]+/g, " ")
              .trim()
              .toLowerCase();
            return title &&
              title !== "研究工作" &&
              title.trim().toLowerCase() !== mechanicalTitle
              ? title
              : "";
          })
          .filter(Boolean),
      ),
    );
    if (!work.length) return [];

    const title = researchDraftStageTitle(stage);
    const distinctWork = title ? work.filter((item) => item !== title) : work;

    if (title && !distinctWork.length) return [title];

    const summary = distinctWork.join("；");
    return [title ? `${title}：${summary}` : summary];
  });

  return {
    primaryResearchQuestions: researchOverviewList(
      understanding?.primaryResearchQuestions,
    ),
    auxiliaryResearchDimensions: researchOverviewList(
      understanding?.auxiliaryResearchDimensions,
    ),
    targetOutputs: researchOverviewList(understanding?.targetOutputs),
    evidenceBoundaries: researchOverviewList(understanding?.evidenceBoundaries),
    riskNotes: researchOverviewList(understanding?.riskNotes),
    researchPath: Array.from(new Set(researchPath)),
  };
}
