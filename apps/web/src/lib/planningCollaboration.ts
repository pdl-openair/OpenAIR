import type { TaskPlan } from "@openair/shared";
import type { ResearchStartSessionStatus } from "../api/client";
import { planningProgressUserCopy } from "./planningProgressUserCopy";
import {
  researchDesignOverview,
  researchDraftText,
} from "./researchDesignPresentation";

export type PlanningStructureState = "pending" | "formed";

export interface PlanningStructureItem {
  key: "question" | "work" | "completion";
  label: string;
  state: PlanningStructureState;
}

export interface PlanningDraftSection {
  key: "question" | "work" | "completion";
  title: string;
  values: string[];
}

export interface PlanningWorkbenchProjection {
  structure: PlanningStructureItem[];
  draftSections: PlanningDraftSection[];
  draftReady: boolean;
}

const INTERNAL_PLANNING_LANGUAGE =
  /(?:\bplanner\b|\bcompiler\b|\bschema\b|\bworkflow\b|\bworker\b|substageinstance|tool\s*call|\bprompt\b|规划器|编译器|工作流路由|工具调用|提示词)/i;

function distinctPublicText(
  values: Array<string | null | undefined>,
  limit = 6,
): string[] {
  return Array.from(
    new Set(
      values
        .map((value) => researchDraftText(value))
        .filter(
          (value) => Boolean(value) && !INTERNAL_PLANNING_LANGUAGE.test(value),
        ),
    ),
  ).slice(0, limit);
}

/**
 * Single public progress sentence for the workbench collaboration panel.
 * Prefer a clean formal message; when it is empty or internal, use the
 * research-user copy table keyed by progress/recovery code. Never invent
 * filler stages from absence alone.
 */
export function publicPlanningProgressMessage(
  snapshot: ResearchStartSessionStatus | null,
): string | null {
  if (!snapshot) return null;
  const code = (
    snapshot.planningProgress?.code ??
    snapshot.recoveryReason ??
    snapshot.reasonCode ??
    ""
  )
    .trim()
    .toLowerCase();
  if (snapshot.status === "completed" || code === "design_ready") return null;

  const message = snapshot.planningProgress?.message?.trim() ?? "";
  if (
    message &&
    /[\u4e00-\u9fff]/.test(message) &&
    !INTERNAL_PLANNING_LANGUAGE.test(message)
  ) {
    return message;
  }

  const fromCode = planningProgressUserCopy(code, "");
  return fromCode || null;
}

export function formatPlanningElapsed(
  seconds: number | null | undefined,
): string | null {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return null;
  const total = Math.floor(seconds);
  if (total < 60) return `${total}秒`;
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  if (minutes < 60)
    return rest > 0 ? `${minutes}分 ${rest}秒` : `${minutes}分钟`;
  const hours = Math.floor(minutes / 60);
  const remainMinutes = minutes % 60;
  return remainMinutes > 0 ? `${hours}小时 ${remainMinutes}分` : `${hours}小时`;
}

/**
 * Visual interpolation from the latest server elapsedSeconds snapshot.
 * Does not use updatedAt or any second timing authority.
 */
export function interpolatePlanningElapsedSeconds(options: {
  elapsedSeconds: number | null | undefined;
  snapshotReceivedAtMs: number;
  nowMs: number;
  frozen: boolean;
}): number | null {
  const { elapsedSeconds, snapshotReceivedAtMs, nowMs, frozen } = options;
  if (
    elapsedSeconds == null ||
    !Number.isFinite(elapsedSeconds) ||
    elapsedSeconds < 0
  ) {
    return null;
  }
  const base = Math.floor(elapsedSeconds);
  if (frozen) return base;
  if (!Number.isFinite(snapshotReceivedAtMs) || !Number.isFinite(nowMs))
    return base;
  const delta = Math.max(0, Math.floor((nowMs - snapshotReceivedAtMs) / 1_000));
  return base + delta;
}

export function projectPlanningWorkbench(
  snapshot: ResearchStartSessionStatus | null,
  plan: TaskPlan | null = null,
): PlanningWorkbenchProjection {
  const keyResults = snapshot?.planningProgress?.keyResults;
  const overview = plan ? researchDesignOverview(plan) : null;
  const questions = distinctPublicText(
    plan
      ? (overview?.primaryResearchQuestions ?? [])
      : (keyResults?.researchQuestions ?? []),
  );
  const work = distinctPublicText(
    plan ? (overview?.researchPath ?? []) : (keyResults?.objectives ?? []),
  );
  const completion = distinctPublicText(
    plan ? (overview?.targetOutputs ?? []) : (keyResults?.targetOutputs ?? []),
  );
  const draftReady = Boolean(plan?.readyToConfirm);
  const valuesByKey = { question: questions, work, completion };
  const keys: Array<PlanningStructureItem["key"]> = [
    "question",
    "work",
    "completion",
  ];
  const labels: Record<PlanningStructureItem["key"], string> = {
    question: "研究问题",
    work: "研究工作",
    completion: "完成标准",
  };
  const structure = keys.map((key): PlanningStructureItem => ({
    key,
    label: labels[key],
    state: draftReady || valuesByKey[key].length > 0 ? "formed" : "pending",
  }));
  const draftSections: PlanningDraftSection[] = [
    { key: "question", title: "研究目标", values: questions },
    { key: "work", title: "研究工作", values: work },
    { key: "completion", title: "完成要求", values: completion },
  ].filter((section) => section.values.length > 0) as PlanningDraftSection[];
  return {
    structure,
    draftSections,
    draftReady,
  };
}
