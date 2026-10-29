export type CoreArtifactScope = "substage" | "stage" | "task";
export type CoreArtifactLocation =
  | { kind: "located"; stageKey: string; substageInstanceId: string; evidence: "substage_instance_id" | "substage_artifact_ref" }
  | { kind: "unlocatable"; reason: string };

export function artifactsForExplicitScope<T>(input: {
  scope: CoreArtifactScope;
  substageArtifacts: T[];
  stageArtifacts: T[];
  taskArtifacts: T[];
}): T[];

export function resolveArtifactLocation(input: {
  artifact: { id: string; relatedStage: string; subStageInstanceId?: string | null } | null;
  stages: { stage: string; subStages: { id: string; artifactRefs?: string[] }[] }[];
}): CoreArtifactLocation;

export function chooseFocusedArtifactId(input: {
  focusedArtifactId: string | null;
  deepLinkedArtifactId: string | null;
  visibleArtifactIds: string[];
  allArtifactIds: string[];
  formatsById?: Map<string, string | null | undefined> | Record<string, string | null | undefined> | null;
}): string | null;

export function shouldAutoFollowCurrentArtifact(input: {
  ownership: "following_current" | "pinned";
  latestArtifactId: string | null;
  focusedArtifactId: string | null;
  deepLinkedArtifactId: string | null;
  location: CoreArtifactLocation | null;
}): boolean;

export function buildArtifactViewNotices(input: {
  ownership: "following_current" | "pinned";
  latestArtifactId: string | null;
  reviewableArtifactId: string | null;
  focusedArtifactId: string | null;
}): { newCurrentArtifactId: string | null; pendingReviewArtifactId: string | null };

export function artifactScopePresentation(input: {
  scope: CoreArtifactScope;
  stageLabel: string;
  substageLabel: string | null;
}): { title: string; path: string; explanation: string; returnLabel: string | null };

export function reconcileArtifactViewSelection<T>(input: {
  selection: T | null;
  snapshot: {
    taskId: string;
    revision: number;
    researchDesignKey: string;
    stageKeys: string[];
    substageIdsByStage: Record<string, string[] | undefined>;
    artifactIds: string[];
  };
  initialSelection: T;
}): T;
