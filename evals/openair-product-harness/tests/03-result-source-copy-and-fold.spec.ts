import { expect, test } from "@playwright/test";
import { disposeDevSession, prepareDevHarnessAuth } from "../lib/auth";
import { API_BASE_URL } from "../lib/config";
import { collectPageEvidence, screenshot } from "../lib/evidence";
import {
  GateRecorder,
  pickSeededAdvancableTask,
  readTasks,
  visibleInternalNameLeaks
} from "../lib/gates";

test("S06 / OA-004 OA-005 OA-006 OA-007: source, plain copy, status separation, side-panel folding, and center hierarchy", async ({
  page,
  playwright
}, testInfo) => {
  const evidence = collectPageEvidence(page, testInfo);
  const gates = new GateRecorder();
  const session = await prepareDevHarnessAuth(playwright, page);
  try {
    const tasks = await readTasks(session.request, API_BASE_URL);
    const seeded = pickSeededAdvancableTask(tasks);
    expect(seeded, "A seeded replay task is required").toBeTruthy();
    if (!seeded) throw new Error("No seeded replay task found");
    const taskId = seeded.taskId as string;
    const taskTitle = seeded.title as string;

    gates.check(
      "DG14",
      seeded.resultSource === "replay",
      "Recorded-run API task is explicitly marked as replay",
      seeded.resultSource,
      "replay"
    );

    await page.goto("/workbench", { waitUntil: "commit" });
    const seededRow = page.locator("tr").filter({ hasText: taskTitle });
    await expect(seededRow.getByTestId("result-source-tag")).toHaveText(
      "回放结果"
    );
    const copyLeaksBySurface: Record<string, string[]> = {};
    copyLeaksBySurface.board = await visibleInternalNameLeaks(page);
    await screenshot(page, testInfo, "01-board-replay-source");

    await page.goto(`/tasks/${taskId}`, { waitUntil: "commit" });
    await expect(page.getByTestId("result-source-tag")).toHaveText("回放结果");
    await expect(page.getByTestId("replay-disclosure")).toBeVisible();
    copyLeaksBySurface.detail = await visibleInternalNameLeaks(page);
    await screenshot(page, testInfo, "02-detail-replay-source");

    await page.goto(`/workbench/${taskId}`, { waitUntil: "commit" });
    await expect(page.getByTestId("result-source-tag")).toHaveText("回放结果");
    await expect(page.getByTestId("replay-disclosure")).toBeVisible();
    await expect(page.getByTestId("task-service-status")).toContainText(
      "任务服务"
    );
    await expect(page.getByTestId("task-service-status")).toContainText(
      "服务正常"
    );

    copyLeaksBySurface.workbench = await visibleInternalNameLeaks(page);
    const hasCopyLeak = Object.values(copyLeaksBySurface).some(
      leaks => leaks.length > 0
    );
    gates.check(
      "DG17",
      !hasCopyLeak,
      "Ordinary product surfaces hide internal implementation names",
      copyLeaksBySurface,
      {}
    );

    await expect(page.getByTestId("workbench-progression-panel")).toBeVisible();
    await expect(page.getByTestId("workbench-current-conclusion")).toBeVisible();
    await page.getByTestId("workbench-progression-panel").getByRole("button").click();
    await expect(page.getByTestId("workbench-blocker-reason")).toBeVisible();
    await expect(page.getByTestId("workbench-unique-next-action")).toBeVisible();
    await expect(
      page.getByTestId("workbench-primary-decision-action")
    ).toHaveText("查看运行进度");
    await expect(
      page.getByTestId("workbench-center").getByRole("button", { name: "查看运行进度", exact: true })
    ).toHaveCount(1);
    gates.check(
      "DG20",
      true,
      "Center decision panel exposes progress, pending confirmations, and exactly one primary next action"
    );

    await page.getByTestId("toggle-left-panel").click();
    await expect(page.getByTestId("left-panel-collapsed")).toBeVisible();
    await expect(page.getByTestId("workbench-center")).toBeVisible();
    await page.getByTestId("toggle-right-panel").click();
    await expect(page.getByTestId("right-panel-collapsed")).toBeVisible();
    await expect(page.getByTestId("workbench-center")).toBeVisible();
    gates.check(
      "DG18",
      true,
      "Left and right panels collapse independently without replacing the center work area"
    );
    await screenshot(page, testInfo, "03-workbench-side-panels-collapsed");

    await page.setViewportSize({ width: 1280, height: 720 });
    const expectCenterPrimaryActionInViewport = async () => {
      await expect(page.getByTestId("workbench-progression-panel")).toBeVisible();
      await expect(
        page.getByTestId("workbench-primary-decision-action")
      ).toBeVisible();
      const box = await page
        .getByTestId("workbench-primary-decision-action")
        .boundingBox();
      expect(
        box,
        "Center primary action should have a visible bounding box at 1280x720"
      ).toBeTruthy();
      if (!box)
        throw new Error(
          "Center primary action has no bounding box at 1280x720"
        );
      expect(box.x + box.width).toBeLessThanOrEqual(1280);
      expect(box.y + box.height).toBeLessThanOrEqual(720);
    };

    await expectCenterPrimaryActionInViewport();
    await page.getByTestId("toggle-left-panel").click();
    await expect(page.getByTestId("right-panel-collapsed")).toBeVisible();
    await expectCenterPrimaryActionInViewport();
    await page.getByTestId("toggle-right-panel").click();
    await expectCenterPrimaryActionInViewport();
    await page.getByTestId("toggle-left-panel").click();
    await expect(page.getByTestId("left-panel-collapsed")).toBeVisible();
    await expectCenterPrimaryActionInViewport();
    await page.getByTestId("toggle-right-panel").click();
    await expect(page.getByTestId("right-panel-collapsed")).toBeVisible();
    await expectCenterPrimaryActionInViewport();
    gates.check(
      "DG21",
      true,
      "Center priority panel and primary next action remain visible at 1280x720 across all side-panel states"
    );
    await screenshot(page, testInfo, "04-workbench-center-priority-1280x720");
    await page.setViewportSize({ width: 1440, height: 900 });

    const draftResponse = await session.request.post(
      `${API_BASE_URL}/api/tasks/draft`,
      {
        data: {
          goalSummary: `验证新建任务生成研究起点草案但保留引用边界 ${Date.now()}`
        }
      }
    );
    expect(
      draftResponse.ok(),
      "Draft task creation should succeed"
    ).toBeTruthy();
    const draft = await draftResponse.json();
    gates.check(
      "DG15",
      draft.resultSource === "live",
      "New draft task has an input-grounded research start draft",
      draft.resultSource,
      "live"
    );
    gates.check(
      "DG16",
      draft.artifacts?.[0]?.kind === "research_start_draft",
      "New draft task exposes a first understandable artifact",
      draft.artifacts?.[0]?.kind,
      "research_start_draft"
    );

    await page.goto(`/workbench/${draft.taskId}`, { waitUntil: "commit" });
    const draftConfirmation = page.getByTestId("workbench-draft-start-confirmation");
    await expect(draftConfirmation).toBeVisible();
    await expect(draftConfirmation).toContainText("任务草案已准备好");
    await expect(draftConfirmation).toContainText("确认前不会开始处理");
    await expect(page.getByTestId("workbench-draft-created-feedback")).toContainText("研究起点草案");
    await screenshot(page, testInfo, "05-draft-research-start-source");

    gates.check(
      "DG19",
      evidence.pageErrors.length === 0,
      "P2.4b source, fold, and center hierarchy flow emits no uncaught page error",
      evidence.pageErrors,
      []
    );
  } finally {
    await gates.attach(testInfo);
    await evidence.flush();
    await disposeDevSession(session);
  }
});
