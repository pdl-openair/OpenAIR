/**
 * Shared formatter for evidence panel data-testid values.
 *
 * Keep this helper aligned with apps/web ArtifactRenderer evidence test-id shape:
 *   artifact-evidence-${part}-${refId}
 */
export function evidenceRefTestId(refId: string, part: string): string {
  return `artifact-evidence-${part}-${refId}`;
}
