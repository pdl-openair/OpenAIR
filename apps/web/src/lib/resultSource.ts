import type { ResultSource } from "@openair/shared";

export const RESULT_SOURCE_LABELS: Record<ResultSource, string> = {
  live: "本次生成",
  replay: "历史结果",
  demo: "演示结果",
  none: "待生成",
};

export function normalizeResultSource(source?: ResultSource): ResultSource {
  return source ?? "none";
}

export function resultSourceLabel(source?: ResultSource): string {
  return RESULT_SOURCE_LABELS[normalizeResultSource(source)];
}
