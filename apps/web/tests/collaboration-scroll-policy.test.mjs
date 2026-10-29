import assert from "node:assert/strict";

import test from "node:test";
import {
  isCollaborationScrollNearBottom,
  shouldFollowCollaborationScroll,
} from "../src/model/collaborationScrollPolicy.mjs";

test("near-bottom reader follows newly grown collaboration content", () => {
  assert.equal(isCollaborationScrollNearBottom({ scrollTop: 500, clientHeight: 500, scrollHeight: 1020 }), true);
  assert.equal(shouldFollowCollaborationScroll({
    userNearBottom: true,
    previousScrollHeight: 1020,
    nextScrollHeight: 1100,
  }), true);
});

test("reader who scrolled up keeps history position when new collaboration content arrives", () => {
  assert.equal(isCollaborationScrollNearBottom({ scrollTop: 200, clientHeight: 500, scrollHeight: 1100 }), false);
  assert.equal(shouldFollowCollaborationScroll({
    userNearBottom: false,
    previousScrollHeight: 1100,
    nextScrollHeight: 1180,
  }), false);
});

test("same-height reconnect or authoritative resync cannot force a bottom jump", () => {
  assert.equal(shouldFollowCollaborationScroll({
    userNearBottom: true,
    previousScrollHeight: 1100,
    nextScrollHeight: 1100,
  }), false);

});
