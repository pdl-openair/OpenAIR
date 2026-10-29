import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import type { Task } from "@openair/shared";
import type { WorkbenchArtifact, WorkbenchTaskModel } from "../model/workbenchTask";

const mocks = vi.hoisted(() => ({
  loadTaskForPages: vi.fn(),
  renameTask: vi.fn(),
  fetchTaskTokenUsage: vi.fn(),
  logout: vi.fn(),
}));

vi.mock("../lib/loadTask", () => ({ loadTaskForPages: mocks.loadTaskForPages }));
vi.mock("../api/client", () => ({
  renameTask: mocks.renameTask,
  fetchTaskTokenUsage: mocks.fetchTaskTokenUsage,
}));
vi.mock("../model/workbenchTask", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../model/workbenchTask")>()),
  workbenchModelFromApiTask: (task: Task) => makeBench({ taskId: task.taskId, title: task.title }),
}));
vi.mock("../context/AppPreferencesContext", () => ({
  useAppPreferences: () => ({ preferences: { language: "zh-CN" } }),
}));
vi.mock("../context/AuthContext", () => ({ useAuth: () => ({ logout: mocks.logout }) }));
vi.mock("../data/recordedRunAdapter", () => ({
  labelForSkill: (value: string) => ({ literature_search: "文献检索", experiment_design: "实验设计" }[value] ?? value),
  labelForSubStage: (value: string) => value,
  isUnderlyingStage: () => false,
  skillTechnicalId: (value: string) => value,
  subStageRowsFromFixture: () => [],
}));

import TaskDetailPage from "./TaskDetailPage";
import { readCurrentTask } from "../lib/userRouteState";

function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    taskId: "task-1",
    ownerId: "owner-1",
    workspaceId: "workspace-1",
    createdBy: "user-1",
    title: "图神经网络小样本研究",
    goalSummary: "比较不同图表示学习方法在少样本条件下的性能。",
    domain: "research_task",
    currentStage: "solution_design",
    status: "running",
    resultSource: "live",
    createdAt: "2026-08-07T10:00:00Z",
    updatedAt: "2026-08-07T11:00:00Z",
    stages: ["problem_modeling", "solution_design", "execution_validation", "insight_generation"],
    stageDetails: [
      {
        stageKey: "problem_modeling",
        status: "completed",
        summary: null,
        risks: null,
        nextSteps: null,
        artifactRefs: ["a1"],
        subStages: [
          {
            subStage: "question_refinement",
            subStageInstanceId: "sub-1",
            label: "明确研究问题",
            status: "completed",
            artifactRefs: ["a1"],
            startedAt: "2026-08-07T10:00:00Z",
            finishedAt: "2026-08-07T10:05:00Z",
          },
        ],
      },
      {
        stageKey: "solution_design",
        status: "running",
        summary: null,
        risks: null,
        nextSteps: null,
        artifactRefs: ["a2"],
        subStages: [
          {
            subStage: "literature_search",
            subStageInstanceId: "sub-2",
            label: "检索相关研究",
            status: "running",
            artifactRefs: ["a2"],
            startedAt: "2026-08-07T10:30:00Z",
          },
        ],
      },
    ],
    artifacts: [],
    confirmations: [],
    capabilityCalls: [
      {
        id: "call-1",
        ownerId: "owner-1",
        workspaceId: "workspace-1",
        createdBy: "user-1",
        kind: "in_app_agent",
        name: "literature_search",
        status: "completed",
        requestedAt: "2026-08-07T10:30:00Z",
        finishedAt: "2026-08-07T10:35:00Z",
        stageKey: "solution_design",
        subStageInstanceId: "sub-2",
        targetAgent: "key_literature_positioning",
      },
    ],
    ...overrides,
  };
}

function artifact(id: string, format: string, subStageInstanceId: string): WorkbenchArtifact {
  return {
    id,
    taskId: "task-1",
    sourceTaskId: "task-1",
    type: format,
    title: `${id} artifact`,
    format,
    relatedStage: subStageInstanceId === "sub-1" ? "problem_modeling" : "solution_design",
    subStageInstanceId,
  };
}

function makeBench(overrides: Partial<WorkbenchTaskModel> = {}): WorkbenchTaskModel {
  return {
    taskId: "task-1",
    ownerId: "owner-1",
    workspaceId: "workspace-1",
    revision: 0,
    title: "图神经网络小样本研究",
    goalSummary: "比较不同图表示学习方法在少样本条件下的性能。",
    domain: "research_task",
    currentUnderlyingStage: "solution_design",
    taskStatus: "running",
    executionDisplayStatus: "running",
    progressTrust: { kind: "legacy" },
    progressRevision: null,
    stageCounts: { completed: 1, total: 4 },
    collaborationNodes: [],
    collaborationEvents: [],
    lifecycleEvents: [],
    resultSource: "live",
    finalResult: null,
    executionMode: "stage_confirmation",
    stageGateResumeApproved: false,
    researchDesign: null,
    createdAt: "2026-08-07T10:00:00Z",
    updatedAt: "2026-08-07T11:00:00Z",
    stages: [
      {
        stage: "problem_modeling",
        label: "问题建模",
        pagePhase: "problem",
        pagePhaseLabel: "问题",
        status: "completed",
        summary: null,
        risks: null,
        nextSteps: null,
        artifactRefs: ["a1"],
        subStages: [{ id: "sub-1", subStage: "question_refinement", label: "明确研究问题", status: "completed", artifactRefs: ["a1"] }],
      },
      {
        stage: "solution_design",
        label: "方案设计",
        pagePhase: "method",
        pagePhaseLabel: "方法",
        status: "running",
        summary: null,
        risks: null,
        nextSteps: null,
        artifactRefs: ["a2"],
        subStages: [{ id: "sub-2", subStage: "literature_search", label: "检索相关研究", status: "running", artifactRefs: ["a2"] }],
      },
      {
        stage: "execution_validation",
        label: "执行验证",
        pagePhase: "experiment",
        pagePhaseLabel: "实验",
        status: "not_started",
        summary: null,
        risks: null,
        nextSteps: null,
        artifactRefs: [],
        subStages: [],
      },
      {
        stage: "insight_generation",
        label: "结果总结",
        pagePhase: "report",
        pagePhaseLabel: "报告",
        status: "not_started",
        summary: null,
        risks: null,
        nextSteps: null,
        artifactRefs: [],
        subStages: [],
      },
    ],
    artifacts: [artifact("a1", "md", "sub-1"), artifact("a2", "pdf", "sub-2")],
    capabilityCalls: [
      {
        id: "call-1",
        name: "literature_search",
        status: "completed",
        startedAt: "2026-08-07T10:30:00Z",
        endedAt: "2026-08-07T10:35:00Z",
        stageKey: "solution_design",
        subStageInstanceId: "sub-2",
        targetAgent: "key_literature_positioning",
      },
    ],
    trustRecords: [],
    userVisibleEvents: [],
    ...overrides,
  };
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location-probe">{`${location.pathname}${location.search}`}</div>;
}

async function renderLoaded(task = makeTask(), model = makeBench()) {
  mocks.loadTaskForPages.mockResolvedValue({ task, model, source: "api" });
  render(
    <MemoryRouter initialEntries={[`/tasks/${task.taskId}`]}>
      <Routes>
        <Route path="/tasks/:taskId" element={<TaskDetailPage />} />
        <Route path="*" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
  await screen.findByText(task.title);
}

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  Object.defineProperty(globalThis, "ResizeObserver", {
    writable: true,
    value: class { observe() {} unobserve() {} disconnect() {} },
  });
});

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  mocks.fetchTaskTokenUsage.mockResolvedValue({ totalTokens: 129000 });
});
afterEach(() => cleanup());

describe("TaskDetailPage compact overview", () => {
  it("records an authoritatively loaded deep link as the current durable Task", async () => {
    await renderLoaded();

    expect(readCurrentTask()).toBe("task-1");
  });

  it("renders task context with the locked four-stage workflow", async () => {
    await renderLoaded();
    expect(screen.getByText("工作流")).toBeInTheDocument();
    expect(screen.getByText("项目统计")).toBeInTheDocument();
    // Overview "当前阶段" and workflow headers may share the same stage label.
    expect(screen.getAllByText("1 问题建模").length).toBeGreaterThan(0);
    expect(screen.getAllByText("2 方案设计").length).toBeGreaterThan(0);
    expect(screen.getAllByText("3 执行验证").length).toBeGreaterThan(0);
    expect(screen.getAllByText("4 洞察生成").length).toBeGreaterThan(0);
  });

  it("opens the current stage by default and shows named artifact entries", async () => {
    await renderLoaded();
    expect(await screen.findByText("检索相关研究")).toBeVisible();
    expect(screen.getAllByTestId("task-detail-artifact-a2")[0]).toHaveTextContent("a2 artifact");
  });

  it("shows the formal Final Result before workflow details when the backend has completed research", async () => {
    const model = makeBench({
      taskStatus: "completed",
      executionDisplayStatus: "completed",
      finalResult: {
        finalResultId: "final-1",
        completionDecisionId: "decision-1",
        scientificStateVersion: 2,
        disposition: "partial",
        directAnswer: null,
        keyFindings: [],
        evidenceRefs: [],
        scientificResult: { conclusion: "Current accepted evidence supports route A." },
        scientificSourceRefs: ["a1"],
        uncertainty: ["Long-term stability remains unknown."],
        gaps: ["One condition remains untested."],
        limitations: ["Applies to the confirmed scope."],
        acceptedScientificFacts: {},
        validatedAt: "2026-09-15T02:00:00Z",
        validationOutcome: {
          contractVersion: "scientific_completion_integrity_v3",
          validatorRef: "core:scientific_completion_integrity:v3",
          status: "passed",
          checkIds: ["scientific_state_snapshot"],
          validatedAt: "2026-09-15T02:00:00Z",
        },
      },
    });
    await renderLoaded(makeTask({ status: "completed" }), model);

    const finalResult = await screen.findByTestId("final-result-panel");
    const workflow = screen.getByRole("heading", { name: "工作流" });
    expect(finalResult.compareDocumentPosition(workflow) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(finalResult).toHaveTextContent("最终科研结果");
    expect(finalResult).toHaveTextContent("当前已接受的研究交付");
    expect(finalResult).toHaveTextContent("部分结果");
    expect(finalResult).not.toHaveTextContent("a1 artifact");
  });


  it("shows the locked project statistics from real available fields", async () => {
    await renderLoaded();
    expect(await screen.findByTestId("task-detail-total-tokens")).toHaveTextContent("12.9万");
    expect(screen.getByTestId("task-detail-stage-durations")).toHaveTextContent("问题建模");
    expect(screen.getByTestId("task-detail-all-artifacts")).toHaveTextContent("2");
    expect(screen.getByTestId("task-detail-skills-summary")).toHaveTextContent("1");
    expect(screen.getByTestId("task-detail-agents-summary")).toHaveTextContent("1");
    expect(screen.queryByText("研究进展")).not.toBeInTheDocument();
  });

  it("keeps the four-stage frame when only real planned stages contain substages", async () => {
    const model = makeBench({ stages: makeBench().stages.slice(0, 2) });
    await renderLoaded(makeTask(), model);

    expect(screen.getAllByText("1 问题建模").length).toBeGreaterThan(0);
    expect(screen.getAllByText("2 方案设计").length).toBeGreaterThan(0);
    expect(screen.getAllByText("3 执行验证").length).toBeGreaterThan(0);
    expect(screen.getAllByText("4 洞察生成").length).toBeGreaterThan(0);
    expect(screen.getAllByText("暂无阶段记录")).toHaveLength(2);
  });

  it("sends a concrete artifact to the filtered artifact library", async () => {
    await renderLoaded();
    fireEvent.click(screen.getAllByTestId("task-detail-artifact-a2")[0]!);
    expect(await screen.findByTestId("location-probe")).toHaveTextContent("/artifacts?sourceTaskId=task-1&artifactId=a2");
  });

  it("sends the total artifact metric to the current-task artifact library", async () => {
    await renderLoaded();
    fireEvent.click(screen.getByTestId("task-detail-all-artifacts"));
    expect(await screen.findByTestId("location-probe")).toHaveTextContent("/artifacts?sourceTaskId=task-1");
  });

  it("opens a user-facing Skills detail modal from project statistics", async () => {
    await renderLoaded();
    fireEvent.click(screen.getByTestId("task-detail-skills-summary"));
    expect(await screen.findByText("技能使用明细")).toBeInTheDocument();
    expect(screen.getByText("文献检索")).toBeInTheDocument();
    expect(screen.getByText("使用 1 次")).toBeInTheDocument();
  });

  it("opens a user-facing Agents detail modal without exposing internal agent ids", async () => {
    await renderLoaded();
    fireEvent.click(screen.getByTestId("task-detail-agents-summary"));
    expect(await screen.findByText("智能体使用明细")).toBeInTheDocument();
    expect(screen.getByText("文献检索 智能体")).toBeInTheDocument();
    expect(screen.queryByText("key_literature_positioning")).not.toBeInTheDocument();
  });
});

describe("TaskDetailPage rename", () => {
  it("keeps one rename request when Enter is followed by blur", async () => {
    mocks.renameTask.mockResolvedValue(makeTask({ title: "新标题" }));
    await renderLoaded();
    fireEvent.click(screen.getByTestId("task-detail-edit-title"));
    const input = screen.getByTestId("task-detail-title-input");
    fireEvent.change(input, { target: { value: "新标题" } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.blur(input);
    await waitFor(() => expect(mocks.renameTask).toHaveBeenCalledTimes(1));
  });

  it("does not rename after Escape", async () => {
    await renderLoaded();
    fireEvent.click(screen.getByTestId("task-detail-edit-title"));
    const input = screen.getByTestId("task-detail-title-input");
    fireEvent.change(input, { target: { value: "不要保存" } });
    fireEvent.keyDown(input, { key: "Escape" });
    fireEvent.blur(input);
    expect(mocks.renameTask).not.toHaveBeenCalled();
  });
});
