import {
  STAGE_LABELS,
  TASK_STAGE_ORDER,
  type StageCatalog,
  type SubStageCatalogEntry,
  type TaskPlan,
  type TaskPlanStage,
  type TaskPlanUpdate,
  type TaskStage,
  type WorkflowSubStage,
} from "@openair/shared";

export type ResearchDesignAddedStep = {
  localKey: string;
  stageKey: TaskStage;
  substage: string;
  label: string;
  objective: string;
};

export type ResearchDesignEditDraft = {
  baseRevision: number;
  basePlan: TaskPlan;
  goalSummary: string;
  objectives: Record<string, string>;
  addedSteps: ResearchDesignAddedStep[];
  removedInstanceIds: string[];
  nextAddedStepSeq: number;
};

export function researchDesignEditSubstageKey(
  stage: TaskPlanStage,
  substage: WorkflowSubStage,
  index: number,
): string {
  return (
    substage.instanceId ?? `${stage.stageKey}:${substage.substage}:${index}`
  );
}

export function researchDesignEditDraftFrom(
  plan: TaskPlan,
  revision: number,
): ResearchDesignEditDraft {
  const objectives: Record<string, string> = {};
  for (const stage of plan.stages) {
    (stage.substages ?? []).forEach((substage, index) => {
      objectives[researchDesignEditSubstageKey(stage, substage, index)] =
        substage.action?.trim() ?? "";
    });
  }
  return {
    baseRevision: revision,
    basePlan: plan,
    goalSummary: plan.goalSummary ?? "",
    objectives,
    addedSteps: [],
    removedInstanceIds: [],
    nextAddedStepSeq: 1,
  };
}

export function researchDesignEditDraftRebaseOnLatest(
  draft: ResearchDesignEditDraft,
  latestPlan: TaskPlan,
  latestRevision: number,
): ResearchDesignEditDraft {
  const latestDraft = researchDesignEditDraftFrom(latestPlan, latestRevision);
  const latestInstanceIds = new Set(
    latestPlan.stages.flatMap((stage) =>
      (stage.substages ?? [])
        .map((substage) => substage.instanceId)
        .filter((id): id is string => Boolean(id)),
    ),
  );
  return {
    baseRevision: latestRevision,
    basePlan: latestPlan,
    goalSummary: draft.goalSummary,
    objectives: { ...latestDraft.objectives, ...draft.objectives },
    addedSteps: draft.addedSteps,
    removedInstanceIds: draft.removedInstanceIds.filter((instanceId) =>
      latestInstanceIds.has(instanceId),
    ),
    nextAddedStepSeq: Math.max(
      latestDraft.nextAddedStepSeq,
      draft.nextAddedStepSeq,
    ),
  };
}

export function researchDesignEditRemainingCount(
  draft: ResearchDesignEditDraft,
): number {
  const removed = new Set(draft.removedInstanceIds);
  const existing = draft.basePlan.stages.reduce(
    (count, stage) =>
      count +
      (stage.substages ?? []).filter(
        (substage) => substage.instanceId && !removed.has(substage.instanceId),
      ).length,
    0,
  );
  return existing + draft.addedSteps.length;
}

export function researchDesignEditDraftDirty(
  draft: ResearchDesignEditDraft,
): boolean {
  if (draft.addedSteps.length > 0 || draft.removedInstanceIds.length > 0)
    return true;
  if (draft.goalSummary.trim() !== (draft.basePlan.goalSummary ?? "").trim())
    return true;
  for (const stage of draft.basePlan.stages) {
    const substages = stage.substages ?? [];
    for (let index = 0; index < substages.length; index += 1) {
      const substage = substages[index];
      const key = researchDesignEditSubstageKey(stage, substage, index);
      if (
        (draft.objectives[key] ?? "").trim() !== (substage.action ?? "").trim()
      )
        return true;
    }
  }
  return false;
}

export function researchDesignEditHasBlankWork(
  draft: ResearchDesignEditDraft,
): boolean {
  if (!draft.goalSummary.trim()) return true;
  if (draft.addedSteps.some((step) => !step.substage || !step.objective.trim()))
    return true;
  const removed = new Set(draft.removedInstanceIds);
  for (const stage of draft.basePlan.stages) {
    const substages = stage.substages ?? [];
    for (let index = 0; index < substages.length; index += 1) {
      const substage = substages[index];
      if (!substage.instanceId || removed.has(substage.instanceId)) continue;
      const value =
        draft.objectives[
          researchDesignEditSubstageKey(stage, substage, index)
        ] ??
        substage.action ??
        "";
      if (!value.trim()) return true;
    }
  }
  return false;
}

export function requiredCanonicalEditText(
  value: string | null | undefined,
  field: string,
): string {
  const normalized = value?.trim() ?? "";
  if (!normalized)
    throw new Error(`canonical_research_design_${field}_missing`);
  return normalized;
}

export function researchDesignEditUpdateFrom(
  draft: ResearchDesignEditDraft,
): TaskPlanUpdate {
  const removed = new Set(draft.removedInstanceIds);
  const stagesByKey = new Map(
    draft.basePlan.stages.map((stage) => [stage.stageKey, stage]),
  );
  return {
    expectedRevision: draft.baseRevision,
    goalSummary: draft.goalSummary.trim(),
    understanding: {
      evidenceBoundaries: [
        ...(draft.basePlan.understanding?.evidenceBoundaries ?? []),
      ],
    },
    clarifications: [...(draft.basePlan.clarifications ?? [])],
    stages: TASK_STAGE_ORDER.map((stageKey) => {
      const stage = stagesByKey.get(stageKey);
      const existing = (stage?.substages ?? []).flatMap((substage, index) => {
        if (!stage || !substage.instanceId || removed.has(substage.instanceId))
          return [];
        return [
          {
            instanceId: substage.instanceId,
            substage: substage.substage,
            label: requiredCanonicalEditText(substage.label, "substage_label"),
            action: requiredCanonicalEditText(
              draft.objectives[
                researchDesignEditSubstageKey(stage, substage, index)
              ] ?? substage.action,
              "substage_objective",
            ),
            expectedArtifact: requiredCanonicalEditText(
              substage.expectedArtifact,
              "expected_artifact",
            ),
          },
        ];
      });
      const added = draft.addedSteps
        .filter((step) => step.stageKey === stageKey)
        .map((step) => {
          const objective = requiredCanonicalEditText(
            step.objective,
            "substage_objective",
          );
          return {
            instanceId: null,
            substage: step.substage,
            label: step.label,
            action: objective,
            expectedArtifact: objective,
          };
        });
      return {
        stageKey,
        title: requiredCanonicalEditText(
          stage?.title?.trim() || STAGE_LABELS[stageKey],
          "stage_title",
        ),
        description: requiredCanonicalEditText(
          stage?.description?.trim() || STAGE_LABELS[stageKey],
          "stage_description",
        ),
        substages: [...existing, ...added],
      };
    }),
  };
}

/** Menu choices exclude existing and selected subgoals, while retaining this row's choice. */
export function researchDesignAvailableSubStages(
  draft: ResearchDesignEditDraft,
  stageKey: TaskStage,
  catalog: StageCatalog[],
  currentLocalKey?: string,
): SubStageCatalogEntry[] {
  const removed = new Set(draft.removedInstanceIds);
  const used = new Set(
    (
      draft.basePlan.stages.find((stage) => stage.stageKey === stageKey)
        ?.substages ?? []
    )
      .filter((work) => !work.instanceId || !removed.has(work.instanceId))
      .map((work) => work.substage),
  );
  for (const step of draft.addedSteps) {
    if (
      step.stageKey === stageKey &&
      step.localKey !== currentLocalKey &&
      step.substage
    ) {
      used.add(step.substage);
    }
  }
  return (
    catalog.find((stage) => stage.stageKey === stageKey)?.allowedSubStages ?? []
  ).filter((entry) => entry.enabled !== false && !used.has(entry.key));
}
