import { userFacingErrorText } from "./userFacingLanguage";

export const START_SESSION_STORAGE_KEY =
  "openair.research-start.intent-session.v1";

const EXAMPLE_POLLUTION_PATTERNS = [
  /示例中的科研\s*agent/gi,
  /demo/gi,
  /fixture/gi,
  /task_cs_ope_001/gi,
  /OPE\s*方法在训练动态中的可靠性研究/gi
];

export function cleanStarterGoalInput(value: string): string {
  return EXAMPLE_POLLUTION_PATTERNS.reduce(
    (text, pattern) => text.replace(pattern, ""),
    value
  )
    .replace(/\s+/g, " ")
    .replace(/[：:，,；;。.\s]+$/g, "")
    .trim();
}

export function createClientRequestId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `research-start-${crypto.randomUUID()}`;
  }
  return `research-start-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function waitForNextPaint(): Promise<void> {
  return new Promise(resolve => {
    window.requestAnimationFrame(() => resolve());
  });
}

export function userFacingResearchStartError(error: unknown): string {
  const messageText =
    error instanceof Error ? error.message : String(error || "");
  const normalized = messageText.toLowerCase();
  if (
    normalized.includes("failed to fetch") ||
    normalized.includes("networkerror") ||
    normalized.includes("load failed")
  ) {
    return "暂时无法连接启动分析服务，请检查网络或稍后重试。";
  }
  if (
    normalized.includes("503") ||
    normalized.includes("service unavailable")
  ) {
    return "启动分析服务暂时不可用，请稍后重试；当前不会创建空任务。";
  }
  if (
    normalized.includes("fingerprint_conflict")
    || normalized.includes("already reserved for different input")
  ) {
    return "这次启动与上次保留的请求冲突，请再试一次。";
  }
  return userFacingErrorText(messageText, "暂时无法完成启动分析，请稍后重试。");
}

export function rememberStartSession(input: {
  rawInput: string;
  sourcePage: "home" | "board" | "starter_plaza" | "sample";
  presetQuery?: string;
  sessionId: string;
}) {
  localStorage.setItem(
    START_SESSION_STORAGE_KEY,
    JSON.stringify({
      rawInput: input.rawInput,
      sourcePage: input.sourcePage,
      presetQuery: input.presetQuery,
      createdAt: new Date().toISOString(),
      sessionId: input.sessionId
    })
  );
}
