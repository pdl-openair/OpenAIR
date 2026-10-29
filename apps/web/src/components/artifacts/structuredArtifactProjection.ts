import {
  researchPresentationParameterName,
  researchPresentationFieldLabel,
  researchPresentationScalarText,
} from "@openair/shared";

const INTERNAL_STRUCTURED_KEYS = new Set([
  "artifactid",
  "assumptionid",
  "assumptionids",
  "blockingevidenceneedids",
  "boundaryid",
  "boundaryids",
  "candidateid",
  "candidateids",
  "evidenceneedid",
  "evidenceneedids",
  "familyid",
  "id",
  "limitationid",
  "localkey",
  "runtimemetadata",
  "orchestrationstate",
  "providername",
  "lifecyclestate",
  "dispatchid",
  "internalrecordid",
  "schema",
  "schemaversion",
  "targetref",
  "unresolvedassumptionids",
  "version",
]);

const SYSTEM_DIAGNOSTIC_KEYS = new Set([
  "failurecode",
  "errorcode",
  "failuremessage",
  "providererror",
  "providerdiagnostics",
  "internalerror",
  "exception",
  "stacktrace",
]);

export function isSystemDiagnosticField(key: string): boolean {
  return SYSTEM_DIAGNOSTIC_KEYS.has(normalizedStructuredKey(key));
}

const RESEARCH_CONTENT_KEYS = new Set([
  "abstract",
  "analysis",
  "comparison",
  "conclusion",
  "conclusions",
  "description",
  "discussion",
  "evidence",
  "experiment",
  "finding",
  "findings",
  "limitations",
  "method",
  "methodology",
  "methods",
  "nextaction",
  "question",
  "questioncandidates",
  "recommendation",
  "recommendations",
  "reasoningsummary",
  "researchdesign",
  "researchquestion",
  "result",
  "results",
  "scope",
  "statement",
  "summary",
  "testablehypothesis",
  "uncertainty",
]);

const AUXILIARY_STRUCTURED_KEYS = new Set([
  "confirmationstatus",
  "executionstatus",
  "handoffstatus",
  "informationsource",
  "planningjudgement",
  "sourcebasis",
  "status",
  "systemdecision",
  "systemplanningjudgement",
]);

const ARRAY_IDENTITY_KEYS = [
  "title",
  "name",
  "question",
  "claim",
  "finding",
  "method",
  "description",
  "statement",
];

const TECHNICAL_DETAIL_KEYS = new Set([
  "exitcode",
  "headmatchesresolvedcommit",
  "stdout",
  "stderr",
  "command",
  "arguments",
  "validationchecks",
  "commandresults",
  "executiondiagnostics",
  "diagnostics",
]);

function partitionTechnicalDetails(value: unknown): {
  main: unknown;
  details: unknown;
} {
  if (Array.isArray(value)) {
    const rows = value.map(partitionTechnicalDetails);
    const details = rows.map((row) => row.details).filter((row) => row != null);
    return {
      main: rows.map((row) => row.main),
      details: details.length ? details : null,
    };
  }
  if (!value || typeof value !== "object")
    return { main: value, details: null };
  const main: Record<string, unknown> = {};
  const details: Record<string, unknown> = {};
  for (const [key, child] of Object.entries(value)) {
    const normalized = normalizedStructuredKey(key);
    if (
      TECHNICAL_DETAIL_KEYS.has(normalized) ||
      (normalized === "operation" && child === "read_head")
    ) {
      details[key] = child;
      continue;
    }
    const part = partitionTechnicalDetails(child);
    main[key] = part.main;
    if (part.details != null) details[key] = part.details;
  }
  return { main, details: Object.keys(details).length ? details : null };
}

function parameterDisplayNames(document: unknown): Map<string, string> {
  const names = new Map<string, Set<string>>();
  const visit = (value: unknown) => {
    if (Array.isArray(value)) {
      value.forEach(visit);
      return;
    }
    if (!value || typeof value !== "object") return;
    const row = value as Record<string, unknown>;
    if (
      typeof row.parameter_key === "string" &&
      typeof row.display_name === "string" &&
      row.display_name.trim()
    ) {
      const labels = names.get(row.parameter_key) ?? new Set<string>();
      labels.add(
        researchPresentationParameterName(
          row.parameter_key,
          row.display_name.trim(),
        ),
      );
      names.set(row.parameter_key, labels);
    }
    Object.values(row).forEach(visit);
  };
  visit(document);
  return new Map(
    [...names].flatMap(([key, labels]) =>
      labels.size === 1 ? [[key, [...labels][0]] as const] : [],
    ),
  );
}

function projectParameterNames(
  value: unknown,
  names: Map<string, string>,
): unknown {
  if (Array.isArray(value))
    return value.map((row) => projectParameterNames(row, names));
  if (!value || typeof value !== "object") return value;
  const row = value as Record<string, unknown>;
  const displayName = (key: string) =>
    researchPresentationParameterName(key, names.get(key) ?? key);
  return Object.fromEntries(
    Object.entries(row).map(([key, child]) => {
      const normalized = normalizedStructuredKey(key);
      if (normalized === "parameterkey" && typeof child === "string")
        return [key, displayName(child)];
      if (
        normalized === "displayname" &&
        typeof row.parameter_key === "string" &&
        typeof child === "string"
      )
        return [
          key,
          researchPresentationParameterName(row.parameter_key, child),
        ];
      if (
        [
          "parameterkeys",
          "coveredparameterkeys",
          "missingparameterkeys",
          "missingrequiredparameterkeys",
        ].includes(normalized) &&
        Array.isArray(child)
      )
        return [
          key,
          child.map((item) =>
            typeof item === "string" ? displayName(item) : item,
          ),
        ];
      return [key, projectParameterNames(child, names)];
    }),
  );
}

function normalizedStructuredKey(key: string): string {
  return key.replace(/[^a-zA-Z0-9]/g, "").toLowerCase();
}

function isInternalStructuredKey(key: string): boolean {
  return (
    INTERNAL_STRUCTURED_KEYS.has(normalizedStructuredKey(key)) ||
    isSystemDiagnosticField(key)
  );
}

function structuredFieldLabel(key: string): string {
  // Unmapped structural keys are shown verbatim instead of masked under a
  // generic "\u8865\u5145\u7814\u7a76\u4fe1\u606f" label that implied false semantics. Specialized
  // presentations co-locate their own field-label maps; the generic projection
  // stays honest about any key it does not know.
  return researchPresentationFieldLabel(key) ?? key;
}
const structuredScalarText = researchPresentationScalarText;

function structuredFieldPriority(key: string): number {
  const normalizedKey = normalizedStructuredKey(key);
  if (["directanswer", "answer", "currentjudgment", "conclusion", "conclusions", "recommendation", "summary"].includes(normalizedKey)) return 0;
  if (["limitations", "uncertainty", "risks", "unknowns", "nextaction", "nextactions", "nextsteps"].includes(normalizedKey)) return 1;
  if (RESEARCH_CONTENT_KEYS.has(normalizedKey)) return 2;
  if (AUXILIARY_STRUCTURED_KEYS.has(normalizedKey)) return 4;
  return 3;
}

function arrayItemIdentity(
  value: Record<string, unknown>,
): { key: string; text: string } | null {
  for (const identityKey of ARRAY_IDENTITY_KEYS) {
    const entry = Object.entries(value).find(
      ([key]) => normalizedStructuredKey(key) === identityKey,
    );
    if (!entry) continue;
    const [key, child] = entry;
    if (
      child === null ||
      !["string", "number", "boolean"].includes(typeof child)
    )
      continue;
    const text = structuredScalarText(
      child as string | number | boolean,
    ).trim();
    if (text) return { key, text };
  }
  return null;
}

function structuredValueMarkdown(
  value: unknown,
  depth = 2,
  omitKey?: string,
): string[] {
  if (value === null) return ["未提供"];
  if (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return [structuredScalarText(value)];
  }

  if (Array.isArray(value)) {
    if (value.length === 0) return ["未提供"];
    if (
      value.every(
        (item) =>
          item === null ||
          ["string", "number", "boolean"].includes(typeof item),
      )
    ) {
      return value.map(
        (item) =>
          `- ${item === null ? "未提供" : structuredScalarText(item as string | number | boolean)}`,
      );
    }
    return value.flatMap((item) => {
      if (item && typeof item === "object" && !Array.isArray(item)) {
        const identity = arrayItemIdentity(item as Record<string, unknown>);
        if (identity) {
          const normalizedIdentity = normalizedStructuredKey(identity.key);
          const content = structuredValueMarkdown(
            item,
            depth + 1,
            normalizedIdentity,
          );
          if (
            normalizedIdentity === "description" ||
            normalizedIdentity === "statement"
          ) {
            return [
              `**${structuredFieldLabel(identity.key)}：** ${identity.text}`,
              "",
              ...content,
              "",
            ];
          }
          return [
            `${"#".repeat(Math.min(depth, 6))} ${identity.text}`,
            "",
            ...content,
            "",
          ];
        }
      }
      return [...structuredValueMarkdown(item, depth + 1), ""];
    });
  }

  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(
        ([key]) =>
          !isInternalStructuredKey(key) &&
          normalizedStructuredKey(key) !== omitKey,
      )
      .sort(
        ([left], [right]) =>
          structuredFieldPriority(left) - structuredFieldPriority(right),
      );
    if (entries.length === 0) return omitKey ? [] : ["未提供"];

    const primary = entries.filter(
      ([key]) => !AUXILIARY_STRUCTURED_KEYS.has(normalizedStructuredKey(key)),
    );
    const auxiliary = entries.filter(([key]) =>
      AUXILIARY_STRUCTURED_KEYS.has(normalizedStructuredKey(key)),
    );
    const projected = primary.flatMap(([key, child]) => {
      const content = structuredValueMarkdown(child, depth + 1);
      return [
        `${"#".repeat(Math.min(depth, 6))} ${structuredFieldLabel(key)}`,
        "",
        ...content,
        "",
      ];
    });

    const scalarAuxiliary = auxiliary.filter(
      ([, child]) =>
        child === null ||
        ["string", "number", "boolean"].includes(typeof child),
    );
    if (scalarAuxiliary.length > 0) {
      projected.push(
        `> ${scalarAuxiliary
          .map(([key, child]) => {
            const text =
              child === null
                ? "未提供"
                : structuredScalarText(child as string | number | boolean);
            return `${structuredFieldLabel(key)}：${text}`;
          })
          .join(" · ")}`,
        "",
      );
    }

    auxiliary
      .filter(
        ([, child]) =>
          child !== null &&
          !["string", "number", "boolean"].includes(typeof child),
      )
      .forEach(([key, child]) => {
        const content = structuredValueMarkdown(child, depth + 1);
        projected.push(
          `> **${structuredFieldLabel(key)}**`,
          ...content.map((line) => (line ? `> ${line}` : ">")),
          "",
        );
      });

    return projected.length > 0 ? projected : ["未提供"];
  }

  return ["未提供"];
}

function structuredEntryByNormalizedKey(
  record: Record<string, unknown>,
  normalized: string,
): [string, unknown] | undefined {
  return Object.entries(record).find(
    ([key]) => normalizedStructuredKey(key) === normalized,
  );
}

function structuredNonEmptyText(value: unknown): string | null {
  if (typeof value === "string") {
    const text = value.trim();
    return text || null;
  }
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return null;
}

/**
 * Presentation-only join: reading_priority.candidate_id → candidate_literature.title.
 * Does not invent titles; unmatched rows keep the existing rank/reason fallback.
 */
function projectReadingPriorityForDisplay(document: unknown): unknown {
  if (!document || typeof document !== "object" || Array.isArray(document))
    return document;
  const root = document as Record<string, unknown>;
  const literature = structuredEntryByNormalizedKey(
    root,
    "candidateliterature",
  )?.[1];
  const priorityEntry = structuredEntryByNormalizedKey(root, "readingpriority");
  if (
    !Array.isArray(literature) ||
    !priorityEntry ||
    !Array.isArray(priorityEntry[1])
  )
    return document;

  const titleByCandidateId = new Map<string, string>();
  for (const item of literature) {
    if (!item || typeof item !== "object" || Array.isArray(item)) continue;
    const row = item as Record<string, unknown>;
    const candidateId = structuredNonEmptyText(
      structuredEntryByNormalizedKey(row, "candidateid")?.[1],
    );
    const title = structuredNonEmptyText(
      structuredEntryByNormalizedKey(row, "title")?.[1],
    );
    if (!candidateId || !title || titleByCandidateId.has(candidateId)) continue;
    titleByCandidateId.set(candidateId, title);
  }

  const projectedPriority = priorityEntry[1].map((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return item;
    const row = item as Record<string, unknown>;
    if (
      structuredNonEmptyText(structuredEntryByNormalizedKey(row, "title")?.[1])
    )
      return item;

    const candidateId = structuredNonEmptyText(
      structuredEntryByNormalizedKey(row, "candidateid")?.[1],
    );
    if (!candidateId) return item;
    const joinedTitle = titleByCandidateId.get(candidateId);
    if (!joinedTitle) return item;

    const rankEntry = structuredEntryByNormalizedKey(row, "rank");
    const rankValue = rankEntry?.[1];
    const rankText =
      typeof rankValue === "number" && Number.isFinite(rankValue)
        ? String(rankValue)
        : structuredNonEmptyText(rankValue);
    const rankLabel = rankText ? `#${rankText}` : null;

    const next: Record<string, unknown> = {
      ...row,
      title: rankLabel ? `${rankLabel} · ${joinedTitle}` : joinedTitle,
    };
    if (rankEntry && rankLabel) delete next[rankEntry[0]];

    const reasonEntry = structuredEntryByNormalizedKey(row, "reason");
    if (reasonEntry && !structuredEntryByNormalizedKey(row, "priorityreason")) {
      next.priority_reason = reasonEntry[1];
      delete next[reasonEntry[0]];
    }
    return next;
  });

  return { ...root, [priorityEntry[0]]: projectedPriority };
}

/** Projects canonical structured content into the same Markdown body used by textual artifacts. */
export function projectStructuredArtifactMarkdown(
  content: string,
  title: string,
): string | null {
  return projectStructuredArtifactForDisplay(content, title)?.markdown ?? null;
}

export function projectStructuredArtifactForDisplay(
  content: string,
  title: string,
): {
  markdown: string;
  technicalDetails: string | null;
} | null {
  try {
    const document: unknown = JSON.parse(content);
    const projected = projectReadingPriorityForDisplay(
      projectParameterNames(document, parameterDisplayNames(document)),
    );
    const parts = partitionTechnicalDetails(projected);
    return {
      markdown: [`# ${title}`, "", ...structuredValueMarkdown(parts.main)]
        .join("\n")
        .trim(),
      technicalDetails:
        parts.details == null
          ? null
          : structuredValueMarkdown(parts.details).join("\n").trim(),
    };
  } catch {
    return null;
  }
}
