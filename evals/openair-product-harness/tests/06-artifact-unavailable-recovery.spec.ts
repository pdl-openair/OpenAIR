import { expect, test } from "../lib/test";
import { API_BASE_URL } from "../lib/config";
import { attachJson, collectPageEvidence, screenshot } from "../lib/evidence";
import { expectNoVisibleSecretLeak, GateRecorder } from "../lib/gates";

const TASK_ID = "task_cs_ope_001";
const ARTIFACT_ID = "art-001";
const ARTIFACT_TITLE = "OPE 文献调研综述";
const ARTIFACT_BODY_HEADING = "文献筛选与综述报告：OPE 算法库生态调研";
const CONTENT_URL = `${API_BASE_URL}/api/tasks/${TASK_ID}/artifacts/${ARTIFACT_ID}/content`;

function artifactWorkbenchUrlPattern(): RegExp {
  return new RegExp(`/workbench/${TASK_ID}\\?artifact=${ARTIFACT_ID}$`);
}

test("S04 / P2.6a: artifact content 404 shows recovery UI and reload restores body", async ({ page, request }, testInfo) => {
  const evidence = collectPageEvidence(page, testInfo);
  const gates = new GateRecorder();
  let contentRequestCount = 0;
  let realContentBody: string | null = null;

  try {
    const probe = await request.get(CONTENT_URL);
    expect(probe.ok(), "Seed artifact content endpoint should be reachable for replay payload").toBeTruthy();
    realContentBody = await probe.text();

    await page.route(`**/api/tasks/${TASK_ID}/artifacts/${ARTIFACT_ID}/content`, async (route) => {
      contentRequestCount += 1;
      if (contentRequestCount === 1) {
        await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "artifact not found" }) });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: realContentBody ?? "{}",
      });
    });

    await page.goto(`/workbench/${TASK_ID}?artifact=${ARTIFACT_ID}`, { waitUntil: "load" });
    await expect(page).toHaveURL(artifactWorkbenchUrlPattern());
    await expect(page.getByTestId("workbench-center")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("artifact-viewer-canvas")).toBeVisible({ timeout: 30_000 });

    const mainCard = page.getByTestId("artifact-viewer-canvas");
    await expect(page.getByTestId("artifact-unavailable-recovery")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("科研产物暂时无法打开")).toBeVisible();
    await expect(page.getByText("当前任务和其他科研产物不受影响")).toBeVisible();
    await expect(page.getByTestId("artifact-reload-button")).toHaveText("重新加载产物");
    await expect(page.getByRole("button", { name: /任务详情/ })).toBeVisible();
    await expect(page.getByTestId(`artifact-markdown-${ARTIFACT_ID}`)).toHaveCount(0);
    gates.check("DG31", contentRequestCount === 1, "First artifact content request simulated as 404", contentRequestCount, 1);
    await screenshot(page, testInfo, "01-artifact-unavailable-recovery");

    await page.getByTestId("artifact-reload-button").click();
    await expect(page.getByTestId("artifact-unavailable-recovery")).toHaveCount(0);
    await expect(mainCard.getByTestId(`artifact-markdown-${ARTIFACT_ID}`).getByRole("heading", { name: ARTIFACT_BODY_HEADING, exact: true })).toBeVisible();
    gates.check("DG32", contentRequestCount >= 2, "Retry triggers a second artifact content request", contentRequestCount, ">=2");
    gates.check("DG33", true, "Reloaded artifact body renders and unavailable recovery UI disappears");
    await screenshot(page, testInfo, "02-artifact-reloaded-body");

    await attachJson(testInfo, "p26a-artifact-unavailable-recovery", {
      taskId: TASK_ID,
      artifactId: ARTIFACT_ID,
      contentRequestCount,
      recoveryCopyVisible: true,
      reloadSucceeded: true,
    });

    await expectNoVisibleSecretLeak(page);
    gates.check("DG34", evidence.pageErrors.length === 0, "P2.6a artifact unavailable recovery emits no uncaught page error", evidence.pageErrors, []);
  } finally {
    await gates.attach(testInfo);
    await evidence.flush();
  }
});
