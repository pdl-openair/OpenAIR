import { planningProgressUserCopy } from "./planningProgressUserCopy";
import { userFacingDynamicText } from "./userFacingLanguage";
import type { ResearchStartSessionStatus } from "../api/client";

const BASE_RETRY_MS = 1_500;
const MAX_RETRY_MS = 20_000;

export type ResearchStartNoticeLevel = "progress" | "info" | "warning" | "error" | "success";

export interface ResearchStartWaitingView {
  title: string;
  message: string;
  terminal: boolean;
  unknown: boolean;
  shouldOpenWorkbench: boolean;
  shouldReturnHome: boolean;
  level: ResearchStartNoticeLevel;
  elapsedSeconds: number | null;
  progressCode: string | null;
  nextActionHint: string | null;
}

function phaseMessage(phase: ResearchStartSessionStatus["currentPhase"]): string {
  if (phase === "understanding") return "正在理解研究请求。";
  if (phase === "organizing") return "正在整理研究信息。";
  if (phase === "planning") return "正在整理研究方案。";
  return "正在确认当前研究状态。";
}

function safeUserMessage(value: string | null | undefined): string | null {
  const message = value?.trim();
  if (!message || !/[\u4e00-\u9fff]/.test(message)) return null;
  const safe = userFacingDynamicText(message, "");
  return safe || null;
}

function progressCodeOf(snapshot: ResearchStartSessionStatus): string | null {
  const code = snapshot.planningProgress?.code
    ?? snapshot.recoveryReason
    ?? snapshot.reasonCode
    ?? null;
  return code ? code.toLowerCase() : null;
}

function progressCopy(code: string | null, fallback: string): string {
  return planningProgressUserCopy(code, fallback);
}

export function formatElapsedSeconds(seconds: number | null | undefined): string | null {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return null;
  const total = Math.floor(seconds);
  if (total < 60) return `已等待 ${total} 秒`;
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  if (minutes < 60) {
    return rest > 0 ? `已等待 ${minutes} 分 ${rest} 秒` : `已等待 ${minutes} 分钟`;
  }
  const hours = Math.floor(minutes / 60);
  const remainMinutes = minutes % 60;
  return remainMinutes > 0 ? `已等待 ${hours} 小时 ${remainMinutes} 分钟` : `已等待 ${hours} 小时`;
}

export function researchStartRecheckDelay(
  snapshot: ResearchStartSessionStatus | null,
  failures = 0,
  retryAfterMs?: number,
): number {
  const code = snapshot ? progressCodeOf(snapshot) : null;
  if (snapshot?.status === "external_result_unknown" || code === "external_result_unknown") {
    return MAX_RETRY_MS;
  }
  if (
    snapshot?.status === "queued"
    || snapshot?.status === "worker_unavailable"
    || code === "still_queued"
    || code === "queued_stale"
    || code === "worker_unavailable"
  ) {
    return Math.max(5_000, retryAfterMs ?? 0);
  }
  const exponential = Math.min(MAX_RETRY_MS, BASE_RETRY_MS * 2 ** Math.min(failures, 4));
  return Math.max(exponential, retryAfterMs ?? 0);
}

export interface ResearchStartWaitingSurfaceOptions {
  /** When true, copy stays inside the workbench shell (no “return to home” default). */
  inWorkbench?: boolean;
}

/** Project only authoritative session fields; browser storage never creates a task state. */
export function projectResearchStartWaitingView(
  snapshot: ResearchStartSessionStatus | null,
  readUnavailable = false,
  surface: ResearchStartWaitingSurfaceOptions = {},
): ResearchStartWaitingView {
  const inWorkbench = surface.inWorkbench === true;
  if (!snapshot) {
    return {
      title: "任务准备状态",
      message: readUnavailable ? "暂时无法读取最新状态，系统会按当前会话继续确认。" : "正在确认当前研究状态。",
      terminal: false,
      unknown: false,
      shouldOpenWorkbench: false,
      shouldReturnHome: false,
      level: readUnavailable ? "warning" : "progress",
      elapsedSeconds: null,
      progressCode: null,
      nextActionHint: readUnavailable ? "请保持当前页面，或稍后在系统提示中查看结果。" : "准备完成后会在工作台内显示研究方案。",
    };
  }

  const userMessage = safeUserMessage(snapshot.userMessage);
  const progressMessage = safeUserMessage(snapshot.planningProgress?.message);
  const reasonCode = snapshot.reasonCode?.toLowerCase() ?? null;
  const queueState = snapshot.queueState?.toLowerCase();
  const progressCode = progressCodeOf(snapshot);
  const elapsedSeconds = typeof snapshot.planningProgress?.elapsedSeconds === "number"
    ? snapshot.planningProgress.elapsedSeconds
    : null;
  const resolvedMessage = userMessage ?? progressMessage;

  if (snapshot.status === "queued" || queueState === "queued" || progressCode === "still_queued") {
    return {
      title: "研究已进入队列",
      message: resolvedMessage ?? progressCopy(progressCode, "研究方案还在准备中。"),
      terminal: false,
      unknown: false,
      shouldOpenWorkbench: false,
      shouldReturnHome: false,
      level: "progress",
      elapsedSeconds,
      progressCode,
      nextActionHint: "页面会自动确认进度，无需重复提交。",
    };
  }
  if (
    snapshot.status === "worker_unavailable"
    || progressCode === "worker_unavailable"
    || progressCode === "queued_stale"
    || reasonCode === "worker_unavailable"
  ) {
    const stale = progressCode === "queued_stale";
    return {
      title: stale ? "准备时间较长" : "研究仍在等待",
      message: resolvedMessage ?? progressCopy(
        progressCode,
        stale
          ? "准备时间比通常更长，你可以先离开，稍后再回来查看。"
          : "研究方案还在准备中。",
      ),
      terminal: false,
      unknown: false,
      shouldOpenWorkbench: false,
      shouldReturnHome: false,
      level: "warning",
      elapsedSeconds,
      progressCode,
      nextActionHint: "你可以继续等待，或返回修改问题。系统不会因此创建第二个任务。",
    };
  }
  if (snapshot.status === "running") {
    return {
      title: "正在准备研究方案",
      message: resolvedMessage ?? progressCopy(progressCode, phaseMessage(snapshot.currentPhase)),
      terminal: false,
      unknown: false,
      shouldOpenWorkbench: false,
      shouldReturnHome: false,
      level: "progress",
      elapsedSeconds,
      progressCode,
      nextActionHint: "准备完成后会在工作台内显示研究方案。",
    };
  }
  if (snapshot.status === "completed") {
    const taskId = snapshot.result?.taskId?.trim();
    if (taskId) {
      return {
        title: "任务准备状态",
        message: "正在打开工作台。",
        terminal: false,
        unknown: false,
        shouldOpenWorkbench: true,
        shouldReturnHome: false,
        level: "success",
        elapsedSeconds,
        progressCode,
        nextActionHint: null,
      };
    }
    return {
      title: "研究起点已完成",
      message: resolvedMessage ?? "已完成判断。请返回研究起点查看建议或补充后继续。",
      terminal: true,
      unknown: false,
      shouldOpenWorkbench: false,
      shouldReturnHome: true,
      level: "success",
      elapsedSeconds,
      progressCode,
      nextActionHint: "可以返回研究起点查看建议或补充后继续。",
    };
  }
  if (snapshot.status === "external_result_unknown") {
    return {
      title: inWorkbench ? "正在再核对这项研究" : "结果暂无法确认",
      message:
        resolvedMessage
        ?? progressCopy(
          "external_result_unknown",
          inWorkbench
            ? "这次方案生成结果尚未核对清楚。可以先再检查当前状态，系统不会另建任务。"
            : "结果尚无法确认。可以继续检查当前研究，系统不会另建任务。",
        ),
      terminal: inWorkbench ? false : true,
      unknown: true,
      shouldOpenWorkbench: false,
      shouldReturnHome: false,
      level: "warning",
      elapsedSeconds,
      progressCode: progressCode ?? "external_result_unknown",
      nextActionHint: inWorkbench
        ? "主操作是再检查，不是重试。"
        : "可以继续检查当前研究。系统不会另建任务。",
    };
  }
  return {
    title: inWorkbench ? "研究方案这次没有形成" : "任务准备未完成",
    message:
      resolvedMessage
      ?? progressCopy(
        "failed",
        inWorkbench
          ? "研究方案这次没有形成。可以点击底栏「重试」，按原来的选择重新生成方案。"
          : "任务准备未完成，请返回研究起点检查输入后重试。",
      ),
    terminal: true,
    unknown: false,
    shouldOpenWorkbench: false,
    shouldReturnHome: !inWorkbench,
    level: "error",
    elapsedSeconds,
    progressCode: progressCode ?? "failed",
    nextActionHint: inWorkbench
      ? "可以点击底栏「重试」重新生成方案。"
      : "请返回修改问题后重试。",
  };
}
