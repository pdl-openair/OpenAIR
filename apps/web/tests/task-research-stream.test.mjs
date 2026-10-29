import assert from "node:assert/strict";

import test from "node:test";
import {
  assertSafeResearchActivity,
  createResearchActivityState,
  distinctResearchActivities,
  foldResearchActivityPresentation,
  reduceResearchActivityEvent,
} from "../src/model/researchActivityStreamCore.mjs";

function event(overrides = {}) {
  return {
    eventId: "tre-1",
    taskId: "task-a",
    sequence: 1,
    eventType: "research_progress",
    presentationLevel: "primary",
    workUnitId: "work-a",
    headline: "当前步骤已经开始",
    summary: null,
    occurredAt: "2026-08-06T00:00:00Z",
    invalidateScopes: ["collaboration"],
    ...overrides,
  };
}

test("heartbeat and stream_reset never enter activity lists", () => {
  const initial = createResearchActivityState("A");
  assert.deepEqual(reduceResearchActivityEvent(initial, event({ eventType: "heartbeat" })), initial);
  assert.deepEqual(reduceResearchActivityEvent(initial, event({ eventType: "stream_reset" })), initial);
});

test("duplicate eventId is idempotent across the whole state", () => {
  const once = reduceResearchActivityEvent(createResearchActivityState("A"), event());
  const twice = reduceResearchActivityEvent(once, event({ presentationLevel: "detail" }));
  assert.equal(twice.primaryActivities.length, 1);
  assert.equal(twice.collapsedDetails.length, 0);
});

test("canonical activity identity collapses one lifecycle but preserves distinct planning actions with identical copy", () => {
  const rows = [
    event({ eventId: "attempt-1-start", workUnitId: null, eventType: "meaningful_progress", activityKey: "planning-attempt:pa-1", headline: "正在组织研究方案", phase: "started", sequence: 1 }),
    event({ eventId: "attempt-2-start", workUnitId: null, eventType: "meaningful_progress", activityKey: "planning-attempt:pa-2", headline: "正在组织研究方案", phase: "started", sequence: 2 }),
    event({ eventId: "design-v2-formed", workUnitId: null, eventType: "meaningful_progress", activityKey: "research-design:rd-1:v2", headline: "研究方案已更新", phase: "formed", sequence: 3 }),
    event({ eventId: "design-v2-confirmed", workUnitId: null, eventType: "meaningful_progress", activityKey: "research-design:rd-1:v2", headline: "研究方案已确认，准备进入执行", phase: "confirmed", sequence: 4 }),
    event({ eventId: "design-v3-formed", workUnitId: null, eventType: "meaningful_progress", activityKey: "research-design:rd-1:v3", headline: "研究方案已更新", phase: "formed", sequence: 5 }),
  ];
  const distinct = distinctResearchActivities(rows);
  assert.deepEqual(distinct.map((row) => row.eventId), [
    "attempt-1-start",
    "attempt-2-start",
    "design-v2-confirmed",
    "design-v3-formed",
  ]);
});

test("same capability-call lifecycle churn compacts but distinct WorkUnit activity stays append-only", () => {
  const first = reduceResearchActivityEvent(createResearchActivityState("A"), event({
    eventId: "tool-1", eventType: "research_tool_summary", presentationLevel: "detail",
    activityKey: "call-a", headline: "正在使用学术检索", phase: "started", occurredAt: "2026-08-06T00:00:03Z",
  }));
  const completed = reduceResearchActivityEvent(first, event({
    eventId: "tool-2", eventType: "research_tool_summary", presentationLevel: "detail",
    activityKey: "call-a", headline: "学术检索已完成", phase: "completed", sequence: 2, occurredAt: "2026-08-06T00:00:08Z",
  }));
  const distinct = reduceResearchActivityEvent(completed, event({
    eventId: "tool-3", eventType: "research_tool_summary", presentationLevel: "detail",
    activityKey: "call-b", headline: "资料筛选已完成", phase: "completed", sequence: 3,
  }));
  assert.deepEqual(distinct.collapsedDetails.map((row) => row.headline), [
    "学术检索已完成",
    "资料筛选已完成",
  ]);
  assert.equal(distinct.collapsedDetails[0].startedAt, "2026-08-06T00:00:03Z");
  assert.equal(distinct.collapsedDetails[0].occurredAt, "2026-08-06T00:00:08Z");
});

test("long research history and eventId dedupe do not expire at a fixed item count", () => {
  const rows = Array.from({ length: 320 }, (_, index) => event({
    eventId: `history-${index + 1}`,
    eventType: "meaningful_progress",
    headline: `Research fact ${index + 1}`,
    sequence: index + 1,
  }));
  const state = rows.reduce(
    (current, row) => reduceResearchActivityEvent(current, row),
    createResearchActivityState("A"),
  );
  assert.equal(state.primaryActivities.length, 320);
  assert.equal(state.seenEventIds.length, 320);

  const replayed = reduceResearchActivityEvent(state, rows[0]);
  assert.equal(replayed.primaryActivities.length, 320);
  assert.equal(replayed.seenEventIds.length, 320);

});

test("Task A/B/A binding resets prior memory", () => {
  const a = reduceResearchActivityEvent(createResearchActivityState("A1"), event());
  const b = reduceResearchActivityEvent(a, event({ eventId: "b-1", taskId: "task-b" }), "B");
  const aAgain = reduceResearchActivityEvent(b, event({ eventId: "a-2" }), "A2");
  assert.deepEqual(aAgain.primaryActivities.map((row) => row.taskId), ["task-a"]);
});

test("nested secrets and raw reasoning are rejected", () => {
  for (const unsafe of [
    { payload: { systemPrompt: "hidden" } },
    { payload: { rawResponse: "provider body" } },
    { payload: { authorization: "Bearer abcdefghijklmnop" } },
    { payload: { shellOutput: ["full"] } },
  ]) assert.throws(() => assertSafeResearchActivity(unsafe));
});

test("presentation preserves research facts and requires canonical authority for completion", () => {
  const rows = [
    event({ eventId: "run-1", workUnitId: null, headline: "研究目标已进入执行准备", phase: "scheduled", sequence: 1, occurredAt: "2026-08-06T00:00:00Z" }),
    event({ eventId: "run-2", workUnitId: null, headline: "研究目标已经开始", phase: "started", sequence: 2, occurredAt: "2026-08-06T00:00:01Z" }),
    event({ eventId: "agent-1", headline: "关键文献定位 Agent 开始工作", phase: "started", sequence: 3, occurredAt: "2026-08-06T00:00:02Z" }),
    event({ eventId: "tool-1", eventType: "research_tool_summary", presentationLevel: "detail", activityKey: "call-a", headline: "正在使用学术检索", phase: "started", sequence: 4, occurredAt: "2026-08-06T00:00:03Z" }),
    event({ eventId: "tool-2", eventType: "research_tool_summary", presentationLevel: "detail", activityKey: "call-a", headline: "学术检索已完成", phase: "completed", sequence: 5, occurredAt: "2026-08-06T00:00:08Z" }),
    event({ eventId: "tool-3", eventType: "research_tool_summary", presentationLevel: "detail", activityKey: "call-b", headline: "资料筛选已完成", phase: "completed", sequence: 6, occurredAt: "2026-08-06T00:00:10Z" }),
    event({ eventId: "tool-4", eventType: "research_tool_summary", presentationLevel: "detail", activityKey: "call-c", headline: "证据对比已完成", phase: "completed", sequence: 7, occurredAt: "2026-08-06T00:00:12Z" }),
    event({ eventId: "artifact-1", eventType: "meaningful_progress", headline: "产物已经形成，正在核对可用性", phase: "persisted", artifactRefs: ["artifact-a"], sequence: 8, occurredAt: "2026-08-06T00:00:13Z" }),
    event({ eventId: "agent-2", eventType: "meaningful_progress", headline: "关键文献定位 Agent 完成当前工作", phase: "completed", sequence: 9, occurredAt: "2026-08-06T00:00:15Z" }),
    event({ eventId: "run-3", workUnitId: null, eventType: "meaningful_progress", headline: "研究执行已结束，正在核对最终状态", phase: "completed", sequence: 10, occurredAt: "2026-08-06T00:00:16Z" }),
  ];
  const beforeWorkUnitTerminal = rows.slice(0, 8).reduce(
    (current, row) => reduceResearchActivityEvent(current, row),
    createResearchActivityState("A"),
  );
  const beforeTerminalFold = foldResearchActivityPresentation(
    beforeWorkUnitTerminal.primaryActivities,
    beforeWorkUnitTerminal.collapsedDetails,
  );
  assert.equal(beforeTerminalFold.blocks[0].terminalActivity, null);

  const state = rows.reduce((current, row) => reduceResearchActivityEvent(current, row), createResearchActivityState("A"));
  const withoutAuthority = foldResearchActivityPresentation(state.primaryActivities, state.collapsedDetails);
  assert.equal(withoutAuthority.blocks[0].terminalActivity, null);
  assert.equal(withoutAuthority.endedAt, null);
  const folded = foldResearchActivityPresentation(state.primaryActivities, state.collapsedDetails, {
    revision: 10,
    taskStatus: "completed",
    taskFinishedAt: "2026-08-06T00:00:16Z",
    workUnits: [{ workUnitId: "work-a", status: "completed", startedAt: "2026-08-06T00:00:02Z", finishedAt: "2026-08-06T00:00:15Z" }],
  });

  assert.equal(state.primaryActivities.length, 6);
  assert.equal(state.collapsedDetails.length, 3);
  assert.equal(folded.unscoped.length, 1);
  assert.equal(folded.blocks.length, 1);
  const processIds = folded.blocks[0].processActivities.map((row) => row.eventId);
  for (const id of ["tool-2", "tool-3", "tool-4", "artifact-1"]) {
    assert.equal(processIds.filter((value) => value === id).length, 1);
  }
  assert.deepEqual(folded.blocks[0].artifactRefs, ["artifact-a"]);
  assert.equal(folded.blocks[0].terminalActivity?.eventId, "agent-2");
  assert.equal(folded.blocks[0].startedAt, "2026-08-06T00:00:02Z");
  assert.equal(folded.blocks[0].endedAt, "2026-08-06T00:00:15Z");
  assert.equal(folded.startedAt, "2026-08-06T00:00:00Z");
  assert.equal(folded.endedAt, "2026-08-06T00:00:16Z");
  assert.equal(state.collapsedDetails[0].startedAt, "2026-08-06T00:00:03Z");
});

test("unscoped research facts use semantic filtering without a fixed-count cutoff", () => {
  const rows = [
    event({ eventId: "run-scheduled", workUnitId: null, eventType: "research_progress", phase: "scheduled", sequence: 1 }),
    event({ eventId: "run-started", workUnitId: null, eventType: "research_progress", phase: "started", sequence: 2 }),
    event({ eventId: "global-1", workUnitId: null, eventType: "meaningful_progress", headline: "Evidence screening completed", phase: "completed", sequence: 3 }),
    event({ eventId: "global-2", workUnitId: null, eventType: "meaningful_progress", headline: "Comparison matrix updated", phase: "completed", sequence: 4 }),
    event({ eventId: "global-3", workUnitId: null, eventType: "meaningful_progress", headline: "Validation summary formed", phase: "completed", sequence: 5 }),
    event({ eventId: "global-4", workUnitId: null, eventType: "meaningful_progress", headline: "Research artifact persisted", phase: "persisted", sequence: 6 }),
  ];
  const state = rows.reduce(
    (current, row) => reduceResearchActivityEvent(current, row),
    createResearchActivityState("A"),
  );
  const folded = foldResearchActivityPresentation(state.primaryActivities, state.collapsedDetails);

  assert.deepEqual(folded.unscoped.map((row) => row.eventId), ["global-1", "global-2", "global-3", "global-4"]);

});

test("same WorkUnit primary research facts remain append-only", () => {
  const first = reduceResearchActivityEvent(createResearchActivityState("A"), event({
    eventId: "progress-1", eventType: "research_progress", headline: "关键文献定位 Agent 开始工作",
  }));
  const second = reduceResearchActivityEvent(first, event({
    eventId: "progress-2", eventType: "meaningful_progress", headline: "关键文献定位 Agent 完成当前工作", sequence: 2,
  }));
  assert.deepEqual(second.primaryActivities.map((row) => row.headline), [
    "关键文献定位 Agent 开始工作",
    "关键文献定位 Agent 完成当前工作",
  ]);
});
