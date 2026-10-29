import { expect, test, type Page } from "@playwright/test";
import { collectPageEvidence, screenshot } from "../lib/evidence";

const mockSystemSkills = [
  {
    skill_id: "literature-review",
    claude_name: "literature-review",
    display_name: "literature-review",
    domain: "cs_research",
    stages: ["problem"],
    substages: ["recommended_readings"],
    skill_type: "system",
    status: "active",
    description: "Extracts source candidates and reading priorities from research context.",
  },
];

async function installIssue345Mocks(page: Page) {
  await page.route("**/api/starters", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ starters: [] }),
  }));
  await page.route("**/api/skills/system", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(mockSystemSkills),
  }));
  await page.route("**/api/skills/user", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify([]),
  }));
  await page.route("**/api/tasks/meta/packs", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify([{ key: "sdr", label: "通用科研流程" }]),
  }));
  await page.route("**/api/tasks", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ tasks: [] }),
  }));
}

test.describe("Product-03 starter plaza and Settings shell", () => {
  test("starter cards, samples, settings i18n, and table settings placeholder are explicit", async ({ page }, testInfo) => {
    const evidence = collectPageEvidence(page, testInfo);
    await installIssue345Mocks(page);

    try {
      await page.goto("/starters?b2shot=1", { waitUntil: "networkidle" });
      await expect(page.getByText("需要输入：").first()).toBeVisible();
      await expect(page.getByText("预计产物：").first()).toBeVisible();
      await screenshot(page, testInfo, "01-starters-zh-desktop");

      await page.locator("#starter-plaza-samples").scrollIntoViewIfNeeded();
      await expect(page.getByTestId("starter-sample-view").first()).toBeVisible();
      await expect(page.getByTestId("starter-sample-copy").first()).toBeVisible();
      await expect(page.getByTestId("starter-sample-unavailable").first()).toBeVisible();
      const sampleShot = testInfo.outputPath("02-starters-samples-zh-desktop.png");
      await page.screenshot({
        path: sampleShot,
        fullPage: false,
      });
      await testInfo.attach("02-starters-samples-zh-desktop", {
        path: sampleShot,
        contentType: "image/png",
      });

      await page.goto("/workbench?b2shot=1", { waitUntil: "networkidle" });
      await expect(page.getByTestId("board-table-settings-disabled")).toBeDisabled();
      await screenshot(page, testInfo, "03-board-table-settings-disabled-zh-desktop");

      await page.addInitScript(() => {
        localStorage.setItem(
          "openair.app.preferences.v1",
          JSON.stringify({ language: "en-US", theme: "system" }),
        );
      });
      await page.goto("/settings?b2shot=1", { waitUntil: "networkidle" });
      await expect(page.getByText("My Skills")).toBeVisible();
      await expect(page.getByText("System Skills")).toBeVisible();
      await expect(page.getByText(/Skill names, domains, and descriptions are capability content data/)).toBeVisible();
      await screenshot(page, testInfo, "04-settings-skills-en-desktop");

      expect(evidence.pageErrors, "Product-03 pages should not emit uncaught page errors").toEqual([]);
    } finally {
      await evidence.flush();
    }
  });
});
