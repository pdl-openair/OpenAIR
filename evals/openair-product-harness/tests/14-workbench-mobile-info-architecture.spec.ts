import { expect, test, type Page } from "@playwright/test";
import { disposeDevSession, prepareDevHarnessAuth } from "../lib/auth";
import { API_BASE_URL } from "../lib/config";
import { collectPageEvidence, screenshot } from "../lib/evidence";
import { pickSeededAdvancableTask, readTasks } from "../lib/gates";

async function expectNoHorizontalOverflow(page: Page, label: string) {
  const metrics = await page.evaluate(() => {
    const root = document.documentElement;
    const body = document.body;
    return {
      innerWidth: window.innerWidth,
      rootScrollWidth: root.scrollWidth,
      bodyScrollWidth: body.scrollWidth,
      visibleWideElements: Array.from(document.querySelectorAll("body *"))
        .map((node) => {
          const element = node as HTMLElement;
          const rect = element.getBoundingClientRect();
          return {
            tag: element.tagName.toLowerCase(),
            testId: element.getAttribute("data-testid"),
            className: typeof element.className === "string" ? element.className : "",
            left: Math.round(rect.left),
            right: Math.round(rect.right),
            width: Math.round(rect.width),
          };
        })
        .filter((item) => item.width > 0 && (item.left < -2 || item.right > window.innerWidth + 2))
        .slice(0, 12),
    };
  });

  expect(metrics.rootScrollWidth, `${label}: document should not overflow horizontally`).toBeLessThanOrEqual(metrics.innerWidth + 2);
  expect(metrics.bodyScrollWidth, `${label}: body should not overflow horizontally`).toBeLessThanOrEqual(metrics.innerWidth + 2);
  expect(metrics.visibleWideElements, `${label}: no visible child should escape viewport`).toEqual([]);
}

async function expectHeaderControlsWithinViewport(page: Page, label: string) {
  const controls = await page.locator("header").first().locator("button, .ant-tag").evaluateAll((nodes) =>
    nodes
      .map((node) => {
        const element = node as HTMLElement;
        const rect = element.getBoundingClientRect();
        const style = window.getComputedStyle(element);
        return {
          text: (element.textContent ?? element.getAttribute("aria-label") ?? "").replace(/\s+/g, " ").trim(),
          visible: style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0,
          left: rect.left,
          right: rect.right,
          width: rect.width,
        };
      })
      .filter((item) => item.visible),
  );
  for (const control of controls) {
    expect(control.width, `${label}: header control has usable width ${control.text}`).toBeGreaterThan(8);
    expect(control.left, `${label}: header control does not start off-screen ${control.text}`).toBeGreaterThanOrEqual(-1);
    expect(control.right, `${label}: header control does not end off-screen ${control.text}`).toBeLessThanOrEqual(await page.evaluate(() => window.innerWidth + 1));
  }
}

async function expectDecisionActionMatchesState(page: Page, label: string) {
  const conclusion = (await page.getByTestId("workbench-current-conclusion").innerText()).trim();
  const nextAction = (await page.getByTestId("workbench-unique-next-action").innerText()).trim();
  const primaryAction = (await page.getByTestId("workbench-center-primary-action").innerText()).trim();
  expect(conclusion, `${label}: current decision text is visible`).not.toEqual("");
  expect(nextAction, `${label}: next action text is visible`).not.toEqual("");
  expect(primaryAction, `${label}: primary action label is visible`).not.toEqual("");

  const blockedDecision = /没有|不能|需要|待|不足|失败|退回|补充/.test(`${conclusion} ${nextAction}`);
  if (blockedDecision) {
    expect(primaryAction, `${label}: blocked decision must not present advancing as the primary action`).not.toBe("下一阶段");
  }
}

async function expectRiskZonesSeparated(page: Page, label: string) {
  const primaryBox = await page.getByTestId("workbench-center-primary-action").boundingBox();
  const inputBox = await page.getByTestId("workbench-collaboration-input-zone").boundingBox();
  expect(primaryBox, `${label}: stage progression action is measurable`).toBeTruthy();
  expect(inputBox, `${label}: collaboration input zone is measurable`).toBeTruthy();
  if (!primaryBox || !inputBox) return;

  const overlapX = Math.max(0, Math.min(primaryBox.x + primaryBox.width, inputBox.x + inputBox.width) - Math.max(primaryBox.x, inputBox.x));
  const overlapY = Math.max(0, Math.min(primaryBox.y + primaryBox.height, inputBox.y + inputBox.height) - Math.max(primaryBox.y, inputBox.y));
  expect(overlapX * overlapY, `${label}: high-risk progression action and low-risk collaboration input do not overlap`).toBe(0);
}

test("Product-02: workbench mobile tabs and 1280 desktop columns keep core actions reachable", async ({ page, playwright }, testInfo) => {
  const evidence = collectPageEvidence(page, testInfo);
  const session = await prepareDevHarnessAuth(playwright, page);
  try {
    const tasks = await readTasks(session.request, API_BASE_URL);
    const seeded = pickSeededAdvancableTask(tasks);
    expect(seeded, "A seeded replay task is required").toBeTruthy();
    if (!seeded) throw new Error("No seeded replay task found");
    const taskId = seeded.taskId as string;

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/workbench/${taskId}`, { waitUntil: "commit" });
    await expect(page.getByTestId("workbench-mobile-tabs")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("workbench-center")).toBeVisible();
    await expect(page.getByTestId("artifact-viewer-canvas")).toBeVisible();
    await expect(page.getByTestId("workbench-center-primary-action")).toBeVisible();
    await expect(page.getByTestId("workbench-left-panel")).toHaveCount(0);
    await expect(page.getByTestId("workbench-right-panel")).toHaveCount(0);
    await expectDecisionActionMatchesState(page, "390 artifact tab");
    await expectNoHorizontalOverflow(page, "390 artifact tab");
    await expectHeaderControlsWithinViewport(page, "390 artifact tab");
    await screenshot(page, testInfo, "product02-mobile-artifact-390");

    await page.getByTestId("workbench-mobile-tabs").getByText("阶段", { exact: true }).click();
    await expect(page.getByTestId("workbench-left-panel")).toBeVisible();
    await expect(page.getByTestId("workbench-left-panel").locator("nav")).toBeVisible();
    await expect(page.getByTestId("workbench-center")).toHaveCount(0);
    await expect(page.getByTestId("workbench-right-panel")).toHaveCount(0);
    await expectNoHorizontalOverflow(page, "390 stage tab");
    await screenshot(page, testInfo, "product02-mobile-stage-390");

    await page.getByTestId("workbench-mobile-tabs").getByText("协作/进度", { exact: true }).click();
    await expect(page.getByTestId("workbench-right-panel")).toBeVisible();
    await expect(page.getByTestId("workbench-conversation-tab-panel")).toBeVisible();
    await expect(page.getByTestId("workbench-right-decision-brief")).toBeVisible();
    await expect(page.getByTestId("workbench-right-current-todo")).not.toHaveText("");
    await expect(page.getByTestId("workbench-right-key-event")).not.toHaveText("");
    await expect(page.getByTestId("workbench-collaboration-input-zone")).toBeVisible();
    await expect(page.getByRole("tab", { name: "审计" })).toBeVisible();
    await expect(page.getByRole("button", { name: "协作记录" })).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByRole("button", { name: "协作记录" })).toHaveAttribute("aria-controls", /\S+/);
    await expect(page.getByTestId("workbench-left-panel")).toHaveCount(0);
    await expect(page.getByTestId("workbench-center")).toHaveCount(0);
    await expectNoHorizontalOverflow(page, "390 support tab");
    await screenshot(page, testInfo, "product02-mobile-support-390");

    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto(`/workbench/${taskId}`, { waitUntil: "commit" });
    await expect(page.getByTestId("workbench-mobile-tabs")).toHaveCount(0);
    await expect(page.getByTestId("workbench-left-panel")).toBeVisible();
    await expect(page.getByTestId("workbench-center")).toBeVisible();
    await expect(page.getByTestId("workbench-right-panel")).toBeVisible();
    const centerBox = await page.getByTestId("workbench-center").boundingBox();
    const artifactBox = await page.getByTestId("artifact-viewer-canvas").boundingBox();
    expect(centerBox?.width ?? 0, "1280 desktop center remains readable").toBeGreaterThanOrEqual(560);
    expect(artifactBox?.height ?? 0, "1280 desktop artifact area remains readable").toBeGreaterThanOrEqual(360);
    expect(artifactBox?.height ?? 0, "1280 desktop artifact area receives the majority of center height").toBeGreaterThan(
      (centerBox?.height ?? 0) * 0.5,
    );
    await expect(page.getByTestId("workbench-center-primary-action")).toBeVisible();
    await expect(page.getByTestId("workbench-right-decision-brief")).toBeVisible();
    await expect(page.getByTestId("workbench-collaboration-input-zone")).toBeVisible();
    await expectDecisionActionMatchesState(page, "1280 desktop");
    await expectRiskZonesSeparated(page, "1280 desktop");
    await expectNoHorizontalOverflow(page, "1280 desktop");
    await expectHeaderControlsWithinViewport(page, "1280 desktop");
    await screenshot(page, testInfo, "product02-desktop-three-column-1280x720");

    expect(evidence.pageErrors, "Product-02 workbench layout flow emits no uncaught page errors").toEqual([]);
  } finally {
    await evidence.flush();
    await disposeDevSession(session);
  }
});
