import assert from "node:assert/strict";

import test from "node:test";

import { safeRightPanelText, projectRightPanelArtifacts } from "../src/components/workbench/workbenchRightPanelCore.mjs";
import { buildWorkbenchScreenView } from "../src/model/workbenchScreenCore.mjs";

test("presentation boundary rejects engineering language but keeps research copy", () => {
  for (const text of [
    "worker claim pending",
    "provider transport uncertain",
    "ExecutionDecision created",
    "WorkUnit running",
    "recovery lifecycle receipt created",
    "taskId=task-1 revision=4",
    "服务端进度投影不可用",
  ]) {
    assert.equal(safeRightPanelText(text, "当前研究进展待确认。"), "当前研究进展待确认。");
  }
  assert.equal(safeRightPanelText("正在整理研究证据", "fallback"), "正在整理研究证据");
});

test("artifact source labels describe user meaning, not runtime plumbing", () => {
  const projection = { state: "usable", badge: { label: "可使用", color: "success" }, gate: {}, visibleAllowedActions: [] };
  const rows = projectRightPanelArtifacts("task-a", [
    { artifact: { id: "a", taskId: "task-a", title: "结果 A", sourceMode: "replay" }, projection },
    { artifact: { id: "b", taskId: "task-a", title: "结果 B", sourceMode: "inline" }, projection },
    { artifact: { id: "c", taskId: "task-a", title: "结果 C", sourceMode: "external" }, projection },
  ]);
  assert.deepEqual(rows.map((row) => row.sourceModeLabel), ["历史结果", "当前项目产物", ""]);
});

test("workbench screen sanitizes authored collaboration labels and details", () => {
  const screen = buildWorkbenchScreenView({
    taskId: "task-a",
    revision: 1,
    progressRevision: null,
    executionDisplayStatus: "running",
    progressTrust: { kind: "legacy" },
    stages: [],
    collaborationNodes: [{
      workUnitId: "wu-a",
      agentName: "Planner worker",
      objective: "provider runtime unavailable",
      status: "running",
    }],
    collaborationEvents: [],
    lifecycleEvents: [],
  });
  assert.equal(screen.rightCollaboration[0].title, "研究协作任务");
  assert.equal(screen.rightCollaboration[0].detail, "正在处理当前研究任务。");
});
