import assert from "node:assert/strict";
import { test } from "vitest";
import {
  formatRoundTime,
  groupSubstageArtifactsByRound,
} from "../../substageArtifactRoundsCore.mjs";

function workUnit(overrides = {}) {
  return {
    workUnitId: "workunit-x",
    executionDecisionId: "decision-1",
    subStageInstanceId: "sub-a",
    status: "completed",
    dispatchedAt: "2026-10-13T01:50:00.000000Z",
    finishedAt: "2026-10-13T01:51:00.000000Z",
    outputArtifactRefs: [],
    ...overrides,
  };
}

test("single decision stays flat (null) even with several attempt artifacts", () => {
  const groups = groupSubstageArtifactsByRound({
    workUnits: [
      workUnit({ outputArtifactRefs: ["a1"] }),
      workUnit({ workUnitId: "w2", outputArtifactRefs: ["a2"] }),
    ],
    substageInstanceId: "sub-a",
    artifactIds: ["a1", "a2"],
  });
  assert.equal(groups, null);
});

test("rounds are keyed by decision and ordered by finish time with per-round indexes", () => {
  const groups = groupSubstageArtifactsByRound({
    workUnits: [
      workUnit({
        executionDecisionId: "decision-r2",
        finishedAt: "2026-10-15T10:06:13.000000Z",
        outputArtifactRefs: ["r2-a", "r2-b"],
      }),
      workUnit({
        workUnitId: "w1",
        executionDecisionId: "decision-r1",
        finishedAt: "2026-10-14T13:26:50.000000Z",
        outputArtifactRefs: ["r1-a"],
      }),
      workUnit({
        subStageInstanceId: "sub-b",
        executionDecisionId: "decision-other",
        outputArtifactRefs: ["sibling"],
      }),
    ],
    substageInstanceId: "sub-a",
    artifactIds: ["r1-a", "r2-a", "r2-b", "sibling"],
  });

  assert.deepEqual(
    groups.map((group) => [group.roundIndex, ...group.artifactIds]),
    [
      [null, "sibling"],
      [1, "r1-a"],
      [2, "r2-a", "r2-b"],
    ],
  );
  assert.equal(groups[1].finishedAt, "2026-10-14T13:26:50.000000Z");
});

test("artifacts without a resolvable decision lead as an unindexed group", () => {
  const groups = groupSubstageArtifactsByRound({
    workUnits: [
      workUnit({
        executionDecisionId: "decision-r1",
        outputArtifactRefs: ["a"],
      }),
      workUnit({
        workUnitId: "w2",
        executionDecisionId: "decision-r2",
        finishedAt: "2026-10-15T10:00:00.000000Z",
        outputArtifactRefs: ["b"],
      }),
    ],
    substageInstanceId: "sub-a",
    artifactIds: ["b", "legacy", "a"],
  });

  assert.deepEqual(
    groups.map((group) => [group.roundIndex, ...group.artifactIds]),
    [
      [null, "legacy"],
      [1, "a"],
      [2, "b"],
    ],
  );
});

test("missing work units or empty ids fall back to flat mode", () => {
  assert.equal(
    groupSubstageArtifactsByRound({
      workUnits: null,
      substageInstanceId: "sub-a",
      artifactIds: ["a"],
    }),
    null,
  );
  assert.equal(
    groupSubstageArtifactsByRound({
      workUnits: [workUnit()],
      substageInstanceId: "sub-a",
      artifactIds: [],
    }),
    null,
  );
});

test("round time formatting is local, short, and tolerant of bad input", () => {
  assert.equal(formatRoundTime("not-a-date"), null);
  assert.equal(formatRoundTime(null), null);
  const formatted = formatRoundTime("2026-10-14T13:32:36.316531Z");
  assert.match(formatted, /^\d{2}-\d{2} \d{2}:\d{2}$/);
});
