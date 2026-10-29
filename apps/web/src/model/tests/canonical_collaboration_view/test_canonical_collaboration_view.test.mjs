import assert from "node:assert/strict";
import { test } from "vitest";

import {
  CanonicalCollaborationContractError,
  buildCanonicalCollaborationView,
  parseCanonicalCollaborationPayload,
} from "../../canonicalCollaborationViewCore.mjs";

function basePayload() {
  return {
    schemaVersion: "canonical_collaboration_v1",
    lifecycleKind: "canonical_v1_2",
    taskId: "task-secret-1",
    taskStatus: "running",
    revision: 8,
    revisionFence: { kind: "research_task_v1_2_revision", revision: 8 },
    sourceIdentity: "decision-secret-current",
    executionDecision: {
      executionDecisionId: "decision-secret-current",
      decisionSequence: 2,
      status: "dispatched",
      outcome: null,
      researchDesignVersion: 3,
      substageInstanceId: "stage-secret-instance",
      substageInstanceRevision: 1,
      decidedAt: "2026-08-05T12:00:00Z",
      closedAt: null,
    },
    workUnits: [
      {
        workUnitId: "wu-secret-primary",
        assignmentId: "assignment-secret-primary",
        role: "执行工作",
        objective: "整理实验结果并形成结论",
        responsibleActorId: "actor-secret-primary",
        responsibleActorType: "agent",
        status: "running",
        parallelGroupId: "parallel-secret-a",
        dependencyWorkUnitIds: [],
        waiting: null,
        reviewerWorkUnitIds: ["wu-secret-reviewer"],
        inputArtifactRefs: [],
        outputArtifactRefs: ["artifact-secret-report"],
        needsReviewArtifactRefs: ["artifact-secret-report"],
        failureReasonCode: null,
        dispatchedAt: "2026-08-05T12:01:00Z",
        finishedAt: null,
        skillId: "skill-secret-internal",
        stageKey: "stage-secret-key",
      },
      {
        workUnitId: "wu-secret-support",
        assignmentId: "assignment-secret-support",
        role: "支持工作",
        objective: "核对来源与实验记录",
        responsibleActorId: "actor-secret-support",
        responsibleActorType: "agent",
        status: "running",
        parallelGroupId: "parallel-secret-a",
        dependencyWorkUnitIds: [],
        waiting: null,
        reviewerWorkUnitIds: ["wu-secret-reviewer"],
        inputArtifactRefs: [],
        outputArtifactRefs: [],
        needsReviewArtifactRefs: [],
        failureReasonCode: null,
        dispatchedAt: "2026-08-05T12:01:30Z",
        finishedAt: null,
      },
      {
        workUnitId: "wu-secret-reviewer",
        assignmentId: "assignment-secret-reviewer",
        role: "复核工作",
        objective: "复核实验结论与来源一致性",
        responsibleActorId: "actor-secret-reviewer",
        responsibleActorType: "agent",
        status: "queued",
        parallelGroupId: null,
        dependencyWorkUnitIds: ["wu-secret-primary"],
        waiting: {
          kind: "dependency",
          reason: "正在等待执行工作形成可复核结论。",
          reasonCode: "dependency_not_completed_internal",
          blockingWorkUnitIds: ["wu-secret-primary"],
        },
        reviewerWorkUnitIds: [],
        inputArtifactRefs: ["artifact-secret-report"],
        outputArtifactRefs: [],
        needsReviewArtifactRefs: [],
        failureReasonCode: null,
        dispatchedAt: "2026-08-05T12:02:00Z",
        finishedAt: null,
      },
    ],
    handoffs: [
      {
        handoffId: "handoff-secret-1",
        state: "completed",
        fromWorkUnitId: "wu-secret-primary",
        toWorkUnitId: "wu-secret-reviewer",
        summary: "实验结论已交给复核工作检查。",
        artifactRefs: ["artifact-secret-report"],
        verdict: { decision: "approved", rationaleCode: "handoff_complete_internal" },
        occurredAt: "2026-08-05T12:20:00Z",
      },
    ],
    reviews: [
      {
        reviewId: "review-secret-1",
        reviewerWorkUnitId: "wu-secret-reviewer",
        targetWorkUnitId: "wu-secret-primary",
        artifactRefs: ["artifact-secret-report"],
        decision: "insufficient_evidence",
        rationaleCode: "evidence_incomplete_internal",
        submittedAt: "2026-08-05T12:25:00Z",
      },
    ],
    artifacts: [
      {
        artifactId: "artifact-secret-report",
        workUnitId: "wu-secret-primary",
        artifactType: "analysis_report",
        artifactState: "needs_review",
        version: 1,
        sourceCapabilityCallId: "capability-secret-1",
      },
    ],
    userActionItems: [],
    allowedActions: [],
    internalErrorCode: "top-secret-error-code",
  };
}

function expectContractError(callback, kind) {
  assert.throws(callback, (error) => {
    assert.ok(error instanceof CanonicalCollaborationContractError);
    assert.equal(error.kind, kind);
    return true;
  });
}

test("projects multiple parallel WorkUnits without exposing the raw parallel group", () => {
  const view = buildCanonicalCollaborationView(parseCanonicalCollaborationPayload(basePayload()));
  const parallel = view.currentWork.find((group) => group.isParallel);
  assert.ok(parallel);
  assert.equal(parallel.title, "并行工作 1");
  assert.equal(parallel.items.length, 2);
  assert.equal(JSON.stringify(parallel).includes("parallel-secret-a"), false);
});

test("projects dependency blocked reason and names the blocking work", () => {
  const view = buildCanonicalCollaborationView(parseCanonicalCollaborationPayload(basePayload()));
  assert.equal(view.waitingAndDependencies.length, 1);
  assert.equal(view.waitingAndDependencies[0].role, "复核工作");
  assert.equal(view.waitingAndDependencies[0].reason, "正在等待执行工作形成可复核结论。");
  assert.deepEqual(
    view.waitingAndDependencies[0].dependencies.map((row) => row.label),
    ["整理实验结果并形成结论"],
  );
});

test("projects Reviewer responsibility, handoff and review without rationale codes", () => {
  const view = buildCanonicalCollaborationView(parseCanonicalCollaborationPayload(basePayload()));
  assert.equal(view.reviewAndHandoffs.length, 2);
  const handoff = view.reviewAndHandoffs.find((row) => row.type === "handoff");
  const review = view.reviewAndHandoffs.find((row) => row.type === "review");
  assert.equal(handoff.payload.to.role, "复核工作");
  assert.equal(handoff.payload.stateLabel, "交接已完成");
  assert.equal(review.payload.decisionLabel, "证据不足");
  const serialized = JSON.stringify(view.reviewAndHandoffs);
  assert.equal(serialized.includes("handoff_complete_internal"), false);
  assert.equal(serialized.includes("evidence_incomplete_internal"), false);
});

test("projects needs_review as a distinct artifact truth", () => {
  const view = buildCanonicalCollaborationView(parseCanonicalCollaborationPayload(basePayload()));
  const primary = view.currentWork.flatMap((group) => group.items).find((item) => item.role === "执行工作");
  assert.equal(primary.needsReviewCount, 1);
  assert.equal(primary.artifacts[0].state, "needs_review");
  assert.equal(primary.artifacts[0].stateLabel, "待复核");
});

test("system review names only materialized reviewer work without exposing ids", () => {
  const payload = basePayload();
  payload.workUnits[0].parallelGroupId = null;
  payload.workUnits[0].waiting = {
    kind: "system_review",
    reason: "产物已经形成，正在等待独立复核；复核完成前不会用于下游。",
    reasonCode: "system_review_pending",
    blockingWorkUnitIds: [],
  };
  const view = buildCanonicalCollaborationView(parseCanonicalCollaborationPayload(payload));
  const waiting = view.waitingAndDependencies.find((row) => row.kind === "system_review");
  assert.ok(waiting);
  assert.deepEqual(waiting.reviewers.map((row) => row.label), ["复核实验结论与来源一致性"]);
  assert.equal(JSON.stringify(waiting).includes("wu-secret-reviewer"), false);
});

test("pending supplement is shown as waiting with no invented action", () => {
  const payload = basePayload();
  payload.userActionItems = [{
    itemId: "supplement-pending:8:1",
    kind: "supplement_pending",
    reason: "系统已收到补充内容，正在等待下一个安全工作节点应用。",
    workUnitId: null,
    artifactRefs: [],
    allowedActions: [],
  }];
  payload.allowedActions = ["supplement_input", "stop"];
  const view = buildCanonicalCollaborationView(parseCanonicalCollaborationPayload(payload));
  assert.equal(view.userActionItems[0].title, "补充已提交");
  assert.match(view.userActionItems[0].reason, /等待下一个安全工作节点应用/);
  assert.deepEqual(view.userActionItems[0].allowedActions, []);
});

test("rejected supplement remains a valid canonical collaboration snapshot", () => {
  const payload = basePayload();
  payload.userActionItems = [{
    itemId: "supplement-rejected:8:1",
    kind: "supplement_rejected",
    reason: "补充没有生效。",
    workUnitId: null,
    artifactRefs: [],
    allowedActions: [],
  }];
  const view = buildCanonicalCollaborationView(parseCanonicalCollaborationPayload(payload));
  assert.equal(view.userActionItems[0].title, "补充未生效");
  assert.equal(view.userActionItems[0].reason, "补充没有生效。");
  assert.deepEqual(view.userActionItems[0].allowedActions, []);
});

test("external_result_unknown is fail-visible and does not invent actions", () => {
  const payload = basePayload();
  payload.workUnits = [
    {
      ...payload.workUnits[0],
      status: "external_result_unknown",
      parallelGroupId: null,
      reviewerWorkUnitIds: [],
      outputArtifactRefs: [],
      needsReviewArtifactRefs: [],
      failureReasonCode: "external_result_unknown_internal",
      waiting: {
        kind: "external_result_unknown",
        reason: "外部执行结果尚无法确认。",
        reasonCode: "external_result_unknown",
        blockingWorkUnitIds: [],
      },
    },
  ];
  payload.artifacts = [];
  payload.handoffs = [];
  payload.reviews = [];
  payload.userActionItems = [
    {
      itemId: "action-secret-external",
      kind: "external_result_unknown",
      reason: "需要人工核对外部执行结果。",
      workUnitId: "wu-secret-primary",
      artifactRefs: [],
      allowedActions: [],
    },
  ];
  const view = buildCanonicalCollaborationView(parseCanonicalCollaborationPayload(payload));
  assert.equal(view.currentWork[0].items[0].statusLabel, "结果待核对");
  assert.equal(view.userActionItems[0].title, "核对外部执行结果");
  assert.deepEqual(view.userActionItems[0].allowedActions, []);
  assert.deepEqual(view.allowedActions, []);
});

test("accepts canonical snapshot with no WorkUnit and no ExecutionDecision", () => {
  const payload = basePayload();
  payload.sourceIdentity = null;
  payload.executionDecision = null;
  payload.workUnits = [];
  payload.artifacts = [];
  payload.handoffs = [];
  payload.reviews = [];
  const view = buildCanonicalCollaborationView(parseCanonicalCollaborationPayload(payload));
  assert.equal(view.hasExecutionDecision, false);
  assert.deepEqual(view.currentWork, []);
});

test("revision mismatch fails visibly", () => {
  expectContractError(
    () => parseCanonicalCollaborationPayload(basePayload(), { expectedRevision: 9 }),
    "revision_mismatch",
  );
});

test("legacy collaboration shape is rejected", () => {
  expectContractError(
    () => parseCanonicalCollaborationPayload({ taskId: "legacy", revision: 3, status: "running", entries: [] }),
    "legacy_shape",
  );
});

test("unknown schema is rejected", () => {
  const payload = basePayload();
  payload.schemaVersion = "canonical_collaboration_v2";
  expectContractError(() => parseCanonicalCollaborationPayload(payload), "unknown_schema");
});

test("sourceIdentity must match the current ExecutionDecision", () => {
  const payload = basePayload();
  payload.sourceIdentity = "decision-other";
  expectContractError(() => parseCanonicalCollaborationPayload(payload), "source_identity_mismatch");
});

test("raw actor, skill, stage, capability and internal error identifiers never enter the view", () => {
  const view = buildCanonicalCollaborationView(parseCanonicalCollaborationPayload(basePayload()));
  const serialized = JSON.stringify(view);
  for (const forbidden of [
    "actor-secret-primary",
    "actor-secret-support",
    "actor-secret-reviewer",
    "skill-secret-internal",
    "stage-secret-key",
    "stage-secret-instance",
    "capability-secret-1",
    "top-secret-error-code",
    "assignment-secret-primary",
    "wu-secret-primary",
    "artifact-secret-report",
    "decision-secret-current",
    "dependency_not_completed_internal",
    "external_result_unknown_internal",
  ]) {
    assert.equal(serialized.includes(forbidden), false, forbidden);
  }
});

test("allowedActions are consumed exactly as returned and never expanded", () => {
  const payload = basePayload();
  payload.userActionItems = [
    {
      itemId: "input-secret-1",
      kind: "provide_input",
      reason: "请补充实验边界。",
      workUnitId: "wu-secret-primary",
      artifactRefs: [],
      allowedActions: ["supplement_input"],
    },
  ];
  payload.allowedActions = ["supplement_input"];
  const view = buildCanonicalCollaborationView(parseCanonicalCollaborationPayload(payload));
  assert.deepEqual(view.allowedActions, ["supplement_input"]);
  assert.deepEqual(view.userActionItems[0].allowedActions, [
    { action: "supplement_input", label: "补充材料" },
  ]);
});
