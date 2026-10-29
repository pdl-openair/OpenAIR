import { describe, expect, it } from "vitest";
import type { TaskPlan } from "@openair/shared";
import type { ResearchStartSessionStatus } from "../../../api/client";
import {
  formatPlanningElapsed,
  interpolatePlanningElapsedSeconds,
  projectPlanningWorkbench,
  publicPlanningProgressMessage,
} from "../../planningCollaboration";

const FORBIDDEN_PROGRESS_FILLERS = [
  "正在接手研究问题",
  "研究请求已经进入当前工作台",
  "正在形成研究方案",
  "正在梳理研究问题",
  "正在组织研究工作",
  "正在核对方案完整性",
  "启动中",
  "梳理中",
  "形成中",
  "核对中",
];

const partialSnapshot: ResearchStartSessionStatus = {
  sessionId: "intent_planning",
  status: "running",
  currentPhase: "planning",
  planningProgress: {
    code: "compiling_design",
    message: "语义规划已返回，服务端正在核验研究合同。",
    elapsedSeconds: 59,
    keyResults: {
      planningMode: "partial",
      substageCount: 1,
      researchQuestions: ["两类方法应在什么一致条件下比较？"],
      objectives: ["统一问题范围、模型条件和评价口径后比较准确性与成本。"],
      targetOutputs: ["可复现的准确性与成本比较"],
      expectedArtifactTypes: ["comparison"],
      planningRationale:
        "只有使用一致的问题范围和评价口径，准确性与成本才可以直接比较。",
    },
  },
};

const readyPlan: TaskPlan = {
  goalSummary: "比较两类方法的准确性、成本和可复现性。",
  domain: "research_task",
  planningSource: "research_design_v1_2",
  understanding: {
    primaryResearchQuestions: ["两类方法在一致条件下如何比较？"],
    targetOutputs: ["准确性、成本和可复现性对照"],
    planningRationale: "先统一比较口径，再组织证据和复现实验。",
  },
  clarifications: [],
  readyToConfirm: true,
  stages: [
    {
      stageKey: "problem_modeling",
      subStages: ["comparison"],
      substages: [
        {
          substage: "comparison",
          label: "统一比较口径",
          action: "固定问题范围、模型条件和评价指标。",
          expectedArtifact: "comparison",
        },
      ],
    },
  ],
};

describe("planning Workbench single public outlet", () => {
  it("does not invent staged filler when only a waiting code is present", () => {
    const projection = projectPlanningWorkbench({
      sessionId: "intent_empty",
      status: "running",
      currentPhase: "understanding",
      planningProgress: {
        code: "still_queued",
        message: "",
        elapsedSeconds: 12,
      },
    });
    const serialized = JSON.stringify(projection);
    for (const filler of FORBIDDEN_PROGRESS_FILLERS) {
      expect(serialized).not.toContain(filler);
    }
    expect(projection.draftSections).toEqual([]);
    expect(
      publicPlanningProgressMessage({
        sessionId: "intent_empty",
        status: "running",
        currentPhase: "understanding",
        planningProgress: {
          code: "still_queued",
          message: "",
          elapsedSeconds: 12,
        },
      }),
    ).toBe("研究方案还在准备中。");
    expect(projection.structure.map((item) => item.state)).toEqual([
      "pending",
      "pending",
      "pending",
    ]);
    expect(projection).not.toHaveProperty("activityLabel");
  });

  it("uses only the formal planningProgress.message as the status outlet", () => {
    const projection = projectPlanningWorkbench({
      sessionId: "intent_msg",
      status: "running",
      currentPhase: "organizing",
      planningProgress: {
        code: "organizing_intent",
        message: "正在整理研究目标、研究范围、约束和相关工作。",
        elapsedSeconds: 4,
      },
    });

    expect(
      publicPlanningProgressMessage({
        sessionId: "intent_msg",
        status: "running",
        planningProgress: {
          code: "organizing_intent",
          message: "正在整理研究目标、研究范围、约束和相关工作。",
          elapsedSeconds: 4,
        },
      }),
    ).toBe("正在整理研究目标、研究范围、约束和相关工作。");
    expect(projection.structure.every((item) => item.state === "pending")).toBe(
      true,
    );
    const serialized = JSON.stringify(projection);
    expect(serialized).not.toContain("梳理中");
    expect(serialized).not.toContain("正在梳理研究问题");
  });

  it("maps internal formal messages to one research-user progress sentence", () => {
    expect(
      publicPlanningProgressMessage({
        sessionId: "intent_worker",
        status: "running",
        planningProgress: {
          code: "still_queued",
          message: "研究任务已进入可靠队列，正在等待规划 Worker 接手。",
          elapsedSeconds: 8,
        },
      }),
    ).toBe("研究方案还在准备中。");
  });

  it("keeps worker_unavailable waiting visible with elapsed-facing progress only", () => {
    const projection = projectPlanningWorkbench({
      sessionId: "intent_unavailable",
      status: "running",
      currentPhase: "understanding",
      recoveryReason: "worker_unavailable",
      planningProgress: {
        code: "worker_unavailable",
        message: "当前没有可确认的规划 Worker 正在处理队列，请稍后重试。",
        elapsedSeconds: 42,
      },
    });
    expect(
      publicPlanningProgressMessage({
        sessionId: "intent_unavailable",
        status: "running",
        recoveryReason: "worker_unavailable",
        planningProgress: {
          code: "worker_unavailable",
          message: "当前没有可确认的规划 Worker 正在处理队列，请稍后重试。",
          elapsedSeconds: 42,
        },
      }),
    ).toBe("研究方案还在准备中。");
    expect(projection.draftSections).toEqual([]);
    expect(formatPlanningElapsed(42)).toBe("42秒");
  });

  it("renders only durable partial planning content without code-derived progress wording", () => {
    const projection = projectPlanningWorkbench(partialSnapshot);

    expect(projection.draftSections.map((section) => section.title)).toEqual([
      "研究目标",
      "研究工作",
      "完成要求",
    ]);
    expect(projection.structure.map((item) => item.state)).toEqual([
      "formed",
      "formed",
      "formed",
    ]);
    expect(formatPlanningElapsed(59)).toBe("59秒");
  });

  it("marks structure formed from formal content without firstMissing-as-current", () => {
    const projection = projectPlanningWorkbench({
      sessionId: "intent_partial_kr",
      status: "running",
      currentPhase: "planning",
      planningProgress: {
        code: "compiling_design",
        message: "语义规划已返回，服务端正在核验研究合同。",
        elapsedSeconds: 20,
        keyResults: {
          planningMode: "partial",
          substageCount: 1,
          researchQuestions: ["问题A"],
          objectives: [],
          targetOutputs: [],
          expectedArtifactTypes: [],
        },
      },
    });
    expect(projection.structure.map((item) => item.state)).toEqual([
      "formed",
      "pending",
      "pending",
    ]);
    expect(projection.structure.every((item) => item.state !== "current")).toBe(
      true,
    );
  });

  it("shows formal ResearchDesign content only when the draft is ready", () => {
    const projection = projectPlanningWorkbench(null, readyPlan);
    expect(projection.structure.map((item) => item.state)).toEqual([
      "formed",
      "formed",
      "formed",
    ]);
    expect(projection.draftReady).toBe(true);
  });
});

describe("planning elapsed single authority", () => {
  it("shows server elapsedSeconds without doubling from updatedAt-equivalent origin", () => {
    const displayed = interpolatePlanningElapsedSeconds({
      elapsedSeconds: 21,
      snapshotReceivedAtMs: 1_000_000,
      nowMs: 1_000_000,
      frozen: false,
    });
    expect(displayed).toBe(21);
    expect(displayed).not.toBe(42);
    expect(formatPlanningElapsed(displayed)).toBe("21秒");
  });

  it("interpolates live seconds from the server snapshot only", () => {
    const t0 = 5_000_000;
    expect(
      interpolatePlanningElapsedSeconds({
        elapsedSeconds: 21,
        snapshotReceivedAtMs: t0,
        nowMs: t0,
        frozen: false,
      }),
    ).toBe(21);
    expect(
      interpolatePlanningElapsedSeconds({
        elapsedSeconds: 21,
        snapshotReceivedAtMs: t0,
        nowMs: t0 + 3_000,
        frozen: false,
      }),
    ).toBe(24);
  });

  it("resets to a new server snapshot on reload semantics", () => {
    expect(
      interpolatePlanningElapsedSeconds({
        elapsedSeconds: 27,
        snapshotReceivedAtMs: 9_000_000,
        nowMs: 9_000_000,
        frozen: false,
      }),
    ).toBe(27);
  });

  it("freezes elapsed when terminal or draftReady", () => {
    expect(
      interpolatePlanningElapsedSeconds({
        elapsedSeconds: 40,
        snapshotReceivedAtMs: 1_000,
        nowMs: 1_000 + 10_000,
        frozen: true,
      }),
    ).toBe(40);
  });
});
