import assert from "node:assert/strict";
import { createHash } from "node:crypto";

import { test } from "vitest";
import { workbenchModelFromApiTask } from "../../workbenchTask.ts";
import {
  assertScreenPanesShareRevision,
  buildWorkbenchScreenView,
  collaborationMatchesTaskRevision,
  coerceStartedStageStatus,
  durableSubstageDisplayLabel,
  hasStartedVsNotStartedContradiction,
  panesShareSingleRevision,
  progressStepsFromScreen,
  projectScreenToPanes,
  projectVisibleDurableResearch,
  resolveSubstageStageKey,
  rollupDurableStageStatus,
  stageStatusAfterExternalFailure,
} from "../../workbenchScreenCore.mjs";

test("durable instance labels distinguish repeated planner instances without exposing technical ids", () => {
  const instance = {
    label: "sdr:question_refinement",
    substageDefinitionId: "sdr:question_refinement",
  };
  assert.equal(durableSubstageDisplayLabel(instance, 1, 3), "研究问题凝练 1");
  assert.equal(durableSubstageDisplayLabel(instance, 2, 3), "研究问题凝练 2");
  assert.doesNotMatch(
    durableSubstageDisplayLabel(instance, 3, 3),
    /sdr:|question_refinement/,
  );
});

test("durable instance labels use canonical semantic titles and retain generic fallback", () => {
  const visible = projectVisibleDurableResearch(
    durableProjection([
      durableSubstage({
        id: "semantic-one",
        objective: "明确研究对象与判别目标",
      }),
      durableSubstage({
        id: "semantic-two",
        definition: "sdr:unknown_definition",
        objective: "建立变量与观测指标",
        sequence: 1,
      }),
      durableSubstage({
        id: "legacy",
        definition: "sdr:unknown_definition",
        objective: null,
        sequence: 2,
      }),
    ]),
  );
  const labels = visible.substageProgress.map((substage) => substage.label);
  assert.deepEqual(labels.slice(0, 2), [
    "明确研究对象与判别目标",
    "建立变量与观测指标",
  ]);
  assert.equal(labels[2], "研究步骤");
  assert.notEqual(labels[0], "研究步骤 1");
  assert.notEqual(labels[1], "研究步骤 2");
});

function liveModel(overrides = {}) {
  return {
    taskId: "task-1",
    revision: 10,
    progressRevision: 10,
    executionDisplayStatus: "failed",
    stageCounts: { completed: 0, total: 1 },
    progressTrust: { kind: "canonical", projection: { revision: 10 } },
    collaborationNodes: [
      {
        workUnitId: "wu-1",
        agentId: "research_question_refinement",
        status: "failed",
      },
    ],
    stages: [
      {
        stage: "problem_modeling",
        status: "not_started",
        projectionStatus: "not_started",
        pagePhaseLabel: "问题建模",
        completedCount: 0,
        totalCount: 1,
        subStages: [
          {
            id: "s1",
            subStage: "research_question_refinement",
            label: "研究问题凝练",
            status: "failed",
            projectionStatus: "failed",
            statusReasonCode: "execution_failed",
          },
        ],
      },
    ],
    ...overrides,
  };
}

test("research_question_refinement maps to problem_modeling by stable key", () => {
  assert.equal(
    resolveSubstageStageKey("research_question_refinement", null),
    "problem_modeling",
  );
  assert.equal(
    resolveSubstageStageKey("question_refinement", null),
    "problem_modeling",
  );
  assert.equal(
    resolveSubstageStageKey("question_refinement", "problem_modeling"),
    "problem_modeling",
  );
  assert.equal(
    resolveSubstageStageKey("other", "solution_design"),
    "solution_design",
  );
});

function durableProjection(
  substageProgress,
  { activeStageKeys = [], stageProgress } = {},
) {
  return {
    revision: 10,
    substageProgress,
    stageProgress:
      stageProgress ??
      [
        "problem_modeling",
        "solution_design",
        "execution_validation",
        "insight_generation",
      ].map((stageKey) => ({
        stageKey,
        status: "not_started",
        substageInstanceRefs: [],
        completedCount: 0,
        totalCount: 0,
        artifactRefs: [],
      })),
    taskProgress: {
      status: "running",
      activeStageKeys,
      awaitingConfirmationRefs: [],
    },
    workbenchProjection: {
      collaborationNodes: [],
      collaborationEvents: [],
      toolResourceRecords: [],
      artifactRefs: [],
    },
  };
}

function durableSubstage({
  id,
  definition = "sdr:question_refinement",
  stageKey = "problem_modeling",
  sequence = 0,
  status = "not_started",
  objective = null,
}) {
  return {
    substageInstanceId: id,
    instanceRevision: 1,
    substageDefinitionId: definition,
    stageKey,
    label: definition,
    objective,
    sequence,
    status,
    statusReasonCode: null,
    workUnitIds: status === "not_started" ? [] : [`wu-${id}`],
    artifactRefs: status === "completed" ? [`artifact-${id}`] : [],
  };
}

test("single-instance research exposes one stage in the durable progress projection", () => {
  const visible = projectVisibleDurableResearch(
    durableProjection(
      [durableSubstage({ id: "instance-one", status: "running" })],
      { activeStageKeys: ["problem_modeling", "solution_design"] },
    ),
  );

  assert.deepEqual(
    visible.stageProgress.map((stage) => stage.stageKey),
    ["problem_modeling"],
  );
  assert.deepEqual(
    visible.substageProgress.map((sub) => sub.substageInstanceId),
    ["instance-one"],
  );
  assert.deepEqual(visible.taskProgress.activeStageKeys, ["problem_modeling"]);
  assert.deepEqual(visible.stageProgress[0].substageInstanceRefs, [
    "instance-one",
  ]);
  assert.equal(visible.stageProgress[0].status, "running");
  assert.equal(visible.stageProgress[0].totalCount, 1);
});

test("non-contiguous real stages preserve canonical order without filling gaps", () => {
  const visible = projectVisibleDurableResearch(
    durableProjection([
      durableSubstage({
        id: "insight-1",
        definition: "sdr:result_reliability_audit",
        stageKey: "insight_generation",
        sequence: 2,
      }),
      durableSubstage({ id: "problem-1", sequence: 1 }),
    ]),
  );

  assert.deepEqual(
    visible.stageProgress.map((stage) => stage.stageKey),
    ["problem_modeling", "insight_generation"],
  );
  assert.deepEqual(
    visible.substageProgress.map((sub) => sub.substageInstanceId),
    ["problem-1", "insight-1"],
  );
});

test("active empty ResearchDesign removes stale durable rows instead of restoring four placeholders", () => {
  const raw = durableProjection(
    [durableSubstage({ id: "stale-instance", status: "running" })],
    {
      activeStageKeys: ["problem_modeling"],
    },
  );
  const visible = projectVisibleDurableResearch(raw, new Map(), true);
  assert.deepEqual(visible.substageProgress, []);
  assert.deepEqual(visible.stageProgress, []);
  assert.deepEqual(visible.taskProgress.activeStageKeys, []);

  const screen = buildWorkbenchScreenView(
    liveModel({ stages: [], stageCounts: { completed: 4, total: 4 } }),
  );
  assert.deepEqual(screen.leftStages, []);
  assert.deepEqual(screen.middlePath, []);
  assert.deepEqual(progressStepsFromScreen(screen), []);
  assert.equal(screen.activeSubstage, null);
  assert.equal(screen.stageCounts, null);
});

test("plan generation failure keeps four dimmed stages and does not mark them failed", () => {
  const screen = buildWorkbenchScreenView({
    ...liveModel({ stages: [], stageCounts: null, executionDisplayStatus: "failed" }),
    taskStatus: "failed",
    creationPlan: null,
    researchDesign: null,
  });
  assert.deepEqual(
    screen.leftStages.map((stage) => stage.stageKey),
    ["problem_modeling", "solution_design", "execution_validation", "insight_generation"],
  );
  assert.ok(screen.leftStages.every((stage) => stage.status === "not_started"));
  assert.deepEqual(
    progressStepsFromScreen(screen).map((stage) => stage.status),
    ["not_started", "not_started", "not_started", "not_started"],
  );
});

test("rollup promotes failed question_refinement over stage not_started", () => {
  assert.equal(
    rollupDurableStageStatus({
      stageStatus: "not_started",
      substages: [{ status: "failed", workUnitIds: ["wu-1"] }],
    }),
    "failed",
  );
  assert.notEqual(
    rollupDurableStageStatus({
      stageStatus: "not_started",
      substages: [{ status: "failed", workUnitIds: ["wu-1"] }],
    }),
    "not_started",
  );
});

test("same revision: problem_modeling/question_refinement=failed aligns left/middle/right", () => {
  const screen = buildWorkbenchScreenView(liveModel());
  assert.equal(screen.revision, 10);
  assert.equal(screen.isLiveResearch, true);
  const problem = screen.leftStages.find(
    (stage) => stage.stageKey === "problem_modeling",
  );
  assert.ok(problem);
  assert.equal(problem.label, "问题建模");
  assert.equal(problem.status, "failed");
  assert.equal(problem.statusLabel, "已失败");
  assert.notEqual(problem.status, "not_started");
  const sub = problem.substages.find(
    (item) => item.substageKey === "research_question_refinement",
  );
  assert.ok(sub);
  assert.equal(sub.status, "failed");
  assert.equal(
    screen.middlePath.find((step) => step.stageKey === "problem_modeling")
      ?.status,
    "failed",
  );
  assert.equal(screen.rightCollaboration[0].status, "failed");
  assert.equal(screen.activeSubstage?.status, "failed");
  assert.equal(screen.activeSubstage?.label, "研究问题凝练");
  assert.ok(assertScreenPanesShareRevision(screen));
  assert.equal(hasStartedVsNotStartedContradiction(screen), false);
  const panes = projectScreenToPanes(screen);
  assert.ok(panesShareSingleRevision(panes));
  assert.ok(
    !panes.left.stages.some(
      (stage) =>
        stage.stageKey === "problem_modeling" && stage.status === "not_started",
    ),
  );
  assert.deepEqual(screen.failureNextActions, []);
});

test("canonical stages are parents; zero-instance stages are absent while repeated definitions stay independent", () => {
  const base = liveModel({
    executionDisplayStatus: "running",
    stageCounts: { completed: 99, total: 99 },
    stages: [
      {
        stage: "problem_modeling",
        status: "completed",
        projectionStatus: "completed",
        planState: "planned",
        subStages: [1, 2, 3].map((sequence) => ({
          id: `instance-${sequence}`,
          subStage: "sdr:question_refinement",
          label: `研究问题凝练 ${sequence}`,
          instanceSequence: sequence,
          objective: `候选问题 ${sequence} 的边界核对`,
          status:
            sequence === 1
              ? "completed"
              : sequence === 2
                ? "running"
                : "not_started",
          projectionStatus:
            sequence === 1
              ? "completed"
              : sequence === 2
                ? "running"
                : "not_started",
        })),
      },
      {
        stage: "solution_design",
        status: "running",
        projectionStatus: "running",
        planState: "unplanned",
        subStages: [],
      },
      {
        stage: "execution_validation",
        status: "completed",
        projectionStatus: "completed",
        planState: "unplanned",
        subStages: [],
      },
      {
        stage: "insight_generation",
        status: "failed",
        projectionStatus: "failed",
        planState: "unplanned",
        subStages: [],
      },
    ],
  });
  const screen = buildWorkbenchScreenView(base);
  assert.deepEqual(
    screen.leftStages.map((stage) => stage.stageKey),
    ["problem_modeling"],
  );
  assert.deepEqual(
    screen.middlePath.map((stage) => stage.stageKey),
    ["problem_modeling"],
  );
  assert.deepEqual(
    progressStepsFromScreen(screen).map((stage) => stage.stageKey),
    ["problem_modeling"],
  );
  const problem = screen.leftStages[0];
  assert.equal(problem.label, "问题建模");
  assert.equal(problem.status, "running");
  assert.deepEqual(
    problem.substages.map((sub) => sub.id),
    ["instance-1", "instance-2", "instance-3"],
  );
  assert.deepEqual(
    problem.substages.map((sub) => sub.instanceSequence),
    [1, 2, 3],
  );
  assert.equal(problem.substages[1].objective, "候选问题 2 的边界核对");
  assert.deepEqual(screen.stageCounts, { completed: 1, total: 3 });
});

test("Task A, Task B, Task A screen builds do not retain another task's instances", () => {
  const taskA = liveModel({
    taskId: "task-a",
    stages: [
      {
        stage: "problem_modeling",
        status: "running",
        projectionStatus: "running",
        subStages: [
          {
            id: "a-instance",
            subStage: "question_refinement",
            label: "A 节点",
            status: "running",
            projectionStatus: "running",
          },
        ],
      },
    ],
  });
  const taskB = liveModel({
    taskId: "task-b",
    stages: [
      {
        stage: "insight_generation",
        status: "running",
        projectionStatus: "running",
        subStages: [
          {
            id: "b-instance",
            subStage: "result_reliability_audit",
            label: "B 节点",
            status: "running",
            projectionStatus: "running",
          },
        ],
      },
    ],
  });

  const firstA = buildWorkbenchScreenView(taskA);
  const screenB = buildWorkbenchScreenView(taskB);
  const secondA = buildWorkbenchScreenView(taskA);

  assert.deepEqual(
    firstA.leftStages.flatMap((stage) => stage.substages.map((sub) => sub.id)),
    ["a-instance"],
  );
  assert.deepEqual(
    screenB.leftStages.flatMap((stage) => stage.substages.map((sub) => sub.id)),
    ["b-instance"],
  );
  assert.deepEqual(
    secondA.leftStages.flatMap((stage) => stage.substages.map((sub) => sub.id)),
    ["a-instance"],
  );
  assert.ok(
    !secondA.leftStages.some((stage) =>
      stage.substages.some((sub) => sub.id === "b-instance"),
    ),
  );
});

test("persisted interaction records appear as collaboration lifecycle events", () => {
  const screen = buildWorkbenchScreenView(
    liveModel({
      collaborationEvents: [
        {
          interactionId: "interaction-1",
          interactionType: "review_submitted",
          sourceWorkUnitId: "review-unit",
          targetWorkUnitId: "specialist-unit",
          summary: "独立审核确认产物可继续使用。",
          createdAt: "2026-07-31T02:00:00Z",
          verdict: { decision: "approved" },
        },
      ],
    }),
  );
  const event = screen.rightCollaboration.find(
    (row) => row.id === "interaction-1",
  );
  assert.ok(event);
  assert.equal(event.kind, "review_submitted");
  assert.equal(event.title, "提交独立审核");
  assert.equal(event.detail, "独立审核确认产物可继续使用。");
  assert.equal(event.status, "completed");
});

test("revision-bound SSE lifecycle facts enter the live collaboration timeline", () => {
  const screen = buildWorkbenchScreenView(
    liveModel({
      lifecycleEvents: [
        {
          id: "lifecycle:run-1:skill:call-1:started",
          type: "skill_lifecycle",
          taskId: "task-1",
          revision: 10,
          sequence: 100,
          occurredAt: "2026-07-31T02:00:00Z",
          payload: {
            revision: 10,
            runId: "run-1",
            workUnitId: "wu-1",
            agentId: "research_question_refinement",
            skillId: "question_refinement",
            phase: "started",
            status: "executing",
            summary: "Refining the research question.",
          },
        },
      ],
    }),
  );
  const event = screen.rightCollaboration.find(
    (row) => row.id === "skill:wu-1:question_refinement",
  );
  assert.ok(event);
  assert.equal(event.kind, "skill_lifecycle");
  assert.equal(event.detail, "Refining the research question.");
  assert.equal(event.workUnitId, "wu-1");
  assert.equal(event.status, "running");
});

test("one specialist WorkUnit keeps one stable row and the latest lifecycle status", () => {
  const lifecycleEvents = [
    ["assigned", "preparing", 100, "2026-07-31T02:00:00Z"],
    ["started", "executing", 200, "2026-07-31T02:00:01Z"],
    ["completed", "completed", 300, "2026-07-31T02:00:02Z"],
  ].map(([phase, status, sequence, occurredAt]) => ({
    id: `lifecycle:run-1:agent_lifecycle:wu-1:${phase}`,
    type: "agent_lifecycle",
    taskId: "task-1",
    revision: 10,
    sequence,
    occurredAt,
    payload: {
      revision: 10,
      runId: "run-1",
      workUnitId: "wu-1",
      agentId: "research_question_refinement",
      phase,
      status,
      summary: `specialist ${phase}`,
    },
  }));
  const screen = buildWorkbenchScreenView(
    liveModel({
      executionDisplayStatus: "completed",
      collaborationNodes: [
        {
          workUnitId: "wu-1",
          agentId: "research_question_refinement",
          status: "preparing",
        },
      ],
      lifecycleEvents: [
        lifecycleEvents[2],
        lifecycleEvents[0],
        lifecycleEvents[1],
      ],
    }),
  );

  const specialistRows = screen.rightCollaboration.filter(
    (row) => row.workUnitId === "wu-1",
  );
  assert.equal(specialistRows.length, 1);
  assert.equal(specialistRows[0].id, "wu-1");
  assert.equal(specialistRows[0].status, "completed");
  assert.equal(specialistRows[0].detail, "specialist completed");
});

test("mandatory reviewer remains a distinct WorkUnit while its lifecycle is deduplicated", () => {
  const screen = buildWorkbenchScreenView(
    liveModel({
      collaborationNodes: [
        {
          workUnitId: "wu-specialist",
          agentId: "research_question_refinement",
          status: "completed",
        },
        {
          workUnitId: "wu-review",
          agentId: "result_reliability_audit",
          kind: "review",
          status: "preparing",
        },
      ],
      lifecycleEvents: ["assigned", "started", "completed"].map(
        (phase, index) => ({
          id: `lifecycle:run-1:mandatory_review_lifecycle:wu-review:${phase}`,
          type: "mandatory_review_lifecycle",
          taskId: "task-1",
          revision: 10,
          sequence: 100 + index,
          occurredAt: `2026-07-31T02:00:0${index}Z`,
          payload: {
            revision: 10,
            runId: "run-1",
            workUnitId: "wu-review",
            agentId: "result_reliability_audit",
            phase,
            status:
              phase === "completed"
                ? "completed"
                : phase === "started"
                  ? "executing"
                  : "preparing",
          },
        }),
      ),
    }),
  );

  const collaborators = screen.rightCollaboration.filter(
    (row) => row.workUnitId,
  );
  assert.deepEqual(
    collaborators.map((row) => row.id),
    ["wu-specialist", "wu-review"],
  );
  assert.equal(collaborators[1].kind, "mandatory_review");
  assert.equal(collaborators[1].status, "completed");
  assert.equal(collaborators[1].title, "独立结果审核");
});

test("collaboration projection never invents capabilities from the plan", () => {
  const screen = buildWorkbenchScreenView(
    liveModel({
      collaborationNodes: [],
      collaborationEvents: [],
      lifecycleEvents: [],
      creationPlan: {
        selectedCapabilities: [
          "research_question_refinement",
          "key_literature_positioning",
        ],
        stages: [{ capabilities: ["result_reliability_audit"] }],
      },
    }),
  );

  assert.deepEqual(screen.rightCollaboration, []);
});

test("needs_input stays visible and is never completed", () => {
  const screen = buildWorkbenchScreenView(
    liveModel({
      executionDisplayStatus: "needs_input",
      stages: liveModel().stages.map((stage, index) =>
        index === 0
          ? {
              ...stage,
              status: "needs_input",
              projectionStatus: "needs_input",
              subStages: [
                {
                  id: "s1",
                  subStage: "research_question_refinement",
                  label: "研究问题凝练",
                  status: "needs_input",
                  projectionStatus: "needs_input",
                },
              ],
            }
          : stage,
      ),
      collaborationNodes: [{ workUnitId: "wu-1", status: "needs_input" }],
    }),
  );
  assert.equal(screen.executionDisplayStatus, "needs_input");
  assert.equal(screen.leftStages[0].status, "needs_input");
  assert.notEqual(screen.leftStages[0].status, "completed");
  assert.deepEqual(screen.failureNextActions, []);
});

test("external_result_unknown never shows retry or completed", () => {
  const screen = buildWorkbenchScreenView(
    liveModel({
      executionDisplayStatus: "external_result_unknown",
      stages: liveModel().stages.map((stage, index) =>
        index === 0
          ? {
              ...stage,
              status: "not_started",
              projectionStatus: "not_started",
              statusReasonCode: "external_result_unknown",
              subStages: [
                {
                  id: "s1",
                  subStage: "research_question_refinement",
                  label: "研究问题凝练",
                  status: "blocked_recoverable",
                  projectionStatus: "blocked_recoverable",
                  statusReasonCode: "external_result_unknown",
                  workUnitIds: ["wu-1"],
                },
              ],
            }
          : stage,
      ),
    }),
  );
  assert.equal(screen.leftStages[0].status, "external_result_unknown");
  assert.notEqual(screen.leftStages[0].status, "completed");
  assert.notEqual(screen.leftStages[0].status, "not_started");
  assert.deepEqual(screen.failureNextActions, []);
  assert.ok(!JSON.stringify(screen.failureNextActions).includes("retry"));
});

test("legacy / no projection uses compatibility note and does not invent live four-stage status", () => {
  const screen = buildWorkbenchScreenView({
    taskId: "legacy-1",
    revision: 3,
    progressRevision: null,
    executionDisplayStatus: "running",
    progressTrust: { kind: "legacy" },
    collaborationNodes: [],
    stages: [
      {
        stage: "problem_modeling",
        status: "running",
        pagePhaseLabel: "自定义标题不应驱动 live",
        subStages: [],
      },
    ],
  });
  assert.equal(screen.isLiveResearch, false);
  assert.equal(
    screen.legacyCompatibilityNote,
    "当前任务使用兼容展示；部分进度信息可能不完整。",
  );
  assert.equal(screen.leftStages.length, 1);
  assert.equal(screen.failureNextActions.length, 0);
  assert.equal(collaborationMatchesTaskRevision({ revision: 9 }, 3), false);
});

test("stale collaboration revision cannot enter the screen", () => {
  const screen = buildWorkbenchScreenView(liveModel(), {
    taskId: "task-1",
    revision: 9,
    status: "failed",
    collaboration: {
      pendingDecisions: [
        { gateId: "g1", allowedActions: ["start_revision_run"] },
      ],
      actual: [],
      handoffs: [],
      needsUserDecision: true,
    },
  });
  assert.equal(screen.matchedCollaboration, null);
  assert.ok(
    !screen.failureNextActions.some(
      (action) => action.kind === "scoped_durable_retry",
    ),
  );
});

test("external action failure helpers never return not_started", () => {
  assert.equal(stageStatusAfterExternalFailure("failed"), "failed");
  assert.equal(
    coerceStartedStageStatus({
      status: "not_started",
      projectionStatus: "not_started",
      workUnitIds: ["wu-1"],
    }),
    "running",
  );
});

test("skipped stays a successful terminal status, never not_started", async () => {
  const {
    mapScreenStatusToNav,
    userVisibleStatusLabel,
    buildWorkbenchScreenView,
    projectScreenToPanes,
    panesShareSingleRevision,
  } = await import("../../workbenchScreenCore.mjs");

  assert.equal(
    coerceStartedStageStatus({
      status: "skipped",
      projectionStatus: "skipped",
    }),
    "skipped",
  );
  assert.equal(userVisibleStatusLabel("skipped"), "已跳过");
  assert.equal(mapScreenStatusToNav("skipped"), "completed");
  assert.notEqual(mapScreenStatusToNav("skipped"), "not_started");
  assert.equal(mapScreenStatusToNav("blocked_recoverable"), "blocked");
  assert.notEqual(mapScreenStatusToNav("blocked_recoverable"), "pending");
  assert.equal(userVisibleStatusLabel("blocked_recoverable"), "执行受阻");
  assert.notEqual(userVisibleStatusLabel("blocked_recoverable"), "待确认");
  assert.equal(userVisibleStatusLabel("awaiting_confirmation"), "待确认");
  assert.equal(userVisibleStatusLabel("needs_input"), "待用户补充");

  const screen = buildWorkbenchScreenView({
    taskId: "task-skip",
    revision: 4,
    progressRevision: 4,
    executionDisplayStatus: "running",
    progressTrust: { kind: "canonical", projection: { revision: 4 } },
    collaborationNodes: [{ workUnitId: "wu-skip", status: "skipped" }],
    stages: [
      {
        stage: "problem_modeling",
        status: "skipped",
        projectionStatus: "skipped",
        subStages: [
          {
            id: "s-skip",
            subStage: "research_question_refinement",
            label: "研究问题凝练",
            status: "skipped",
            projectionStatus: "skipped",
          },
        ],
      },
      {
        stage: "solution_design",
        status: "running",
        projectionStatus: "running",
        subStages: [],
      },
      {
        stage: "execution_validation",
        status: "not_started",
        projectionStatus: "not_started",
        subStages: [],
      },
      {
        stage: "insight_generation",
        status: "not_started",
        projectionStatus: "not_started",
        subStages: [],
      },
    ],
  });
  const problem = screen.leftStages.find(
    (stage) => stage.stageKey === "problem_modeling",
  );
  assert.equal(problem.status, "skipped");
  assert.equal(problem.statusLabel, "已跳过");
  assert.notEqual(problem.status, "not_started");
  assert.equal(
    screen.middlePath.find((step) => step.stageKey === "problem_modeling")
      .status,
    "skipped",
  );
  assert.equal(
    screen.middlePath.find((step) => step.stageKey === "problem_modeling")
      .statusLabel,
    "已跳过",
  );
  assert.ok(panesShareSingleRevision(projectScreenToPanes(screen)));
});

test("B2-5 status presentation never exposes backend status keys", async () => {
  const { userVisibleStatusLabel } =
    await import("../../workbenchScreenCore.mjs");

  assert.equal(userVisibleStatusLabel("preparing"), "准备中");
  assert.equal(userVisibleStatusLabel("executing"), "执行中");
  assert.equal(userVisibleStatusLabel("system_reviewing"), "核查中");
  assert.equal(
    userVisibleStatusLabel("awaiting_user_confirmation"),
    "等待你确认",
  );
  assert.equal(userVisibleStatusLabel("stopped_by_user"), "已停止");
  assert.equal(userVisibleStatusLabel("opaque_backend_state"), "状态待确认");
  assert.notEqual(
    userVisibleStatusLabel("opaque_backend_state"),
    "opaque_backend_state",
  );
});

function t2CanonicalCurrent(
  status,
  {
    revision = 20,
    startedAt = "2026-10-06T02:00:00Z",
    finishedAt = "2026-10-06T02:20:00Z",
    taskStatus = "running",
    taskFinishedAt = null,
  } = {},
) {
  return {
    revision,
    taskStatus,
    taskFinishedAt,
    workUnits: [{ workUnitId: "wu-1", status, startedAt, finishedAt }],
  };
}

function t2Lifecycle({
  eventId,
  phase,
  occurredAt,
  sequence,
  revision = null,
  eventType = "research_progress",
  headline = null,
  activityKey = null,
}) {
  return {
    eventId,
    eventType,
    presentationLevel: "primary",
    workUnitId: "wu-1",
    headline: headline ?? `研究步骤${phase}`,
    summary: null,
    sequence,
    revision,
    occurredAt,
    startedAt: "2026-10-06T02:00:00Z",
    aggregateCount: null,
    activityKey,
    phase,
    artifactRefs: [],
    resultRefs: [],
    scientificStateVersions: [],
  };
}

test("T2 canonical completed cannot be overwritten by an older SSE failed terminal", async () => {
  const { foldResearchActivityPresentation, mergeResearchActivityHistory } =
    await import("../../researchActivityStreamCore.mjs");
  const canonical = t2CanonicalCurrent("completed");
  const durable = [
    t2Lifecycle({
      eventId: "durable:wu-1:completed",
      phase: "completed",
      occurredAt: "2026-10-06T02:20:00Z",
      sequence: -1,
    }),
  ];
  const olderFailed = [
    t2Lifecycle({
      eventId: "stream:wu-1:failed-old",
      phase: "failed",
      occurredAt: "2026-10-06T02:10:00Z",
      sequence: 12,
    }),
  ];

  const merged = mergeResearchActivityHistory(
    durable,
    olderFailed,
    [],
    canonical,
  );
  const folded = foldResearchActivityPresentation(
    merged.primaryActivities,
    merged.collapsedDetails,
    canonical,
  );
  assert.equal(folded.blocks[0].currentStatus, "completed");
  assert.equal(
    folded.blocks[0].terminalActivity.eventId,
    "durable:wu-1:completed",
  );
  assert.ok(
    !folded.blocks[0].rows.some(
      (row) => row.eventId === "stream:wu-1:failed-old",
    ),
  );
});

test("T2 canonical failed cannot be overwritten by an older SSE completed terminal", async () => {
  const { foldResearchActivityPresentation, mergeResearchActivityHistory } =
    await import("../../researchActivityStreamCore.mjs");
  const canonical = t2CanonicalCurrent("failed");
  const durable = [
    t2Lifecycle({
      eventId: "durable:wu-1:failed",
      phase: "failed",
      occurredAt: "2026-10-06T02:20:00Z",
      sequence: -1,
    }),
  ];
  const olderCompleted = [
    t2Lifecycle({
      eventId: "stream:wu-1:completed-old",
      phase: "completed",
      occurredAt: "2026-10-06T02:10:00Z",
      sequence: 12,
    }),
  ];

  const merged = mergeResearchActivityHistory(
    durable,
    olderCompleted,
    [],
    canonical,
  );
  const folded = foldResearchActivityPresentation(
    merged.primaryActivities,
    merged.collapsedDetails,
    canonical,
  );
  assert.equal(folded.blocks[0].currentStatus, "failed");
  assert.equal(
    folded.blocks[0].terminalActivity.eventId,
    "durable:wu-1:failed",
  );
  assert.ok(
    !folded.blocks[0].rows.some(
      (row) => row.eventId === "stream:wu-1:completed-old",
    ),
  );
});

test("T2 refresh or re-enter plus full replay keeps canonical state and does not duplicate durable history", async () => {
  const { foldResearchActivityPresentation, mergeResearchActivityHistory } =
    await import("../../researchActivityStreamCore.mjs");
  const canonical = t2CanonicalCurrent("completed", { revision: 20 });
  const durable = [
    t2Lifecycle({
      eventId: "durable:wu-1:completed",
      phase: "completed",
      occurredAt: "2026-10-06T02:20:00Z",
      sequence: -2,
    }),
    t2Lifecycle({
      eventId: "durable:accepted-result",
      phase: "accepted",
      occurredAt: "2026-10-06T02:21:00Z",
      sequence: -1,
      eventType: "meaningful_progress",
      activityKey: "accepted-result:result-1",
      headline: "可信研究结果已更新",
    }),
  ].map((row, index) =>
    index === 1
      ? { ...row, resultRefs: ["result-1"], scientificStateVersions: [4] }
      : row,
  );
  const fullReplay = [
    t2Lifecycle({
      eventId: "stream:wu-1:completed",
      phase: "completed",
      occurredAt: "2026-10-06T02:20:00Z",
      sequence: 100,
      revision: 20,
    }),
    {
      ...durable[1],
      eventId: "stream:accepted-result",
      sequence: 101,
      revision: 20,
    },
  ];

  const first = mergeResearchActivityHistory(
    durable,
    fullReplay,
    [],
    canonical,
  );
  const second = mergeResearchActivityHistory(
    durable,
    fullReplay,
    [],
    canonical,
  );
  assert.deepEqual(second, first);
  assert.deepEqual(
    first.primaryActivities.map((row) => row.eventId),
    ["durable:wu-1:completed", "durable:accepted-result"],
  );
  assert.equal(
    new Set(first.primaryActivities.map((row) => row.eventId)).size,
    first.primaryActivities.length,
  );
  const folded = foldResearchActivityPresentation(
    first.primaryActivities,
    first.collapsedDetails,
    canonical,
  );
  assert.equal(folded.blocks[0].currentStatus, "completed");
  assert.deepEqual(folded.blocks[0].resultRefs, ["result-1"]);
  assert.deepEqual(folded.blocks[0].scientificStateVersions, [4]);
});

test("T2 full replay dedupes a durable capability call with its SSE event", async () => {
  const { foldResearchActivityPresentation, mergeResearchActivityHistory } =
    await import("../../researchActivityStreamCore.mjs");
  const canonical = t2CanonicalCurrent("completed", { revision: 20 });
  const activityKey = createHash("sha256")
    .update("call-1")
    .digest("hex")
    .slice(0, 24);
  const durable = [
    {
      ...t2Lifecycle({
        eventId: "durable:capability-call:call-1",
        phase: "completed",
        occurredAt: "2026-10-06T02:19:00Z",
        sequence: -1,
        eventType: "research_tool_summary",
        activityKey,
      }),
      presentationLevel: "detail",
    },
  ];
  const replay = [
    {
      ...durable[0],
      eventId: "stream:capability-call:call-1",
      sequence: 100,
      revision: 20,
    },
  ];

  const merged = mergeResearchActivityHistory(durable, [], replay, canonical);
  assert.equal(merged.collapsedDetails.length, 1);
  assert.equal(
    merged.collapsedDetails[0].eventId,
    "durable:capability-call:call-1",
  );
  const folded = foldResearchActivityPresentation(
    merged.primaryActivities,
    merged.collapsedDetails,
    canonical,
  );
  assert.equal(folded.blocks[0].processActivities.length, 1);
});

test("T2 stream reset bootstrap then old SSE replay cannot replace canonical terminal", async () => {
  const {
    createResearchActivityState,
    foldResearchActivityPresentation,
    mergeResearchActivityHistory,
    reduceResearchActivityEvent,
  } = await import("../../researchActivityStreamCore.mjs");
  const canonical = t2CanonicalCurrent("completed", { revision: 20 });
  const durable = [
    t2Lifecycle({
      eventId: "durable:wu-1:completed",
      phase: "completed",
      occurredAt: "2026-10-06T02:20:00Z",
      sequence: -1,
    }),
  ];

  // stream_reset creates a fresh stream buffer; an old replay may arrive after
  // the durable Task bootstrap. Its revision still cannot become current truth.
  let replayState = createResearchActivityState("task-1:reset");
  replayState = reduceResearchActivityEvent(
    replayState,
    t2Lifecycle({
      eventId: "stream:replayed-failed",
      phase: "failed",
      occurredAt: "2026-10-06T02:10:00Z",
      sequence: 12,
      revision: 19,
    }),
    "task-1:reset",
  );

  const merged = mergeResearchActivityHistory(
    durable,
    replayState.primaryActivities,
    replayState.collapsedDetails,
    canonical,
  );
  const folded = foldResearchActivityPresentation(
    merged.primaryActivities,
    merged.collapsedDetails,
    canonical,
  );
  assert.equal(folded.blocks[0].currentStatus, "completed");
  assert.equal(
    folded.blocks[0].terminalActivity.eventId,
    "durable:wu-1:completed",
  );
  assert.ok(
    !merged.primaryActivities.some(
      (row) => row.eventId === "stream:replayed-failed",
    ),
  );
});

test("T2 canonical revision advance makes older terminal SSE presentation-only and non-authoritative", async () => {
  const { foldResearchActivityPresentation, mergeResearchActivityHistory } =
    await import("../../researchActivityStreamCore.mjs");
  const canonical = t2CanonicalCurrent("running", {
    revision: 21,
    finishedAt: null,
  });
  const durable = [
    t2Lifecycle({
      eventId: "durable:wu-1:running",
      phase: "running",
      occurredAt: "2026-10-06T02:20:00Z",
      sequence: -1,
    }),
  ];
  const staleFailed = [
    t2Lifecycle({
      eventId: "stream:wu-1:failed-rev20",
      phase: "failed",
      occurredAt: "2026-10-06T02:30:00Z",
      sequence: 200,
      revision: 20,
    }),
  ];

  const merged = mergeResearchActivityHistory(
    durable,
    staleFailed,
    [],
    canonical,
  );
  const folded = foldResearchActivityPresentation(
    merged.primaryActivities,
    merged.collapsedDetails,
    canonical,
  );
  assert.equal(folded.blocks[0].currentStatus, "running");
  assert.equal(folded.blocks[0].terminalActivity, null);
  assert.ok(
    !merged.primaryActivities.some(
      (row) => row.eventId === "stream:wu-1:failed-rev20",
    ),
  );
});

test("T2 retained failed history can never become the current terminal without canonical support", async () => {
  const { foldResearchActivityPresentation, mergeResearchActivityHistory } =
    await import("../../researchActivityStreamCore.mjs");
  const canonical = t2CanonicalCurrent("completed", {
    revision: 20,
    // Missing finish time means this no-revision SSE row cannot be proven stale;
    // it may be retained as history, but current state still comes from canonical.
    finishedAt: null,
  });
  const durable = [
    t2Lifecycle({
      eventId: "durable:wu-1:completed",
      phase: "completed",
      occurredAt: "2026-10-06T02:20:00Z",
      sequence: -1,
    }),
  ];
  const unverifiedPastFailed = [
    t2Lifecycle({
      eventId: "stream:wu-1:failed-unverified",
      phase: "failed",
      occurredAt: "2026-10-06T02:10:00Z",
      sequence: 12,
    }),
  ];

  const merged = mergeResearchActivityHistory(
    durable,
    unverifiedPastFailed,
    [],
    canonical,
  );
  const folded = foldResearchActivityPresentation(
    merged.primaryActivities,
    merged.collapsedDetails,
    canonical,
  );
  assert.ok(
    folded.blocks[0].rows.some(
      (row) => row.eventId === "stream:wu-1:failed-unverified",
    ),
  );
  assert.equal(folded.blocks[0].currentStatus, "completed");
  assert.equal(
    folded.blocks[0].terminalActivity.eventId,
    "durable:wu-1:completed",
  );
  assert.ok(
    !folded.blocks[0].processActivities.some(
      (row) => row.eventId === "stream:wu-1:failed-unverified",
    ),
  );
});

test("T2 missing canonical current state fails closed instead of deriving terminal from timeline", async () => {
  const { foldResearchActivityPresentation } =
    await import("../../researchActivityStreamCore.mjs");
  const failedHistory = [
    t2Lifecycle({
      eventId: "history:wu-1:failed",
      phase: "failed",
      occurredAt: "2026-10-06T02:10:00Z",
      sequence: 1,
    }),
  ];

  const folded = foldResearchActivityPresentation(failedHistory, [], null);
  assert.equal(folded.blocks[0].currentStatus, null);
  assert.equal(folded.blocks[0].terminalActivity, null);
  assert.equal(folded.endedAt, null);
});

test("T2 activity dedupe never drops the same activity key from a different WorkUnit", async () => {
  const { distinctResearchActivities } =
    await import("../../researchActivityStreamCore.mjs");
  const rows = [
    { eventId: "e1", workUnitId: "wu-1", activityKey: "tool", headline: "A" },
    { eventId: "e2", workUnitId: "wu-2", activityKey: "tool", headline: "B" },
  ];
  assert.deepEqual(distinctResearchActivities(rows), rows);
});

test("T2 research activity Artifact refs fail closed against the canonical visible set", async () => {
  const { filterResearchActivityArtifactRefs } =
    await import("../../researchActivityStreamCore.mjs");
  const [filtered] = filterResearchActivityArtifactRefs(
    [
      {
        eventId: "event-1",
        artifactRefs: [
          "artifact-visible",
          "artifact-hidden",
          "artifact-visible",
        ],
      },
    ],
    ["artifact-visible"],
  );

  assert.deepEqual(filtered.artifactRefs, ["artifact-visible"]);
});

test("T2 Task GET model wires durable history and canonical current state into the production panel", () => {
  const projection = durableProjection([
    durableSubstage({ id: "instance-one", status: "completed" }),
  ]);
  projection.taskProgress.status = "completed";
  projection.researchProcessHistory = [
    {
      eventId: "durable:capability-call:call-1",
      sequence: -1,
      revision: null,
      eventType: "research_tool_summary",
      presentationLevel: "detail",
      workUnitId: "wu-1",
      headline: "研究工具执行完成",
      summary: null,
      occurredAt: "2026-10-06T02:19:00Z",
      startedAt: "2026-10-06T02:10:00Z",
      aggregateCount: null,
      activityKey: createHash("sha256")
        .update("call-1")
        .digest("hex")
        .slice(0, 24),
      phase: "completed",
      artifactRefs: ["artifact-visible"],
      resultRefs: [],
      scientificStateVersions: [],
    },
  ];
  const model = workbenchModelFromApiTask({
    taskId: "task-1",
    ownerId: "owner-1",
    workspaceId: "workspace-1",
    createdBy: "user-1",
    title: "Authority wiring",
    revision: 10,
    currentStage: "problem_modeling",
    status: "completed",
    resultSource: "live",
    stages: [
      "problem_modeling",
      "solution_design",
      "execution_validation",
      "insight_generation",
    ],
    artifacts: [],
    confirmations: [],
    capabilityCalls: [],
    workUnits: [
      {
        workUnitId: "wu-1",
        status: "completed",
        dispatchedAt: "2026-10-06T02:10:00Z",
        finishedAt: "2026-10-06T02:20:00Z",
      },
    ],
    startedAt: "2026-10-06T02:00:00Z",
    finishedAt: "2026-10-06T02:21:00Z",
    progressProjection: projection,
  });

  assert.equal(model.researchProcessHistory.length, 1);
  assert.equal(model.researchCurrentState.taskStatus, "completed");
  assert.deepEqual(model.researchCurrentState.workUnits, [
    {
      workUnitId: "wu-1",
      status: "completed",
      startedAt: "2026-10-06T02:10:00Z",
      finishedAt: "2026-10-06T02:20:00Z",
    },
  ]);

});
