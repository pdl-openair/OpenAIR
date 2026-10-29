export const RESOURCE_KINDS = ["skill", "agent", "workflow"] as const;
export type ResourceKind = (typeof RESOURCE_KINDS)[number];

export const RESOURCE_AVAILABILITIES = ["tryable", "configuration_required", "view_only", "unavailable"] as const;
/** Internal availability values for the resource-plaza feature only. */
export type ResourceAvailability = (typeof RESOURCE_AVAILABILITIES)[number];
export type ResourceSafetyRisk = "execution_blocked";
export type ResourcePrimaryAction = "try" | "configure" | "view" | "none";

export const RESOURCE_DOMAINS = ["cs_research", "general", "materials", "ope", "opentrajectory", "sdr"] as const;
export type ResourceDomain = (typeof RESOURCE_DOMAINS)[number];

export const RESOURCE_SCENARIOS = [
  "problem_modeling",
  "solution_design",
  "execution_validation",
  "insight_generation",
] as const;
export type ResourceScenario = (typeof RESOURCE_SCENARIOS)[number];

export interface ResourcePlazaResource {
  id: string;
  name: string;
  kind: ResourceKind;
  description: string;
  inputs: string[];
  outputs: string[];
  trustSummary: string;
  usageConditions: string;
  limitations: string;
  source?: string;
  maintainer?: string;
  releaseVersion: string;
  visibleOnHome: boolean;
  visibleInCatalog: boolean;
  visibleInDetail: boolean;
  usagePolicy: string;
  licenseSummary: string;
  evidenceStatus: string;
  evidenceConflictDisposition: string;
  configurationRequirements: string[];
  permissionRequirements: string[];
  executionEntry?: string;
  availability: ResourceAvailability;
  executionBlocked: boolean;
  primaryAction: ResourcePrimaryAction;
  safetyRisk?: ResourceSafetyRisk;
  capabilityId: string;
  durableExecutable?: boolean;
  launchMode?: string;
  dependencyGaps: string[];
  unavailableReason?: string;
  runtimeFamily?: string;
  domains: ResourceDomain[];
  scenarios: ResourceScenario[];
}

export interface ResourceAvailabilityPresentation {
  label: "可试用" | "需要配置" | "仅可查看" | "暂不可用";
  primaryActionLabel: string;
  color: "success" | "warning" | "processing" | "default";
  executionDisabled: boolean;
}

export interface ResourceCatalogItemDto {
  id?: unknown;
  name?: unknown;
  kind?: unknown;
  description?: unknown;
  inputs?: unknown;
  outputs?: unknown;
  trustSummary?: unknown;
  usageConditions?: unknown;
  limitations?: unknown;
  source?: unknown;
  maintainer?: unknown;
  releaseVersion?: unknown;
  visibleOnHome?: unknown;
  visibleInCatalog?: unknown;
  visibleInDetail?: unknown;
  usagePolicy?: unknown;
  licenseSummary?: unknown;
  evidenceStatus?: unknown;
  evidenceConflictDisposition?: unknown;
  configurationRequirements?: unknown;
  permissionRequirements?: unknown;
  executionEntry?: unknown;
  availability?: unknown;
  safetyStatus?: unknown;
  resourceType?: unknown;
  input?: unknown;
  output?: unknown;
  requirements?: unknown;
  executionBlocked?: unknown;
  safetyRisk?: unknown;
  primaryAction?: unknown;
  keywords?: unknown;
  capabilityId?: unknown;
  durableExecutable?: unknown;
  launchMode?: unknown;
  dependencyGaps?: unknown;
  unavailableReason?: unknown;
  runtimeFamily?: unknown;
  domains?: unknown;
  scenarios?: unknown;
}

export interface ResourceCatalogListDto {
  resources?: unknown;
  total?: unknown;
  page?: unknown;
  pageSize?: unknown;
}

export interface ResourceCatalogPage {
  resources: ResourcePlazaResource[];
  total: number;
  page: number;
  pageSize: number;
}

export interface ResourceSaveMutationResult {
  resourceId: string;
  saved: boolean;
}

export interface ResourceSaveStatusResult {
  savedResourceIds: string[];
}

export interface ResourceSavesPage {
  items: ResourcePlazaResource[];
  page: number;
  pageSize: number;
  total: number;
}

export interface ResearchStartIntentResult {
  intentId: string;
  researchStartUrl: string;
}

export interface ResearchStartIntentPromoteResult {
  sessionId: string;
  selectedResourceId: string;
  selectedResourceName: string;
  selectedResourceKind: ResourceKind;
}
