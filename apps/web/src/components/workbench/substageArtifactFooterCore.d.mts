export interface SubstageArtifactFooterSource {
  id: string;
  taskId?: string;
  title?: string | null;
  format?: string | null;
  artifactState?: string | null;
  subStageInstanceId?: string | null;
  artifactVisibility?: string | null;
  visibility?: string | null;
  audience?: string | null;
}

export interface SubstageArtifactFooterItem {
  id: string;
  title: string;
  format: string | null;
  artifactState: string | null;
}

export interface SubstageArtifactFooterSelection {
  taskId: string;
  substageInstanceId: string;
  artifactId: string | null;
}

export function projectSubstageArtifactFooter(input: {
  taskId: string;
  substageInstanceId: string;
  artifactRefs: string[];
  artifacts: SubstageArtifactFooterSource[];
}): SubstageArtifactFooterItem[];

export function reconcileSubstageArtifactFooterSelection(input: {
  selection: SubstageArtifactFooterSelection | null;
  taskId: string;
  substageInstanceId: string;
  artifactIds: string[];
  deepLinkedArtifactId?: string | null;
}): SubstageArtifactFooterSelection;

export function buildSubstageArtifactDeepLink(input: {
  pathname: string;
  search?: string;
  artifactId: string | null;
}): string;
