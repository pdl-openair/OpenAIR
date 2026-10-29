import { expect, test } from "../lib/test";
import { API_BASE_URL } from "../lib/config";
import { collectPageEvidence, screenshot, snapshotTask } from "../lib/evidence";

test("S21: a contract-confirmed clarification is persisted as a recoverable draft", async ({ page, request }, testInfo) => {
  const evidence = collectPageEvidence(page, testInfo);
  const input = "帮我看看这个";
  let taskId: string | null = null;

  try {
    await page.goto("/", { waitUntil: "commit" });
    const researchInput = page.getByTestId("research-intent-input");
    await expect(researchInput).toBeVisible({ timeout: 30_000 });
    await researchInput.fill(input);
    await page.getByTestId("starter-home-main-create").click();
    await expect(page.getByTestId("starter-task-contract-preview")).toBeVisible();

    await page.getByTestId("starter-home-contract-confirm").click();
    await expect(page.getByTestId("workbench-draft-start-confirmation")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("workbench-draft-start-confirmation")).toContainText("任务草案已准备好");
    const draftDetails = page.getByTestId("workbench-draft-details-scroll");
    await expect(draftDetails).toContainText("研究对象");
    await expect(draftDetails).toContainText("已有材料");
    await expect(draftDetails).toContainText("想得到的结果");
    await expect(page.getByTestId("workbench-draft-confirm-start")).toBeEnabled();
    await expect(page.getByText("当前还没有任务", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "上传材料", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "修改输入", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "补充后重新整理", exact: true })).toBeVisible();
    await screenshot(page, testInfo, "contract-clarification-draft-workbench");

    taskId = await page.evaluate(() => window.sessionStorage.getItem("openair.userRoute.currentTaskId"));
    expect(taskId, "contract-confirmed clarification must persist a current task").toBeTruthy();
    const task = await snapshotTask(request, testInfo, API_BASE_URL, taskId!, "contract-clarification-draft");
    expect(task.status).toBe("draft");
    expect(task.creationStatus).toBe("awaiting_confirmation");
    const artifact = task.artifacts.find((row: { kind?: string }) => row.kind === "research_start_draft");
    expect(artifact?.missingInputs).toEqual(["研究对象", "已有材料", "想得到的结果"]);

    await page.reload();
    await expect(page.getByTestId("workbench-draft-start-confirmation")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("button", { name: "上传材料", exact: true })).toBeVisible();
    expect(await page.evaluate(() => window.sessionStorage.getItem("openair.userRoute.currentTaskId"))).toBe(taskId);
    expect(evidence.pageErrors).toEqual([]);
  } finally {
    await evidence.flush();
    if (taskId) await request.delete(`${API_BASE_URL}/api/tasks/${taskId}`);
  }
});
