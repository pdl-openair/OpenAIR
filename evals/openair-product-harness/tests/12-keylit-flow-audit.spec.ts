import fs from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";
import { disposeDevSession, prepareDevHarnessAuth } from "../lib/auth";

test.describe("key literature start flow audit", () => {
  test("S12: key literature starts through workbench draft, not a home result panel", async ({ page, playwright }, testInfo) => {
    const session = await prepareDevHarnessAuth(playwright, page);
    const outDir = path.join(testInfo.outputDir, "keylit-flow-audit");
    fs.mkdirSync(outDir, { recursive: true });
    const submitResponses: Array<{ url: string; status: number; body: unknown }> = [];

    page.on("response", async (response) => {
      if (!response.url().includes("/api/research-start/submit")) return;
      let body: unknown = null;
      try {
        body = await response.json();
      } catch {
        body = await response.text().catch(() => null);
      }
      submitResponses.push({ url: response.url(), status: response.status(), body });
    });

    try {
      await page.goto("/", { waitUntil: "networkidle" });
      await page.screenshot({ path: path.join(outDir, "01-home.png"), fullPage: true });
      const initialText = await page.locator("body").innerText();
      fs.writeFileSync(path.join(outDir, "01-home.txt"), initialText, "utf8");

      const input = page.getByTestId("research-intent-input");
      await expect(input).toBeVisible();
      await input.fill("find literature");
      await page.getByTestId("starter-home-main-create").click();
      await expect(page.getByTestId("start-decision-notice")).toBeVisible();
      await expect(page).toHaveURL(/\/(?:\?.*)?$/);
      await expect(page.getByTestId("key-literature-assist-panel")).toHaveCount(0);
      await page.screenshot({ path: path.join(outDir, "02-generic-clarification.png"), fullPage: true });

      const keyLitCard = page.getByRole("button", { name: /找关键文献/ }).first();
      await expect(keyLitCard).toBeVisible();
      await keyLitCard.click();

      const currentValue = await input.inputValue();
      if (!currentValue.trim()) {
        await input.fill(
          "导师让我先看锂金属负极 SEI，我现在只有两篇综述和几个关键词。帮我先找关键文献、检索词、绕不开的代表论文和主要争议点。",
        );
      }
      await page.screenshot({ path: path.join(outDir, "02-before-submit.png"), fullPage: true });

      await page.getByTestId("starter-home-main-create").click();
      await expect(page).toHaveURL(/\/workbench\/current/);
      await expect(page.getByTestId("workbench-draft-start-confirmation")).toBeVisible();
      await page.screenshot({ path: path.join(outDir, "03-after-specific-submit.png"), fullPage: true });
      const afterText = await page.locator("body").innerText();
      fs.writeFileSync(path.join(outDir, "03-after-submit.txt"), afterText, "utf8");

      const summary = {
        finalUrl: page.url(),
        submitResponses,
        hasHomeKeyLiteraturePanel: (await page.getByTestId("key-literature-assist-panel").count()) > 0,
        hasStartDecisionNotice: (await page.getByTestId("start-decision-notice").count()) > 0,
        hasWorkbenchDraftCopy: /任务草案已准备好|我理解对了吗|确认前不会开始/.test(afterText),
        hasEnglishPanelCopy: /Key literature positioning|Candidate readings|Source attempts|Coverage|Boundary/.test(afterText),
        hasArxivCopy: /arxiv/i.test(afterText),
        hasMojibake: /锛|绉|鐮|寮|鏂|熷/.test(afterText),
      };
      const summaryPath = path.join(outDir, "summary.json");
      fs.writeFileSync(summaryPath, JSON.stringify(summary, null, 2), "utf8");
      await testInfo.attach("keylit-flow-audit-summary", {
        path: summaryPath,
        contentType: "application/json",
      });
      console.log(JSON.stringify(summary, null, 2));

      expect(submitResponses).toHaveLength(2);
      expect((submitResponses[0].body as { outcome?: string }).outcome).toBe("needs_clarification");
      expect((submitResponses[1].body as { outcome?: string; panel?: unknown; redirectTo?: string }).outcome).toBe("research_task");
      expect((submitResponses[1].body as { panel?: unknown }).panel).toBeNull();
      expect((submitResponses[1].body as { redirectTo?: string }).redirectTo).toBe("/workbench/current");
      expect(summary.hasHomeKeyLiteraturePanel).toBe(false);
      expect(summary.hasWorkbenchDraftCopy).toBe(true);
      expect(summary.hasEnglishPanelCopy).toBe(false);
      expect(summary.hasArxivCopy).toBe(false);
      expect(summary.hasMojibake).toBe(false);
    } finally {
      await disposeDevSession(session);
    }
  });
});
