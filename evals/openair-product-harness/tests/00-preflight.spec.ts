import { expect, test } from "@playwright/test";
import { disposeDevSession, prepareDevHarnessAuth } from "../lib/auth";
import { API_BASE_URL } from "../lib/config";
import {
  collectPageEvidence,
  screenshot,
  snapshotApiResponse
} from "../lib/evidence";
import { expectNoVisibleSecretLeak } from "../lib/gates";

test("PREFLIGHT: API health and task board shell", async ({
  page,
  playwright
}, testInfo) => {
  const evidence = collectPageEvidence(page, testInfo);
  const session = await prepareDevHarnessAuth(playwright, page);
  try {
    const health = await session.request.get(`${API_BASE_URL}/health`);
    const { bodyText: healthBody } = await snapshotApiResponse(
      testInfo,
      "health",
      health
    );
    expect(health.ok(), "API /health should be reachable").toBeTruthy();
    const healthPayload = JSON.parse(healthBody);
    expect(healthPayload).toMatchObject({
      status: "ok",
      runtimeVersion: { startedAt: expect.any(String) },
    });
    for (const field of ["buildTime", "gitBranch", "gitCommit", "gitCommitShort"]) {
      expect(
        healthPayload.runtimeVersion[field] === null || typeof healthPayload.runtimeVersion[field] === "string",
        `API /health runtimeVersion.${field} should be null or a string`,
      ).toBeTruthy();
    }

    const tasks = await session.request.get(`${API_BASE_URL}/api/tasks`);
    await snapshotApiResponse(testInfo, "tasks-before-browser-flow", tasks);
    expect(
      tasks.ok(),
      "API /api/tasks should be reachable for dev user"
    ).toBeTruthy();

    await page.goto("/workbench", { waitUntil: "commit" });
    await expect(page.getByText("任务列表", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "新建研究任务", exact: true })).toBeVisible();
    await screenshot(page, testInfo, "01-board-shell");
    await expectNoVisibleSecretLeak(page);
    expect(
      evidence.pageErrors,
      "Task board must not emit uncaught page errors"
    ).toEqual([]);
  } finally {
    await evidence.flush();
    await disposeDevSession(session);
  }
});
