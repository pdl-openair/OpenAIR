import { expect, test } from "../lib/test";
import { API_BASE_URL } from "../lib/config";
import { attachJson, collectPageEvidence, screenshot, snapshotTask } from "../lib/evidence";

const INPUT = "我想调研近几年大语言模型做代码生成时，怎样评估生成代码的可靠性和安全性，有哪些代表性论文？";
const REQUIRE_LIVE = process.env.OPENAIR_PRODUCT_REQUIRE_LIVE === "1";

async function readTask(request: Parameters<typeof snapshotTask>[0], taskId: string) {
  // A live model may be completing an artifact write while this independent
  // verifier reads the task snapshot. Keep the verification request resilient
  // without changing the product's interactive browser controls.
  const response = await request.get(`${API_BASE_URL}/api/tasks/${encodeURIComponent(taskId)}`, { timeout: 30_000 });
  expect(response.ok()).toBeTruthy();
  return response.json();
}

test("S34 / G04: automatic research streams every substage and stops at a resumable stage gate", async ({ page, request }, testInfo) => {
  test.setTimeout(900_000);
  const evidence = collectPageEvidence(page, testInfo);
  let taskId: string | null = null;

  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/", { waitUntil: "commit" });
    await page.getByTestId("research-intent-input").fill(INPUT);
    await page.getByTestId("starter-home-main-create").click();
    await expect(page.getByTestId("workbench-draft-start-confirmation")).toBeVisible({ timeout: 420_000 });

    taskId = await page.evaluate(() => window.sessionStorage.getItem("openair.userRoute.currentTaskId"));
    expect(taskId).toBeTruthy();
    await page.getByTestId("workbench-draft-confirm-start").click();

    const input = page.getByTestId("workbench-collaboration-input-zone").locator("textarea");
    await expect(input).toBeEditable();
    await expect(page.getByTestId("workbench-collaboration-stop-run")).toBeVisible();

    await expect.poll(async () => {
      const task = await readTask(request, taskId!);
      return (task.capabilityCalls ?? []).some((call: { status?: string; outputArtifactRefs?: string[] }) => (
        call.status === "completed" && (call.outputArtifactRefs?.length ?? 0) > 0
      ));
    }, { timeout: 70_000 }).toBe(true);

    const modeControl = page.locator(".workbench-execution-mode-control");
    await expect(modeControl).not.toHaveClass(/ant-segmented-disabled/, { timeout: 30_000 });
    await modeControl.getByText("自动研究", { exact: true }).click();

    // This is one long collaboration feed. Automatic progression appends more
    // events; it must not reset the right-side conversation for each substage.
    const stream = page.getByTestId("workbench-collaboration-stream");
    await expect.poll(async () => stream.locator(":scope > *").count(), { timeout: 70_000 }).toBeGreaterThan(2);

    await expect.poll(async () => {
      const task = await readTask(request, taskId!);
      return task.status;
    }, { timeout: 720_000, intervals: [500, 1_000, 2_000] }).toBe("awaiting_confirmation");

    const completed = await snapshotTask(request, testInfo, API_BASE_URL, taskId!, "s34-automatic-stage-gate-task");
    const calls = completed.capabilityCalls ?? [];
    const completedCalls = calls.filter((call: { status?: string }) => call.status === "completed");
    expect(completedCalls.length).toBeGreaterThan(1);
    expect(completedCalls.every((call: { outputArtifactRefs?: string[] }) => (call.outputArtifactRefs?.length ?? 0) > 0)).toBe(true);
    expect(calls.filter((call: { status?: string }) => call.status === "failed")).toEqual([]);
    expect(calls.some((call: { status?: string }) => call.status === "running")).toBe(false);
    expect(completed.artifacts?.length ?? 0).toBeGreaterThanOrEqual(completedCalls.length);
    if (REQUIRE_LIVE) {
      expect(completed.resultSource).not.toBe("replay");
      expect(calls.length).toBeGreaterThan(0);
      expect(calls.every((call: { runtime?: string; provider?: string | null; runMode?: string | null; sourceMode?: string | null }) => (
        call.runtime === "huiyan_chat_completions"
        && call.provider === "huiyan_chat_completions"
        && call.runMode !== "replay"
        && call.sourceMode !== "replay"
      ))).toBe(true);
    }
    await attachJson(testInfo, "s34-completed-call-artifacts", completedCalls.map((call: { id: string; name: string; outputArtifactRefs?: string[] }) => ({
      id: call.id,
      name: call.name,
      outputArtifactRefs: call.outputArtifactRefs,
    })));

    await expect(input).toBeEditable();
    await expect(page.getByTestId("workbench-collaboration-stream")).toBeVisible();
    await expect(page.getByTestId("artifact-viewer-canvas")).toContainText("输出", { timeout: 30_000 });
    await screenshot(page, testInfo, "s34-automatic-stage-gate");
    await page.reload({ waitUntil: "commit" });
    await expect(page.getByTestId("workbench-collaboration-stream")).toBeVisible();
    await expect(input).toBeEditable();
    const restored = await snapshotTask(request, testInfo, API_BASE_URL, taskId!, "s34-automatic-stage-gate-restored-task");
    expect(restored.status).toBe("awaiting_confirmation");
    expect((restored.capabilityCalls ?? []).filter((call: { status?: string }) => call.status === "completed")
      .every((call: { outputArtifactRefs?: string[] }) => (call.outputArtifactRefs?.length ?? 0) > 0)).toBe(true);
    expect(evidence.pageErrors).toEqual([]);
  } finally {
    await evidence.flush();
    if (taskId) await request.delete(`${API_BASE_URL}/api/tasks/${taskId}`);
  }
});
