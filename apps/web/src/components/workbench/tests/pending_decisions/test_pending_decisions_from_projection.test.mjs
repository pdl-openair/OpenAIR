import assert from "node:assert/strict";
import { test } from "vitest";
import { pendingDecisionsFromProjection } from "../../pendingDecisionsFromProjection.mjs";

test("missing or non-object projections yield an empty list", () => {
  assert.deepEqual(pendingDecisionsFromProjection(null), []);
  assert.deepEqual(pendingDecisionsFromProjection(undefined), []);
  assert.deepEqual(pendingDecisionsFromProjection("flat"), []);
});

test("absent nested collaboration does not throw and yields empty", () => {
  assert.deepEqual(pendingDecisionsFromProjection({ taskId: "t1", revision: 5 }), []);
  assert.deepEqual(pendingDecisionsFromProjection({ collaboration: null }), []);
  assert.deepEqual(pendingDecisionsFromProjection({ collaboration: { pendingDecisions: null } }), []);
});

test("non-array pendingDecisions cannot crash extraction", () => {
  assert.deepEqual(
    pendingDecisionsFromProjection({ collaboration: { pendingDecisions: "boom" } }),
    [],
  );
  assert.deepEqual(
    pendingDecisionsFromProjection({ collaboration: { pendingDecisions: { gateId: "g1" } } }),
    [],
  );
});

test("flat top-level pendingDecisions are recovered when nested list is absent", () => {
  const decisions = pendingDecisionsFromProjection({
    pendingDecisions: [
      { gateId: "g-flat", kind: "execution_policy", reason: "确认策略", allowedActions: ["continue_run"] },
    ],
  });
  assert.equal(decisions.length, 1);
  assert.equal(decisions[0].gateId, "g-flat");
  assert.deepEqual(decisions[0].allowedActions, ["continue_run"]);
});

test("malformed rows are skipped and allowedActions is always an array", () => {
  const decisions = pendingDecisionsFromProjection({
    collaboration: {
      pendingDecisions: [
        null,
        { reason: "missing gate" },
        { gateId: "g1", reason: "ok", allowedActions: "continue_run" },
        { gateId: "g2", kind: "artifact_review", reason: "审", allowedActions: ["terminate_run", 1, ""] },
      ],
    },
  });
  assert.equal(decisions.length, 2);
  assert.deepEqual(decisions[0].allowedActions, []);
  assert.deepEqual(decisions[1].allowedActions, ["terminate_run"]);
  // Rendering must be able to call .includes without throwing.
  assert.equal(decisions[0].allowedActions.includes("continue_run"), false);
  assert.equal(decisions[1].allowedActions.includes("terminate_run"), true);
});
