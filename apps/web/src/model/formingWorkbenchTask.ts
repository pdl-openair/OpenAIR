import type { WorkbenchTaskModel, WorkbenchStageRow } from "./workbenchTask";

/** Reserved-task workbench model before the durable task row exists. */
export function createFormingWorkbenchTaskModel(
  input: {
    taskId: string;
    title: string;
  },
  stages: WorkbenchStageRow[],
): WorkbenchTaskModel {
  const now = new Date(0).toISOString();
  const title = input.title.trim() || "新研究";
  return {
    taskId: input.taskId,
    revision: 0,
    title,
    goalSummary: "",
    domain: "",
    currentUnderlyingStage: "problem_modeling",
    taskStatus: "draft",
    executionDisplayStatus: "draft",
    progressTrust: { kind: "legacy" },
    progressRevision: null,
    stageCounts: null,
    collaborationNodes: [],
    collaborationEvents: [],
    lifecycleEvents: [],
    researchProcessHistory: [],
    researchCurrentState: null,
    resultSource: "none",
    finalResult: null,
    executionMode: "automatic_research",
    stageGateResumeApproved: false,
    creationStatus: undefined,
    creationPlan: null,
    researchDesign: null,
    researchDesignHistory: [],
    createdAt: now,
    updatedAt: now,
    stages,
    artifacts: [],
    capabilityCalls: [],
    trustRecords: [],
    userVisibleEvents: [],
    workUnits: [],
  };
}
