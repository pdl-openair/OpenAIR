import { App } from "antd";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { PageHeaderProvider } from "../../../context/PageHeaderContext";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  useWorkbenchTaskModel: vi.fn(),
  reload: vi.fn(),
  updateTaskPlan: vi.fn(),
  agentReviseResearchDesign: vi.fn(),
  fetchTask: vi.fn(),
  confirmResearchDesign: vi.fn(),
  startTask: vi.fn(),
  fetchResearchStartSession: vi.fn(),
  fetchResearchStartDisplayContext: vi.fn(),
  fetchTaskExecutionPolicy: vi.fn(),
  durableRefresh: vi.fn(),
  collaborationRefresh: vi.fn(),
}));

vi.mock("../../../hooks/useWorkbenchTaskModel", () => ({
  useWorkbenchTaskModel: mocks.useWorkbenchTaskModel,
}));
vi.mock("../../../hooks/useDurableRunProjection", () => ({
  useDurableRunProjection: () => ({ run: null, refresh: mocks.durableRefresh }),
}));
vi.mock("../../../hooks/useCollaborationProjection", () => ({
  useCollaborationProjection: () => ({
    projection: null,
    refresh: mocks.collaborationRefresh,
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
  WorkbenchFlowNav: () => <div data-testid="workbench-flow-nav-stub" />,
}));
vi.mock("../../../components/artifacts/ArtifactVersionDrawer", () => ({
  ArtifactVersionDrawer: () => null,
}));
vi.mock("../../../components/workbench/DraftPlanEditorModal", () => ({
  DraftPlanEditorModal: () => null,
}));
vi.mock("../../../api/researchStartDisplayContext", () => ({
  fetchResearchStartDisplayContext: mocks.fetchResearchStartDisplayContext,
}));
vi.mock("../../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../../api/client")>()),
  updateTaskPlan: mocks.updateTaskPlan,
  agentReviseResearchDesign: mocks.agentReviseResearchDesign,
  fetchTask: mocks.fetchTask,
  confirmResearchDesign: mocks.confirmResearchDesign,
  startTask: mocks.startTask,
  fetchResearchStartSession: mocks.fetchResearchStartSession,
  fetchTaskExecutionPolicy: mocks.fetchTaskExecutionPolicy,
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

import { formingWorkbenchTaskModel } from "../../../model/workbenchTask";

const TASK_ID = "task-context-one";
const SESSION_ID = "intent_123456abcdef";
const namedModel = () => ({
  ...formingWorkbenchTaskModel({ taskId: TASK_ID, title: "用户短名" }),
  goalSummary:
    "生成的研究目标比用户命名长很多，也不能拿它替换原文或者改写项目名。",
});
let currentModel = namedModel();
const secondModel = formingWorkbenchTaskModel({
  taskId: "task-context-two",
  title: "第二项研究",
});

function NextTask() {
  const navigate = useNavigate();
  return (
    <button onClick={() => navigate("/workbench/task-context-two")}>
      切换研究
    </button>
  );
}
function Tree({ planning = false }: { planning?: boolean }) {
  return (
    <MemoryRouter
      initialEntries={[
        `/workbench/${TASK_ID}${planning ? `?planningSessionId=${SESSION_ID}` : ""}`,
      ]}
    >
      <App>
        <PageHeaderProvider>
          <Routes>
            <Route
              path="/workbench/:taskId"
              element={
                <>
                  <WorkbenchPage />
                  <NextTask />
                </>
              }
            />
          </Routes>
        </PageHeaderProvider>
      </App>
    </MemoryRouter>
  );
}
const context = (originalInput: string | null, overrides = {}) => ({
  taskId: TASK_ID,
  sessionId: SESSION_ID,
  originalInput,
  displayTitle: "用户短名",
  titlePending: false,
  ...overrides,
});

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  localStorage.clear();
  currentModel = namedModel();
  mocks.useWorkbenchTaskModel.mockImplementation((taskId: string) => ({
    status: "ready",
    source: "api",
    model: taskId === TASK_ID ? currentModel : secondModel,
    reload: mocks.reload,
    silentReload: mocks.reload,
    isRefreshing: false,
    progressStreamStale: false,
  }));
  mocks.fetchResearchStartDisplayContext.mockResolvedValue(context("完整原文"));
  mocks.fetchTaskExecutionPolicy.mockImplementation(async (taskId: string) => ({
    taskId,
    currentMode: "automatic_research",
    pendingMode: null,
  }));
  mocks.fetchResearchStartSession.mockResolvedValue({
    sessionId: SESSION_ID,
    reservedTaskId: TASK_ID,
    status: "running",
    currentPhase: "planning",
    planningProgress: {
      code: "planning_design",
      message: "正在组织研究方案。",
      elapsedSeconds: 4,
    },
  });
});
afterEach(cleanup);

it.each([
  "@科研证据综合分析 研究轨迹评测证据\n保留全部原文和换行",
  "@工艺优化 比较PVD材料工艺\n保留全部原文和换行",
  "@论文复现 研究CS实验复现\n保留全部原文和换行",
  "研究城市内涝预测的证据边界\n自由输入不含Workflow",
])(
  "reads submitted input independently of generated goals and keeps saved name: %s",
  async (raw) => {
    mocks.fetchResearchStartDisplayContext.mockResolvedValue(context(raw));
    render(<Tree />);
    const details = await screen.findByTestId("workbench-original-input");
    expect(details.querySelector(".ant-typography")?.textContent).toBe(raw);
    expect(screen.getByText("用户短名")).toBeVisible();
    expect(details).not.toHaveTextContent(currentModel.goalSummary);
    expect(mocks.fetchResearchStartDisplayContext).toHaveBeenCalledWith(
      TASK_ID,
      null,
    );
  },
);

it("keeps input expanded while the server updates generated goals", async () => {
  const view = render(<Tree />);
  const details = (await screen.findByTestId(
    "workbench-original-input",
  )) as HTMLDetailsElement;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  currentModel = {
    ...currentModel,
    revision: 1,
    goalSummary: "更新后的生成目标",
  };
  view.rerender(<Tree />);
  await waitFor(() =>
    expect(screen.getByTestId("workbench-original-input")).toBe(details),
  );
  expect(details.open).toBe(true);
  expect(details.querySelector(".ant-typography")?.textContent).toBe(
    "完整原文",
  );
});

it("distinguishes missing historical input from unavailable reads without substituting goal", async () => {
  mocks.fetchResearchStartDisplayContext.mockResolvedValue(context(null));
  const first = render(<Tree />);
  expect(
    await screen.findByTestId("workbench-original-input-notice"),
  ).toHaveTextContent("该历史版本未保存原始输入");
  first.unmount();
  mocks.fetchResearchStartDisplayContext.mockRejectedValue(
    new Error("temporary read failure"),
  );
  render(<Tree />);
  await waitFor(() =>
    expect(
      screen.getByTestId("workbench-original-input-notice"),
    ).toHaveTextContent("暂时无法读取"),
  );
});

it("uses only the pending input matching both current task and session while server read is unavailable", async () => {
  sessionStorage.setItem(
    "openair.researchStart.pending.v2",
    JSON.stringify({
      status: "accepted",
      startedAt: Date.now(),
      input: "本次已提交原文",
      clientRequestId: "request-one",
      intentFingerprint: "query-one",
      sessionId: SESSION_ID,
      reservedTaskId: TASK_ID,
    }),
  );
  mocks.fetchResearchStartDisplayContext.mockRejectedValue(
    new Error("temporarily unavailable"),
  );
  render(<Tree planning />);
  await waitFor(() =>
    expect(screen.getByTestId("workbench-original-input")).toHaveTextContent(
      "本次已提交原文",
    ),
  );
});

it("does not apply an old task context response after navigation", async () => {
  let resolveOld!: (value: ReturnType<typeof context>) => void;
  mocks.fetchResearchStartDisplayContext.mockImplementation((taskId: string) =>
    taskId === TASK_ID
      ? new Promise((resolve) => {
          resolveOld = resolve;
        })
      : Promise.resolve(
          context("第二项研究的原文", {
            taskId,
            sessionId: "intent_abcdef123456",
            displayTitle: "第二项研究",
          }),
        ),
  );
  render(<Tree />);
  await waitFor(() =>
    expect(mocks.fetchResearchStartDisplayContext).toHaveBeenCalled(),
  );
  fireEvent.click(screen.getByRole("button", { name: "切换研究" }));
  expect(
    await screen.findByTestId("workbench-original-input"),
  ).toHaveTextContent("第二项研究的原文");
  await act(async () => resolveOld(context("旧研究的迟到原文")));
  expect(screen.getByTestId("workbench-original-input")).not.toHaveTextContent(
    "旧研究的迟到原文",
  );
  expect(screen.getByText("第二项研究")).toBeVisible();
});
