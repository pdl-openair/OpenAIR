import {
  RESOURCE_DOMAIN_LABELS,
  RESOURCE_SCENARIO_LABELS,
  humanizeResourceContractField,
} from "./state";
import type { ResourcePlazaResource } from "./types";

const DESCRIPTION_SUMMARY_LIMIT = 168;
const PRIMARY_FACT_LIMIT = 3;
const PRIMARY_FACT_LENGTH_LIMIT = 96;
const OUTPUT_FALLBACK = "当前未声明标准化输出，开始前建议确认交付形式。";
const INPUT_FALLBACK = "当前未声明需要提供的材料或信息。";
const LIMITATION_FALLBACK = "当前未声明额外限制。";
const OBSOLETE_RUNTIME_ENTRY_FACT = "尚未声明可独立验证的用户运行入口";

export interface ResourceDetailFactGroup {
  label: string;
  values: string[];
}

export interface ResourceDetailProgressiveSection {
  key: "description" | "scope-and-delivery" | "limitations";
  title: string;
  summary: string;
  body?: string;
  groups: ResourceDetailFactGroup[];
}

export interface ResourceDetailProjection {
  valueSummary: string;
  suitability: {
    scenarios: string[];
    domains: string[];
    hasMore: boolean;
  };
  inputs: {
    values: string[];
    hasMore: boolean;
  };
  outputs: {
    values: string[];
    hasMore: boolean;
    usesFallback: boolean;
  };
  criticalLimitation: string;
  canStart: boolean;
  startGuidance: string;
  progressiveSections: ResourceDetailProgressiveSection[];
}

interface ProjectedFacts {
  primary: string[];
  full: string[];
  hasMore: boolean;
}

function cleanText(value: string | undefined): string {
  return value?.trim().replace(/\r\n?/g, "\n").replace(/[\t ]+/g, " ").replace(/\n{3,}/g, "\n\n") ?? "";
}

function toSingleLine(value: string): string {
  return cleanText(value).replace(/\s*\n\s*/g, " ").replace(/\s{2,}/g, " ").trim();
}

function isResearchFacingFact(value: string): boolean {
  return !(
    /[A-Z]{3,}(?:_[A-Z0-9]+){1,}/u.test(value)
    || /[A-Za-z]{3,}/u.test(value)
    || /\b(?:api\s*key|token|endpoint|runtime|payload|contract|schema)\b/iu.test(value)
  );
}

export function normalizeResourceDetailText(value: string): string {
  return toSingleLine(value)
    .normalize("NFKC")
    .toLocaleLowerCase()
    .replace(/[\s\p{P}\p{S}]+/gu, "");
}

export function dedupeResourceDetailTexts(values: Array<string | undefined>): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const value of values) {
    const display = toSingleLine(value ?? "");
    const key = normalizeResourceDetailText(display);
    if (!display || !key || seen.has(key)) continue;
    seen.add(key);
    result.push(display);
  }
  return result;
}

function splitFacts(value: string | undefined): string[] {
  const normalized = cleanText(value);
  if (!normalized) return [];
  return normalized
    .split(/(?:\n+|；)+/g)
    .map(item => item.trim())
    .filter(Boolean);
}

function clampAtWordBoundary(value: string, maxLength: number): string {
  if (value.length <= maxLength) return value;
  const candidate = value.slice(0, maxLength + 1);
  const whitespaceBoundary = Math.max(candidate.lastIndexOf(" "), candidate.lastIndexOf("\n"));
  const punctuationBoundary = Math.max(
    candidate.lastIndexOf("，"),
    candidate.lastIndexOf(","),
    candidate.lastIndexOf("；"),
    candidate.lastIndexOf(";"),
    candidate.lastIndexOf("、"),
  );
  const boundary = Math.max(whitespaceBoundary, punctuationBoundary);
  const clipped = candidate.slice(0, boundary >= Math.floor(maxLength * 0.6) ? boundary : maxLength).trim();
  return `${clipped.replace(/[，,；;、:：]+$/u, "")}…`;
}

function firstMeaningfulSentence(value: string): string {
  const paragraph = cleanText(value)
    .split(/\n\s*\n/g)
    .map(item => toSingleLine(item))
    .find(item => normalizeResourceDetailText(item).length >= 6) ?? "";
  if (!paragraph) return "";

  const sentenceEndPattern = /[。！？!?]|\.(?=\s|$)/g;
  let match: RegExpExecArray | null;
  while ((match = sentenceEndPattern.exec(paragraph)) !== null) {
    const end = match.index + match[0].length;
    if (end >= 24) return paragraph.slice(0, end).trim();
  }
  return paragraph;
}

export function summarizeResourceDescription(description: string): string {
  const sentence = firstMeaningfulSentence(description);
  if (!sentence) return "当前未声明资源价值说明。";
  return clampAtWordBoundary(sentence, DESCRIPTION_SUMMARY_LIMIT);
}

function projectFacts(values: string[], fallback: string): ProjectedFacts {
  const full = dedupeResourceDetailTexts(values.map(humanizeResourceContractField)).filter(isResearchFacingFact);
  if (!full.length) return { primary: [fallback], full: [], hasMore: false };

  const primary = full.slice(0, PRIMARY_FACT_LIMIT).map(value => clampAtWordBoundary(value, PRIMARY_FACT_LENGTH_LIMIT));
  const wasShortened = primary.some((value, index) => value !== full[index]);
  return {
    primary,
    full,
    hasMore: full.length > PRIMARY_FACT_LIMIT || wasShortened,
  };
}

function isObsoleteRuntimeEntryFact(value: string): boolean {
  return normalizeResourceDetailText(value).includes(normalizeResourceDetailText(OBSOLETE_RUNTIME_ENTRY_FACT));
}

function isExplicitInputCondition(value: string): boolean {
  return /(?:需(?:要)?|请|应|必须)(?:先)?(?:提供|提交|上传|输入|选择|明确|准备)|(?:已有|具备|可读取的).*(?:材料|数据|目标|问题|范围|记录)/iu.test(value);
}

function canStartResource(resource: ResourcePlazaResource): boolean {
  return (
    resource.availability === "tryable" &&
    resource.primaryAction === "try" &&
    resource.durableExecutable === true &&
    !resource.executionBlocked &&
    resource.dependencyGaps.length === 0
  );
}

function pushUnique(target: string[], seen: Set<string>, values: string[]): void {
  for (const value of values) {
    const display = toSingleLine(value);
    const key = normalizeResourceDetailText(display);
    if (!display || !key || !isResearchFacingFact(display) || seen.has(key)) continue;
    seen.add(key);
    target.push(display);
  }
}

function buildStartGuidance(
  resource: ResourcePlazaResource,
  canStart: boolean,
): string {
  if (canStart) return "可以从此处直接进入智能体会话。";
  if (resource.executionBlocked || resource.dependencyGaps.length) return "当前可查看研究说明，暂不能在平台内直接使用。";
  if (resource.availability === "view_only") return "当前可以查看说明，但还不能直接开始使用。";
  if (resource.availability === "configuration_required") return "请先准备好研究材料，并确认使用条件。";
  if (resource.availability === "unavailable") return "当前没有可用的启动入口。";
  return "当前运行事实尚不足以提供启动入口。";
}

function buildCriticalLimitation(
  resource: ResourcePlazaResource,
  canStart: boolean,
  limitations: string[],
): { display: string; source?: string } {
  if (resource.executionBlocked || resource.dependencyGaps.length) return { display: "当前仅提供研究说明，暂不能在平台内直接使用。" };
  if (resource.availability === "view_only") {
    return { display: "当前仅可查看说明，暂不能直接开始使用。" };
  }
  if (resource.availability === "unavailable") return { display: "当前暂不能使用，请先查看研究说明。" };
  const source = limitations.find(value => !(canStart && isObsoleteRuntimeEntryFact(value)));
  return source
    ? { display: clampAtWordBoundary(source, PRIMARY_FACT_LENGTH_LIMIT), source }
    : { display: LIMITATION_FALLBACK };
}

export function projectResourceDetail(resource: ResourcePlazaResource): ResourceDetailProjection {
  const canStart = canStartResource(resource);
  const fullDescription = resource.description.trim();
  const valueSummary = summarizeResourceDescription(fullDescription);

  const scenarios = dedupeResourceDetailTexts(
    resource.scenarios.map(value => RESOURCE_SCENARIO_LABELS[value] ?? humanizeResourceContractField(value)),
  );
  const domains = dedupeResourceDetailTexts(
    resource.domains.map(value => RESOURCE_DOMAIN_LABELS[value] ?? humanizeResourceContractField(value)),
  );
  const visibleScenarios = scenarios.slice(0, 2);
  const visibleDomains = domains.slice(0, 2);

  const usageConditions = dedupeResourceDetailTexts(splitFacts(resource.usageConditions).map(humanizeResourceContractField));
  const inputUsageConditions = usageConditions.filter(
    isExplicitInputCondition,
  );

  const seenPrerequisiteFacts = new Set<string>();
  const inputFacts: string[] = [];
  pushUnique(inputFacts, seenPrerequisiteFacts, [...resource.inputs, ...inputUsageConditions].map(humanizeResourceContractField));

  const inputProjection = projectFacts(inputFacts, INPUT_FALLBACK);
  const outputProjection = projectFacts(resource.outputs, OUTPUT_FALLBACK);

  const rawLimitations = dedupeResourceDetailTexts(splitFacts(resource.limitations).map(humanizeResourceContractField));
  const limitations = canStart
    ? rawLimitations.filter(value => !isObsoleteRuntimeEntryFact(value))
    : rawLimitations;
  const critical = buildCriticalLimitation(resource, canStart, limitations);

  const progressiveSections: ResourceDetailProgressiveSection[] = [];
  if (fullDescription) {
    progressiveSections.push({
      key: "description",
      title: "详细说明",
      summary: "查看资源的完整原始说明。",
      body: fullDescription,
      groups: [],
    });
  }

  const scopeAndDeliveryGroups: ResourceDetailFactGroup[] = [];
  if (scenarios.length > visibleScenarios.length) scopeAndDeliveryGroups.push({ label: "全部科研场景", values: scenarios });
  if (domains.length > visibleDomains.length) scopeAndDeliveryGroups.push({ label: "全部研究领域", values: domains });
  if (inputProjection.hasMore) scopeAndDeliveryGroups.push({ label: "完整输入与材料", values: inputProjection.full });
  if (outputProjection.hasMore) scopeAndDeliveryGroups.push({ label: "完整输出与交付", values: outputProjection.full });
  if (scopeAndDeliveryGroups.length) {
    progressiveSections.push({
      key: "scope-and-delivery",
      title: "完整适用范围与交付",
      summary: "查看首屏未展开的适用范围、输入和输出事实。",
      groups: scopeAndDeliveryGroups,
    });
  }

  const limitationSeen = new Set<string>();
  const criticalSourceKey = normalizeResourceDetailText(critical.source ?? "");
  if (criticalSourceKey) limitationSeen.add(criticalSourceKey);
  const additionalLimitations: string[] = [];
  pushUnique(additionalLimitations, limitationSeen, limitations);

  const limitationGroups: ResourceDetailFactGroup[] = [];
  if (additionalLimitations.length) limitationGroups.push({ label: "使用说明", values: additionalLimitations });
  if (limitationGroups.length) {
    progressiveSections.push({
      key: "limitations",
      title: "使用说明",
      summary: "查看研究使用时需要注意的事项。",
      groups: limitationGroups,
    });
  }

  return {
    valueSummary,
    suitability: {
      scenarios: visibleScenarios.length ? visibleScenarios : ["当前未声明适用的科研场景。"],
      domains: visibleDomains.length ? visibleDomains : ["当前未限定研究领域。"],
      hasMore: scenarios.length > visibleScenarios.length || domains.length > visibleDomains.length,
    },
    inputs: {
      values: inputProjection.primary,
      hasMore: inputProjection.hasMore,
    },
    outputs: {
      values: outputProjection.primary,
      hasMore: outputProjection.hasMore,
      usesFallback: outputProjection.full.length === 0,
    },
    criticalLimitation: critical.display,
    canStart,
    startGuidance: buildStartGuidance(resource, canStart),
    progressiveSections,
  };
}
