import { expect, test } from "../lib/test";
import { collectPageEvidence, screenshot } from "../lib/evidence";

const TASK_ID = "task_cs_ope_001";

test("S22: a workbench with no active substage blocks progression and offers refresh recovery", async ({ page }, testInfo) => {
  const evidence = collectPageEvidence(page, testInfo);
  let serveMissingActiveSubstage = true;

  await page.route(new RegExp(`/api/tasks/${TASK_ID}$`), async (route) => {
    const response = await route.fetch();
    if (!serveMissingActiveSubstage) {
      await route.fulfill({ response });
      return;
    }

    const task = await response.json() as {
      currentStage?: string;
      stageDetails?: Array<{ stageKey?: string; subStages?: Array<{ status?: string }> }>;
    };
    for (const stage of task.stageDetails ?? []) {
      if (stage.stageKey !== task.currentStage) continue;
      for (const subStage of stage.subStages ?? []) subStage.status = "completed";
    }
    await route.fulfill({ response, json: task });
  });

  try {
    await page.goto(`/workbench/${TASK_ID}`, { waitUntil: "commit" });
    await expect(page.getByTestId("workbench-center")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("workbench-current-conclusion")).toContainText("当前阶段没有可推进的活动子阶段");
    await expect(page.getByTestId("workbench-next-stage-disabled-reason")).toContainText("当前没有可推进的活动子阶段");
    await expect(page.getByTestId("workbench-primary-decision-action")).toHaveText("刷新状态");
    await page.getByTestId("workbench-progression-panel").getByRole("button").first().click();
    await expect(page.locator(".workbench-execution-mode-control")).toHaveClass(/ant-segmented-disabled/);
    await expect(page.getByText("no active substage found", { exact: false })).toHaveCount(0);
    await screenshot(page, testInfo, "missing-active-substage-blocked");

    serveMissingActiveSubstage = false;
    await page.getByTestId("workbench-primary-decision-action").click();
    await expect(page.getByTestId("workbench-current-conclusion")).not.toContainText("当前阶段没有可推进的活动子阶段");
    await screenshot(page, testInfo, "missing-active-substage-recovered");
    expect(evidence.pageErrors).toEqual([]);
  } finally {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await evidence.flush();
  }
});
