import { expect, test } from "@playwright/test";
import { disposeDevSession, prepareDevHarnessAuth } from "../lib/auth";
import { collectPageEvidence, screenshot } from "../lib/evidence";
import {
  appShellScrollFitPassed,
  GateRecorder,
  measureAppShellScrollFit
} from "../lib/gates";

const VIEWPORTS = [
  { width: 1440, height: 900, label: "1440x900" },
  { width: 1280, height: 720, label: "1280x720" }
] as const;

const APP_SHELL_SURFACES = [
  { path: "/workbench", name: "board" },
  { path: "/tasks/task_cs_ope_001", name: "detail" },
  { path: "/settings", name: "settings" }
] as const;

function scrollFitGateId(surface: string, viewportLabel: string): string {
  return `DG36-${surface}-${viewportLabel.replace(/×/g, "x")}`;
}

test.describe("S16 / UI13: app shell scroll container fits viewport", () => {
  for (const viewport of VIEWPORTS) {
    test(`scroll track fills viewport at ${viewport.label}`, async ({
      page,
      playwright
    }, testInfo) => {
      const evidence = collectPageEvidence(page, testInfo);
      const gates = new GateRecorder();
      const session = await prepareDevHarnessAuth(playwright, page);
      try {
        await page.setViewportSize({
          width: viewport.width,
          height: viewport.height
        });

        for (const surface of APP_SHELL_SURFACES) {
          await page.goto(surface.path, { waitUntil: "commit" });
          await expect(page.getByTestId("app-shell-scroll")).toBeVisible();

          const metrics = await measureAppShellScrollFit(page);
          const passed = appShellScrollFitPassed(metrics);
          gates.check(
            scrollFitGateId(surface.name, viewport.label),
            passed,
            `${surface.name} app shell scroll container must align with viewport and avoid page-level double scroll at ${viewport.label}`,
            metrics,
            {
              bottomGapWithin: 2,
              topGapFromHeaderWithin: 2,
              bodyScrollable: false
            }
          );

          await screenshot(
            page,
            testInfo,
            `${surface.name}-scroll-fit-${viewport.label}`
          );
        }

        gates.check(
          `DG39-scroll-fit-errors-${viewport.label.replace(/×/g, "x")}`,
          evidence.pageErrors.length === 0,
          "Viewport scroll fit checks emit no uncaught page error",
          evidence.pageErrors,
          []
        );
      } finally {
        await gates.attach(testInfo);
        await evidence.flush();
        await disposeDevSession(session);
      }
    });
  }
});
