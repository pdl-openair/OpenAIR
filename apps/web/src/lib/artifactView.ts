import type { TaskStage } from "@openair/shared";
import type { WorkbenchArtifact, WorkbenchStageRow, WorkbenchTaskModel } from "../model/workbenchTask";
import {
  artifactsForExplicitScope as artifactsForExplicitScopeCore,
  artifactScopePresentation as artifactScopePresentationCore,
  buildArtifactViewNotices as buildArtifactViewNoticesCore,
  chooseFocusedArtifactId as chooseFocusedArtifactIdCore,
  reconcileArtifactViewSelection as reconcileArtifactViewSelectionCore,
  resolveArtifactLocation as resolveArtifactLocationCore,
  shouldAutoFollowCurrentArtifact as shouldAutoFollowCurrentArtifactCore,
} from "./artifactViewSelectionCore.mjs";

export function artifactById(model: WorkbenchTaskModel, id: string): WorkbenchArtifact | undefined {
  return model.artifacts.find((a) => a.id === id);
}

export function artifactsByRefs(model: WorkbenchTaskModel, refs: string[]): WorkbenchArtifact[] {
  const out: WorkbenchArtifact[] = [];
  for (const id of refs) {
    const hit = artifactById(model, id);
    if (hit) out.push(hit);
  }
  return out;
}

export function findSubStageKeyForArtifact(
  model: WorkbenchTaskModel,
  phaseStageKey: string,
  artifactId: string,
): string | null {
  const location = resolveArtifactLocation(model, artifactId);
  return location.kind === "located" && location.stageKey === phaseStageKey
    ? location.substageInstanceId
    : null;
}

/** 进入某页面阶段时默认选中的二级节点 */
export function defaultSubStageKey(
  subStages: { id: string; subStage?: string; status: string; artifactRefs: string[] }[],
): string | null {
  if (subStages.length === 0) return null;
  const running = subStages.find((s) => s.status === "running");
  if (running) return running.id;
  const priority = [
    "literature_synthesis",
    "short_literature_review",
    "short_literature_review_draft",
    "source_table",
    "recommended_readings",
    "top_k_source_set",
    "research_plan",
    "literature_search_strategy",
    "gap_next_steps",
    "literature_gaps_next_steps",
  ];
  for (const key of priority) {
    const hit = subStages.find((s) => (s.subStage ?? s.id) === key && (s.artifactRefs ?? []).length > 0);
    if (hit) return hit.id;
  }
  const withArt = subStages.find((s) => (s.artifactRefs ?? []).length > 0);
  if (withArt) return withArt.id;
  return subStages[0].id;
}

export type ArtifactScope = "substage" | "stage" | "task";
export type ArtifactViewOwnership = "following_current" | "pinned";
export type ArtifactLocation =
  | { kind: "located"; stageKey: TaskStage; substageInstanceId: string; evidence: "substage_instance_id" | "substage_artifact_ref" }
  | { kind: "unlocatable"; reason: string };

export function artifactsForExplicitScope<T>(input: {
  scope: ArtifactScope;
  substageArtifacts: T[];
  stageArtifacts: T[];
  taskArtifacts: T[];
}): T[] {
  return artifactsForExplicitScopeCore(input) as T[];
}

export function resolveArtifactLocation(
  model: Pick<WorkbenchTaskModel, "artifacts" | "stages">,
  artifactId: string,
): ArtifactLocation {
  return resolveArtifactLocationCore({
    artifact: model.artifacts.find((artifact) => artifact.id === artifactId) ?? null,
    stages: model.stages,
  }) as ArtifactLocation;
}

export function chooseFocusedArtifactId(input: {
  focusedArtifactId: string | null;
  deepLinkedArtifactId: string | null;
  visibleArtifactIds: string[];
  allArtifactIds: string[];
  formatsById?:
    | Map<string, string | null | undefined>
    | Record<string, string | null | undefined>
    | null;
}): string | null {
  return chooseFocusedArtifactIdCore(input) as string | null;
}

export function shouldAutoFollowCurrentArtifact(input: {
  ownership: ArtifactViewOwnership;
  latestArtifactId: string | null;
  focusedArtifactId: string | null;
  deepLinkedArtifactId: string | null;
  location: ArtifactLocation | null;
}): boolean {
  return shouldAutoFollowCurrentArtifactCore(input) as boolean;
}

export function buildArtifactViewNotices(input: {
  ownership: ArtifactViewOwnership;
  latestArtifactId: string | null;
  reviewableArtifactId: string | null;
  focusedArtifactId: string | null;
}): { newCurrentArtifactId: string | null; pendingReviewArtifactId: string | null } {
  return buildArtifactViewNoticesCore(input) as {
    newCurrentArtifactId: string | null;
    pendingReviewArtifactId: string | null;
  };
}

export function artifactScopePresentation(input: {
  scope: ArtifactScope;
  stageLabel: string;
  substageLabel: string | null;
}): { title: string; path: string; explanation: string; returnLabel: string | null } {
  return artifactScopePresentationCore(input) as {
    title: string;
    path: string;
    explanation: string;
    returnLabel: string | null;
  };
}

export type ArtifactViewSelectionSnapshot = {
  taskId: string;
  revision: number;
  researchDesignKey: string;
  stageKeys: TaskStage[];
  substageIdsByStage: Partial<Record<TaskStage, string[]>>;
  artifactIds: string[];
};

export type ArtifactViewSelectionState = {
  taskId: string;
  revision: number;
  researchDesignKey: string;
  stageKey: TaskStage;
  selectedSubstageId: string | null;
  focusedArtifactId: string | null;
  scope: ArtifactScope;
  ownership: ArtifactViewOwnership;
};

export function reconcileArtifactViewSelection(input: {
  selection: ArtifactViewSelectionState | null;
  snapshot: ArtifactViewSelectionSnapshot;
  initialSelection: ArtifactViewSelectionState;
}): ArtifactViewSelectionState {
  return reconcileArtifactViewSelectionCore(input) as ArtifactViewSelectionState;
}

export function artifactViewSnapshot(model: WorkbenchTaskModel): ArtifactViewSelectionSnapshot {
  return {
    taskId: model.taskId,
    revision: model.revision,
    researchDesignKey: model.researchDesign
      ? `${model.researchDesign.designId}:${model.researchDesign.version}`
      : "none",
    stageKeys: model.stages.map((stage) => stage.stage),
    substageIdsByStage: Object.fromEntries(
      model.stages.map((stage: WorkbenchStageRow) => [stage.stage, stage.subStages.map((substage) => substage.id)]),
    ) as Partial<Record<TaskStage, string[]>>,
    artifactIds: model.artifacts.map((artifact) => artifact.id),
  };
}
