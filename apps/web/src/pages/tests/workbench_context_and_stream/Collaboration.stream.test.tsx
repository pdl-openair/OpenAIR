import { App } from "antd";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { WorkbenchInteractionPanel } from "../../../components/workbench/WorkbenchInteractionPanel";
import {
  ResearchActivityStream,
  type ResearchActivity,
} from "../../../components/workbench/ResearchActivityStream";
import { formingWorkbenchTaskModel } from "../../../model/workbenchTask";
import { buildWorkbenchScreenView } from "../../../model/workbenchScreenView";
import type { WorkbenchUserActionsController } from "../../../hooks/useWorkbenchUserActions";
import type { ResearchStartSessionStatus } from "../../../api/client";

const deps = vi.hoisted(() => ({
  primary: [] as unknown[],
  details: [] as unknown[],
}));
vi.mock("../../../hooks/useTaskResearchStream", () => ({
  useTaskResearchStream: () => ({
    primaryActivities: deps.primary,
    collapsedDetails: deps.details,
    connectionState: "connected",
  }),
}));
vi.mock("../../../api/agent", () => ({
  fetchAgentMessages: vi.fn().mockResolvedValue({ messages: [] }),
}));
const actions: WorkbenchUserActionsController = {
  snapshot: null,
  items: [],
  lifecycleKind: null,
  loading: false,
  error: null,
  operationState: () => ({ state: "idle", error: null }),
  submitSupplement: vi.fn(),
  explicitRetry: vi.fn(),
  stop: vi.fn(),
  resume: vi.fn(),
  refresh: vi.fn(),
  controller: {
    busy: false,
    intent: null,
    receipt: null,
    operationState: "idle",
    error: null,
    submit: vi.fn(),
    reconcile: vi.fn(),
  },
};
function row(
  id: string,
  phase: string,
  key: string,
  text: string,
  extra = {},
): ResearchActivity {
  return {
    eventId: id,
    eventType: key.startsWith("planning-attempt:")
      ? "meaningful_progress"
      : "research_progress",
    presentationLevel: "primary",
    phase,
    activityKey: key,
    headline: text,
    summary: null,
    sequence: 1,
    revision: 1,
    occurredAt: "2026-10-09T00:00:00Z",
    startedAt: "2026-10-09T00:00:00Z",
    ...extra,
  };
}
const planning: ResearchStartSessionStatus = {
  sessionId: "intent_stream",
  status: "running",
  currentPhase: "planning",
  planningProgress: {
    code: "organizing_plan",
    message: "正在整理研究目标与可执行研究步骤。",
    elapsedSeconds: 2,
    startedAt: "2026-10-09T00:00:00Z",
    updatedAt: "2026-10-09T00:00:02Z",
    thoughts: [
      {
        key: "thought-one",
        state: "formed",
        text: "需要分别核查数据与实验条件。",
      },
    ],
  },
};
let model = formingWorkbenchTaskModel({
  taskId: "task-stream",
  title: "研究短名",
});
function panel(snapshot: ResearchStartSessionStatus = planning) {
  return (
    <App>
      <WorkbenchInteractionPanel
        taskId={model.taskId}
        model={{
          ...model,
          researchProcessHistory: [
            ...model.researchProcessHistory,
            row(
              "thought-one",
              "formed",
              "planning-thought:thought-one",
              "方案思考",
              {
                summary: "需要分别核查数据与实验条件。",
                eventType: "meaningful_progress",
              },
            ),
          ],
        }}
        artifacts={[]}
        screen={buildWorkbenchScreenView(model)}
        apiConnected
        userActions={actions}
        supplementNote=""
        onSupplementNoteChange={vi.fn()}
        planningStatus={snapshot}
        planningStatusActive
      />
    </App>
  );
}
beforeEach(() => {
  deps.primary = [];
  deps.details = [];
  model = formingWorkbenchTaskModel({
    taskId: "task-stream",
    title: "研究短名",
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows one current planning summary and retains distinct starts, failures, retries and thoughts", async () => {
  model.researchProcessHistory = [
    row(
      "start-one",
      "started",
      "planning-attempt:session:1",
      "正在组织研究方案",
    ),
    row(
      "failure-one",
      "failed",
      "planning-attempt:session:1",
      "首次规划失败，已保留输入",
    ),
  ];
  deps.primary = [
    row(
      "retry-start",
      "started",
      "planning-attempt:session:2",
      "正在组织研究方案",
    ),
    row(
      "retry-failure",
      "failed",
      "planning-attempt:session:2",
      "第二次规划失败，等待重试",
    ),
  ];
  model.researchCurrentState = {
    revision: 1,
    taskStatus: "draft",
    workUnits: [],
  };
  const view = render(panel());
  await waitFor(() =>
    expect(screen.getByTestId("workbench-planning-status")).toHaveTextContent(
      planning.planningProgress!.message!,
    ),
  );
  expect(screen.queryByText("正在准备研究方案")).toBeNull();
  expect(screen.queryByText("正在组织研究方案")).toBeNull();
  expect(screen.getAllByText("已开始组织研究方案")).toHaveLength(2);
  expect(screen.getByText("首次规划失败，已保留输入")).toBeVisible();
  expect(screen.getByText("第二次规划失败，等待重试")).toBeVisible();
  expect(screen.getByText("需要分别核查数据与实验条件。")).toBeVisible();
  view.rerender(
    panel({
      ...planning,
      status: "completed",
      planningProgress: { ...planning.planningProgress!, code: "design_ready" },
    }),
  );
  expect(screen.getAllByText("已开始组织研究方案")).toHaveLength(2);
  expect(screen.getByText("首次规划失败，已保留输入")).toBeVisible();
});

it("keeps process expansion and artifact choice across canonical updates and conflicting SSE", () => {
  const durable = [
    row("work-start", "started", "work:a", "研究步骤开始", {
      workUnitId: "wu-a",
    }),
    row("detail-one", "running", "detail:a", "核查实验条件", {
      workUnitId: "wu-a",
      presentationLevel: "detail",
      artifactRefs: ["artifact-a", "private-artifact"],
    }),
  ];
  const choose = vi.fn();
  const props = {
    taskId: "task-stream",
    enabled: true,
    active: true,
    durableActivities: durable,
    artifacts: [{ id: "artifact-a", title: "实验记录" }],
    onSelectArtifact: choose,
    canonicalCurrentState: {
      revision: 1,
      taskStatus: "running",
      workUnits: [{ workUnitId: "wu-a", status: "running" }],
    },
  };
  const view = render(<ResearchActivityStream {...props} />);
  const toggle = screen.getByTestId("research-stream-process-toggle");
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  fireEvent.click(toggle);
  fireEvent.click(screen.getByRole("button", { name: /实验记录/ }));
  expect(choose).toHaveBeenCalledTimes(1);
  expect(choose).toHaveBeenCalledWith("artifact-a");
  deps.primary = [
    row("stale-start", "running", "work:a", "研究步骤正在执行", {
      workUnitId: "wu-a",
    }),
  ];
  view.rerender(
    <ResearchActivityStream
      {...props}
      active={false}
      canonicalCurrentState={{
        revision: 2,
        taskStatus: "completed",
        workUnits: [{ workUnitId: "wu-a", status: "completed" }],
      }}
    />,
  );
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByTestId("research-stream-work-unit")).toHaveTextContent(
    "已完成",
  );
  expect(screen.queryByText(/正在进行/)).toBeNull();
  expect(screen.queryByRole("button", { name: /private-artifact/ })).toBeNull();
  expect(choose).toHaveBeenCalledTimes(1);
  fireEvent.click(toggle);
  expect(screen.getByText("核查实验条件")).toBeVisible();
});

it("preserves reading position and selection when content grows, and follows only at the bottom", async () => {
  let resize: ResizeObserverCallback | undefined;
  let frame: FrameRequestCallback | undefined;
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback: ResizeObserverCallback) {
        resize = callback;
      }
      observe() {}
      disconnect() {}
      unobserve() {}
    },
  );
  vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
    frame = callback;
    return 1;
  });
  vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
  render(panel());
  await waitFor(() =>
    expect(screen.getByText("需要分别核查数据与实验条件。")).toBeVisible(),
  );
  const viewport = screen.getByTestId("workbench-interaction-scroll");
  const stream = screen.getByTestId("continuous-interaction-stream");
  let height = 1000;
  Object.defineProperties(viewport, {
    scrollHeight: { configurable: true, get: () => height },
    clientHeight: { configurable: true, value: 200 },
  });
  Object.defineProperty(stream, "scrollHeight", {
    configurable: true,
    get: () => height,
  });
  viewport.scrollTop = 100;
  fireEvent.scroll(viewport);
  const selection = window.getSelection()!;
  const range = document.createRange();
  range.selectNodeContents(screen.getByText("需要分别核查数据与实验条件。"));
  selection.removeAllRanges();
  selection.addRange(range);
  const selected = selection.toString();
  act(() => resize?.([], {} as ResizeObserver));
  height = 1200;
  act(() => resize?.([], {} as ResizeObserver));
  expect(viewport.scrollTop).toBe(100);
  expect(selection.toString()).toBe(selected);
  viewport.scrollTop = 1000;
  fireEvent.scroll(viewport);
  height = 1400;
  act(() => resize?.([], {} as ResizeObserver));
  viewport.scrollTop = 400;
  fireEvent.scroll(viewport);
  act(() => frame?.(1));
  expect(viewport.scrollTop).toBe(400);
  viewport.scrollTop = 1200;
  fireEvent.scroll(viewport);
  height = 1600;
  act(() => resize?.([], {} as ResizeObserver));
  act(() => frame?.(2));
  expect(viewport.scrollTop).toBe(1600);
});
