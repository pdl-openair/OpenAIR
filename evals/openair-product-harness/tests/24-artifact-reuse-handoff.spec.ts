import { expect, test } from "../lib/test";
import { API_BASE_URL } from "../lib/config";
import { attachJson, collectPageEvidence, screenshot, snapshotTask } from "../lib/evidence";

const SOURCE_TASK_ID = "task_cs_ope_001";
const SOURCE_ARTIFACT_ID = "art-001";
const SOURCE_ARTIFACT_TITLE = "OPE 文献调研综述";

test("S24 / G09: a reviewed artifact can be reused into a new task without losing its source trail", async ({ page, request }, testInfo) => {
  const evidence = collectPageEvidence(page, testInfo);
  try {
    const source = await snapshotTask(request, testInfo, API_BASE_URL, SOURCE_TASK_ID, "s24-source-before-reuse");
    const sourceArtifact = source.artifacts.find((artifact: { id?: string }) => artifact.id === SOURCE_ARTIFACT_ID);
    expect(sourceArtifact, "The seeded literature artifact must exist before reuse").toBeTruthy();

    const targetTitle = `Artifact handoff target ${Date.now()}`;
    const targetResponse = await request.post(`${API_BASE_URL}/api/tasks`, {
      data: {
        title: targetTitle,
        goalSummary: "Use a reviewed literature artifact as bounded input for the next research task.",
        domain: "computer_science",
        currentStage: "problem_modeling",
        status: "draft",
      },
    });
    expect(targetResponse.ok(), "A fresh target task should be created for artifact handoff").toBeTruthy();
    const target = await targetResponse.json();
    const targetTaskId = target.taskId as string;

    await page.goto("/artifacts", { waitUntil: "commit" });
    const sourceRow = page.getByTestId(`artifact-library-row-${SOURCE_TASK_ID}-${SOURCE_ARTIFACT_ID}`);
    await expect(sourceRow).toBeVisible();
    await sourceRow.getByRole("button", { name: SOURCE_ARTIFACT_TITLE, exact: true }).click();
    const drawer = page.getByRole("dialog").filter({ hasText: SOURCE_ARTIFACT_TITLE });
    await expect(drawer).toBeVisible();

    const reviewButton = drawer.getByRole("button", { name: "核查通过", exact: true });
    if (await reviewButton.isVisible()) {
      await reviewButton.click();
      await expect(drawer.getByText("产物状态：已核查入库", { exact: true })).toBeVisible();
    }
    const reuseButton = page.getByTestId(`artifact-library-reuse-${SOURCE_ARTIFACT_ID}`);
    await expect(reuseButton).toBeVisible();
    await reuseButton.click();

    const reuseModal = page.getByRole("dialog").filter({ hasText: "复用产物到任务" });
    await expect(reuseModal).toBeVisible();
    await reuseModal.getByRole("combobox").click();
    await page.getByText(targetTitle, { exact: true }).last().click();
    await reuseModal.locator(".ant-btn-primary").click();

    let targetTask: any;
    let imported: any;
    await expect
      .poll(async () => {
        targetTask = await snapshotTask(request, testInfo, API_BASE_URL, targetTaskId, "s24-target-after-reuse");
        imported = (targetTask.artifacts ?? []).find((artifact: { origin?: string }) => artifact.origin === "imported");
        return imported?.id ?? "missing";
      }, { timeout: 15_000 })
      .not.toBe("missing");

    expect(imported.id).not.toBe(SOURCE_ARTIFACT_ID);
    expect(imported.reusedFrom).toEqual(
      expect.objectContaining({ artifactId: SOURCE_ARTIFACT_ID, taskId: SOURCE_TASK_ID, artifactTitle: SOURCE_ARTIFACT_TITLE }),
    );
    const sourceContent = await request.get(`${API_BASE_URL}/api/artifacts/${SOURCE_ARTIFACT_ID}/content`);
    expect(sourceContent.ok(), "The original artifact remains readable after reuse").toBeTruthy();

    const relationResponse = await request.get(`${API_BASE_URL}/api/artifacts/${imported.id}/relations`);
    expect(relationResponse.ok(), "The reused artifact should expose its provenance relation").toBeTruthy();
    const relations = (await relationResponse.json()).relations as Array<Record<string, unknown>>;
    expect(relations).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ relationType: "reuses", direction: "outgoing", artifactId: SOURCE_ARTIFACT_ID }),
      ]),
    );

    await page.goto("/artifacts", { waitUntil: "commit" });
    const importedRow = page.getByTestId(`artifact-library-row-${targetTaskId}-${imported.id}`);
    await expect(importedRow).toBeVisible();
    await importedRow.getByRole("button", { name: SOURCE_ARTIFACT_TITLE, exact: true }).click();
    await expect(page.getByText("文献筛选与综述报告：OPE 算法库生态调研", { exact: true })).toBeVisible();
    await expect(page.getByText(`来源产物：OPE 方法在训练动态中的可靠性研究 / ${SOURCE_ARTIFACT_TITLE}`, { exact: true })).toBeVisible();
    await screenshot(page, testInfo, "s24-imported-artifact-library-preview");
    await page.reload({ waitUntil: "commit" });
    await expect(page.getByTestId(`artifact-library-row-${targetTaskId}-${imported.id}`)).toBeVisible();
    await attachJson(testInfo, "s24-artifact-handoff-observation", {
      sourceTaskId: SOURCE_TASK_ID,
      sourceArtifactId: SOURCE_ARTIFACT_ID,
      targetTaskId,
      importedArtifactId: imported.id,
      reusedFrom: imported.reusedFrom,
      relations,
    });
    expect(evidence.pageErrors).toEqual([]);
  } finally {
    await evidence.flush();
  }
});
