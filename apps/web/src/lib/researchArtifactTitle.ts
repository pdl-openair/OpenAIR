import { RESEARCH_ARTIFACT_LABELS_ZH } from "@openair/shared";

export interface ResearchArtifactIdentity {
  title?: string | null;
  artifactType?: string | null;
  type?: string | null;
  relatedSkillId?: string | null;
  validatorResult?: unknown;
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

/** Producer identity comes from formal metadata, never from the authored title. */
export function researchArtifactProducerIdentity(artifact: ResearchArtifactIdentity): string | null {
  const validator = record(artifact.validatorResult);
  const authority = record(validator?.producerCompletionAuthority);
  const executor = record(authority?.executorRef);
  if (executor?.executorType === "skill" && typeof executor.executorId === "string")
    return executor.executorId;
  const contract = validator?.artifactContract;
  const directSkill = typeof contract === "string"
    ? /^direct_skill:([^:]+):v\d+$/.exec(contract)
    : null;
  return directSkill?.[1] ?? artifact.relatedSkillId ?? null;
}

function mappedLabel(value?: string | null): string | undefined {
  const key = value?.trim().toLowerCase().replace(/-/g, "_");
  return key ? RESEARCH_ARTIFACT_LABELS_ZH[key] : undefined;
}

/**
 * Keep the distinguishing tail of the authored title (「 · leaderboard.csv」)
 * when the semantic label wins, so sibling outputs from one producer don't
 * collapse onto the same display name. Machine-only tails are dropped.
 */
function authoredTitleTail(title: string): string | null {
  const separator = title.lastIndexOf("·");
  if (separator < 0) return null;
  const tail = title.slice(separator + 1).trim();
  if (!tail || /^(?:results?|outputs?)$/i.test(tail)) return null;
  return tail;
}

/** One naming boundary for cards, footer links, library entries and result bodies. */
export function researchArtifactTitle(artifact: ResearchArtifactIdentity): string {
  const title = artifact.title?.trim() ?? "";
  if (title && /[\u3400-\u9fff]/.test(title) && !/^(?:研究成果|科研成果|研究结果|产物)$/.test(title))
    return title;
  const type = artifact.artifactType ?? artifact.type;
  // A semantic-only work type can have several distinct producer outputs.
  const producer = mappedLabel(researchArtifactProducerIdentity(artifact));
  const typeLabel = mappedLabel(type);
  const label = type?.endsWith(".planned_output")
    ? producer ?? typeLabel
    : typeLabel ?? producer;
  if (label) {
    const tail = authoredTitleTail(title);
    return tail ? `${label} · ${tail}` : label;
  }
  const titleLabel = mappedLabel(title);
  if (titleLabel) return titleLabel;
  return title && !/^[a-z0-9]+(?:[._:-][a-z0-9]+)+$/i.test(title)
    ? title
    : "研究成果";
}
