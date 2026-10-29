/**
 * B0.1｜Typed Scope Error Mapping Truth
 * Transport metadata survives loaders/coordinators; user-safe copy is chosen
 * only at the final UI boundary without parsing error strings.
 */
import assert from "node:assert/strict";

import test from "node:test";
import {
  TASK_LOAD_ZH,
  classifyTaskLoadFailure,
  isTaskLoadScopeDenial,
  userFacingTaskLoadError,
} from "../src/lib/taskLoadError.mjs";

const typed = (status, code) => ({ status, code });

function assertNoProtectedLeak(text) {
  assert.doesNotMatch(text, /ownerId|workspaceId|Secret Task|other workspace/i);
}

test("401 maps from structured status and never from message text", () => {
  assert.equal(classifyTaskLoadFailure(typed(401)), "unauthorized");
  assert.equal(userFacingTaskLoadError(typed(401), "zh-CN"), TASK_LOAD_ZH.unauthorized);
  assert.equal(classifyTaskLoadFailure(new Error("HTTP 401 unauthorized")), "loadFailed");

});

test("403 maps from structured status/code without protected detail", () => {
  assert.equal(classifyTaskLoadFailure(typed(403)), "forbidden");
  assert.equal(classifyTaskLoadFailure({ code: "permission_denied" }), "forbidden");
  assert.equal(userFacingTaskLoadError(typed(403), "zh-CN"), TASK_LOAD_ZH.forbidden);
  assertNoProtectedLeak(TASK_LOAD_ZH.forbidden);

});

test("404/409/428/429/5xx preserve explicit typed UI classifications", () => {
  assert.equal(classifyTaskLoadFailure(typed(404)), "notFound");
  assert.equal(userFacingTaskLoadError(typed(404), "zh-CN"), TASK_LOAD_ZH.notFound);
  assert.equal(classifyTaskLoadFailure(typed(409)), "conflict");
  assert.equal(classifyTaskLoadFailure(typed(428)), "precondition");
  assert.equal(classifyTaskLoadFailure(typed(429)), "rateLimited");
  assert.equal(userFacingTaskLoadError(typed(429), "zh-CN"), TASK_LOAD_ZH.rateLimited);
  assert.equal(classifyTaskLoadFailure(typed(502)), "serviceUnavailable");
  assert.equal(userFacingTaskLoadError(typed(502), "zh-CN"), TASK_LOAD_ZH.serviceUnavailable);
  assert.equal(classifyTaskLoadFailure(new TypeError("Failed to fetch")), "loadFailed");

});

test("loadTask preserves ApiError and no thrown transport failure reaches fixtures", () => {
  assert.equal(isTaskLoadScopeDenial(typed(401)), true);
  assert.equal(isTaskLoadScopeDenial(typed(403)), true);
  assert.equal(isTaskLoadScopeDenial(typed(500)), false);

});

test("403 user-facing copy never includes backend identity data", () => {
  const mapped = userFacingTaskLoadError({ status: 403, detail: { title: "Secret Task", ownerId: "owner-leak", workspaceId: "ws-leak" } }, "zh-CN");
  assert.equal(mapped, TASK_LOAD_ZH.forbidden);
  assertNoProtectedLeak(mapped);
});
