import assert from "node:assert/strict";

import test from "node:test";
import {
  collaborationCanonicalArtifactExists,
  looksLikeInternalExecutionDetail,
  presentCollaborationActionReason,
  presentCollaborationResultSummary,
  projectRightPanelArtifacts,
} from "../src/components/workbench/workbenchRightPanelCore.mjs";

const ARTIFACT_FORMED = "研究产物已形成，可查看相关产物。";
const FALSE_NO_BUSINESS = "当前步骤没有形成可用业务产物";
const INTERNAL_DUMP_SUMMARY = [
  "configuration",
  "adapter",
  "trace",
  "skill_id",
  "failed to materialize business product",
].join(" ");

function evaluateResultPresentation({ summary, artifactRefs, visibleArtifactIds }) {
  const canonicalExists = collaborationCanonicalArtifactExists(artifactRefs, visibleArtifactIds);
  const presented = presentCollaborationResultSummary(summary, {
    canonicalArtifactExists: canonicalExists,
  });
  return {
    CANONICAL_ARTIFACT_EXISTS: canonicalExists ? "YES" : "NO",
    FALSE_NO_BUSINESS_ARTIFACT_MESSAGE: String(presented ?? "").includes("没有形成可用业务产物") ? "YES" : "NO",
    RAW_INTERNAL_DETAIL_VISIBLE: looksLikeInternalExecutionDetail(presented) ? "YES" : "NO",
    ARTIFACT_ENTRY_VISIBLE: canonicalExists ? "YES" : "NO",
    presented,
  };
}

test("Case A result: canonical Artifact truth overrides raw dump and false no-business summary", () => {
  const visibleArtifactIds = ["artifact-a"];
  const dump = evaluateResultPresentation({
    summary: INTERNAL_DUMP_SUMMARY,
    artifactRefs: ["artifact-a"],
    visibleArtifactIds,
  });
  assert.equal(dump.CANONICAL_ARTIFACT_EXISTS, "YES");
  assert.equal(dump.FALSE_NO_BUSINESS_ARTIFACT_MESSAGE, "NO");
  assert.equal(dump.RAW_INTERNAL_DETAIL_VISIBLE, "NO");
  assert.equal(dump.ARTIFACT_ENTRY_VISIBLE, "YES");
  assert.equal(dump.presented, ARTIFACT_FORMED);
  assert.doesNotMatch(dump.presented, /configuration|adapter|trace|skill_id/i);

  const falseNoBusiness = evaluateResultPresentation({
    summary: FALSE_NO_BUSINESS,
    artifactRefs: ["artifact-a"],
    visibleArtifactIds,
  });
  assert.equal(falseNoBusiness.CANONICAL_ARTIFACT_EXISTS, "YES");
  assert.equal(falseNoBusiness.FALSE_NO_BUSINESS_ARTIFACT_MESSAGE, "NO");
  assert.equal(falseNoBusiness.ARTIFACT_ENTRY_VISIBLE, "YES");
  assert.equal(falseNoBusiness.presented, ARTIFACT_FORMED);
});

test("Case A action: internal reason is replaced while Artifact entry remains available", () => {
  const visibleArtifactIds = ["artifact-a"];
  const exists = collaborationCanonicalArtifactExists(["artifact-a"], visibleArtifactIds);
  assert.equal(exists, true);
  const presented = presentCollaborationActionReason(
    'configuration=prod adapter="huiyan" trace=abc skill_id=literature_review',
    { canonicalArtifactExists: exists },
  );
  assert.equal(presented, ARTIFACT_FORMED);
  assert.equal(looksLikeInternalExecutionDetail(presented), false);
  assert.doesNotMatch(presented, /configuration=|adapter=|trace=|skill_id=/i);
});

test("Case B: payload artifactRef without canonical visible Artifact never claims artifact formed", () => {
  const result = evaluateResultPresentation({
    summary: INTERNAL_DUMP_SUMMARY,
    artifactRefs: ["artifact-missing"],
    visibleArtifactIds: ["artifact-a"],
  });
  assert.equal(result.CANONICAL_ARTIFACT_EXISTS, "NO");
  assert.equal(result.RAW_INTERNAL_DETAIL_VISIBLE, "NO");
  assert.equal(result.ARTIFACT_ENTRY_VISIBLE, "NO");
  assert.doesNotMatch(result.presented, /研究产物已形成/);
  assert.notEqual(result.presented, ARTIFACT_FORMED);

  assert.equal(
    collaborationCanonicalArtifactExists(["artifact-only-in-payload"], []),
    false,
  );
  assert.equal(
    presentCollaborationResultSummary(FALSE_NO_BUSINESS, { canonicalArtifactExists: false }),
    FALSE_NO_BUSINESS,
  );
});

test("Regression: scientific single-word adapter/configuration copy stays visible", () => {
  const scientific = "We compare adapter configuration strategies.";
  assert.equal(looksLikeInternalExecutionDetail(scientific), false);
  assert.equal(
    presentCollaborationResultSummary(scientific, { canonicalArtifactExists: false }),
    scientific,
  );
  assert.equal(
    presentCollaborationActionReason(scientific, { canonicalArtifactExists: true }),
    scientific,
  );
});

test("removes the review-status fallback because it adds no user action", () => {
  const fallback = "复核结论将以最新任务状态为准。";
  assert.equal(presentCollaborationResultSummary(fallback), null);
  assert.equal(presentCollaborationActionReason(fallback), null);
});

test("visibleArtifactIds come from projected canonical rows, not raw payload refs alone", () => {
  const projected = projectRightPanelArtifacts("task-a", [{
    artifact: {
      id: "artifact-a",
      taskId: "task-a",
      title: "关键文献定位",
      sourceMode: "live",
    },
    projection: {
      state: "usable",
      badge: { label: "可使用", color: "success" },
      gate: {},
      visibleAllowedActions: [],
    },
  }]);
  const visibleArtifactIds = projected.map((row) => row.id);
  assert.deepEqual(visibleArtifactIds, ["artifact-a"]);
  assert.equal(
    collaborationCanonicalArtifactExists(["artifact-a", "artifact-ghost"], visibleArtifactIds),
    true,
  );
  assert.equal(
    collaborationCanonicalArtifactExists(["artifact-ghost"], visibleArtifactIds),
    false,
  );
});
