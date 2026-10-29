import fs from "node:fs";
import path from "node:path";
import { expect, test } from "../lib/test";
import { API_BASE_URL } from "../lib/config";
import { collectPageEvidence, snapshotTask } from "../lib/evidence";

const llzo = (JSON.parse(
  fs.readFileSync(path.resolve(__dirname, "../scenarios/g01-representative-research-inputs.json"), "utf8"),
) as Array<{ input: string; expectedTaskType: string }>)[1];

test("S28 / G01: LLZO Live HUIYAN planning repairs semantic drift without executing a Skill", async ({ page, request }, testInfo) => {
  test.setTimeout(180_000);
  const evidence = collectPageEvidence(page, testInfo);
  try {
    await page.goto("/", { waitUntil: "commit" });
    await page.getByTestId("research-intent-input").fill(llzo.input);
    await page.getByTestId("starter-home-main-create").click();
    await expect(page.getByTestId("starter-task-contract-preview")).toBeVisible({ timeout: 30_000 });
    await page.getByTestId("starter-home-contract-confirm").click();
    await expect(page.getByTestId("workbench-draft-start-confirmation")).toBeVisible({ timeout: 150_000 });
    const taskId = await page.evaluate(() => window.sessionStorage.getItem("openair.userRoute.currentTaskId"));
    expect(taskId).toBeTruthy();
    const draft = await snapshotTask(request, testInfo, API_BASE_URL, taskId!, "s28-llzo-draft");
    expect(draft.creationPlan?.planningSource).toBe("llm");
    expect(draft.creationPlan?.planningAudit?.provider).toBe("huiyan_chat_completions");
    expect(draft.creationPlan?.understanding?.taskType).toBe(llzo.expectedTaskType);
    expect(draft.capabilityCalls ?? []).toEqual([]);
    await page.reload({ waitUntil: "commit" });
    await expect(page.getByTestId("workbench-draft-start-confirmation")).toBeVisible({ timeout: 30_000 });
    expect(evidence.pageErrors).toEqual([]);
  } finally {
    await evidence.flush();
  }
});
