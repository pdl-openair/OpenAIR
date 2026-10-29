/** 设备资源 / 算力资源平台清单视图类型（与后端 platform_inventory 模型对齐）。 */

export type ResourceGroup = "train" | "hpc" | "vm" | "container";

export interface ComputeResource {
  id: string;
  specId: string;
  group: string;
  name: string;
  type: string;
  clusterId: string;
  region: string;
  availableCount: number | null;
  totalCount: number | null;
  details: { name: string; totalValue: number; totalUnit: string }[];
}

/** 平台配额字段有 "8-32" 字符串与 {min,max} 对象两种形态。 */
export type QuotaValue = string | { min?: number; max?: number } | null;

export interface JccQuotaRange {
  type: string | null;
  cpu: QuotaValue;
  gpuNumber: QuotaValue;
  memory: QuotaValue;
  storage: QuotaValue;
}

export interface JccTaskRow {
  id: string | number | null;
  name: string | null;
  status: string | null;
  createdTime: string | null;
  startTime: string | null;
  endTime: string | null;
}

export interface JccImageRow {
  id: string | number | null;
  name: string | null;
  arch: string | null;
  computeType: string | null;
  workloadKind: string | null;
  availabilityStatus: string | null;
}

export interface JccSnapshot {
  observedAt: string | null;
  resources: ComputeResource[];
  trainQuota: JccQuotaRange[];
  inferQuota: JccQuotaRange[];
  tasks: JccTaskRow[];
  images: JccImageRow[];
  imageTotal: number | null;
  partialErrors?: string[];
}

export interface JccInventoryMeta {
  source: "seed" | "cache" | (string & {});
  observedAt: string | null;
  collecting: boolean;
  lastAttemptAt: string | null;
  lastError: string | null;
}

export interface JccInventoryResult {
  meta: JccInventoryMeta;
  payload: JccSnapshot | null;
}

export interface FluxTemplate {
  id: string;
  templateName: string | null;
  taskType: string | number | null;
  labId: string | null;
  labName: string | null;
  experimentDesc: string | null;
  estimateDays: number | null;
  priority: string | null;
}

export interface FluxTemplateParam {
  paramName: string | null;
  displayName: string | null;
  dataType: string | null;
  isRequired: string | null;
  paramOrder: number | null;
  valueSource: string | null;
  jsonTemplate: string | null;
  defaultValue: string | null;
  children: FluxTemplateParam[];
}

export interface FluxSubRecipe {
  id: number | string | null;
  subRecipeTypeId: number | string | null;
  subRecipeId: string | number | null;
  subRecipeName: string | null;
  groupName: string | null;
  enabled: string | null;
  sortOrder: number | null;
  remark: string | null;
}

export interface FluxSnapshot {
  observedAt: string;
  templates: FluxTemplate[];
  templateParams: Record<string, FluxTemplateParam[]>;
  pvdSubRecipes: FluxSubRecipe[];
  partialErrors: string[];
}

export interface DemoRecord {
  id: string;
  resource: string;
  project: string;
  requirement: string;
  time: string;
  createdAt: string;
}
