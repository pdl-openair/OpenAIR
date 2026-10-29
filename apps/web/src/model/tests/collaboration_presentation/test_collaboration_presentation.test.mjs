import assert from "node:assert/strict";
import { test } from "vitest";

import { composeCollaborationPresentation } from "../../collaborationPresentationCore.mjs";

const research = (id, workUnitId, occurredAt, artifactRefs = []) => ({
  id,
  workUnitId,
  occurredAt,
  artifactRefs,
  rows: [{ eventId: `${id}-event`, occurredAt }],
});

const userMessage = (id, at, text = "补充范围") => ({
  id,
  role: "user",
  text,
  at,
  messageType: "controller_supplement",
  artifactRefs: [],
});

const resultMessage = (id, at, artifactRefs = []) => ({
  id,
  role: "assistant",
  text: "internal-readable-result",
  at,
  messageType: "research_dispatch_result",
  artifactRefs,
  capabilityCallId: `call-${id}`,
  userFacingNarrative: { summary: `结果 ${id}` },
});

const action = (itemId, workUnitId, allowedActions = ["supplement_input"]) => ({
  itemId,
  kind: "provide_input",
  title: "需要补充研究材料",
  reason: "当前材料不足",
  workUnitId,
  allowedActions,
  commandContexts: {},
});

const artifact = (id, createdAt, sourceCapabilityCallId = null) => ({
  id,
  createdAt,
  sourceCapabilityCallId,
});

function ids(nodes) {
  return nodes.map((node) => node.id);
}

function relatedIds(node) {
  return node.related.map((item) => item.id);
}

test("CROSS_TYPE_CHRONOLOGY: heterogeneous public facts are composed by chronology and explicit relation, not source bucket", () => {
  const nodes = composeCollaborationPresentation({
    researchFacts: [
      research("research-work-unit:A", "A", "2026-08-11T09:00:00Z"),
      research("research-work-unit:B", "B", "2026-08-11T09:20:00Z", ["artifact-B"]),
    ],
    messages: [
      resultMessage("result-independent", "2026-08-11T09:30:00Z"),
      userMessage("user-1", "2026-08-11T09:10:00Z"),
    ],
    actions: [action("action-B", "B")],
    artifacts: [artifact("artifact-B", "2026-08-11T09:25:00Z")],
  });

  assert.deepEqual(ids(nodes), [
    "research-work-unit:A",
    "message:user-1",
    "research-work-unit:B",
    "message:result-independent",
  ]);
  const nodeB = nodes.find((node) => node.id === "research-work-unit:B");
  assert.deepEqual(relatedIds(nodeB), ["artifact:artifact-B", "action:action-B"]);
});

test("DETERMINISTIC_ORDER: shuffled source arrays produce identical presentation order", () => {
  const facts = {
    researchFacts: [
      research("research-work-unit:A", "A", "2026-08-11T09:00:00Z"),
      research("research-work-unit:B", "B", "2026-08-11T09:20:00Z"),
    ],
    messages: [
      userMessage("u2", "2026-08-11T09:15:00Z"),
      userMessage("u1", "2026-08-11T09:05:00Z"),
    ],
    actions: [action("z", null), action("a", null)],
    artifacts: [artifact("z-art", "2026-08-11T09:25:00Z"), artifact("a-art", "2026-08-11T09:25:00Z")],
  };
  const first = composeCollaborationPresentation(facts);
  const second = composeCollaborationPresentation({
    researchFacts: [...facts.researchFacts].reverse(),
    messages: [...facts.messages].reverse(),
    actions: [...facts.actions].reverse(),
    artifacts: [...facts.artifacts].reverse(),
  });
  assert.deepEqual(ids(first), ids(second));
});

test("USER_INTERVENTION_CHRONOLOGY: a durable user message stays at its real timestamp", () => {
  const nodes = composeCollaborationPresentation({
    researchFacts: [
      research("research-work-unit:A", "A", "2026-08-11T09:00:00Z"),
      research("research-work-unit:B", "B", "2026-08-11T09:20:00Z"),
    ],
    messages: [userMessage("u", "2026-08-11T09:10:00Z")],
  });
  assert.deepEqual(ids(nodes), ["research-work-unit:A", "message:u", "research-work-unit:B"]);
});

test("SAFE_ACTION_COLOCATION: only explicit workUnit identity can grow a research node", () => {
  const nodes = composeCollaborationPresentation({
    researchFacts: [research("research-work-unit:A", "A", "2026-08-11T09:00:00Z")],
    actions: [action("related", "A"), action("unrelated", null)],
  });
  const nodeA = nodes.find((node) => node.id === "research-work-unit:A");
  assert.deepEqual(relatedIds(nodeA), ["action:related"]);
  assert.ok(nodes.some((node) => node.id === "action:unrelated"));
});

test("SAFE_ARTIFACT_COLOCATION: artifact identity links only to the research node that explicitly references it", () => {
  const nodes = composeCollaborationPresentation({
    researchFacts: [
      research("research-work-unit:A", "A", "2026-08-11T09:00:00Z", ["artifact-A"]),
      research("research-work-unit:B", "B", "2026-08-11T09:10:00Z"),
    ],
    artifacts: [artifact("artifact-A", "2026-08-11T09:05:00Z"), artifact("artifact-free", "2026-08-11T09:15:00Z")],
  });
  const nodeA = nodes.find((node) => node.id === "research-work-unit:A");
  assert.deepEqual(relatedIds(nodeA), ["artifact:artifact-A"]);
  assert.ok(nodes.some((node) => node.id === "artifact:artifact-free"));
});

test("NO_UNSAFE_JOIN: similar titles and nearby time never create a relation", () => {
  const nodes = composeCollaborationPresentation({
    researchFacts: [research("research-work-unit:A", "A", "2026-08-11T09:00:00Z")],
    messages: [{
      ...resultMessage("similar", "2026-08-11T09:00:01Z"),
      text: "正在筛选相关研究",
      userFacingNarrative: { summary: "正在筛选相关研究" },
    }],
    actions: [{ ...action("similar-action", null), title: "正在筛选相关研究" }],
  });
  const nodeA = nodes.find((node) => node.id === "research-work-unit:A");
  assert.deepEqual(nodeA.related, []);
  assert.ok(nodes.some((node) => node.id === "message:similar"));
  assert.ok(nodes.some((node) => node.id === "action:similar-action"));
});

test("LATE_RELATED_UPDATE: a late action/artifact grows Node A without reordering A or B", () => {
  const base = composeCollaborationPresentation({
    researchFacts: [
      research("research-work-unit:A", "A", "2026-08-11T09:00:00Z", ["artifact-A"]),
      research("research-work-unit:B", "B", "2026-08-11T09:10:00Z"),
    ],
  });
  const updated = composeCollaborationPresentation({
    researchFacts: [
      research("research-work-unit:A", "A", "2026-08-11T09:00:00Z", ["artifact-A"]),
      research("research-work-unit:B", "B", "2026-08-11T09:10:00Z"),
    ],
    actions: [action("late-A", "A")],
    artifacts: [artifact("artifact-A", "2026-08-11T09:30:00Z")],
  });
  assert.deepEqual(ids(base), ["research-work-unit:A", "research-work-unit:B"]);
  assert.deepEqual(ids(updated), ["research-work-unit:A", "research-work-unit:B"]);
  assert.deepEqual(relatedIds(updated[0]), ["artifact:artifact-A", "action:late-A"]);
});

test("ACTION_AUTHORITY_PRESERVED: composer preserves server allowedActions and never invents an action", () => {
  const serverActions = ["explicit_retry"];
  const nodes = composeCollaborationPresentation({
    actions: [action("authority", null, serverActions)],
  });
  assert.equal(nodes.length, 1);
  assert.deepEqual(nodes[0].source.allowedActions, serverActions);
});

test("ACTION_ARTIFACT_CONTEXT_PRESERVED: artifact refs stay visible without affiliating a standalone action", () => {
  const nodes = composeCollaborationPresentation({
    researchFacts: [research("research-work-unit:A", "A", "2026-08-11T09:00:00Z", ["artifact-A"])],
    actions: [{ ...action("action-artifact", null), objective: "比较检索方案", artifactRefs: ["artifact-A"] }],
  });
  const nodeA = nodes.find((node) => node.id === "research-work-unit:A");
  const actionNode = nodes.find((node) => node.id === "action:action-artifact");
  assert.deepEqual(nodeA.related, []);
  assert.equal(actionNode.source.objective, "比较检索方案");
  assert.deepEqual(actionNode.source.artifactRefs, ["artifact-A"]);
});

test("UNDOCUMENTED_OCCURRED_AT_IGNORED: raw occurredAt cannot change action chronology", () => {
  const clean = composeCollaborationPresentation({
    researchFacts: [research("research-work-unit:A", "A", "2026-08-11T09:00:00Z")],
    actions: [action("poison-time", null)],
  });
  const poisoned = composeCollaborationPresentation({
    researchFacts: [research("research-work-unit:A", "A", "2026-08-11T09:00:00Z")],
    actions: [{ ...action("poison-time", null), occurredAt: "2026-08-11T08:00:00Z" }],
  });
  assert.deepEqual(ids(poisoned), ids(clean));
});

test("COMBINED_RAW_EXTRAS_IGNORED: undocumented extras cannot alter affiliation or order", () => {
  const cleanAction = action("combined-poison", null);
  const facts = {
    researchFacts: [research("research-work-unit:A", "A", "2026-08-11T09:00:00Z", ["artifact-A"])],
    actions: [cleanAction],
  };
  const clean = composeCollaborationPresentation(facts);
  const poisoned = composeCollaborationPresentation({
    ...facts,
    actions: [{
      ...cleanAction,
      occurredAt: "2026-08-11T08:00:00Z",
      workUnitIdAlias: "A",
      capabilityCallId: "call-A",
      arbitraryRawExtra: { workUnitId: "A" },
    }],
  });
  assert.deepEqual(poisoned, clean);
});

test("VALID_WORKUNIT_ID_PRESERVED: declared workUnitId still safely colocates the action", () => {
  const nodes = composeCollaborationPresentation({
    researchFacts: [research("research-work-unit:A", "A", "2026-08-11T09:00:00Z")],
    actions: [action("valid-work-unit", "A")],
  });
  const nodeA = nodes.find((node) => node.id === "research-work-unit:A");
  assert.deepEqual(relatedIds(nodeA), ["action:valid-work-unit"]);
  assert.ok(!nodes.some((node) => node.id === "action:valid-work-unit"));
});
