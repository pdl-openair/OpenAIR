import { App } from "antd";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  positiveInteger,
  nonNegativeInteger,
  type Task,
} from "@openair/shared";
import { StrictMode, type ReactNode } from "react";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PageHeaderProvider } from "../../../context/PageHeaderContext";
import type { CanonicalTaskStartStatus } from "../../../api/taskStartSemantics";

const mocks = vi.hoisted(() => ({
  load: vi.fn(),
  reload: vi.fn(),
  refresh: vi.fn(),
  userRefresh: vi.fn(),
  confirmDesign: vi.fn(),
  confirmPlan: vi.fn(),
  start: vi.fn(),
  currentStatus: vi.fn(),
  receipt: vi.fn(),
  policy: vi.fn(),
  chat: vi.fn(),
  retry: vi.fn(),
  resume: vi.fn(),
  supplement: vi.fn(),
  controllerReceipt: vi.fn(),
}));

vi.mock("../../../hooks/useWorkbenchTaskModel", () => ({
  useWorkbenchTaskModel: mocks.load,
}));
vi.mock("../../../hooks/useDurableRunProjection", () => ({
  useDurableRunProjection: () => ({ run: null, refresh: mocks.refresh }),
}));
vi.mock("../../../hooks/useCollaborationProjection", () => ({
  useCollaborationProjection: () => ({
    projection: null,
    refresh: mocks.refresh,
  }),
}));
vi.mock("../../../hooks/useTaskRecoveryProjection", () => ({
  useTaskRecoveryProjection: () => null,
}));
vi.mock("../../../hooks/useWorkbenchUserActions", () => {
  const actions = {
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
      get receipt() {
        return mocks.controllerReceipt();
      },
      operationState: "idle",
      reconcile: mocks.refresh,
    },
    operationState: () => ({ state: "idle" }),
    explicitRetry: mocks.retry,
    resume: mocks.resume,
    stop: vi.fn(),
    submitSupplement: mocks.supplement,
    refresh: mocks.userRefresh,
  };
  return { useWorkbenchUserActions: () => actions };
});
vi.mock("../../../context/AuthContext", () => ({
  useAuth: () => ({ logout: vi.fn() }),
}));
vi.mock("../../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../../api/client")>()),
  confirmResearchDesign: mocks.confirmDesign,
  confirmTaskPlan: mocks.confirmPlan,
  startTask: mocks.start,
  fetchTaskExecutionPolicy: mocks.policy,
}));
vi.mock("../../../api/taskStartStatus", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../../api/taskStartStatus")>()),
  fetchCurrentTaskStartStatus: mocks.currentStatus,
  fetchTaskStartStatus: mocks.receipt,
}));
vi.mock("../../../api/agent", () => ({
  fetchAgentMessages: vi.fn().mockResolvedValue({ messages: [] }),
  postAgentChatStream: mocks.chat,
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
  WorkbenchFlowNav: () => null,
}));
vi.mock("../../../components/artifacts/ArtifactVersionDrawer", () => ({
  ArtifactVersionDrawer: () => null,
}));
vi.mock("../../../components/workbench/DraftPlanEditorModal", () => ({
  DraftPlanEditorModal: () => null,
}));

import WorkbenchPage from "../../WorkbenchPage";
import { workbenchModelFromApiTask } from "../../../model/workbenchTask";

const TASK_ID = "task-start-safety";
const DESIGN_ID = "design-start-safety";
const REVISION = 41;

function taskInput(confirmed = false, canonical = true): Task {
  const goal = "在确认的范围内比较研究方法。";
  return {
    taskId: TASK_ID,
    ownerId: "owner-start-safety",
    workspaceId: "workspace-start-safety",
    createdBy: "user-start-safety",
    title: "启动安全测试",
    revision: REVISION,
    goalSummary: goal,
    domain: "research_task",
    currentStage: "problem_modeling",
    status: confirmed ? "not_started" : "draft",
    resultSource: "live",
    executionMode: "stage_confirmation",
    creationStatus: confirmed ? "confirmed" : "awaiting_confirmation",
    creationPlan: {
      goalSummary: goal,
      domain: "research_task",
      planningSource: canonical ? "research_design_v1_2" : "llm",
      understanding: {
        primaryResearchQuestions: [goal],
        targetOutputs: ["比较报告"],
      },
      clarifications: [],
      readyToConfirm: true,
      stages: [
        {
          stageKey: "problem_modeling",
          title: "问题建模",
          subStages: ["question_refinement"],
          substages: [
            {
              instanceId: "instance-1",
              substage: "question_refinement",
              label: "明确研究问题",
              action: goal,
              expectedArtifact: "research_question",
            },
          ],
        },
      ],
    },
    researchDesignVersions: canonical
      ? ([
          {
            researchDesignId: DESIGN_ID,
            taskId: TASK_ID,
            version: positiveInteger(1),
            supersedesVersion: null,
            status: confirmed ? "confirmed" : "draft",
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
              userIntent: goal,
              materialSummary: null,
              primaryResearchQuestions: [],
              auxiliaryResearchDimensions: [],
              targetOutputs: [],
              evidenceBoundaries: [],
              missingInputs: [],
              riskNotes: [],
              planningRationale: "形成可验证的研究方案。",
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
                instanceRevision: positiveInteger(1),
                substageDefinitionId: "question_refinement",
                substageDefinitionVersion: positiveInteger(1),
                stageKey: "problem_modeling",
                objective: goal,
                inputRefs: [],
                expectedArtifactTypes: ["research_question"],
                acceptanceCriteria: [],
                dependencies: [],
                sequenceHint: nonNegativeInteger(0),
                repeatableReentryAuthority: null,
              },
            ],
            coverageReport: null,
            createdAt: "2026-10-09T00:00:00Z",
            confirmedAt: confirmed ? "2026-10-09T00:01:00Z" : null,
            confirmationRef: confirmed ? "confirmation-1" : null,
          },
        ] as Task["researchDesignVersions"])
      : [],
    createdAt: "2026-10-09T00:00:00Z",
    updatedAt: "2026-10-09T00:00:00Z",
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

function statusInput(
  overrides: Partial<CanonicalTaskStartStatus> = {},
): CanonicalTaskStartStatus {
  return {
    taskId: TASK_ID,
    taskRevision: REVISION,
    state: "not_committed",
    startAllowed: true,
    responsibility: "USER",
    retryable: true,
    ...overrides,
  };
}

function RouteProbe() {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <output data-testid="supplement-route">{location.search}</output>
      <button
        data-testid="task-B"
        onClick={() => navigate(`/workbench/${TASK_ID}-B`)}
      >
        B
      </button>
      <button
        data-testid="task-A"
        onClick={() => navigate(`/workbench/${TASK_ID}`)}
      >
        A
      </button>
      <button
        data-testid="pin-artifact"
        onClick={() => navigate("?artifact=selected-artifact")}
      >
        artifact
      </button>
    </>
  );
}

function renderWorkbench(
  task = taskInput(),
  source: "api" | "fixture" = "api",
  strictMode = false,
) {
  mocks.load.mockReturnValue({
    status: "ready",
    source,
    model: workbenchModelFromApiTask(task),
    reload: mocks.reload,
    silentReload: mocks.reload,
    isRefreshing: false,
    progressStreamStale: false,
  });
  return render(
    <MemoryRouter initialEntries={[`/workbench/${TASK_ID}`]}>
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
    </MemoryRouter>,
    { wrapper: strictMode ? StrictMode : undefined },
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  window.sessionStorage.clear();
  mocks.refresh.mockResolvedValue(null);
  mocks.controllerReceipt.mockReturnValue(null);
  mocks.userRefresh.mockResolvedValue(null);
  mocks.reload.mockImplementation(() => undefined);
  mocks.policy.mockResolvedValue({
    taskId: TASK_ID,
    currentMode: "stage_confirmation",
    pendingMode: null,
  });
  mocks.currentStatus.mockResolvedValue(statusInput());
  mocks.receipt.mockImplementation(
    async (taskId: string, selectionRequestId: string) => ({
      taskId,
      selectionRequestId,
      state: "committed",
      taskRevision: REVISION,
      executionDecisionId: "decision-1",
    }),
  );
  mocks.confirmDesign.mockResolvedValue(taskInput(true));
  mocks.confirmPlan.mockResolvedValue({
    ...taskInput(true, false),
    revision: REVISION + 1,
  });
  mocks.start.mockResolvedValue(undefined);
  mocks.chat.mockResolvedValue({
    reply: "已收到补充信息。",
    appliedAction: null,
  });
});

describe("Workbench start and operation safety", () => {
  it("StrictMode still permits one confirmed start and reconciles its receipt", async () => {
    renderWorkbench(taskInput(true), "api", true);
    fireEvent.click(
      await screen.findByTestId("workbench-start-confirmed-research"),
    );
    await waitFor(() => expect(mocks.receipt).toHaveBeenCalled());
    expect(mocks.start).toHaveBeenCalledTimes(1);
  });

  it.each(["new draft", "same text"])(
    "a chat acknowledgement cannot clear a later edit (%s)",
    async (later) => {
      const chat = deferred<{ reply: string; appliedAction: null }>();
      mocks.chat.mockReturnValue(chat.promise);
      renderWorkbench();
      const input = await screen.findByTestId("workbench-supplement-draft");
      fireEvent.change(input, { target: { value: "same text" } });
      fireEvent.click(
        screen.getByTestId("workbench-running-submit-controller-supplement"),
      );
      fireEvent.change(input, { target: { value: "intermediate edit" } });
      fireEvent.change(input, { target: { value: later } });
      await act(async () =>
        chat.resolve({ reply: "acknowledged", appliedAction: null }),
      );
      expect(input).toHaveValue(later);
    },
  );

  it("a restored controller receipt cannot erase newly typed identical text", async () => {
    mocks.controllerReceipt.mockReturnValue({
      commandId: "old-command",
      taskId: TASK_ID,
      kind: "supplement_input",
      state: "applied",
      userMessage: "same text",
      submittedTaskRevision: REVISION,
      appliedExecutionDecisionId: null,
      appliedWorkUnitId: null,
      terminalReasonCode: null,
      submittedAt: "2026-10-09T00:00:00Z",
      updatedAt: "2026-10-09T00:00:01Z",
    });
    renderWorkbench();
    const input = await screen.findByTestId("workbench-supplement-draft");
    fireEvent.change(input, { target: { value: "same text" } });
    expect(input).toHaveValue("same text");
    expect(mocks.chat).not.toHaveBeenCalled();
  });

  it("artifact navigation retains the unsent draft", async () => {
    renderWorkbench();
    const current = mocks.load.mock.results[0].value;
    current.model.artifacts = [
      {
        id: "selected-artifact",
        title: "产物",
        type: "analysis_report",
        format: "markdown",
        relatedStage: "problem_modeling",
        content: "已有产物",
      },
    ];
    const input = await screen.findByTestId("workbench-supplement-draft");
    fireEvent.change(input, { target: { value: "未发送约束" } });
    fireEvent.click(screen.getByTestId("pin-artifact"));
    await waitFor(() =>
      expect(screen.getByTestId("supplement-route")).toHaveTextContent(
        "artifact=selected-artifact",
      ),
    );
    expect(input).toHaveValue("未发送约束");
  });

  it("A -> B -> A isolates drafts and ignores the first task session's chat response", async () => {
    const chat = deferred<{ reply: string; appliedAction: null }>();
    mocks.chat.mockReturnValue(chat.promise);
    renderWorkbench();
    const current = mocks.load.mock.results[0].value;
    const other = {
      ...current,
      model: { ...current.model, taskId: `${TASK_ID}-B` },
    };
    mocks.load.mockImplementation((id: string) =>
      id === TASK_ID ? current : other,
    );
    mocks.policy.mockImplementation(async (taskId: string) => ({
      taskId,
      currentMode: "stage_confirmation",
      pendingMode: null,
    }));
    fireEvent.change(await screen.findByTestId("workbench-supplement-draft"), {
      target: { value: "old A" },
    });
    fireEvent.click(
      screen.getByTestId("workbench-running-submit-controller-supplement"),
    );
    fireEvent.click(screen.getByTestId("task-B"));
    expect(await screen.findByTestId("workbench-supplement-draft")).toHaveValue(
      "",
    );
    fireEvent.click(screen.getByTestId("task-A"));
    const input = await screen.findByTestId("workbench-supplement-draft");
    fireEvent.change(input, { target: { value: "new A" } });
    mocks.userRefresh.mockClear();
    await act(async () =>
      chat.resolve({ reply: "old response", appliedAction: null }),
    );
    expect(input).toHaveValue("new A");
    expect(mocks.userRefresh).not.toHaveBeenCalled();
  });

  it("an error-only chat result preserves the draft and does not refresh authority", async () => {
    mocks.chat.mockResolvedValue({
      reply: "",
      error: "send unavailable",
      appliedAction: null,
    });
    renderWorkbench();
    const input = await screen.findByTestId("workbench-supplement-draft");
    fireEvent.change(input, { target: { value: "未发送约束" } });
    mocks.userRefresh.mockClear();
    fireEvent.click(
      screen.getByTestId("workbench-running-submit-controller-supplement"),
    );
    await waitFor(() =>
      expect(
        screen.getByTestId("workbench-running-submit-controller-supplement"),
      ).toBeEnabled(),
    );
    expect(input).toHaveValue("未发送约束");
    expect(mocks.userRefresh).not.toHaveBeenCalled();
  });

  it.each(["automatic_research", "stage_confirmation"])(
    "%s confirms the exact design once and observes backend startup without a second launch",
    async (mode) => {
      mocks.policy.mockResolvedValue({
        taskId: TASK_ID,
        currentMode: mode,
        pendingMode: null,
      });
      mocks.currentStatus.mockResolvedValue(
        statusInput({ state: "committed", startAllowed: false }),
      );
      const confirmation = deferred<Task>();
      mocks.confirmDesign.mockReturnValue(confirmation.promise);
      renderWorkbench();
      const confirm = await screen.findByTestId(
        "workbench-stream-confirm-start",
      );
      expect(
        screen.queryByTestId("workbench-start-confirmed-research"),
      ).not.toBeInTheDocument();
      await waitFor(() => expect(confirm).toBeEnabled());
      expect(
        screen.queryByTestId("workbench-execution-mode-section"),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByTestId("workbench-continue-research"),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByTestId("workbench-collaboration-stop-run"),
      ).not.toBeInTheDocument();
      fireEvent.click(confirm);
      fireEvent.click(confirm);
      expect(mocks.confirmDesign).toHaveBeenCalledTimes(1);
      expect(mocks.confirmDesign).toHaveBeenCalledWith(TASK_ID, {
        designId: DESIGN_ID,
        version: 1,
        confirmationId: expect.any(String),
        decision: "approved",
        expectedTaskRevision: REVISION,
      });
      expect(confirm).toBeDisabled();
      await act(async () => confirmation.resolve(taskInput(true)));
      await waitFor(() =>
        expect(mocks.receipt).toHaveBeenCalledWith(
          TASK_ID,
          `research-design:auto-start:${DESIGN_ID}:v1`,
        ),
      );
      await waitFor(() => expect(mocks.reload).toHaveBeenCalled());
      expect(mocks.start).not.toHaveBeenCalled();
      expect(mocks.confirmPlan).not.toHaveBeenCalled();
    },
  );

  it("a rejected confirmation never starts work or queries an invented start receipt", async () => {
    mocks.confirmDesign.mockRejectedValue(new Error("confirmation rejected"));
    renderWorkbench();
    const confirm = await screen.findByTestId("workbench-stream-confirm-start");
    await waitFor(() => expect(confirm).toBeEnabled());
    fireEvent.click(confirm);
    expect(
      await screen.findByText("草案确认失败，请刷新后重试。"),
    ).toBeVisible();
    expect(mocks.start).not.toHaveBeenCalled();
    expect(mocks.receipt).not.toHaveBeenCalled();
  });

  it("manual mode confirms the design without automatically starting work", async () => {
    mocks.policy.mockResolvedValue({
      taskId: TASK_ID,
      currentMode: "manual_step",
      pendingMode: null,
    });
    renderWorkbench();
    const confirm = await screen.findByTestId("workbench-stream-confirm-start");
    await waitFor(() => expect(confirm).toBeEnabled());
    fireEvent.click(confirm);
    expect(
      await screen.findByText(
        "研究方案已确认。当前推进方式需要手动开始下一步。",
      ),
    ).toBeVisible();
    expect(mocks.confirmDesign).toHaveBeenCalledTimes(1);
    expect(mocks.start).not.toHaveBeenCalled();
    expect(mocks.receipt).not.toHaveBeenCalled();
  });

  it("a confirmed API task starts once with current revision while its request is pending", async () => {
    const start = deferred<void>();
    mocks.start.mockReturnValue(start.promise);
    renderWorkbench(taskInput(true));
    const button = await screen.findByTestId(
      "workbench-start-confirmed-research",
    );
    fireEvent.click(button);
    fireEvent.click(button);
    expect(mocks.start).toHaveBeenCalledTimes(1);
    expect(mocks.start).toHaveBeenCalledWith(
      TASK_ID,
      REVISION,
      expect.any(String),
      "initial_start",
    );
    expect(
      screen.queryByTestId("workbench-start-confirmed-research"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("当前页面只能在已连接 API 的真实任务中开始执行。"),
    ).not.toBeInTheDocument();
    await act(async () => start.resolve());
    await waitFor(() => expect(mocks.reload).toHaveBeenCalled());
    expect(mocks.start).toHaveBeenCalledTimes(1);
  });

  it.each(["blocked", "committed"] as const)(
    "backend %s authority cannot expose a start operation",
    async (state) => {
      const reading = deferred<CanonicalTaskStartStatus>();
      mocks.currentStatus.mockReturnValue(reading.promise);
      renderWorkbench(taskInput(true));
      await waitFor(() =>
        expect(mocks.currentStatus).toHaveBeenCalledWith(TASK_ID),
      );
      expect(
        screen.queryByTestId("workbench-start-confirmed-research"),
      ).not.toBeInTheDocument();
      await act(async () =>
        reading.resolve(
          statusInput({
            state,
            startAllowed: false,
            blockingReasonCode:
              state === "blocked" ? "required_input_missing" : null,
          }),
        ),
      );
      expect(
        screen.queryByTestId("workbench-start-confirmed-research"),
      ).not.toBeInTheDocument();
      expect(mocks.start).not.toHaveBeenCalled();
    },
  );

  it("fixture tasks cannot confirm or start even when task data resembles a real confirmed plan", async () => {
    const draft = renderWorkbench(taskInput(), "fixture");
    expect(
      screen.queryByTestId("workbench-stream-confirm-start"),
    ).not.toBeInTheDocument();
    expect(mocks.confirmDesign).not.toHaveBeenCalled();
    draft.unmount();
    renderWorkbench(taskInput(true), "fixture");
    expect(
      screen.queryByTestId("workbench-start-confirmed-research"),
    ).not.toBeInTheDocument();
    expect(mocks.currentStatus).not.toHaveBeenCalled();
    expect(mocks.start).not.toHaveBeenCalled();
  });

  it("chat pending prevents duplicate sends and retains text until completion", async () => {
    const chat = deferred<{ reply: string; appliedAction: null }>();
    mocks.chat.mockReturnValue(chat.promise);
    renderWorkbench();
    const input = await screen.findByTestId("workbench-supplement-draft");
    fireEvent.change(input, { target: { value: "补充研究约束" } });
    const send = screen.getByTestId(
      "workbench-running-submit-controller-supplement",
    );
    fireEvent.click(send);
    expect(send).toBeDisabled();
    fireEvent.click(send);
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
    expect(mocks.chat).toHaveBeenCalledTimes(1);
    expect(input).toHaveValue("补充研究约束");
    await act(async () =>
      chat.resolve({ reply: "已收到", appliedAction: null }),
    );
    await waitFor(() => expect(input).toHaveValue(""));
    expect(mocks.chat).toHaveBeenCalledTimes(1);
  });

  it.each(["确认并开始", "继续研究", "重试"])(
    "free text '%s' goes to assistant chat without structured operations",
    async (text) => {
      renderWorkbench();
      await waitFor(() => expect(mocks.policy).toHaveBeenCalled());
      mocks.userRefresh.mockClear();
      fireEvent.change(
        await screen.findByTestId("workbench-supplement-draft"),
        { target: { value: text } },
      );
      fireEvent.click(
        screen.getByTestId("workbench-running-submit-controller-supplement"),
      );
      await waitFor(() =>
        expect(mocks.chat).toHaveBeenCalledWith(
          TASK_ID,
          text,
          "workbench_chat",
          expect.any(Function),
        ),
      );
      await waitFor(() =>
        expect(screen.getByTestId("workbench-supplement-draft")).toHaveValue(
          "",
        ),
      );
      expect(mocks.confirmDesign).not.toHaveBeenCalled();
      expect(mocks.confirmPlan).not.toHaveBeenCalled();
      expect(mocks.start).not.toHaveBeenCalled();
      expect(mocks.retry).not.toHaveBeenCalled();
      expect(mocks.resume).not.toHaveBeenCalled();
      expect(mocks.supplement).not.toHaveBeenCalled();
      await waitFor(() => expect(mocks.userRefresh).toHaveBeenCalled());
    },
  );
  it("applied regeneration routes after void reload even when auxiliary refresh fails", async () => {
    mocks.chat.mockResolvedValue({
      reply: "已开始",
      appliedAction: {
        type: "regenerate_research_plan",
        applied: true,
        sessionId: "new-session",
      },
    });
    renderWorkbench();
    await waitFor(() => expect(mocks.policy).toHaveBeenCalled());
    mocks.userRefresh.mockClear();
    mocks.userRefresh.mockRejectedValueOnce(
      new Error("auxiliary refresh failed"),
    );
    fireEvent.change(await screen.findByTestId("workbench-supplement-draft"), {
      target: { value: "重建研究方案" },
    });
    fireEvent.click(
      screen.getByTestId("workbench-running-submit-controller-supplement"),
    );
    await waitFor(() =>
      expect(screen.getByTestId("supplement-route").textContent).toBe(
        "?planningSessionId=new-session",
      ),
    );
    expect(mocks.reload).toHaveBeenCalled();
    expect(mocks.userRefresh).toHaveBeenCalled();
    expect(mocks.chat).toHaveBeenCalledTimes(1);
  });
  it("failed supplement keeps the draft and does not refresh or navigate", async () => {
    mocks.chat.mockRejectedValue(new Error("send failed"));
    renderWorkbench();
    await waitFor(() => expect(mocks.policy).toHaveBeenCalled());
    mocks.userRefresh.mockClear();
    mocks.reload.mockClear();
    const input = await screen.findByTestId("workbench-supplement-draft");
    fireEvent.change(input, { target: { value: "未发送约束" } });
    fireEvent.click(
      screen.getByTestId("workbench-running-submit-controller-supplement"),
    );
    await waitFor(() =>
      expect(
        screen.getByTestId("workbench-running-submit-controller-supplement"),
      ).toBeEnabled(),
    );
    expect(mocks.chat).toHaveBeenCalledTimes(1);
    expect(input).toHaveValue("未发送约束");
    expect(mocks.userRefresh).not.toHaveBeenCalled();
    expect(mocks.reload).not.toHaveBeenCalled();
    expect(screen.getByTestId("supplement-route").textContent).toBe("");
  });
});
