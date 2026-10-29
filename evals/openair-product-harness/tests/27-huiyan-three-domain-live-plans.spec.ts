import fs from "node:fs";
import path from "node:path";
import { expect, test } from "../lib/test";
import { API_BASE_URL } from "../lib/config";
import { attachJson, collectPageEvidence, snapshotTask } from "../lib/evidence";

type Scenario = {
  id: string;
  expectedTaskType: string;
  input: string;
};

const scenarios: Scenario[] = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, "../scenarios/g01-representative-research-inputs.json"), "utf8"),
) as Scenario[];

test.describe("HUIYAN three-domain Live planning", () => {
  test("S27 / G01: realistic computer-science and materials inputs remain auditable, confirmable, and non-executing", async ({ page, request }, testInfo) => {
    test.setTimeout(240_000);
    const evidence = collectPageEvidence(page, testInfo);
    const observations: Array<Record<string, unknown>> = [];
    try {
      for (const scenario of scenarios) {
        await page.goto("/", { waitUntil: "commit" });
        const intentInput = page.getByTestId("research-intent-input");
        await expect(intentInput).toBeVisible({ timeout: 30_000 });
        await intentInput.fill(scenario.input);
        await page.getByTestId("starter-home-main-create").click();
        await expect(page.getByTestId("starter-task-contract-preview")).toBeVisible({ timeout: 30_000 });
        await page.getByTestId("starter-home-contract-confirm").click();
        await expect(page.getByTestId("workbench-draft-start-confirmation")).toBeVisible({ timeout: 120_000 });

        const taskId = await page.evaluate(() => window.sessionStorage.getItem("openair.userRoute.currentTaskId"));
        expect(taskId, `${scenario.id} must preserve a task identity`).toBeTruthy();
        const draft = await snapshotTask(request, testInfo, API_BASE_URL, taskId!, `s27-${scenario.id}-draft`);
        expect(draft.creationPlan?.planningSource).toBe("llm");
        expect(draft.creationPlan?.planningAudit?.provider).toBe("huiyan_chat_completions");
        expect(draft.creationPlan?.planningAudit?.model).toBeTruthy();
        expect(draft.creationPlan?.planningAudit?.runId).toBeTruthy();
        expect(draft.creationPlan?.understanding?.taskType).toBe(scenario.expectedTaskType);

        const confirmation = page.getByTestId("research-start-confirmation-panel");
        for (const checkbox of await confirmation.getByRole("checkbox").all()) {
          await checkbox.check();
        }
        await confirmation.getByRole("radio").nth(1).check();
        await page.getByTestId("workbench-draft-confirm-start").click();
        await expect(page.getByTestId("workbench-draft-start-confirmation")).toBeHidden({ timeout: 30_000 });
        await page.reload({ waitUntil: "commit" });

        const confirmed = await snapshotTask(request, testInfo, API_BASE_URL, taskId!, `s27-${scenario.id}-confirmed`);
        expect(confirmed.status).toBe("not_started");
        expect(confirmed.creationStatus).toBe("confirmed");
        expect(confirmed.capabilityCalls ?? []).toEqual([]);
        observations.push({
          id: scenario.id,
          taskId,
          taskType: confirmed.creationPlan?.understanding?.taskType,
          planningAudit: confirmed.creationPlan?.planningAudit,
          capabilityCallCount: (confirmed.capabilityCalls ?? []).length,
        });
      }
      await attachJson(testInfo, "s27-huiyan-three-domain-observations", observations);
      expect(evidence.pageErrors).toEqual([]);
    } finally {
      await evidence.flush();
    }
  });
});
