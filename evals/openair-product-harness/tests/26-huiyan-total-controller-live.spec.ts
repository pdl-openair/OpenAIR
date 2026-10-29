import { expect, test } from "../lib/test";
import { API_BASE_URL } from "../lib/config";
import { attachJson, collectPageEvidence, screenshot, snapshotTask } from "../lib/evidence";

const RESEARCH_INPUT = [
  "I am preparing a source-bounded systems research note, not a completed result.",
  "Supplied references: Jeffrey Dean and Sanjay Ghemawat, 'MapReduce: Simplified Data Processing on Large Clusters,' OSDI 2004; Matei Zaharia et al., 'Spark: Cluster Computing with Working Sets,' HotCloud 2010.",
  "Research scope: formulate a confirmable comparison of the papers' stated fault-tolerance model, intermediate-data reuse model, scheduler implications, and workload boundaries.",
  "Constraints: use only these supplied bibliographic records and later user-provided excerpts; do not retrieve external papers, run benchmarks, or claim performance results.",
  "Requested first artifact: a claim-evidence comparison matrix with explicit evidence gaps and a review checklist for my advisor.",
].join("\n");

test.describe("HUIYAN total-controller live product flow", () => {
  test("S26 / G04: Home to confirmed workbench persists a HUIYAN Live planning audit without invoking CC execution", async ({ page, request }, testInfo) => {
    test.setTimeout(180_000);
    const evidence = collectPageEvidence(page, testInfo);
    try {
      await page.goto("/", { waitUntil: "commit" });
      const intentInput = page.getByTestId("research-intent-input");
      await expect(intentInput).toBeVisible({ timeout: 30_000 });
      await intentInput.fill(RESEARCH_INPUT);
      await page.getByTestId("starter-home-main-create").click();
      await expect(page.getByTestId("starter-task-contract-preview")).toBeVisible({ timeout: 30_000 });
      await screenshot(page, testInfo, "s26-01-contract");

      await page.getByTestId("starter-home-contract-confirm").click();
      await expect(page.getByTestId("workbench-draft-start-confirmation")).toBeVisible({ timeout: 120_000 });
      const taskId = await page.evaluate(() => window.sessionStorage.getItem("openair.userRoute.currentTaskId"));
      expect(taskId, "the confirmed contract must create a durable task identity").toBeTruthy();
      await screenshot(page, testInfo, "s26-02-huiyan-draft");

      const draft = await snapshotTask(request, testInfo, API_BASE_URL, taskId!, "s26-huiyan-draft");
      const audit = draft.creationPlan?.planningAudit ?? {};
      expect(draft.status).toBe("draft");
      expect(draft.creationStatus).toBe("awaiting_confirmation");
      expect(draft.creationPlan?.planningSource).toBe("llm");
      expect(audit.mode).toBe("live");
      expect(audit.provider).toBe("huiyan_chat_completions");
      expect(audit.model).toBeTruthy();
      expect(audit.runId).toBeTruthy();
      expect(draft.capabilityCalls ?? []).toEqual([]);

      const confirmation = page.getByTestId("research-start-confirmation-panel");
      await expect(confirmation).toBeVisible();
      for (const checkbox of await confirmation.getByRole("checkbox").all()) {
        await checkbox.check();
      }
      const acceptLimitations = confirmation.getByRole("radio").nth(1);
      await acceptLimitations.check();
      await page.getByTestId("workbench-draft-confirm-start").click();
      await expect(page.getByTestId("workbench-draft-start-confirmation")).toBeHidden({ timeout: 30_000 });
      await page.reload({ waitUntil: "commit" });

      const confirmed = await snapshotTask(request, testInfo, API_BASE_URL, taskId!, "s26-huiyan-confirmed");
      expect(confirmed.status).toBe("not_started");
      expect(confirmed.creationStatus).toBe("confirmed");
      expect(confirmed.creationPlan?.planningAudit).toEqual(audit);
      expect(confirmed.capabilityCalls ?? []).toEqual([]);
      await screenshot(page, testInfo, "s26-03-confirmed-workbench");
      await attachJson(testInfo, "s26-huiyan-live-audit", {
        taskId,
        planningSource: confirmed.creationPlan?.planningSource,
        planningAudit: confirmed.creationPlan?.planningAudit,
        capabilityCallCount: (confirmed.capabilityCalls ?? []).length,
      });
      expect(evidence.pageErrors).toEqual([]);
    } finally {
      await evidence.flush();
    }
  });
});
