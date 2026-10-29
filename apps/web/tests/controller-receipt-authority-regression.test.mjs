import assert from "node:assert/strict";

import test from "node:test";

import { composeCollaborationPresentation } from "../src/model/collaborationPresentationCore.mjs";

const COMMAND_ID = "controller_command_same";

function supplementUserMessage(at = "2026-08-13T00:00:00Z") {
  return {
    id: `msg_controller_command_${COMMAND_ID}`,
    role: "user",
    text: "只使用新增边界。",
    at,
    messageType: "controller_supplement",
    controllerCommandId: COMMAND_ID,
    artifactRefs: [],
  };
}

function receiptControl(state, submittedAt = "2026-08-13T00:00:01Z") {
  return {
    id: `controller-receipt:${COMMAND_ID}`,
    kind: "controller_receipt",
    occurredAt: submittedAt,
    workUnitId: state === "applied" ? "wu-applied" : null,
    source: {
      commandId: COMMAND_ID,
      state,
      submittedAt,
      userMessage: "只使用新增边界。",
      appliedWorkUnitId: state === "applied" ? "wu-applied" : null,
    },
  };
}

test("user controller_supplement with same commandId keeps user message and received receipt", () => {
  const nodes = composeCollaborationPresentation({
    messages: [supplementUserMessage()],
    controls: [receiptControl("received")],
  });
  assert.deepEqual(
    nodes.map((node) => node.id),
    [`message:msg_controller_command_${COMMAND_ID}`, `control:controller-receipt:${COMMAND_ID}`],
  );
  assert.equal(nodes[0].kind, "user_intervention");
  assert.equal(nodes[0].source.controllerCommandId, COMMAND_ID);
  assert.equal(nodes[0].source.messageType, "controller_supplement");
  assert.equal(nodes[1].controlKind, "controller_receipt");
  assert.equal(nodes[1].source.state, "received");

});

test("applied and rejected receipts remain alongside the durable user supplement", () => {
  for (const state of ["applied", "rejected"]) {
    const nodes = composeCollaborationPresentation({
      messages: [supplementUserMessage()],
      controls: [receiptControl(state)],
    });
    const message = nodes.find((node) => node.kind === "user_intervention");
    const receipts = nodes.filter((node) => node.controlKind === "controller_receipt");
    assert.ok(message, `user message must remain for ${state}`);
    assert.equal(message.source.controllerCommandId, COMMAND_ID);
    assert.equal(receipts.length, 1, `exactly one authoritative receipt for ${state}`);
    assert.equal(receipts[0].source.state, state);

  }
});

test("duplicate authoritative receipt controls do not produce a second visible receipt node id collision", () => {
  // Panel pushes at most one controller_receipt per commandId. Composer keeps a
  // single control node when given one receipt fact; a second identical control
  // id would collide and must not be introduced by message provenance.
  const nodes = composeCollaborationPresentation({
    messages: [supplementUserMessage()],
    controls: [receiptControl("received")],
  });
  const receiptIds = nodes
    .filter((node) => node.controlKind === "controller_receipt")
    .map((node) => node.id);
  assert.deepEqual(receiptIds, [`control:controller-receipt:${COMMAND_ID}`]);
  assert.equal(
    nodes.filter((node) => node.kind === "user_intervention").length,
    1,
  );
});
