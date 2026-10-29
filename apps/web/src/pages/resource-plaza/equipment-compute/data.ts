import jccSnapshot from "./jccSnapshot.json";
import fluxSnapshot from "./fluxSnapshot.json";
import type {
  FluxSnapshot,
  JccInventoryResult,
  JccQuotaRange,
  JccSnapshot,
  QuotaValue,
  ResourceGroup,
} from "./types";

export type {
  ComputeResource,
  DemoRecord,
  FluxSnapshot,
  FluxSubRecipe,
  FluxTemplate,
  FluxTemplateParam,
  JccImageRow,
  JccInventoryMeta,
  JccInventoryResult,
  JccQuotaRange,
  JccSnapshot,
  JccTaskRow,
  QuotaValue,
  ResourceGroup,
} from "./types";

export const groupLabels: Record<ResourceGroup, string> = {
  train: "智算训练",
  hpc: "超算",
  vm: "云主机",
  container: "云容器",
};

/** 本地一次性快照：后端不可达 / 平台不可达时的兜底数据。 */
export const fallbackFluxInventory: FluxSnapshot =
  fluxSnapshot as unknown as FluxSnapshot;
export const fallbackJccInventory: JccInventoryResult = {
  meta: {
    source: "seed",
    observedAt: (jccSnapshot as unknown as JccSnapshot).observedAt,
    collecting: false,
    lastAttemptAt: null,
    lastError: null,
  },
  payload: jccSnapshot as unknown as JccSnapshot,
};

export function formatBeijingTime(
  value: string | null | undefined,
): string {
  return value
    ? new Date(value).toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour12: false,
      })
    : "未取得";
}

/** 兼容历史命名：任务/草稿时间格式化。 */
export const dateTime = formatBeijingTime;

export function jccMetrics(payload: JccSnapshot) {
  return {
    categories: new Set(
      payload.resources.filter((r) => r.group === "train").map((r) => r.type),
    ).size,
    training: payload.resources.filter((r) => r.group === "train").length,
    hpc: payload.resources.filter((r) => r.group === "hpc").length,
    completed: payload.tasks.filter((t) => t.status === "Succeeded").length,
  };
}

export function fluxMetrics(snapshot: FluxSnapshot) {
  return {
    templates: snapshot.templates.length,
    labs: new Set(
      snapshot.templates.map((t) => t.labId).filter(Boolean),
    ).size,
    recipes: snapshot.pvdSubRecipes.length,
  };
}

/** 平台存在纯数字命名的测试模板（如 "11221212"），作能力标签展示没有信息量。 */
export const isNoiseTemplateName = (name: string | null | undefined): boolean =>
  !name?.trim() || /^\d+$/.test(name.trim());

/** 配额字段展示："8-32" 字符串或 {min,max} 对象统一成可读文本。 */
export function quotaRangeText(value: QuotaValue): string {
  if (value == null) return "未取得";
  if (typeof value === "string") return value;
  const { min, max } = value;
  if (min == null && max == null) return "未取得";
  return `${min ?? "?"}–${max ?? "?"}`;
}

export function quotaByType(
  ranges: JccQuotaRange[] | undefined,
  type: string | null | undefined,
): JccQuotaRange | undefined {
  if (!type) return undefined;
  return (ranges ?? []).find((q) => q.type === type);
}
