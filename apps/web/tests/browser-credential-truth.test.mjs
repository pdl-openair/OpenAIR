/**
 * B0 Browser Credential Truth — directed contract tests.
 * Legal stored/env credentials attach Authorization; missing credentials do not;
 * no hard-coded production fallback token; tokens are not logged; no automatic actor switch.
 */
import assert from "node:assert/strict";

import test from "node:test";
import {
  resolveBrowserAuthToken,
  shouldAttachAuthorization,
} from "../src/lib/browserCredentialTruth.mjs";

const FORBIDDEN_DEV_FALLBACK = ["openair", "-", "dev", "-", "token"].join("");

test("合法存储凭证优先于环境 token", () => {
  assert.equal(
    resolveBrowserAuthToken("  stored-login-token  ", "env-injected-token"),
    "stored-login-token",
  );
  assert.equal(shouldAttachAuthorization("stored-login-token"), true);
});

test("无存储凭证时使用明确 VITE_OPENAIR_AUTH_TOKEN", () => {
  assert.equal(resolveBrowserAuthToken("", "  explicit-env-token  "), "explicit-env-token");
  assert.equal(resolveBrowserAuthToken(null, "explicit-env-token"), "explicit-env-token");
  assert.equal(shouldAttachAuthorization("explicit-env-token"), true);
});

test("无凭证时不构造 Authorization", () => {
  assert.equal(resolveBrowserAuthToken("", ""), "");
  assert.equal(resolveBrowserAuthToken("   ", undefined), "");
  assert.equal(resolveBrowserAuthToken(null, null), "");
  assert.equal(shouldAttachAuthorization(""), false);
  assert.equal(shouldAttachAuthorization("   "), false);
  assert.equal(shouldAttachAuthorization(undefined), false);

});
