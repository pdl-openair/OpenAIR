import { getSubStageLabels } from "@openair/shared";

const researchWorkLabels = getSubStageLabels("zh-CN");

/**
 * Presentation-boundary guard for ordinary research UI.
 * Internal contracts, enum values and logs stay unchanged; only rendered copy is filtered.
 */
const INTERNAL_ENGINEERING_TERM_PATTERN = /\bcanonical\b|\borchestrat(?:ion|or)\b|\bplanner\b|\bworker\b|\bclaim(?:ed|ing)?\b|\bruntime\b|\bprovider\b|\breceipt\b|\brecovery\b|\blifecycle\b|\brevision\b|\bheartbeat\b|\bsse\b|execution[\s_-]*decision|work[\s_-]*unit|\bcontroller\b|\bcommand\b|\breconcile\b|\bfence\b|run[\s_-]*id|task[\s_-]*id|workspace|dispatch|external[\s_-]*result[\s_-]*unknown|transport[\s_-]*uncertain|dependency[\s_-]*preflight|artifact[\s_-]*scope|idempotenc(?:y|e)|traceback|stack\s*trace|\bexception\b|\bhttp\b|bad\s*gateway|gateway\s*timeout|error[\s_-]*code|endpoint|postgres|database|request[\s_-]*id|correlation[\s_-]*id/i;

const INTERNAL_CHINESE_PATTERN = /任务\s*(?:ID|Id|id|编号|标识)|运行\s*(?:ID|Id|id|编号|标识)|工作区(?:目录|路径|标识)?|运行时|服务端|后端|数据库|内部错误|回执|恢复生命周期|技术详情|调用栈|接口地址|请求标识|追踪标识|幂等(?:键|标识)|进度投影|权威投影|版本不一致|版本号/i;

export function hasInternalEngineeringLanguage(value: unknown): boolean {
  if (typeof value !== "string") return false;
  const text = value.trim();
  if (!text) return false;
  return INTERNAL_ENGINEERING_TERM_PATTERN.test(text) || INTERNAL_CHINESE_PATTERN.test(text);
}

export function userFacingDynamicText(value: unknown, fallback: string): string {
  if (typeof value !== "string") return fallback;
  const text = value.trim();
  if (!text || text.length > 400 || hasInternalEngineeringLanguage(text)) return fallback;
  return text.replace(/\b[a-z][a-z0-9_]+\b/g, (identity) => researchWorkLabels[identity] ?? identity);
}

export function userFacingErrorText(value: unknown, fallback = "当前操作未完成，请重试。"): string {
  if (typeof value !== "string") return fallback;
  const text = value.trim();
  if (!text || text.length > 240 || /[\r\n]/.test(text) || /^(?:\{|\[)/.test(text) || /https?:\/\//i.test(text) || hasInternalEngineeringLanguage(text)) return fallback;
  return text;
}
