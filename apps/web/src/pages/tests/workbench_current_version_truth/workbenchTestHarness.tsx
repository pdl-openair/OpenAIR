import { App } from "antd";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { PageHeaderProvider } from "../../../context/PageHeaderContext";
import { beforeEach, expect, vi } from "vitest";
import {
  positiveInteger,
  nonNegativeInteger,
  awareDateTime,
  type ResearchDesignVersion,
  type Task,
} from "@openair/shared";

const hoistedMocks = vi.hoisted(() => ({
  useWorkbenchTaskModel: vi.fn(),
  reload: vi.fn(),
  updateTaskPlan: vi.fn(),
  agentReviseResearchDesign: vi.fn(),
  fetchTask: vi.fn(),
  confirmResearchDesign: vi.fn(),
  startTask: vi.fn(),
  fetchResearchStartSession: vi.fn(),
  fetchTaskExecutionPolicy: vi.fn(),
  fetchPackStageCatalog: vi.fn(),
  durableRefresh: vi.fn(),
  collaborationRefresh: vi.fn(),
}));

vi.mock("../../../hooks/useWorkbenchTaskModel", () => ({
  useWorkbenchTaskModel: hoistedMocks.useWorkbenchTaskModel,
}));
vi.mock("../../../hooks/useDurableRunProjection", () => ({
  useDurableRunProjection: () => ({
    run: null,
    refresh: hoistedMocks.durableRefresh,
  }),
}));
vi.mock("../../../hooks/useCollaborationProjection", () => ({
  useCollaborationProjection: () => ({
    projection: null,
    refresh: hoistedMocks.collaborationRefresh,
  }),
}));
vi.mock("../../../hooks/useTaskRecoveryProjection", () => ({
  useTaskRecoveryProjection: () => null,
}));
vi.mock("../../../hooks/useWorkbenchUserActions", () => ({
  useWorkbenchUserActions: () => ({
    lifecycleKind: "canonical",
    snapshot: {
      allowedActions: [],
      executionStopped: false,
      unrecoverableMessage: null,
    },
    items: [],
    controller: {
      busy: false,
      intent: null,
      receipt: null,
      operationState: "idle",
      reconcile: vi.fn(),
    },
    operationState: () => ({ state: "idle" }),
    explicitRetry: vi.fn(),
    resume: vi.fn(),
    stop: vi.fn(),
    submitSupplement: vi.fn(),
  }),
}));
vi.mock("../../../context/AuthContext", () => ({
  useAuth: () => ({ logout: vi.fn() }),
}));
vi.mock("../../../api/agent", () => ({
  fetchAgentMessages: vi.fn().mockResolvedValue({ messages: [] }),
}));
vi.mock("../../../components/workbench/ResearchActivityStream", () => ({
  ResearchActivityStream: ({
    children,
  }: {
    children: (value: unknown) => ReactNode;
  }) =>
    children({
      facts: [],
      summary: null,
      connection: null,
      renderFact: () => null,
    }),
}));
vi.mock("../../../components/workbench/WorkbenchFlowNav", () => ({
  WorkbenchFlowNav: ({
    selectedSubStage,
    onSelectSubStage,
  }: {
    selectedSubStage: string | null;
    onSelectSubStage: (id: string) => void;
  }) => (
    <div
      data-testid="workbench-flow-nav-stub"
      data-selected-substage={selectedSubStage ?? ""}
    >
      <button onClick={() => onSelectSubStage("old-node")}>查看旧节点</button>
    </div>
  ),
}));
vi.mock("../../../components/artifacts/ArtifactVersionDrawer", () => ({
  ArtifactVersionDrawer: () => null,
}));
vi.mock("../../../components/workbench/DraftPlanEditorModal", () => ({
  DraftPlanEditorModal: () => null,
}));
vi.mock("../../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../../api/client")>()),
  updateTaskPlan: hoistedMocks.updateTaskPlan,
  agentReviseResearchDesign: hoistedMocks.agentReviseResearchDesign,
  fetchTask: hoistedMocks.fetchTask,
  confirmResearchDesign: hoistedMocks.confirmResearchDesign,
  startTask: hoistedMocks.startTask,
  fetchResearchStartSession: hoistedMocks.fetchResearchStartSession,
  fetchTaskExecutionPolicy: hoistedMocks.fetchTaskExecutionPolicy,
  fetchPackStageCatalog: hoistedMocks.fetchPackStageCatalog,
}));

// This Suite owns confirmation identity; unavailable startup queries are
// controlled dependencies, not evidence of successful backend execution.
vi.mock("../../../api/taskStartStatus", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../../api/taskStartStatus")>()),
  fetchCurrentTaskStartStatus: vi
    .fn()
    .mockRejectedValue(
      new Error("start authority unavailable in version suite"),
    ),
  fetchTaskStartStatus: vi
    .fn()
    .mockRejectedValue(new Error("start receipt unavailable in version suite")),
}));

import WorkbenchPage from "../../WorkbenchPage";
import {
  workbenchModelFromApiTask,
  type WorkbenchTaskModel,
} from "../../../model/workbenchTask";

export const TASK_ID = "task-rdx2-current-version";
export const DESIGN_ID = "design-rdx2-current-version";
export const V1_GOAL = "V1：比较现有路线并确定实验边界。";
export const MANUAL_V2_GOAL = "V2（手动）：补充可复现基线与消融实验。";
export const AGENT_V2_GOAL = "V2（Agent）：增加证据覆盖与引用可靠性评估。";
export const V1_WORK = "梳理代表性方法与初始比较维度。";
export const MANUAL_V2_WORK = "梳理代表性方法，并加入可复现基线与消融设计。";
export const AGENT_V2_WORK = "梳理代表性方法，并加入证据覆盖与引用可靠性评估。";
export const RESEARCH_QUESTION = "现有路线在一致评价口径下如何比较？";
export const TARGET_OUTPUT = "准确性、成本与可复现性对照";
export const PLANNING_RATIONALE =
  "先统一评价口径，再组织代表性方法和可复现基线。";
export const EVIDENCE_BOUNDARY =
  "当前方案不代表已经完成实验或获得最终科研结论。";
export const RISK_NOTE = "仍需通过正式执行验证研究结论。";

export function designVersion(
  version: number,
  status: "draft" | "superseded",
  goal: string,
  objective: string,
): ResearchDesignVersion {
  return {
    researchDesignId: DESIGN_ID,
    taskId: TASK_ID,
    version: positiveInteger(version),
    supersedesVersion: version === 1 ? null : positiveInteger(version - 1),
    status,
    planningMode: "sdr",
    planningComplexity: {
      problemClarity: "high",
      expectedStageSpan: "multiple",
      dependencyComplexity: "low",
      evidenceBurden: "medium",
      externalActionRisk: "low",
      researchUncertainty: "medium",
      rationale: "比较研究路线并验证证据。",
    },
    goal,
    understanding: {
      researchType: null,
      userIntent: "比较已有研究路线",
      materialSummary: null,
      primaryResearchQuestions: [RESEARCH_QUESTION],
      auxiliaryResearchDimensions: [],
      targetOutputs: [TARGET_OUTPUT],
      missingInputs: [],
      planningRationale: PLANNING_RATIONALE,
      evidenceBoundaries: [EVIDENCE_BOUNDARY],
      riskNotes: [RISK_NOTE],
    },
    constraints: [],
    scopeBoundary: {
      allowedObjectiveKinds: [],
      excludedActions: [],
      allowedMaterialRefs: [],
      maxSubstageInstances: null,
    },
    budgetBoundary: {
      maxModelCalls: null,
      maxToolCalls: null,
      maxWallTimeSec: null,
      maxCostAmount: null,
      currency: null,
    },
    externalActionBoundary: {
      allowedActionKinds: [],
      confirmationRequiredKinds: [],
      allowedDestinations: [],
    },
    riskBoundary: {
      maxRiskClass: "low",
      prohibitedActions: [],
      humanGateTriggers: [],
    },
    workflowProvenanceRef: null,
    projectCompletionRequirements: [],
    instances: [
      {
        substageInstanceId: "instance-1",
        instanceRevision: positiveInteger(version),
        substageDefinitionId: "question_refinement",
        substageDefinitionVersion: positiveInteger(1),
        stageKey: "problem_modeling",
        objective,
        inputRefs: [],
        expectedArtifactTypes: ["research_question"],
        acceptanceCriteria: [],
        dependencies: [],
        sequenceHint: nonNegativeInteger(0),
        repeatableReentryAuthority: null,
      },
    ],
    coverageReport: null,
    createdAt: awareDateTime(`2026-08-12T0${version}:00:00Z`),
    confirmedAt: null,
    confirmationRef: null,
  };
}

export function apiTask({
  revision,
  version,
  goal,
  objective,
}: {
  revision: number;
  version: 1 | 2;
  goal: string;
  objective: string;
}): Task {
  const v1 = designVersion(
    1,
    version === 1 ? "draft" : "superseded",
    V1_GOAL,
    V1_WORK,
  );
  const versions =
    version === 1 ? [v1] : [v1, designVersion(2, "draft", goal, objective)];
  return {
    taskId: TASK_ID,
    ownerId: "owner-rdx2",
    workspaceId: "workspace-rdx2",
    createdBy: "user-rdx2",
    title: "RD-X2 current-version behavior",
    revision,
    goalSummary: goal,
    domain: "research_task",
    currentStage: "problem_modeling",
    status: "draft",
    resultSource: "live",
    executionMode: "stage_confirmation",
    stageGateResumeApproved: false,
    creationStatus: "awaiting_confirmation",
    creationPlan: {
      goalSummary: goal,
      domain: "research_task",
      planningSource: "research_design_v1_2",
      understanding: {
        primaryResearchQuestions: [RESEARCH_QUESTION],
        targetOutputs: [TARGET_OUTPUT],
        planningRationale: PLANNING_RATIONALE,
        evidenceBoundaries: [EVIDENCE_BOUNDARY],
        riskNotes: [RISK_NOTE],
      },
      clarifications: [],
      readyToConfirm: true,
      stages: [
        {
          stageKey: "problem_modeling",
          title: "问题建模",
          description: "明确研究问题与评价边界。",
          subStages: ["question_refinement"],
          substages: [
            {
              instanceId: "instance-1",
              substage: "question_refinement",
              label: "明确研究问题",
              action: objective,
              expectedArtifact: "research_question",
            },
          ],
        },
      ],
    },
    researchDesignVersions: versions,
    createdAt: "2026-08-12T00:00:00Z",
    updatedAt: `2026-08-12T0${version}:10:00Z`,
    stages: [
      "problem_modeling",
      "solution_design",
      "execution_validation",
      "insight_generation",
    ],
    stageDetails: [],
    artifacts: [],
    confirmations: [],
    capabilityCalls: [],
    trustRecords: [],
    userVisibleEvents: [],
  };
}

export const V1_TASK = apiTask({
  revision: 41,
  version: 1,
  goal: V1_GOAL,
  objective: V1_WORK,
});
export const MANUAL_V2_TASK = apiTask({
  revision: 42,
  version: 2,
  goal: MANUAL_V2_GOAL,
  objective: MANUAL_V2_WORK,
});
export const AGENT_V2_TASK = apiTask({
  revision: 52,
  version: 2,
  goal: AGENT_V2_GOAL,
  objective: AGENT_V2_WORK,
});

function RouteProbe() {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <div>
      <output data-testid="workbench-route-probe">{`${location.pathname}${location.search}`}</output>
      <button
        type="button"
        data-testid="workbench-route-back"
        onClick={() => navigate(-1)}
      >
        back
      </button>
      <button
        type="button"
        data-testid="workbench-route-forward"
        onClick={() => navigate(1)}
      >
        forward
      </button>
    </div>
  );
}

export function renderWorkbench(
  initialTask: Task = V1_TASK,
  options: {
    tasks?: Task[];
    initialEntries?: string[];
    initialIndex?: number;
  } = {},
) {
  const tasks = options.tasks ?? [initialTask];
  const loadStates = new Map(
    tasks.map((task) => [
      task.taskId,
      {
        status: "ready" as const,
        model: workbenchModelFromApiTask(task),
        source: "api" as const,
        reload: hoistedMocks.reload,
        silentReload: vi.fn(),
        isRefreshing: false,
        progressStreamStale: false,
      },
    ]),
  );
  const initialLoadState = loadStates.get(initialTask.taskId)!;
  hoistedMocks.useWorkbenchTaskModel.mockImplementation(
    (requestedTaskId: string | undefined) =>
      loadStates.get(requestedTaskId ?? "") ?? initialLoadState,
  );
  const tree = () => (
    <MemoryRouter
      initialEntries={
        options.initialEntries ?? [`/workbench/${initialTask.taskId}`]
      }
      initialIndex={options.initialIndex}
    >
      <App>
        <PageHeaderProvider>
          <Routes>
            <Route
              path="/workbench/:taskId"
              element={
                <>
                  <WorkbenchPage />
                  <RouteProbe />
                </>
              }
            />
          </Routes>
        </PageHeaderProvider>
      </App>
    </MemoryRouter>
  );
  const result = render(tree());
  const updateModel = (model: WorkbenchTaskModel) => {
    const previous = loadStates.get(model.taskId);
    if (!previous) throw new Error(`Task not mounted: ${model.taskId}`);
    loadStates.set(model.taskId, { ...previous, model });
    result.rerender(tree());
  };
  return {
    ...result,
    updateModel,
    updateTask(task: Task) {
      updateModel(workbenchModelFromApiTask(task));
    },
  };
}

export async function openResearchDesignEdit() {
  fireEvent.click(
    await within(screen.getByTestId("workbench-center")).findByRole("button", {
      name: "修改研究方案",
    }),
  );
  await screen.findByTestId("workbench-research-design-edit-goal");
}

export async function assertHistoryCurrentVersion(version: number) {
  fireEvent.click(screen.getByText("修改记录"));
  const currentRow = await screen.findByTestId(
    `workbench-research-design-history-version-${version}`,
  );
  expect(within(currentRow).getByText("当前")).toBeVisible();
}

export async function exitCleanEditAndConfirm() {
  fireEvent.click(screen.getByTestId("workbench-draft-complete-edit"));
  fireEvent.click(
    await within(screen.getByTestId("workbench-center")).findByRole("button", {
      name: "确认并开始",
    }),
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  window.sessionStorage.clear();
  hoistedMocks.fetchTaskExecutionPolicy.mockImplementation(
    async (taskId: string) => ({
      taskId,
      currentMode: "stage_confirmation",
      pendingMode: null,
    }),
  );
  hoistedMocks.startTask.mockResolvedValue(undefined);
  hoistedMocks.fetchPackStageCatalog.mockResolvedValue([
    {
      stageKey: "problem_modeling",
      allowedSubStages: [
        {
          key: "question_refinement",
          stageKey: "problem_modeling",
          label: "明确研究问题",
        },
        {
          key: "hypothesis_formation",
          stageKey: "problem_modeling",
          label: "假说形成",
        },
      ],
    },
  ]);
});

export function taskWithArtifact(
  taskId: string,
  artifactId: string,
  artifactTitle: string,
): Task {
  return {
    ...V1_TASK,
    taskId,
    title: `Route identity ${taskId}`,
    revision: taskId === "task-route-a" ? 71 : 72,
    status: "running",
    creationStatus: "confirmed",
    creationPlan: undefined,
    researchDesignVersions: [],
    stageDetails: [],
    artifacts: [
      {
        id: artifactId,
        taskId,
        sourceTaskId: taskId,
        ownerId: "owner-rdx2",
        workspaceId: "workspace-rdx2",
        createdBy: "user-rdx2",
        kind: "markdown",
        artifactType: "research_note",
        title: artifactTitle,
        createdAt: "2026-10-06T08:00:00Z",
        format: "md",
        relatedStage: "solution_design",
        storageType: "inline",
        inlineContent: `# ${artifactTitle}`,
      },
    ],
  };
}

export const mocks = hoistedMocks;
