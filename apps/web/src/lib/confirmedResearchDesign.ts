import type { ResearchDesignVersion } from "@openair/shared";

/** Keep the current confirmed authority available even while a new draft exists. */
export function projectResearchDesigns(versions: ResearchDesignVersion[]) {
  const ordered = [...versions].sort((a, b) => b.version - a.version);
  const latest = ordered[0];
  return {
    researchDesign: latest
      ? {
          designId: latest.researchDesignId,
          version: latest.version,
          status: latest.status,
        }
      : null,
    confirmedResearchDesign:
      ordered.find((design) => design.status === "confirmed") ?? null,
  };
}
