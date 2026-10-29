import assert from "node:assert/strict";

import test from "node:test";

import { safeRightPanelText } from "../src/components/workbench/workbenchRightPanelCore.mjs";

test("internal skill and definition references cannot enter the main collaboration copy", () => {
  assert.equal(safeRightPanelText("sdr.intent_understanding.plan", "安全文案"), "安全文案");
  assert.equal(safeRightPanelText("skill id: internal-only", "安全文案"), "安全文案");
  assert.equal(safeRightPanelText("definition_key: internal-only", "安全文案"), "安全文案");
});
