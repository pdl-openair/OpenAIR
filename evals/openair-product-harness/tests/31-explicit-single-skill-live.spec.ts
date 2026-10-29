import { expect, test } from "../lib/test";
import { API_BASE_URL } from "../lib/config";
import { attachJson, collectPageEvidence, snapshotTask } from "../lib/evidence";

const INPUT = "Prepare a source-bounded research question refinement draft for comparing MapReduce and Spark/RDD. Do not retrieve sources, execute benchmarks, or claim completed results.";

test("S31 / G04: a user-selected current-substage Skill creates one audited Live call", async ({ page, request }, testInfo) => {
  test.setTimeout(360_000);
  const evidence = collectPageEvidence(page, testInfo);
  let taskId: string | null = null;
  try {
    await page.goto("/", { waitUntil: "commit" });
    await page.getByTestId("research-intent-input").fill(INPUT);
    await page.getByTestId("starter-home-main-create").click();
    await page.getByTestId("starter-home-contract-confirm").click();
    await expect(page.getByTestId("workbench-draft-start-confirmation")).toBeVisible({ timeout: 135_000 });
    taskId = await page.evaluate(() => window.sessionStorage.getItem("openair.userRoute.currentTaskId"));
    expect(taskId).toBeTruthy();

    await page.getByTestId("workbench-draft-confirm-start").click();
    const start = page.waitForResponse((response) => response.request().method() === "POST" && response.url().endsWith(`/api/tasks/${taskId}/start`) && response.status() === 200);
    await page.getByTestId("workbench-confirmed-task-start").click();
    await start;

    const inputZone = page.getByTestId("workbench-collaboration-input-zone");
    const primaryAction = inputZone.getByTestId("workbench-primary-execution-action");
    await expect(primaryAction).toBeEnabled({ timeout: 15_000 });
    const selectedSkillLabel = await primaryAction.textContent();
    await inputZone.getByRole("textbox").fill(
      "请界定比较研究问题，列出可检验的比较维度与证据边界；不要检索来源、运行基准或宣称任何研究结果。",
    );
    await primaryAction.click();

    await expect.poll(async () => {
      const task = await snapshotTask(request, testInfo, API_BASE_URL, taskId!, "s31-poll-task");
      return task.capabilityCalls ?? [];
    }, { timeout: 180_000 }).toEqual(expect.arrayContaining([
      expect.objectContaining({ runtime: "huiyan_chat_completions", status: "completed", runMode: "live", sourceMode: "live" }),
    ]));
    const task = await snapshotTask(request, testInfo, API_BASE_URL, taskId!, "s31-live-task");
    const call = (task.capabilityCalls ?? []).find((item: { runtime?: string; runMode?: string }) => item.runtime === "huiyan_chat_completions" && item.runMode === "live");
    expect(call).toBeTruthy();
    expect((task.capabilityCalls ?? []).filter((item: { runtime?: string; runMode?: string }) => item.runtime === "huiyan_chat_completions" && item.runMode === "live")).toHaveLength(1);
    expect(call.runId).toBeTruthy();
    expect(call.skillHash ?? call.metadata?.skillHash).toBeTruthy();
    expect(call.provider).toBe("huiyan_chat_completions");
    expect(call.model).toBeTruthy();
    expect(call.sourceMetadata?.providerRunId).toBeTruthy();
    expect(task.artifacts ?? []).toEqual(expect.arrayContaining([
      expect.objectContaining({ sourceCapabilityCallId: call.id }),
    ]));
    await attachJson(testInfo, "s31-explicit-live-skill-audit", { taskId, selectedSkillLabel, call });
    expect(evidence.pageErrors).toEqual([]);
  } finally {
    await evidence.flush();
    if (taskId) await request.delete(`${API_BASE_URL}/api/tasks/${taskId}`);
  }
});
