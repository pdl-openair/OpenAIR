import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { positiveInteger, type Task } from "@openair/shared";
import {
  mocks,
  TASK_ID,
  DESIGN_ID,
  V1_GOAL,
  MANUAL_V2_GOAL,
  AGENT_V2_GOAL,
  V1_WORK,
  MANUAL_V2_WORK,
  EVIDENCE_BOUNDARY,
  RISK_NOTE,
  V1_TASK,
  MANUAL_V2_TASK,
  AGENT_V2_TASK,
  designVersion,
  renderWorkbench,
  taskWithArtifact,
  openResearchDesignEdit,
  assertHistoryCurrentVersion,
  exitCleanEditAndConfirm,
} from "./workbenchTestHarness";
import { App } from "antd";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { PageHeaderProvider } from "../../../context/PageHeaderContext";
import WorkbenchPage from "../../WorkbenchPage";
import { workbenchModelFromApiTask } from "../../../model/workbenchTask";

describe("Workbench product layout contract", () => {
  it("keeps the Chinese work title visible when editing its objective", async () => {
    renderWorkbench();
    const work = await screen.findByTestId(
      "workbench-research-design-stage-work-0",
    );
    expect(work).toHaveTextContent("明确研究问题");
    await openResearchDesignEdit();
    expect(work).toHaveTextContent("明确研究问题");
    expect(work).not.toHaveTextContent("研究工作 1.1");
  });

  it("ending and reopening edit eligibility cannot resurrect the old unsaved draft", async () => {
    const show = (task: Task) =>
      mocks.useWorkbenchTaskModel.mockReturnValue({
        status: "ready",
        source: "api",
        model: workbenchModelFromApiTask(task),
        reload: mocks.reload,
        silentReload: mocks.reload,
        isRefreshing: false,
        progressStreamStale: false,
      });
    const tree = () => (
      <MemoryRouter initialEntries={[`/workbench/${TASK_ID}`]}>
        <App>
          <PageHeaderProvider>
            <Routes>
              <Route path="/workbench/:taskId" element={<WorkbenchPage />} />
            </Routes>
          </PageHeaderProvider>
        </App>
      </MemoryRouter>
    );
    show(V1_TASK);
    const view = render(tree());
    await openResearchDesignEdit();
    fireEvent.change(
      screen.getByTestId("workbench-research-design-edit-goal"),
      {
        target: { value: "旧会话未保存的草稿" },
      },
    );
    show({ ...V1_TASK, creationStatus: "confirmed", status: "not_started" });
    view.rerender(tree());
    await waitFor(() =>
      expect(
        screen.queryByTestId("workbench-research-design-edit-goal"),
      ).not.toBeInTheDocument(),
    );
    show(V1_TASK);
    view.rerender(tree());
    await openResearchDesignEdit();
    expect(
      screen.getByTestId("workbench-research-design-edit-goal"),
    ).toHaveValue(V1_GOAL);
    expect(mocks.updateTaskPlan).not.toHaveBeenCalled();
  });

  it("keeps the task bar plus left navigation, center research content, and right collaboration zones", async () => {
    renderWorkbench();

    expect(
      await screen.findByTestId("workbench-task-context"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("workbench-left-panel")).toBeInTheDocument();
    expect(screen.getByTestId("workbench-center")).toBeInTheDocument();
    expect(screen.getByTestId("workbench-right-panel")).toBeInTheDocument();
  });

  it("keeps the main workbench shell while the reserved task is still missing", async () => {
    const taskId = "task-forming-1";
    const sessionId = "session-forming-1";
    mocks.useWorkbenchTaskModel.mockReturnValue({
      status: "error",
      message: "未找到要打开的项目",
      errorKind: "notFound",
      reload: mocks.reload,
      silentReload: vi.fn(),
    });
    mocks.fetchResearchStartSession.mockResolvedValue({
      sessionId,
      status: "running",
      reservedTaskId: taskId,
      planningProgress: {
        code: "planning",
        message: "正在组织研究方案",
        elapsedSeconds: 8,
      },
    });

    render(
      <MemoryRouter
        initialEntries={[`/workbench/${taskId}?planningSessionId=${sessionId}`]}
      >
        <App>
          <PageHeaderProvider>
            <Routes>
              <Route path="/workbench/:taskId" element={<WorkbenchPage />} />
            </Routes>
          </PageHeaderProvider>
        </App>
      </MemoryRouter>,
    );

    expect(
      await screen.findByTestId("workbench-task-context"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("workbench-load-error"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("加载工作台…")).not.toBeInTheDocument();
    expect(
      within(screen.getByTestId("workbench-left-panel")).getByText("研究阶段"),
    ).toBeVisible();
    expect(screen.getByTestId("workbench-flow-nav-stub")).toBeVisible();
    expect(screen.getByTestId("workbench-artifact-empty")).toBeVisible();
    expect(
      screen.getByTestId("workbench-collaboration-input-zone"),
    ).toBeVisible();
    expect(screen.getByTestId("workbench-supplement-draft")).toBeVisible();
  });
});

describe("Workbench Artifact route identity wiring", () => {
  const taskA = taskWithArtifact(
    "task-route-a",
    "artifact-route-a",
    "Task A Artifact",
  );
  const taskB = taskWithArtifact(
    "task-route-b",
    "artifact-route-b",
    "Task B Artifact",
  );

  function followModels() {
    const original = workbenchModelFromApiTask(taskA);
    const makeNode = (id: string) => ({
      id,
      subStage: id,
      label: id,
      status: "completed" as const,
      artifactRefs: [],
    });
    const oldArtifact = {
      ...original.artifacts[0],
      subStageInstanceId: "old-node",
    };
    const newArtifact = {
      ...oldArtifact,
      id: "new-output",
      title: "New output",
      subStageInstanceId: "new-node",
    };
    const oldModel = {
      ...original,
      currentUnderlyingStage: "solution_design" as const,
      stages: original.stages.map((row) =>
        row.stage === "solution_design"
          ? { ...row, subStages: [makeNode("old-node")] }
          : row,
      ),
      artifacts: [oldArtifact],
      capabilityCalls: [
        {
          id: "completed-call",
          name: "Research operation",
          status: "completed",
          stageKey: "solution_design" as const,
          outputArtifactRefs: [oldArtifact.id],
        },
      ],
    };
    const pendingLocation = {
      ...oldModel,
      artifacts: [oldArtifact, newArtifact],
      capabilityCalls: [
        {
          ...oldModel.capabilityCalls[0],
          outputArtifactRefs: [oldArtifact.id, newArtifact.id],
        },
      ],
    };
    const located = {
      ...pendingLocation,
      stages: pendingLocation.stages.map((row) =>
        row.stage === "solution_design"
          ? { ...row, subStages: [...row.subStages, makeNode("new-node")] }
          : row,
      ),
    };
    return { oldModel, pendingLocation, located, oldArtifact, newArtifact };
  }

  it("follows a completed output when its node location arrives after the artifact", async () => {
    const { oldModel, pendingLocation, located, oldArtifact, newArtifact } =
      followModels();
    const view = renderWorkbench(taskA);
    view.updateModel(oldModel);
    await waitFor(() =>
      expect(screen.getByTestId("workbench-flow-nav-stub")).toHaveAttribute(
        "data-selected-substage",
        "old-node",
      ),
    );
    view.updateModel(pendingLocation);
    expect(
      screen.getByTestId("workbench-durable-artifact-projection"),
    ).toHaveAttribute("data-artifact-id", oldArtifact.id);
    view.updateModel(located);
    await waitFor(() =>
      expect(
        screen.getByTestId("workbench-durable-artifact-projection"),
      ).toHaveAttribute("data-artifact-id", newArtifact.id),
    );
    expect(screen.getByTestId("workbench-flow-nav-stub")).toHaveAttribute(
      "data-selected-substage",
      "new-node",
    );
    expect(screen.getByTestId("workbench-route-probe")).not.toHaveTextContent(
      "artifact=",
    );
    expect(
      JSON.parse(localStorage.getItem(`openair.workbench.v1:${taskA.taskId}`)!),
    ).toMatchObject({ lastSubStageKey: "new-node" });
  });

  it("keeps an explicit artifact URL pinned when a newer completed output arrives", async () => {
    const { oldModel, located, oldArtifact } = followModels();
    const view = renderWorkbench(taskA, {
      initialEntries: [`/workbench/${taskA.taskId}?artifact=${oldArtifact.id}`],
    });
    view.updateModel(oldModel);
    view.updateModel(located);
    expect(
      screen.getByTestId("workbench-durable-artifact-projection"),
    ).toHaveAttribute("data-artifact-id", oldArtifact.id);
    expect(screen.getByTestId("workbench-route-probe")).toHaveTextContent(
      `artifact=${oldArtifact.id}`,
    );
  });

  it("keeps a manually selected node pinned without an artifact URL", async () => {
    const { oldModel, located, oldArtifact } = followModels();
    const view = renderWorkbench(taskA);
    view.updateModel(oldModel);
    await waitFor(() =>
      expect(screen.getByTestId("workbench-flow-nav-stub")).toHaveAttribute(
        "data-selected-substage",
        "old-node",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "查看旧节点" }));
    view.updateModel(located);
    expect(
      screen.getByTestId("workbench-durable-artifact-projection"),
    ).toHaveAttribute("data-artifact-id", oldArtifact.id);
    expect(screen.getByTestId("workbench-route-probe")).not.toHaveTextContent(
      "artifact=",
    );
  });

  it.each(["running", "failed", "hidden"])(
    "does not follow %s outputs",
    async (condition) => {
      const { oldModel, located, oldArtifact, newArtifact } = followModels();
      const view = renderWorkbench(taskA);
      view.updateModel(oldModel);
      await waitFor(() =>
        expect(screen.getByTestId("workbench-flow-nav-stub")).toHaveAttribute(
          "data-selected-substage",
          "old-node",
        ),
      );
      view.updateModel({
        ...located,
        capabilityCalls: [
          oldModel.capabilityCalls[0],
          {
            ...oldModel.capabilityCalls[0],
            id: "new-call",
            status: condition === "hidden" ? "completed" : condition,
            outputArtifactRefs: [newArtifact.id],
          },
        ],
        artifacts: located.artifacts.map((artifact) =>
          artifact.id === newArtifact.id && condition === "hidden"
            ? { ...artifact, artifactVisibility: "hidden" }
            : artifact,
        ),
      });
      expect(
        screen.getByTestId("workbench-durable-artifact-projection"),
      ).toHaveAttribute("data-artifact-id", oldArtifact.id);
      expect(screen.getByTestId("workbench-flow-nav-stub")).toHaveAttribute(
        "data-selected-substage",
        "old-node",
      );
    },
  );

  it("switches mobile to the new output once and does not steal the pane on refresh", async () => {
    const { oldModel, located, oldArtifact } = followModels();
    const realMatchMedia = window.matchMedia.bind(window);
    const media = vi
      .spyOn(window, "matchMedia")
      .mockImplementation((query) => ({
        ...realMatchMedia(query),
        matches: query === "(max-width: 760px)",
      }));
    try {
      const view = renderWorkbench(taskA);
      view.updateModel(oldModel);
      await waitFor(() =>
        expect(
          screen.getByTestId("workbench-durable-artifact-projection"),
        ).toHaveAttribute("data-artifact-id", oldArtifact.id),
      );
      fireEvent.click(
        screen.getByText("协作区", { selector: ".ant-segmented-item-label" }),
      );
      expect(screen.getByTestId("workbench-right-panel")).toBeVisible();
      view.updateModel(located);
      await waitFor(() =>
        expect(screen.getByTestId("workbench-center")).toBeVisible(),
      );
      fireEvent.click(
        screen.getByText("协作区", { selector: ".ant-segmented-item-label" }),
      );
      view.updateModel({ ...located, revision: located.revision + 1 });
      expect(screen.getByTestId("workbench-right-panel")).toBeVisible();
      expect(screen.queryByTestId("workbench-center")).not.toBeInTheDocument();
    } finally {
      media.mockRestore();
    }
  });

  it("retains a pinned artifact across model revision and artifact reordering", async () => {
    const second = taskWithArtifact(
      taskA.taskId,
      "artifact-second",
      "Second Artifact",
    ).artifacts[0];
    const view = renderWorkbench({
      ...taskA,
      artifacts: [...taskA.artifacts, second],
    });
    fireEvent.click(
      await screen.findByRole("button", { name: "Task A Artifact" }),
    );
    view.updateTask({
      ...taskA,
      revision: (taskA.revision ?? 0) + 1,
      artifacts: [second, ...taskA.artifacts],
    });
    expect(
      screen.getByTestId("workbench-durable-artifact-projection"),
    ).toHaveAttribute("data-artifact-id", "artifact-route-a");
  });

  it("drops a removed substage from navigation while preserving other current task data", async () => {
    const view = renderWorkbench(taskA);
    const original = workbenchModelFromApiTask(taskA);
    const stage = original.stages.find(
      (row) => row.stage === "solution_design",
    )!;
    const substage = {
      id: "selected-instance",
      subStage: "route-selection",
      label: "Selected node",
      status: "running" as const,
      artifactRefs: [],
    };
    const withNode = {
      ...original,
      stages: original.stages.map((row) =>
        row === stage ? { ...row, subStages: [substage] } : row,
      ),
    };
    view.updateModel(withNode);
    // An explicit URL entry initializes the located node, then the model
    // update removes that node without changing the artifact or route.
    view.updateModel({
      ...withNode,
      artifacts: withNode.artifacts.map((artifact) => ({
        ...artifact,
        subStageInstanceId: substage.id,
      })),
    });
    fireEvent.click(
      await screen.findByRole("button", { name: "Task A Artifact" }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("workbench-flow-nav-stub")).toHaveAttribute(
        "data-selected-substage",
        substage.id,
      ),
    );
    view.updateModel({
      ...withNode,
      revision: original.revision + 1,
      artifacts: withNode.artifacts.map((artifact) => ({
        ...artifact,
        subStageInstanceId: substage.id,
      })),
    });
    expect(screen.getByTestId("workbench-flow-nav-stub")).toHaveAttribute(
      "data-selected-substage",
      substage.id,
    );
    view.updateModel({ ...original, revision: original.revision + 1 });
    expect(screen.getByTestId("workbench-flow-nav-stub")).toHaveAttribute(
      "data-selected-substage",
      "",
    );
    expect(
      screen.getByRole("button", { name: "Task A Artifact" }),
    ).toBeVisible();
    view.updateModel({
      ...withNode,
      revision: original.revision + 2,
      artifacts: withNode.artifacts.map((artifact) => ({
        ...artifact,
        subStageInstanceId: substage.id,
      })),
    });
    expect(screen.getByTestId("workbench-flow-nav-stub")).toHaveAttribute(
      "data-selected-substage",
      substage.id,
    );
  });

  it("writes a user-selected Artifact to the URL and restores it through back/forward", async () => {
    renderWorkbench(taskA);

    fireEvent.click(
      await screen.findByRole("button", { name: "Task A Artifact" }),
    );
    await waitFor(() => {
      expect(screen.getByTestId("workbench-route-probe")).toHaveTextContent(
        "/workbench/task-route-a?artifact=artifact-route-a",
      );
    });
    expect(
      screen.getByTestId("workbench-durable-artifact-projection"),
    ).toHaveAttribute("data-artifact-id", "artifact-route-a");

    fireEvent.click(screen.getByTestId("workbench-route-back"));
    await waitFor(() => {
      expect(screen.getByTestId("workbench-route-probe")).toHaveTextContent(
        "/workbench/task-route-a",
      );
      expect(screen.getByTestId("workbench-route-probe")).not.toHaveTextContent(
        "artifact=",
      );
    });
    expect(
      sessionStorage.getItem("openair.userRoute.currentArtifactId"),
    ).toBeNull();

    fireEvent.click(screen.getByTestId("workbench-route-forward"));
    await waitFor(() => {
      expect(screen.getByTestId("workbench-route-probe")).toHaveTextContent(
        "/workbench/task-route-a?artifact=artifact-route-a",
      );
      expect(
        screen.getByTestId("workbench-durable-artifact-projection"),
      ).toHaveAttribute("data-artifact-id", "artifact-route-a");
    });
  });

  it("restores a valid deep-link and preserves unrelated query parameters", async () => {
    renderWorkbench(taskA, {
      initialEntries: [
        "/workbench/task-route-a?planningSessionId=session-1&artifact=artifact-route-a",
      ],
    });

    expect(
      await screen.findByTestId("workbench-durable-artifact-projection"),
    ).toHaveAttribute("data-artifact-id", "artifact-route-a");
    expect(screen.getByTestId("workbench-route-probe")).not.toHaveTextContent(
      "planningSessionId=",
    );
    expect(screen.getByTestId("workbench-route-probe")).toHaveTextContent(
      "artifact=artifact-route-a",
    );
  });

  it("lets an explicit current-route URL outrank stale session state without reviving it on back", async () => {
    sessionStorage.setItem("openair.userRoute.currentTaskId", "task-route-a");
    sessionStorage.setItem(
      "openair.userRoute.currentArtifactId",
      "artifact-route-b",
    );
    renderWorkbench(taskA, {
      initialEntries: [
        "/workbench/current",
        "/workbench/current?artifact=artifact-route-a",
      ],
      initialIndex: 1,
    });

    expect(
      await screen.findByTestId("workbench-durable-artifact-projection"),
    ).toHaveAttribute("data-artifact-id", "artifact-route-a");
    expect(sessionStorage.getItem("openair.userRoute.currentArtifactId")).toBe(
      "artifact-route-a",
    );

    fireEvent.click(screen.getByTestId("workbench-route-back"));
    await waitFor(() => {
      expect(screen.getByTestId("workbench-route-probe")).toHaveTextContent(
        "/workbench/current",
      );
      expect(screen.getByTestId("workbench-route-probe")).not.toHaveTextContent(
        "artifact=",
      );
      expect(
        sessionStorage.getItem("openair.userRoute.currentArtifactId"),
      ).toBeNull();
    });
  });

  it("fails closed when Task B receives Task A's Artifact query", async () => {
    renderWorkbench(taskA, {
      tasks: [taskA, taskB],
      initialEntries: [
        "/workbench/task-route-a?artifact=artifact-route-a",
        "/workbench/task-route-b?planningSessionId=session-b&artifact=artifact-route-a",
      ],
      initialIndex: 0,
    });
    expect(
      await screen.findByTestId("workbench-durable-artifact-projection"),
    ).toHaveAttribute("data-artifact-id", "artifact-route-a");

    fireEvent.click(screen.getByTestId("workbench-route-forward"));
    await waitFor(() => {
      expect(screen.getByTestId("workbench-route-probe")).toHaveTextContent(
        "/workbench/task-route-b",
      );
      expect(screen.getByTestId("workbench-route-probe")).not.toHaveTextContent(
        "artifact=",
      );
    });
    expect(screen.queryByText("Task A Artifact")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Task B Artifact" }),
    ).toBeInTheDocument();
    expect(
      sessionStorage.getItem("openair.userRoute.currentArtifactId"),
    ).toBeNull();
  });
});

describe("ResearchDesign evidence-boundary visibility", () => {
  it("renders canonical evidence boundaries before confirmation", async () => {
    renderWorkbench();

    const boundarySection = await screen.findByTestId(
      "workbench-research-design-evidence-boundaries",
    );
    expect(boundarySection).toHaveTextContent(EVIDENCE_BOUNDARY);
    expect(
      screen.queryByTestId("workbench-research-design-risks"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(RISK_NOTE)).not.toBeInTheDocument();
    expect(screen.getByTestId("workbench-flow-nav-stub")).toBeVisible();
    const center = screen.getByTestId("workbench-center");
    expect(
      within(center).getByRole("button", { name: "修改研究方案" }),
    ).toBeVisible();
    expect(
      within(center).getByRole("button", { name: "确认并开始" }),
    ).toBeVisible();
    expect(screen.queryByTestId("planning-collaboration-items")).toBeNull();
    expect(screen.getByTestId("workbench-supplement-draft")).toBeVisible();
    expect(screen.queryByText("等待确认")).not.toBeInTheDocument();
    expect(screen.queryByText("最终结果待生成")).not.toBeInTheDocument();
  });

  it("switches from the research plan to formal research progress after confirmation", async () => {
    const confirmed = {
      ...V1_TASK,
      status: "not_started",
      creationStatus: "confirmed",
      researchDesignVersions: [
        {
          ...designVersion(1, "draft", V1_GOAL, V1_WORK),
          status: "confirmed",
          confirmedAt: "2026-10-07T08:00:00Z",
          confirmationRef: "confirmation-v1",
        },
      ],
      activeResearchDesignRef: {
        researchDesignId: DESIGN_ID,
        version: positiveInteger(1),
      },
    } as Task;

    renderWorkbench(confirmed);

    expect(
      await within(screen.getByTestId("workbench-left-panel")).findByText(
        "研究阶段",
      ),
    ).toBeVisible();
    expect(screen.getByTestId("workbench-flow-nav-stub")).toBeVisible();
    expect(
      screen.queryByTestId("workbench-research-design-outline"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("planning-collaboration-panel"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "确认并开始" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("当前补充入口未开放；已有成果可继续查看。"),
    ).not.toBeInTheDocument();
  });
});

describe("RD-X2 canonical current-version behavioral regression", () => {
  it("manual revision: rendered v2 and current marker v2 are the exact design/revision sent by Confirm", async () => {
    mocks.updateTaskPlan.mockResolvedValue(MANUAL_V2_TASK);
    mocks.fetchTask.mockResolvedValue(MANUAL_V2_TASK);
    mocks.confirmResearchDesign.mockResolvedValue({
      ...MANUAL_V2_TASK,
      revision: 43,
    });

    renderWorkbench();
    expect(await screen.findByText(V1_GOAL)).toBeVisible();

    await openResearchDesignEdit();
    fireEvent.change(
      screen.getByTestId("workbench-research-design-edit-goal"),
      {
        target: { value: MANUAL_V2_GOAL },
      },
    );
    fireEvent.change(
      screen.getByTestId("workbench-research-design-edit-work-0-0"),
      {
        target: { value: MANUAL_V2_WORK },
      },
    );
    fireEvent.click(screen.getByTestId("workbench-draft-complete-edit"));

    await waitFor(() => expect(mocks.updateTaskPlan).toHaveBeenCalledTimes(1));
    expect(mocks.updateTaskPlan).toHaveBeenCalledWith(
      TASK_ID,
      expect.objectContaining({
        expectedRevision: 41,
        goalSummary: MANUAL_V2_GOAL,
      }),
    );
    expect(await screen.findByText(MANUAL_V2_GOAL)).toBeVisible();
    expect(screen.queryByText(V1_GOAL)).not.toBeInTheDocument();

    await openResearchDesignEdit();
    await assertHistoryCurrentVersion(2);
    expect(mocks.reload).not.toHaveBeenCalled();
    await exitCleanEditAndConfirm();

    await waitFor(() =>
      expect(mocks.confirmResearchDesign).toHaveBeenCalledTimes(1),
    );
    expect(mocks.confirmResearchDesign).toHaveBeenCalledWith(
      TASK_ID,
      expect.objectContaining({
        designId: DESIGN_ID,
        version: 2,
        decision: "approved",
        expectedTaskRevision: 42,
      }),
    );
    await waitFor(() => expect(mocks.durableRefresh).toHaveBeenCalled());
  });

  it("Agent revision: rendered v2 and current marker v2 are the exact design/revision sent by Confirm", async () => {
    mocks.agentReviseResearchDesign.mockResolvedValue(AGENT_V2_TASK);
    mocks.fetchTask.mockResolvedValue(AGENT_V2_TASK);
    mocks.confirmResearchDesign.mockResolvedValue({
      ...AGENT_V2_TASK,
      revision: 53,
    });

    renderWorkbench();
    expect(await screen.findByText(V1_GOAL)).toBeVisible();

    await openResearchDesignEdit();
    fireEvent.change(
      screen.getByTestId("workbench-research-design-agent-instruction"),
      {
        target: { value: "增加证据覆盖与引用可靠性评估。" },
      },
    );
    fireEvent.click(
      screen.getByTestId("workbench-research-design-agent-submit"),
    );

    await waitFor(() =>
      expect(mocks.agentReviseResearchDesign).toHaveBeenCalledWith(TASK_ID, {
        instruction: "增加证据覆盖与引用可靠性评估。",
        expectedRevision: 41,
      }),
    );
    await waitFor(() => {
      expect(
        screen.getByTestId("workbench-research-design-edit-goal"),
      ).toHaveValue(AGENT_V2_GOAL);
    });
    await assertHistoryCurrentVersion(2);
    mocks.reload.mockClear();
    mocks.durableRefresh.mockClear();

    fireEvent.click(screen.getByTestId("workbench-draft-complete-edit"));
    expect(await screen.findByText(AGENT_V2_GOAL)).toBeVisible();
    expect(screen.queryByText(V1_GOAL)).not.toBeInTheDocument();
    fireEvent.click(
      await within(screen.getByTestId("workbench-center")).findByRole(
        "button",
        { name: "确认并开始" },
      ),
    );

    await waitFor(() =>
      expect(mocks.confirmResearchDesign).toHaveBeenCalledTimes(1),
    );
    expect(mocks.confirmResearchDesign).toHaveBeenCalledWith(
      TASK_ID,
      expect.objectContaining({
        designId: DESIGN_ID,
        version: 2,
        decision: "approved",
        expectedTaskRevision: 52,
      }),
    );
    await waitFor(() => expect(mocks.durableRefresh).toHaveBeenCalled());
  });
});
