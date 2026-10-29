import { describe, expect, it } from "vitest";
import { STATUS_LABELS, type Task } from "@openair/shared";
import type { WorkbenchTaskModel } from "../model/workbenchTask";
import { taskFromWorkbenchModel } from "../model/taskFromFixture";
import {
  boardStatusPresentation,
  buildBoardPackOptions,
  buildBoardRowsFromTasks,
  filterBoardRowsForView,
  type BoardTaskRow
} from "./boardRows";

const NOW = Date.parse("2026-08-06T12:00:00.000Z");

function row(taskId: string, updatedAt: string, skillPack: string): BoardTaskRow {
  const task = {
    taskId,
    ownerId: "owner-1",
    workspaceId: "workspace-1",
    createdBy: "owner-1",
    title: taskId,
    currentStage: "problem_modeling",
    status: "running",
    resultSource: "live",
    skillPack,
    stages: ["problem_modeling"],
    artifacts: [],
    confirmations: [],
    capabilityCalls: [],
    updatedAt
  } satisfies Task;
  return {
    task,
    flowLabel: skillPack,
    currentPhaseLabel: "问题建模",
    keyProgress: "—",
    updatedAt,
    artifactKinds: []
  };
}

describe("TaskBoard row filters", () => {
  it("applies the inclusive 7-day updatedAt boundary with a controlled clock", () => {
    const exactlySevenDays = new Date(NOW - 7 * 24 * 60 * 60 * 1000).toISOString();
    const olderByOneMs = new Date(NOW - 7 * 24 * 60 * 60 * 1000 - 1).toISOString();
    const rows = [
      row("inside", exactlySevenDays, "new-pack"),
      row("outside", olderByOneMs, "new-pack")
    ];

    expect(filterBoardRowsForView(rows, "all", "all", "7d", NOW).map(r => r.task.taskId))
      .toEqual(["inside"]);
    expect(filterBoardRowsForView(rows, "all", "all", "all", NOW).map(r => r.task.taskId))
      .toEqual(["inside", "outside"]);
  });

  it("groups all non-terminal project states under the user-facing active filter", () => {
    const updatedAt = new Date(NOW).toISOString();
    const active = [
      row("running", updatedAt, "pack"),
      { ...row("draft", updatedAt, "pack"), task: { ...row("draft", updatedAt, "pack").task, status: "draft" as const } },
      { ...row("confirm", updatedAt, "pack"), task: { ...row("confirm", updatedAt, "pack").task, status: "awaiting_confirmation" as const } },
    ];
    const terminal = [
      { ...row("done", updatedAt, "pack"), task: { ...row("done", updatedAt, "pack").task, status: "completed" as const } },
      { ...row("failed", updatedAt, "pack"), task: { ...row("failed", updatedAt, "pack").task, status: "failed" as const } },
      { ...row("cancelled", updatedAt, "pack"), task: { ...row("cancelled", updatedAt, "pack").task, status: "cancelled" as const } },
    ];

    expect(filterBoardRowsForView([...active, ...terminal], "active", "all", "all", NOW).map(r => r.task.taskId))
      .toEqual(["running", "draft", "confirm"]);
  });

  it("adds server packs and real task pack values without a hard-coded allowlist", () => {
    const rows = [row("task-1", new Date(NOW).toISOString(), "task-only-pack")];
    const options = buildBoardPackOptions(rows, [
      { key: "brand-new-pack", label: "Brand New Pack" }
    ]);

    expect(options).toEqual([
      { value: "all", label: "全部流程" },
      { value: "brand-new-pack", label: "Brand New Pack" },
      { value: "task-only-pack", label: "task-only-pack" }
    ]);
  });
});

function workbenchModel(
  executionDisplayStatus: string,
  taskStatus: WorkbenchTaskModel["taskStatus"] = "awaiting_confirmation",
): WorkbenchTaskModel {
  return {
    taskId: `task-${executionDisplayStatus}`,
    revision: 1,
    title: executionDisplayStatus,
    goalSummary: "status consistency",
    domain: "research_task",
    currentUnderlyingStage: "problem_modeling",
    taskStatus,
    executionDisplayStatus,
    progressTrust: { kind: "legacy" },
    progressRevision: null,
    stageCounts: null,
    collaborationNodes: [],
    collaborationEvents: [],
    lifecycleEvents: [],
    resultSource: "live",
    finalResult: null,
    executionMode: "stage_confirmation",
    stageGateResumeApproved: false,
    researchDesign: null,
    createdAt: "2026-08-06T00:00:00.000Z",
    updatedAt: "2026-08-06T01:00:00.000Z",
    stages: [],
    artifacts: [],
    capabilityCalls: [],
    trustRecords: [],
    userVisibleEvents: [],
  };
}

describe("TaskBoard canonical execution status", () => {
  it.each([
    ["needs_input", "awaiting_confirmation", "待用户补充"],
    ["blocked_recoverable", "running", "执行受阻"],
    ["external_result_unknown", "awaiting_confirmation", "结果待核查"],
    ["research_design_incomplete", "awaiting_confirmation", "研究方案未形成"],
    ["awaiting_confirmation", "awaiting_confirmation", STATUS_LABELS.awaiting_confirmation],
    ["running", "running", STATUS_LABELS.running],
    ["completed", "completed", STATUS_LABELS.completed],
  ] as const)(
    "preserves %s from the canonical workbench model instead of collapsing to %s",
    (executionDisplayStatus, taskStatus, expectedLabel) => {
      const model = workbenchModel(executionDisplayStatus, taskStatus);
      const converted = taskFromWorkbenchModel(model);
      const [boardRow] = buildBoardRowsFromTasks([converted], false);
      const presentation = boardStatusPresentation(boardRow.task);

      expect(converted.executionDisplayStatus).toBe(model.executionDisplayStatus);
      expect(boardRow.task.executionDisplayStatus).toBe(model.executionDisplayStatus);
      expect(presentation.status).toBe(model.executionDisplayStatus);
      expect(presentation.label).toBe(expectedLabel);
    },
  );

  it("falls back to taskStatus only when executionDisplayStatus is absent", () => {
    const legacy = row("legacy", new Date(NOW).toISOString(), "legacy-pack").task;
    expect(boardStatusPresentation(legacy)).toEqual({ status: "running", label: STATUS_LABELS.running });

    const canonicalUnknown = taskFromWorkbenchModel(workbenchModel("future_canonical_status", "running"));
    expect(boardStatusPresentation(canonicalUnknown)).toEqual({
      status: "future_canonical_status",
      label: "状态待确认",
    });
  });
});
