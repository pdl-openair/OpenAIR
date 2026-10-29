/** 列表「最近更新」相对时间（对齐看板设计图） */
import type { UiLanguage } from "@openair/shared";
import { formatBeijingDate } from "./beijingTime";

export function formatRelativeTime(
  iso: string,
  languageOrNow: UiLanguage | number = "zh-CN",
  nowArg?: number,
): string {
  if (!iso) return "—";
  const language = typeof languageOrNow === "number" ? "zh-CN" : languageOrNow;
  const now = typeof languageOrNow === "number" ? languageOrNow : nowArg ?? Date.now();
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return formatBeijingDate(iso);
  const diffMs = Math.max(0, now - t);
  const diffMin = Math.floor(diffMs / 60_000);
  if (diffMin < 1) return language === "en-US" ? "Just now" : "刚刚";
  if (diffMin < 60) return language === "en-US" ? `${diffMin} min ago` : `${diffMin} 分钟前`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return language === "en-US" ? `${diffH} hr ago` : `${diffH} 小时前`;
  const diffD = Math.floor(diffH / 24);
  if (diffD < 7) return language === "en-US" ? `${diffD} day${diffD > 1 ? "s" : ""} ago` : `${diffD} 天前`;
  return formatBeijingDate(iso);
}
