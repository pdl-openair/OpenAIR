import assert from "node:assert/strict";
import test from "node:test";
import { parseRetryAfter, RateLimitBackoff } from "../src/lib/rateLimitBackoff.mjs";

test("Retry-After header wins over JSON retryAfter", () => {
  assert.equal(parseRetryAfter({ headers: new Headers({ "Retry-After": "7" }), body: { retryAfter: 99 } }), 7_000);
});

test("Retry-After HTTP date and default are bounded", () => {
  const now = Date.now();
  const date = new Date(now + 2_000).toUTCString();
  const parsed = parseRetryAfter({ headers: new Headers({ "Retry-After": date }) }, now);
  assert.ok(parsed >= 1_000 && parsed <= 2_000);
  assert.equal(parseRetryAfter({ headers: new Headers() }, now), 5_000);
});

test("exponential jitter circuit transitions open, half-open, closed", () => {
  let now = 0;
  const backoff = new RateLimitBackoff({ now: () => now, random: () => 0.5 });
  backoff.retryAfter(1_000);
  assert.equal(backoff.circuitState, "open");
  now = backoff.backoffUntil;
  assert.equal(backoff.beginProbe(), true);
  backoff.success();
  assert.equal(backoff.circuitState, "closed");
});
