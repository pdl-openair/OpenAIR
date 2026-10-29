import { expect, test } from "@playwright/test";
import { disposeDevSession, prepareDevHarnessAuth } from "../lib/auth";
import { API_BASE_URL } from "../lib/config";
import {
  attachJson,
  collectPageEvidence,
  screenshot,
  snapshotTask
} from "../lib/evidence";
import {
  expectNoVisibleSecretLeak,
  GateRecorder,
  pickSeededAdvancableTask,
  readTasks,
  taskDigest
} from "../lib/gates";

test.describe.serial("P2.1 generic product mainline baseline", () => {
  test("S01: create draft task, inspect workbench, detail, board, and refresh recovery", async ({
    page,
    playwright
  }, testInfo) => {
    const evidence = collectPageEvidence(page, testInfo);
    const gates = new GateRecorder();
    const session = await prepareDevHarnessAuth(playwright, page);
    try {
      const goal = `验证 OpenAIR 新建科研任务、跨页面状态一致性与刷新恢复 ${Date.now()}`;

      await page.goto("/workbench", { waitUntil: "commit" });
      await expect(page.getByRole("button", { name: "新建研究任务", exact: true })).toBeVisible();
      await screenshot(page, testInfo, "01-board-before-create");

      await page.getByRole("button", { name: "新建研究任务", exact: true }).click();
      await expect(page).toHaveURL((url) => url.pathname === "/" && url.searchParams.get("intent") === "create");
      const intentInput = page.getByTestId("research-intent-input");
      await expect(intentInput).toBeVisible();
      await expect(intentInput).toBeFocused();
      await intentInput.fill(goal);
      await screenshot(page, testInfo, "02-research-start-input");

      await page.getByTestId("starter-home-main-create").click();
      await expect(page.getByTestId("starter-task-contract-preview")).toBeVisible();
      await screenshot(page, testInfo, "03-task-contract-preview");
      await page.getByTestId("starter-home-contract-confirm").click();

      await page.waitForURL(/\/workbench\/current$/);
      await expect(page.getByText(goal, { exact: true })).toBeVisible();
      const draftConfirmation = page.getByTestId("workbench-draft-start-confirmation");
      await expect(draftConfirmation).toBeVisible();
      await expect(draftConfirmation).toContainText("任务草案已准备好");
      await expect(draftConfirmation).toContainText("确认前不会开始处理");
      await expect(page.getByTestId("workbench-progression-panel")).toContainText("研究起点草案尚未确认");
      const taskRows = await readTasks(session.request, API_BASE_URL);
      const createdDigest = taskRows.find(task => task.goalSummary === goal);
      expect(
        createdDigest,
        "Created task should be visible in task list"
      ).toBeTruthy();
      const taskId = createdDigest?.taskId as string;
      const created = await snapshotTask(session.request, testInfo, API_BASE_URL, taskId, "created-draft-task");
      const createdTitle = String(created.title ?? "").trim();
      expect(createdTitle, "Created task should have a visible title").toBeTruthy();
      gates.check("DG03", created.status === "draft", "UI-created task is persisted as draft", created.status, "draft");
      gates.check("DG04", created.creationStatus === "awaiting_confirmation", "UI-created draft starts with a confirmable plan", created.creationStatus, "awaiting_confirmation");
      await screenshot(page, testInfo, "03-draft-workbench");

      await page.reload({ waitUntil: "commit" });
      await expect(page.getByText(goal, { exact: true })).toBeVisible();
      await expect(draftConfirmation).toBeVisible();
      await expect(draftConfirmation).toContainText("确认前不会开始处理");
      const afterWorkbenchRefresh = await snapshotTask(session.request, testInfo, API_BASE_URL, taskId, "draft-after-workbench-refresh");
      gates.check("DG05", afterWorkbenchRefresh.taskId === taskId, "Workbench refresh restores the same draft task", afterWorkbenchRefresh.taskId, taskId);
      await screenshot(page, testInfo, "04-draft-workbench-after-refresh");

      await page.getByRole("button", { name: /任务详情/ }).click();
      await expect(page).toHaveURL(/\/tasks\/current$/);
      const taskDetailGoal = page.getByText(goal, { exact: true }).first();
      await expect(taskDetailGoal).toBeVisible();
      await screenshot(page, testInfo, "05-draft-task-detail");

      await page.reload({ waitUntil: "commit" });
      await expect(taskDetailGoal).toBeVisible();
      await screenshot(page, testInfo, "06-draft-detail-after-refresh");

      await page.getByTestId("app-shell-header").getByRole("link", { name: "任务看板", exact: true }).click();
      await expect(page).toHaveURL(/\/workbench$/);
      await expect(page.getByText(createdTitle, { exact: true }).first()).toBeVisible();
      await screenshot(page, testInfo, "07-board-shows-created-draft");
      await expectNoVisibleSecretLeak(page);
      gates.check(
        "DG06",
        evidence.pageErrors.length === 0,
        "Draft create and recovery flow emits no uncaught page error",
        evidence.pageErrors,
        []
      );
    } finally {
      await gates.attach(testInfo);
      await evidence.flush();
      await disposeDevSession(session);
    }
  });

  test("S02: seeded running task keeps one informational primary action and remains consistent after refresh", async ({ page, playwright }, testInfo) => {
    const evidence = collectPageEvidence(page, testInfo);
    const gates = new GateRecorder();
    const session = await prepareDevHarnessAuth(playwright, page);
    try {
      const tasks = await readTasks(session.request, API_BASE_URL);
      const seeded = pickSeededAdvancableTask(tasks);
      expect(
        seeded,
        "A seeded running task with substages is required for the first baseline"
      ).toBeTruthy();
      if (!seeded) throw new Error("No seeded advancable task found");

      const taskId = seeded.taskId as string;
      const seededTitle = String(seeded.title ?? "").trim();
      expect(seededTitle, "Seeded task should have a visible title").toBeTruthy();
      const before = await snapshotTask(session.request, testInfo, API_BASE_URL, taskId, "seeded-task-before-advance");
      await attachJson(testInfo, "seeded-task-before-digest", taskDigest(before));

      await page.goto(`/workbench/${taskId}`, { waitUntil: "commit" });
      const primaryDecisionAction = page.getByTestId("workbench-primary-decision-action");
      await expect(primaryDecisionAction).toBeVisible();
      await expect(primaryDecisionAction).toContainText("查看运行进度");
      await expect(page.getByRole("button", { name: "继续执行下一步", exact: true })).toHaveCount(0);
      await screenshot(page, testInfo, "01-seeded-workbench-before-advance");

      await primaryDecisionAction.click();
      const after = await snapshotTask(session.request, testInfo, API_BASE_URL, taskId, "seeded-task-after-primary-action");
      await attachJson(testInfo, "seeded-task-after-primary-action-digest", taskDigest(after));
      gates.check("DG07", after.updatedAt === before.updatedAt, "Running-state primary action does not push task state", after.updatedAt, before.updatedAt);
      gates.check("DG08", after.status === "running", "Running-state primary action preserves the running task", after.status, "running");
      await screenshot(page, testInfo, "02-seeded-workbench-after-advance");

      await page.reload({ waitUntil: "commit" });
      await expect(primaryDecisionAction).toBeVisible();
      await expect(primaryDecisionAction).toContainText("查看运行进度");
      const afterWorkbenchRefresh = await snapshotTask(session.request, testInfo, API_BASE_URL, taskId, "seeded-task-after-workbench-refresh");
      gates.check("DG09", afterWorkbenchRefresh.updatedAt === after.updatedAt, "Workbench refresh restores the persisted task version", afterWorkbenchRefresh.updatedAt, after.updatedAt);
      await screenshot(page, testInfo, "03-seeded-workbench-after-refresh");

      await page.getByRole("button", { name: /任务详情/ }).click();
      await expect(page).toHaveURL(/\/tasks\/current$/);
      await expect(page.getByText(seededTitle, { exact: true }).first()).toBeVisible();
      await screenshot(page, testInfo, "04-seeded-task-detail-after-advance");

      await page.getByTestId("app-shell-header").getByRole("link", { name: "任务看板", exact: true }).click();
      await expect(page).toHaveURL(/\/workbench$/);
      await expect(page.getByText(seededTitle, { exact: true })).toBeVisible();
      await screenshot(page, testInfo, "05-board-after-seeded-advance");
      await expectNoVisibleSecretLeak(page);
      gates.check("DG10", evidence.pageErrors.length === 0, "Running-state and cross-page recovery flow emits no uncaught page error", evidence.pageErrors, []);
    } finally {
      await gates.attach(testInfo);
      await evidence.flush();
      await disposeDevSession(session);
    }
  });
});
