const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

if (!process.env.HUIYAN_API_KEY) {
  console.error(JSON.stringify({ stage: "preflight", error: "provider_key_missing" }));
  process.exit(2);
}

const runId = process.env.OPENAIR_PRODUCT_HARNESS_RUN_ID
  || `t10-night-${new Date().toISOString().replace(/[:.]/g, "-")}`;
const runDir = process.env.OPENAIR_PRODUCT_HARNESS_RUN_DIR
  || path.join(os.tmpdir(), "openair-t10-night-evidence", runId);
if (fs.existsSync(runDir)) {
  console.error(JSON.stringify({ stage: "preflight", error: "run_directory_exists", runDir }));
  process.exit(2);
}
fs.mkdirSync(runDir, { recursive: true });

const password = `T10!${crypto.randomBytes(24).toString("hex")}`;
const apiBaseUrl = process.env.OPENAIR_PRODUCT_HARNESS_API_BASE_URL
  || "http://127.0.0.1:8000";
const webBaseUrl = process.env.OPENAIR_PRODUCT_HARNESS_WEB_BASE_URL
  || "http://127.0.0.1:5173";
const email = process.env.OPENAIR_PRODUCT_HARNESS_DEV_EMAIL
  || `${runId.replace(/[^a-zA-Z0-9]/g, "-")}@openair.dev`;
const username = process.env.OPENAIR_PRODUCT_HARNESS_DEV_USERNAME
  || `t10_${crypto.createHash("sha256").update(runId).digest("hex").slice(0, 16)}`;
const harnessEnv = {
  ...process.env,
  NODE_TLS_REJECT_UNAUTHORIZED: "1",
  OPENAIR_PRODUCT_HARNESS_API_BASE_URL: apiBaseUrl,
  OPENAIR_PRODUCT_HARNESS_WEB_BASE_URL: webBaseUrl,
  OPENAIR_PRODUCT_HARNESS_AUTH_REQUIRED: "1",
  OPENAIR_PRODUCT_HARNESS_DEV_EMAIL: email,
  OPENAIR_PRODUCT_HARNESS_DEV_USERNAME: username,
  OPENAIR_PRODUCT_HARNESS_DEV_PASSWORD: password,
  OPENAIR_PRODUCT_HARNESS_RUN_ID: runId,
  OPENAIR_PRODUCT_HARNESS_RUN_DIR: runDir,
  OPENAIR_PRODUCT_HARNESS_TRACE_MODE: "off",
  OPENAIR_PRODUCT_REQUIRE_LIVE: "1",
};
const bootstrap = spawnSync(
  process.execPath,
  [path.join(process.cwd(), "evals", "openair-product-harness", "scripts", "t10-night-bootstrap.cjs")],
  {
    cwd: process.cwd(),
    env: harnessEnv,
    stdio: "inherit",
  },
);
if (bootstrap.status !== 0) {
  console.error(JSON.stringify({
    stage: "auth_bootstrap",
    exitCode: bootstrap.status ?? 1,
    signal: bootstrap.signal ?? null,
  }));
  process.exit(bootstrap.status ?? 1);
}
const playwrightBin = path.join(
  process.cwd(),
  "evals",
  "openair-product-harness",
  "node_modules",
  ".bin",
  "playwright.cmd",
);

const requestedSpecs = process.argv.slice(2);
const specs = requestedSpecs.length > 0
  ? requestedSpecs
  : ["evals/openair-product-harness/tests/35-t10-durable-multiagent-sse.spec.ts"];
const result = spawnSync(
  playwrightBin,
  [
    "test",
    ...specs,
    "--config",
    "evals/openair-product-harness/playwright.config.ts",
    "--reporter=line",
  ],
  {
    cwd: process.cwd(),
    env: harnessEnv,
    stdio: "inherit",
    shell: process.platform === "win32",
  },
);

console.log(JSON.stringify({
  stage: "playwright_complete",
  runId,
  runDir,
  exitCode: result.status ?? 1,
  signal: result.signal ?? null,
}));
if (result.error) console.error(JSON.stringify({ stage: "spawn", error: result.error.name }));
process.exit(result.status ?? 1);
