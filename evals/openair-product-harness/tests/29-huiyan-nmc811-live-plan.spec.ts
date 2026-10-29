import fs from "node:fs";
import path from "node:path";
import { expect, test } from "../lib/test";
import { API_BASE_URL } from "../lib/config";
import { collectPageEvidence, snapshotTask } from "../lib/evidence";

const nmc811 = (JSON.parse(
  fs.readFileSync(path.resolve(__dirname, "../scenarios/g01-representative-research-inputs.json"), "utf8"),
) as Array<{ input: string; expectedTaskType: string }>)[2];

test("S29 / G01: NMC811 Live HUIYAN experimental plan remains confirmable without implicit execution", async ({ page, request }, testInfo) => {
  test.setTimeout(180_000);
  const evidence = collectPageEvidence(page, testInfo);
  try {
    await page.goto("/", { waitUntil: "commit" });
    await page.getByTestId("research-intent-input").fill(nmc811.input);
    await page.getByTestId("starter-home-main-create").click();
    await expect(page.getByTestId("starter-task-contract-preview")).toBeVisible({ timeout: 30_000 });
    await page.getByTestId("starter-home-contract-confirm").click();
    await expect(page.getByTestId("workbench-draft-start-confirmation")).toBeVisible({ timeout: 150_000 });
    const taskId = await page.evaluate(() => window.sessionStorage.getItem("openair.userRoute.currentTaskId"));
    expect(taskId).toBeTruthy();
    const draft = await snapshotTask(request, testInfo, API_BASE_URL, taskId!, "s29-nmc811-draft");
    expect(draft.creationPlan?.planningSource).toBe("llm");
    expect(draft.creationPlan?.planningAudit?.provider).toBe("huiyan_chat_completions");
    expect(draft.creationPlan?.understanding?.taskType).toBe(nmc811.expectedTaskType);
    expect(draft.capabilityCalls ?? []).toEqual([]);
    await page.reload({ waitUntil: "commit" });
    await expect(page.getByTestId("workbench-draft-start-confirmation")).toBeVisible({ timeout: 30_000 });
    expect(evidence.pageErrors).toEqual([]);
  } finally {
    await evidence.flush();
  }
});
