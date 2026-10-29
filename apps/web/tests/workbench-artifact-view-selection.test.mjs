import assert from "node:assert/strict";
import test from "node:test";
import {
  artifactsForExplicitScope,
  artifactScopePresentation,
  buildArtifactViewNotices,
  chooseFocusedArtifactId,
  reconcileArtifactViewSelection,
  resolveArtifactLocation,
  shouldAutoFollowCurrentArtifact,
} from "../src/lib/artifactViewSelectionCore.mjs";

const stages = [{
  stage: "problem_modeling",
  subStages: [
    { id: "sub-a", artifactRefs: [] },
    { id: "sub-b", artifactRefs: ["artifact-b"] },
  ],
}];

test("substage scope never expands to sibling artifacts implicitly", () => {
  const substage = artifactsForExplicitScope({
    scope: "substage",
    substageArtifacts: [],
    stageArtifacts: [{ id: "artifact-b" }],
    taskArtifacts: [{ id: "artifact-b" }],
  });
  assert.deepEqual(substage, []);

  const stage = artifactsForExplicitScope({
    scope: "stage",
    substageArtifacts: [],
    stageArtifacts: [{ id: "artifact-b" }],
    taskArtifacts: [{ id: "artifact-b" }],
  });
  assert.deepEqual(stage.map((artifact) => artifact.id), ["artifact-b"]);
});

test("explicit stage scope keeps title, path, explanation, and return action aligned", () => {
  const substage = artifactScopePresentation({
    scope: "substage",
    stageLabel: "问题建模",
    substageLabel: "问题 A",
  });
  assert.equal(substage.title, "问题 A");
  assert.equal(substage.path, "问题建模 / 问题 A");

  const stage = artifactScopePresentation({
    scope: "stage",
    stageLabel: "问题建模",
    substageLabel: "问题 A",
  });
  assert.equal(stage.title, "问题建模 · 本阶段产物");
  assert.equal(stage.path, "问题建模 / 本阶段全部节点产物");
  assert.match(stage.explanation, /多个研究节点/);
  assert.equal(stage.returnLabel, "返回 问题 A");
});

test("a pinned historical view ignores a newly produced current artifact", () => {
  assert.equal(shouldAutoFollowCurrentArtifact({
    ownership: "pinned",
    latestArtifactId: "artifact-new",
    focusedArtifactId: "artifact-history",
    deepLinkedArtifactId: null,
    location: { kind: "located", stageKey: "problem_modeling", substageInstanceId: "sub-b" },
  }), false);
});

test("a following-current view may follow a newly produced current artifact", () => {
  assert.equal(shouldAutoFollowCurrentArtifact({
    ownership: "following_current",
    latestArtifactId: "artifact-new",
    focusedArtifactId: "artifact-old",
    deepLinkedArtifactId: null,
    location: { kind: "located", stageKey: "problem_modeling", substageInstanceId: "sub-b" },
  }), true);
});

test("explicit artifact selection outranks reviewable/latest list order", () => {
  assert.equal(chooseFocusedArtifactId({
    focusedArtifactId: "artifact-selected",
    deepLinkedArtifactId: null,
    visibleArtifactIds: ["artifact-reviewable", "artifact-selected", "artifact-latest"],
    allArtifactIds: ["artifact-reviewable", "artifact-selected", "artifact-latest"],
  }), "artifact-selected");
});

test("reviewable artifact produces a notice instead of replacing the focused body", () => {
  const focused = chooseFocusedArtifactId({
    focusedArtifactId: "artifact-history",
    deepLinkedArtifactId: null,
    visibleArtifactIds: ["artifact-reviewable", "artifact-history"],
    allArtifactIds: ["artifact-reviewable", "artifact-history"],
  });
  const notices = buildArtifactViewNotices({
    ownership: "pinned",
    latestArtifactId: null,
    reviewableArtifactId: "artifact-reviewable",
    focusedArtifactId: focused,
  });
  assert.equal(focused, "artifact-history");
  assert.deepEqual(notices, {
    newCurrentArtifactId: null,
    pendingReviewArtifactId: "artifact-reviewable",
  });
});

test("artifact deep link resolves the real substage instance", () => {
  assert.deepEqual(resolveArtifactLocation({
    artifact: {
      id: "artifact-b",
      relatedStage: "problem_modeling",
      subStageInstanceId: "sub-b",
      substage: "shared-definition",
    },
    stages,
  }), {
    kind: "located",
    stageKey: "problem_modeling",
    substageInstanceId: "sub-b",
    evidence: "substage_instance_id",
  });
});

test("task A/B/A resets cross-task state while same-task revision only clears illegal selections", () => {
  const snapshotA2 = {
    taskId: "task-a",
    revision: 2,
    researchDesignKey: "design-a:2",
    stageKeys: ["problem_modeling"],
    substageIdsByStage: { problem_modeling: ["sub-a2"] },
    artifactIds: ["artifact-a2"],
  };
  const initialA2 = {
    taskId: "task-a",
    revision: 2,
    researchDesignKey: "design-a:2",
    stageKey: "problem_modeling",
    selectedSubstageId: "sub-a2",
    focusedArtifactId: null,
    scope: "substage",
    ownership: "following_current",
  };
  const staleA1 = {
    ...initialA2,
    revision: 1,
    researchDesignKey: "design-a:1",
    selectedSubstageId: "sub-a1",
    focusedArtifactId: "artifact-a1",
    ownership: "pinned",
  };
  const sameTask = reconcileArtifactViewSelection({ selection: staleA1, snapshot: snapshotA2, initialSelection: initialA2 });
  assert.equal(sameTask.taskId, "task-a");
  assert.equal(sameTask.selectedSubstageId, null);
  assert.equal(sameTask.focusedArtifactId, null);
  assert.equal(sameTask.ownership, "pinned");

  const staleTaskB = { ...staleA1, taskId: "task-b", selectedSubstageId: "sub-b", focusedArtifactId: "artifact-b" };
  const returnedToA = reconcileArtifactViewSelection({ selection: staleTaskB, snapshot: snapshotA2, initialSelection: initialA2 });
  assert.deepEqual(returnedToA, initialA2);
});

test("artifact without instance or unique artifact-ref evidence is never guessed onto a node", () => {
  assert.deepEqual(resolveArtifactLocation({
    artifact: {
      id: "artifact-unowned",
      relatedStage: "problem_modeling",
      substage: "shared-definition",
    },
    stages,
  }), {
    kind: "unlocatable",
    reason: "missing_substage_evidence",
  });
});
