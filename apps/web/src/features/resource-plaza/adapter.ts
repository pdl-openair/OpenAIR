import {
  isExecutionBlocked,
  isKnownResourceAvailability,
  isKnownResourceKind,
  isConsistentResourceAction,
  resolveResourceAvailability,
  resolveResourceKind,
  resolveResourcePrimaryAction,
} from "./state";
import type { ResourceCatalogItemDto, ResourcePlazaResource } from "./types";

function firstText(...values: unknown[]): string | undefined {
  for (const value of values) if (typeof value === "string" && value.trim()) return value.trim();
  return undefined;
}

function textList(value: unknown): string[] {
  if (Array.isArray(value)) return value.filter((item): item is string => typeof item === "string" && Boolean(item.trim())).map((item) => item.trim());
  const text = firstText(value);
  return text ? [text] : [];
}

export function adaptResourceCatalogDto(dto: ResourceCatalogItemDto): ResourcePlazaResource {
  const source = firstText(dto.source);
  const maintainer = firstText(dto.maintainer);
  const releaseVersion = firstText(dto.releaseVersion);
  const usagePolicy = firstText(dto.usagePolicy);
  const licenseSummary = firstText(dto.licenseSummary);
  const evidenceStatus = firstText(dto.evidenceStatus);
  const evidenceConflictDisposition = firstText(dto.evidenceConflictDisposition);
  const kindValue = dto.kind ?? dto.resourceType;
  const safetyValue = dto.safetyStatus ?? dto.safetyRisk;
  const availabilityValue = resolveResourceAvailability(dto.availability);
  const primaryActionValue = resolveResourcePrimaryAction(dto.primaryAction);
  const safetyBlocked = isExecutionBlocked(safetyValue);
  const failClosed =
    safetyBlocked ||
    !firstText(dto.id) ||
    !isKnownResourceKind(kindValue) ||
    !isKnownResourceAvailability(dto.availability) ||
    typeof dto.executionBlocked !== "boolean" ||
    !isConsistentResourceAction(availabilityValue, primaryActionValue, dto.executionBlocked === true);
  const availability = failClosed ? "unavailable" : availabilityValue;
  return {
    id: firstText(dto.id) ?? "",
    name: firstText(dto.name) ?? "",
    kind: resolveResourceKind(kindValue),
    description: firstText(dto.description) ?? "",
    inputs: textList(dto.inputs ?? dto.input),
    outputs: textList(dto.outputs ?? dto.output),
    trustSummary: firstText(dto.trustSummary) ?? "",
    usageConditions: textList(dto.usageConditions ?? dto.requirements).join("；"),
    limitations: textList(dto.limitations).join("；"),
    source,
    maintainer,
    releaseVersion: releaseVersion ?? "",
    visibleOnHome: dto.visibleOnHome === true,
    visibleInCatalog: dto.visibleInCatalog === true,
    visibleInDetail: dto.visibleInDetail === true,
    usagePolicy: usagePolicy ?? "",
    licenseSummary: licenseSummary ?? "",
    evidenceStatus: evidenceStatus ?? "",
    evidenceConflictDisposition: evidenceConflictDisposition ?? "",
    configurationRequirements: textList(dto.configurationRequirements),
    permissionRequirements: textList(dto.permissionRequirements),
    executionEntry: firstText(dto.executionEntry),
    availability,
    executionBlocked: failClosed ? true : dto.executionBlocked === true,
    primaryAction: failClosed ? "none" : primaryActionValue ?? "none",
    safetyRisk: failClosed ? "execution_blocked" : undefined,
    capabilityId: firstText(dto.capabilityId) ?? firstText(dto.id) ?? "",
    durableExecutable: typeof dto.durableExecutable === "boolean" ? dto.durableExecutable : undefined,
    launchMode: firstText(dto.launchMode),
    dependencyGaps: textList(dto.dependencyGaps),
    unavailableReason: firstText(dto.unavailableReason),
    runtimeFamily: firstText(dto.runtimeFamily),
    domains: textList(dto.domains).filter((value): value is ResourcePlazaResource["domains"][number] =>
      ["cs_research", "general", "materials", "ope", "opentrajectory", "sdr"].includes(value),
    ),
    scenarios: textList(dto.scenarios).filter((value): value is ResourcePlazaResource["scenarios"][number] =>
      ["problem_modeling", "solution_design", "execution_validation", "insight_generation"].includes(value),
    ),
  };
}
