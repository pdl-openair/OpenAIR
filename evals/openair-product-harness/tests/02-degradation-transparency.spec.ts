import { expect, test } from "@playwright/test";
import { disposeDevSession, prepareDevHarnessAuth } from "../lib/auth";
import { collectPageEvidence, screenshot } from "../lib/evidence";
import {
  expectNoVisibleSecretLeak,
  GateRecorder,
  hasVisibleFixtureDisclosure
} from "../lib/gates";

test("S05 / G03: API failure must visibly disclose demo data across product surfaces", async ({
  page,
  playwright
}, testInfo) => {
  const evidence = collectPageEvidence(page, testInfo);
  const gates = new GateRecorder();
  const session = await prepareDevHarnessAuth(playwright, page);
  try {
    await page.route("**/api/tasks**", async route => {
      await route.abort("failed");
    });

    await page.goto("/workbench", { waitUntil: "commit" });
    await expect(page.getByTestId("fixture-disclosure")).toBeVisible();
    await expect(page.getByText("当前暂时无法连接任务服务", { exact: false })).toBeVisible();
    await expect(page.locator("tbody tr")).toHaveCount(0);
    await screenshot(page, testInfo, "01-board-after-task-api-failure");

    const boardDisclosed = await hasVisibleFixtureDisclosure(page);
    gates.check(
      "DG11",
      boardDisclosed,
      "When /api/tasks fails, the board visibly discloses the unavailable task service instead of fabricating task rows",
      { disclosed: boardDisclosed },
      { disclosed: true }
    );

    await page.goto("/tasks/task_cs_ope_001", { waitUntil: "commit" });
    await expect(page.getByTestId("fixture-disclosure")).toBeVisible();
    await expect(page.getByTestId("result-source-tag")).toHaveText("演示数据");
    await screenshot(page, testInfo, "02-detail-after-task-api-failure");

    const detailDisclosed = await hasVisibleFixtureDisclosure(page);
    gates.check(
      "DG12",
      detailDisclosed,
      "When task detail falls back to local demo data, ordinary users must see an explicit disclosure",
      { disclosed: detailDisclosed },
      { disclosed: true }
    );

    await page.goto("/workbench/task_cs_ope_001", { waitUntil: "commit" });
    await expect(page.getByTestId("fixture-disclosure")).toBeVisible();
    await expect(page.getByTestId("result-source-tag")).toHaveText("演示数据");
    await expect(page.getByTestId("task-service-status")).toContainText(
      "服务不可用"
    );
    await screenshot(page, testInfo, "03-workbench-after-task-api-failure");

    const workbenchDisclosed = await hasVisibleFixtureDisclosure(page);
    gates.check(
      "DG13",
      workbenchDisclosed,
      "When workbench falls back to local demo data, ordinary users must see an explicit disclosure",
      { disclosed: workbenchDisclosed },
      { disclosed: true }
    );
    gates.check(
      "DG16",
      (await page.getByTestId("task-service-status").innerText()).includes(
        "服务不可用"
      ),
      "Demo fallback separates service unavailability from task execution state and result source",
      await page.getByTestId("task-service-status").innerText(),
      "任务服务 服务不可用"
    );

    await expectNoVisibleSecretLeak(page);
  } finally {
    await gates.attach(testInfo);
    await evidence.flush();
    await disposeDevSession(session);
  }
});
