import { defineConfig, devices } from "@playwright/test";
import os from "node:os";
import path from "node:path";

const runId = process.env.OPENAIR_PRODUCT_HARNESS_RUN_ID ?? new Date().toISOString().replace(/[:.]/g, "-");
const runDir = process.env.OPENAIR_PRODUCT_HARNESS_RUN_DIR ?? path.join(os.tmpdir(), "openair-product-harness-runs", runId);
const webBaseUrl = process.env.OPENAIR_PRODUCT_HARNESS_WEB_BASE_URL ?? "http://127.0.0.1:5173";
const chromiumExecutablePath = process.env.OPENAIR_PRODUCT_HARNESS_CHROMIUM_EXECUTABLE_PATH?.trim();
const requestedTraceMode = process.env.OPENAIR_PRODUCT_HARNESS_TRACE_MODE?.trim();
const recordVideo = process.env.OPENAIR_PRODUCT_HARNESS_RECORD_VIDEO === "1";
const traceMode: "off" | "on" | "retain-on-failure" = (
  requestedTraceMode === "off"
    || requestedTraceMode === "on"
    || requestedTraceMode === "retain-on-failure"
)
  ? requestedTraceMode
  : (process.env.OPENAIR_PRODUCT_HARNESS_ALLOW_TRACE === "1" ? "retain-on-failure" : "off");

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  outputDir: path.join(runDir, "test-results"),
  reporter: [
    ["line"],
    ["html", { outputFolder: path.join(runDir, "html-report"), open: "never" }],
    ["json", { outputFile: path.join(runDir, "playwright-report.json") }],
  ],
  use: {
    baseURL: webBaseUrl,
    trace: traceMode,
    video: recordVideo ? "on" : "off",
    screenshot: "only-on-failure",
    viewport: { width: 1440, height: 900 },
    actionTimeout: 10_000,
    navigationTimeout: 15_000,
  },
  projects: [
    {
      name: "chromium-desktop",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 900 },
        launchOptions: chromiumExecutablePath ? { executablePath: chromiumExecutablePath } : undefined,
      },
    },
  ],
});
