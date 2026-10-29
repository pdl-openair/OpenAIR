import fs from "node:fs";
import path from "node:path";
import { expect, test } from "../lib/test";
import { API_BASE_URL } from "../lib/config";
import { attachJson, collectPageEvidence, screenshot, snapshotTask } from "../lib/evidence";

type RepresentativeScenario = {
  id: string;
  role: string;
  expectedTaskType: string;
  expectedCapability: string;
  requiredArtifactTypes: string[];
  expectedDraftOutput: string;
  expectedMissingInputs: string[];
  expectedArtifact: string;
  input: string;
};

const scenarios: RepresentativeScenario[] = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, "../scenarios/g01-representative-research-inputs.json"), "utf8"),
) as RepresentativeScenario[];

test("S23: representative computer-science, theoretical-materials, and experimental-materials inputs each create recoverable research drafts", async ({ page, request }, testInfo) => {
  const evidence = collectPageEvidence(page, testInfo);
  const observations: Array<Record<string, unknown>> = [];
  try {
    expect(scenarios.map((scenario) => scenario.role)).toEqual([
      "计算机科学科研人员",
      "理论材料科研人员",
      "实验材料科研人员",
    ]);

    for (const scenario of scenarios) {
      await page.goto("/", { waitUntil: "commit" });
      const input = page.getByTestId("research-intent-input");
      await expect(input).toBeVisible({ timeout: 30_000 });
      await input.fill(scenario.input);
      await page.getByTestId("starter-home-main-create").click();
      await expect(page.getByTestId("starter-task-contract-preview")).toBeVisible();
      await page.getByTestId("starter-home-contract-confirm").click();
      await expect(page.getByTestId("workbench-draft-start-confirmation")).toBeVisible({ timeout: 30_000 });

      const taskId = await page.evaluate(() => window.sessionStorage.getItem("openair.userRoute.currentTaskId"));
      expect(taskId, `${scenario.id} should persist a current task`).toBeTruthy();
      const task = await snapshotTask(request, testInfo, API_BASE_URL, taskId!, `s23-${scenario.id}-draft`);
      const draftArtifact = task.artifacts.find((artifact: { kind?: string }) => artifact.kind === "research_start_draft");
      expect(task.status, `${scenario.id} starts as a draft before execution`).toBe("draft");
      expect(task.creationStatus, `${scenario.id} requires explicit confirmation`).toBe("awaiting_confirmation");
      expect(draftArtifact, `${scenario.id} stores a durable research-start artifact`).toBeTruthy();
      expect(task.creationPlan?.understanding?.taskType, `${scenario.id} preserves the requested research task semantics`).toBe(
        scenario.expectedTaskType,
      );
      const selectedCapabilities = task.creationPlan?.selectedCapabilities ?? [];
      const capability = selectedCapabilities.find((item: { id?: string }) => item.id === scenario.expectedCapability);
      expect(capability, `${scenario.id} selects its domain-appropriate planning capability`).toBeTruthy();
      expect(capability?.expectedArtifactTypes ?? [], `${scenario.id} retains reviewable expected artifacts`).toEqual(
        expect.arrayContaining(scenario.requiredArtifactTypes),
      );
      expect(draftArtifact?.inlineContent ?? "", `${scenario.id} shows the planned research output in the confirmation draft`).toContain(
        scenario.expectedDraftOutput,
      );
      expect(draftArtifact?.missingInputs ?? [], `${scenario.id} does not request inputs already supplied by the researcher`).toEqual(
        scenario.expectedMissingInputs,
      );

      await page.reload({ waitUntil: "commit" });
      await expect(page.getByTestId("workbench-draft-start-confirmation")).toBeVisible({ timeout: 30_000 });
      await screenshot(page, testInfo, `s23-${scenario.id}-recoverable-draft`);
      observations.push({
        id: scenario.id,
        role: scenario.role,
        taskId,
        researchType: task.creationPlan?.researchType ?? null,
        taskType: task.creationPlan?.understanding?.taskType ?? null,
        capability: capability?.id ?? null,
        expectedArtifactTypes: capability?.expectedArtifactTypes ?? [],
        missingInputs: draftArtifact?.missingInputs ?? [],
        expectedArtifact: scenario.expectedArtifact,
      });
    }
    await attachJson(testInfo, "s23-representative-input-observations", observations);
    expect(evidence.pageErrors).toEqual([]);
  } finally {
    await evidence.flush();
  }
});
