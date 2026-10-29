import type { CapabilityCallStatus, Task, TaskStage } from "@openair/shared";
import type { WorkbenchTaskModel } from "./workbenchTask";

const STAGE_ORDER: TaskStage[] = [
  "problem_modeling",
  "solution_design",
  "execution_validation",
  "insight_generation",
];

function mapFixtureCallStatus(status: string): CapabilityCallStatus {
  if (status === "completed") return "completed";
  if (status === "running") return "running";
  if (status === "needs_input") return "needs_input";
  if (status === "blocked_recoverable") return "blocked_recoverable";
  if (status === "insufficient_evidence") return "insufficient_evidence";
  if (status === "failed") return "failed";
  return "not_started";
}

const DEFAULT_OWNER_ID = "user_demo_owner";
const DEFAULT_WORKSPACE_ID = "ws_demo_default";
const DEFAULT_CREATED_BY = "fixture_adapter";

export type TaskWithExecutionDisplayStatus = Task & {
  /** Canonical user-visible execution status; absent only for legacy/raw Task fallbacks. */
  executionDisplayStatus?: string;
};

/** 将工作台 fixture 模型转为共享 Task，供详情页 / 看板复用 */
export function taskFromWorkbenchModel(m: WorkbenchTaskModel): TaskWithExecutionDisplayStatus {
  return {
    taskId: m.taskId,
    ownerId: DEFAULT_OWNER_ID,
    workspaceId: DEFAULT_WORKSPACE_ID,
    createdBy: DEFAULT_CREATED_BY,
    title: m.title,
    goalSummary: m.goalSummary,
    domain: m.domain,
    currentStage: m.currentUnderlyingStage,
    status: m.taskStatus,
    executionDisplayStatus: m.executionDisplayStatus,
    resultSource: m.resultSource,
    finalResult: m.finalResult,
    stages: [...STAGE_ORDER],
    artifacts: m.artifacts.map((a) => ({
      id: a.id,
      ownerId: DEFAULT_OWNER_ID,
      workspaceId: DEFAULT_WORKSPACE_ID,
      createdBy: DEFAULT_CREATED_BY,
      kind: a.type,
      title: a.title,
      createdAt: m.updatedAt,
      trustRecords: a.trustRecords ?? [],
    })),
    confirmations: [],
    capabilityCalls: m.capabilityCalls.map((c) => ({
      id: c.id,
      ownerId: DEFAULT_OWNER_ID,
      workspaceId: DEFAULT_WORKSPACE_ID,
      createdBy: DEFAULT_CREATED_BY,
      kind: "in_app_agent",
      name: c.name,
      status: mapFixtureCallStatus(c.status),
      requestedAt: c.startedAt ?? m.createdAt,
      finishedAt: c.endedAt ?? undefined,
    })),
    trustRecords: m.trustRecords ?? [],
  };
}
