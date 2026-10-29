/** 北京时间（UTC+8）格式化工具
 *
 * 后端返回的 ISO 字符串均为 UTC，直接 slice 会显示 UTC 时间。
 * 此处统一通过 Asia/Shanghai 时区转换，避免依赖运行环境的本地时区。
 */

const BEIJING_TZ = "Asia/Shanghai";

function buildParts(iso: string, withTime: boolean, withSeconds: boolean): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  const options: Intl.DateTimeFormatOptions = {
    timeZone: BEIJING_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  };
  if (withTime) {
    options.hour = "2-digit";
    options.minute = "2-digit";
    if (withSeconds) options.second = "2-digit";
    options.hour12 = false;
  }
  const parts = new Intl.DateTimeFormat("en-CA", options).formatToParts(date);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  const ymd = `${get("year")}-${get("month")}-${get("day")}`;
  if (!withTime) return ymd;
  const hms = `${get("hour")}:${get("minute")}${withSeconds ? `:${get("second")}` : ""}`;
  return `${ymd} ${hms}`;
}

/** 格式化为北京时间 `YYYY-MM-DD HH:mm`，ISO 无效或为空时回退到 ISO 前 10 位或 `—` */
export function formatBeijingDateTime(iso: string, withSeconds = false): string {
  return buildParts(iso, true, withSeconds) ?? (iso ? iso.slice(0, 10) : "—");
}

/** 格式化为北京时间 `YYYY-MM-DD`，ISO 无效或为空时回退到 ISO 前 10 位或 `—` */
export function formatBeijingDate(iso: string): string {
  return buildParts(iso, false, false) ?? (iso ? iso.slice(0, 10) : "—");
}
