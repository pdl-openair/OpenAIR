import { expect, test } from "../lib/test";
import { API_BASE_URL } from "../lib/config";
import { attachJson, collectPageEvidence, snapshotTask } from "../lib/evidence";

const INPUT = "Create a confirmable, source-bounded research plan comparing the supplied MapReduce and Spark/RDD papers. Do not retrieve sources, run benchmarks, or claim completed results.";

test("S30 / G04: explicit task start immediately runs the first planned skill", async ({ page, request }, testInfo) => {
  test.setTimeout(180_000);
  const evidence = collectPageEvidence(page, testInfo);
  let taskId: string | null = null;
  try {
    await page.setViewportSize({ width: 1440, height: 640 });
    await page.goto("/", { waitUntil: "commit" });
    await page.getByTestId("research-intent-input").fill(INPUT);
    await page.getByTestId("starter-home-main-create").click();
    const feedbackStartedAt = Date.now();
    await page.getByTestId("starter-home-contract-confirm").click();
    await expect(page.getByTestId("research-start-pending-panel")).toBeVisible({ timeout: 1_000 });
    const feedbackMilliseconds = Date.now() - feedbackStartedAt;
    await attachJson(testInfo, "s30-start-feedback", { feedbackMilliseconds });
    expect(feedbackMilliseconds).toBeLessThanOrEqual(1_000);
    await expect(page.getByTestId("workbench-draft-start-confirmation")).toBeVisible({ timeout: 70_000 });
    taskId = await page.evaluate(() => window.sessionStorage.getItem("openair.userRoute.currentTaskId"));
    expect(taskId).toBeTruthy();

    const draftConfirmButton = page.getByTestId("workbench-draft-confirm-start");
    await expect(draftConfirmButton).toBeVisible();
    const draftLayout = await page.evaluate(() => {
      const center = document.querySelector('[data-testid="workbench-center"]') as HTMLElement | null;
      const button = document.querySelector('[data-testid="workbench-draft-confirm-start"]')?.getBoundingClientRect();
      return {
        centerOverflowY: center ? getComputedStyle(center).overflowY : null,
        buttonBottom: button?.bottom ?? 0,
        viewportHeight: window.innerHeight,
      };
    });
    expect(draftLayout.centerOverflowY).toBe("auto");
    await page.getByTestId("workbench-draft-details-scroll").locator("textarea").fill(
      "文献调研约束：优先比较公开同行评审研究中的可靠性与安全性评价口径；保留检索式、纳排标准、证据定位、冲突处理和停止条件。".repeat(24),
    );
    const overflowedDraftLayout = await page.evaluate(() => {
      const center = document.querySelector('[data-testid="workbench-center"]') as HTMLElement | null;
      return {
        clientHeight: center?.clientHeight ?? 0,
        scrollHeight: center?.scrollHeight ?? 0,
      };
    });
    expect(overflowedDraftLayout.scrollHeight).toBeGreaterThan(overflowedDraftLayout.clientHeight);
    const didScrollDraftDetails = await page.getByTestId("workbench-center").evaluate((element) => {
      const node = element as HTMLElement;
      const before = node.scrollTop;
      node.scrollTop = node.scrollHeight;
      return node.scrollTop > before;
    });
    expect(didScrollDraftDetails).toBe(true);
    await draftConfirmButton.scrollIntoViewIfNeeded();
    await expect(draftConfirmButton).toBeInViewport();
    await draftConfirmButton.click();
    const startPanel = page.getByTestId("workbench-confirmed-task-start-panel");
    await expect(startPanel).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("workbench-planning-record-disclosure")).toBeVisible();
    await expect(page.getByTestId("task-execution-status")).toContainText("任务未开始");
    const preStartToggle = page.getByTestId("workbench-pre-start-confirmation-toggle");
    await expect(preStartToggle).toHaveText("展开");
    const layout = await page.evaluate(() => {
      const center = document.querySelector('[data-testid="workbench-center"]')?.getBoundingClientRect();
      const artifact = document.querySelector('[data-testid="artifact-viewer-canvas"]')?.getBoundingClientRect();
      return { centerHeight: center?.height ?? 0, artifactHeight: artifact?.height ?? 0 };
    });
    expect(layout.artifactHeight, "confirmed task keeps the artifact workspace as the primary center region").toBeGreaterThanOrEqual(
      layout.centerHeight * 0.5,
    );
    await preStartToggle.click();
    await expect(preStartToggle).toHaveText("收起");
    const startButton = page.getByTestId("workbench-confirmed-task-start");
    await expect(startButton).toBeVisible({ timeout: 30_000 });
    const startResponse = page.waitForResponse((response) => (
      response.request().method() === "POST"
      && response.url().endsWith(`/api/tasks/${taskId}/start`)
      && response.status() === 200
    ));
    await startButton.click();
    await startResponse;
    await expect(page.getByTestId("workbench-confirmed-plan-canvas")).toBeVisible();
    await expect(page.getByTestId("workbench-collaboration-input-zone").locator("textarea")).toBeEditable();
    await expect(page.getByTestId("workbench-collaboration-stop-run")).toBeVisible();

    await expect.poll(async () => {
      const task = await snapshotTask(request, testInfo, API_BASE_URL, taskId!, "s30-first-skill-started");
      return task.capabilityCalls ?? [];
    }, { timeout: 30_000 }).toEqual(expect.arrayContaining([
      expect.objectContaining({ name: "intent-understanding" }),
    ]));

    const started = await snapshotTask(request, testInfo, API_BASE_URL, taskId!, "s30-started-task");
    expect(started.status).toBe("running");
    expect(started.creationStatus).toBe("confirmed");
    expect(started.capabilityCalls ?? []).toEqual(expect.arrayContaining([
      expect.objectContaining({
        name: "intent-understanding",
        outputArtifactRefs: expect.arrayContaining([expect.any(String)]),
      }),
    ]));
    await expect(page.getByTestId("artifact-viewer-canvas")).toContainText("意图理解 输出", { timeout: 30_000 });
    await page.reload({ waitUntil: "commit" });
    await expect(page.getByRole("tab", { name: "协作" })).toBeVisible();
    await expect(page.getByText("执行过程", { exact: true })).toBeVisible();
    await expect(page.getByTestId("workbench-current-conclusion")).not.toContainText("需要恢复");
    const restored = await snapshotTask(request, testInfo, API_BASE_URL, taskId!, "s30-restored-task");
    expect(restored.capabilityCalls ?? []).toEqual(expect.arrayContaining([
      expect.objectContaining({
        name: "intent-understanding",
        outputArtifactRefs: expect.arrayContaining([expect.any(String)]),
      }),
    ]));
    expect(evidence.pageErrors).toEqual([]);
  } finally {
    await evidence.flush();
    if (taskId) await request.delete(`${API_BASE_URL}/api/tasks/${taskId}`);
  }
});
