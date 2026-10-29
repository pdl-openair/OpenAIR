import type { Task } from "@openair/shared";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { workbenchModelFromApiTask } from "../../../model/workbenchTask";

const mocks = vi.hoisted(() => ({ load: vi.fn(), usage: vi.fn() }));
vi.mock("../../../lib/loadTask", () => ({ loadTaskForPages: mocks.load }));
vi.mock("../../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../../api/client")>()),
  fetchTaskTokenUsage: mocks.usage,
}));
vi.mock("../../../context/AppPreferencesContext", () => ({
  useAppPreferences: () => ({ preferences: { language: "zh-CN" } }),
}));
vi.mock("../../../context/AuthContext", () => ({
  useAuth: () => ({ logout: vi.fn() }),
}));

import TaskDetailPage from "../../TaskDetailPage";

function loaded(taskId: string, title: string) {
  const task: Task = {
    taskId,
    title,
    ownerId: "owner",
    workspaceId: "workspace",
    createdBy: "user",
    revision: 1,
    currentStage: "solution_design",
    status: "running",
    resultSource: "live",
    stages: ["solution_design"],
    artifacts: [],
    confirmations: [],
    capabilityCalls: [],
  };
  return {
    task,
    model: workbenchModelFromApiTask(task),
    source: "api" as const,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function Navigation() {
  const navigate = useNavigate();
  const location = useLocation();
  return (
    <>
      <button onClick={() => navigate("/tasks/task-a")}>切换 A</button>
      <button onClick={() => navigate("/tasks/task-b")}>切换 B</button>
      <output data-testid="current-route">{location.pathname}</output>
    </>
  );
}

function renderPage() {
  render(
    <MemoryRouter initialEntries={["/tasks/task-a"]}>
      <Navigation />
      <Routes>
        <Route path="/tasks/:taskId" element={<TaskDetailPage />} />
        <Route path="/workbench/:taskId" element={<div>工作台</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mocks.load.mockReset();
  mocks.usage.mockResolvedValue({ totalTokens: 0 });
  localStorage.clear();
});

describe("production TaskDetail route lifetime", () => {
  it("late A and B responses cannot overwrite a newer A response or redirect its action", async () => {
    const firstA = deferred<ReturnType<typeof loaded>>();
    const b = deferred<ReturnType<typeof loaded>>();
    const secondA = deferred<ReturnType<typeof loaded>>();
    mocks.load
      .mockReturnValueOnce(firstA.promise)
      .mockReturnValueOnce(b.promise)
      .mockReturnValueOnce(secondA.promise);
    renderPage();
    await waitFor(() => expect(mocks.load).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "切换 B" }));
    await waitFor(() => expect(mocks.load).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole("button", { name: "切换 A" }));
    await waitFor(() => expect(mocks.load).toHaveBeenCalledTimes(3));
    await act(async () => secondA.resolve(loaded("task-a", "最新 A 方案")));
    expect(await screen.findByText("最新 A 方案")).toBeTruthy();
    await act(async () => {
      firstA.resolve(loaded("task-a", "旧 A 方案"));
      b.resolve(loaded("task-b", "其他 B 方案"));
    });
    expect(screen.queryByText("旧 A 方案")).toBeNull();
    expect(screen.queryByText("其他 B 方案")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /进入工作台/ }));
    expect(screen.getByTestId("current-route").textContent).toBe(
      "/workbench/task-a",
    );
  });

  it("late not-found from B cannot erase current A or restore B actions", async () => {
    const b = deferred<null>();
    mocks.load
      .mockResolvedValueOnce(loaded("task-a", "初始 A 方案"))
      .mockReturnValueOnce(b.promise)
      .mockResolvedValueOnce(loaded("task-a", "当前 A 方案"));
    renderPage();
    await screen.findByText("初始 A 方案");
    fireEvent.click(screen.getByRole("button", { name: "切换 B" }));
    await waitFor(() => expect(mocks.load).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("button", { name: /进入工作台/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "切换 A" }));
    await screen.findByText("当前 A 方案");
    await act(async () => b.resolve(null));
    expect(screen.getByText("当前 A 方案")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("button", { name: /进入工作台/ })).toBeTruthy();
  });
});

it("keeps canonical workflow artifact titles without redundant final sources", async () => {
  const result = loaded("task-a", "成果名称验收");
  result.model.artifacts = [
    {
      id: "a",
      taskId: "task-a",
      title: "ot_problem_framing",
      type: "report",
      artifactType: "opentrajectory.problem_framing",
      format: "json",
      relatedStage: "solution_design",
    },
    {
      id: "b",
      taskId: "task-a",
      title: "evidence_search_plan",
      type: "report",
      artifactType: "opentrajectory.retrieval_strategy_design",
      format: "json",
      relatedStage: "solution_design",
    },
  ];
  result.model.stages[0].subStages = [
    {
      id: "sub-a",
      subStage: "literature_search",
      label: "收集研究依据",
      status: "completed",
      artifactRefs: ["a", "b"],
    },
  ];
  result.model.finalResult = {
    finalResultId: "final-a",
    completionDecisionId: "decision-a",
    scientificStateVersion: 1,
    disposition: "full",
    directAnswer: null,
    keyFindings: [],
    evidenceRefs: [],
    scientificResult: {},
    scientificSourceRefs: ["a", "b"],
    uncertainty: [],
    gaps: [],
    limitations: [],
    acceptedScientificFacts: {},
    validatedAt: "2026-10-10T00:00:00Z",
    validationOutcome: {
      contractVersion: "scientific_completion_integrity_v3",
      validatorRef: "core:scientific_completion_integrity:v3",
      status: "passed",
      checkIds: [],
      validatedAt: "2026-10-10T00:00:00Z",
    },
  };
  mocks.load.mockResolvedValue(result);
  renderPage();
  const panel = await screen.findByTestId("final-result-panel");
  expect(panel).not.toHaveTextContent("来源");
  expect(screen.getByTestId("task-detail-artifact-a")).toHaveTextContent(
    "研究问题与边界",
  );
  expect(screen.getByTestId("task-detail-artifact-b")).toHaveTextContent(
    "证据检索计划",
  );
  expect(
    screen.queryByText("研究产物", { exact: true }),
  ).not.toBeInTheDocument();
});
