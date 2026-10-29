import { expect, test } from "@playwright/test";
import { disposeDevSession, prepareDevHarnessAuth } from "../lib/auth";
import { API_BASE_URL } from "../lib/config";
import { attachJson, collectPageEvidence, screenshot, snapshotApiResponse } from "../lib/evidence";
import { expectNoVisibleSecretLeak, GateRecorder, readTasks } from "../lib/gates";

const TASK_ID = "task_cs_ope_001";
const ARTIFACT_ID = "art-001";
const ARTIFACT_TITLE = "OPE 文献调研综述";
const ARTIFACT_PATH = "literature.md";
const ARTIFACT_BODY_HEADING = "文献筛选与综述报告：OPE 算法库生态调研";
const CAPABILITY_CALL_ID = "call_001";
const CAPABILITY_LABEL = "文献调研";

function artifactWorkbenchUrlPattern(): RegExp {
  return new RegExp(`/workbench/${TASK_ID}\\?artifact=${ARTIFACT_ID}$`);
}

test("S03 / P2.5a: replay artifact opens, hydrates, restores, and links back to its capability call", async ({ page, playwright }, testInfo) => {
  const evidence = collectPageEvidence(page, testInfo);
  const gates = new GateRecorder();
  const session = await prepareDevHarnessAuth(playwright, page);
  try {
    const tasks = await readTasks(session.request, API_BASE_URL);
    const seeded = tasks.find((task) => task.taskId === TASK_ID);
    expect(seeded, `Replay task ${TASK_ID} should exist`).toBeTruthy();
    if (!seeded) throw new Error(`Replay task ${TASK_ID} missing`);

    const artifact = (seeded.artifacts ?? []).find((item: any) => item.id === ARTIFACT_ID);
    const capabilityCall = (seeded.capabilityCalls ?? []).find((item: any) => item.id === CAPABILITY_CALL_ID);
    gates.check("DG22", seeded.resultSource === "replay", "P2.5a seed task is explicitly marked as replay", seeded.resultSource, "replay");
    gates.check(
      "DG23",
      Boolean(artifact) && artifact.title === ARTIFACT_TITLE && artifact.path === ARTIFACT_PATH,
      "Replay task exposes a stable literature artifact anchor",
      artifact,
      { id: ARTIFACT_ID, title: ARTIFACT_TITLE, path: ARTIFACT_PATH },
    );
    gates.check(
      "DG24",
      Boolean(capabilityCall) && (capabilityCall.artifactRefs ?? []).includes(ARTIFACT_ID),
      "Replay capability call references the literature artifact",
      capabilityCall,
      { id: CAPABILITY_CALL_ID, artifactRefsIncludes: ARTIFACT_ID },
    );

    await attachJson(testInfo, "p25a-artifact-contract-observation", {
      taskId: TASK_ID,
      artifactId: ARTIFACT_ID,
      sourceCapabilityCallId: artifact?.sourceCapabilityCallId ?? null,
      structurallyLinkedByCapabilityCall: capabilityCall?.id ?? null,
      hasArtifactVersionField: Boolean(artifact && Object.prototype.hasOwnProperty.call(artifact, "version")),
      hasStructuredEvidenceRefs: Array.isArray(artifact?.evidenceRefs) && artifact.evidenceRefs.length > 0,
      observation: "Replay fixture links capability call -> artifact and exposes artifact-level structured evidence/source references. Artifact versioning remains outside P2.5b scope.",
    });

    await page.goto("/workbench", { waitUntil: "commit" });
    await page.getByText("近 7 天", { exact: true }).click();
    await page.getByText("全部时间", { exact: true }).click();
    const boardRow = page.locator("tr").filter({ hasText: seeded.title });
    const boardPageCount = Math.ceil(tasks.length / 20);
    for (let pageNumber = 1; pageNumber <= boardPageCount && await boardRow.count() === 0; pageNumber += 1) {
      const currentPage = (await page.locator(".ant-pagination-item-active").textContent())?.trim();
      const nextPage = page.locator(".ant-pagination-next:not(.ant-pagination-disabled)");
      if (await nextPage.count() === 0) break;
      await nextPage.click();
      await expect.poll(async () => (await page.locator(".ant-pagination-item-active").textContent())?.trim()).not.toBe(currentPage);
    }
    expect(await boardRow.count(), "Replay task should be discoverable through the task board pagination").toBeGreaterThan(0);
    await expect(boardRow.getByTestId("result-source-tag")).toHaveText("回放结果");
    await screenshot(page, testInfo, "00-board-replay-source-disclosure");

    const contentResponse = await session.request.get(`${API_BASE_URL}/api/tasks/${TASK_ID}/artifacts/${ARTIFACT_ID}/content`);
    const { bodyText: contentBody } = await snapshotApiResponse(testInfo, "p25a-literature-artifact-content", contentResponse);
    expect(contentResponse.ok(), "Replay artifact content endpoint should succeed").toBeTruthy();
    const content = JSON.parse(contentBody);
    gates.check(
      "DG25",
      content.artifactId === ARTIFACT_ID && content.path === ARTIFACT_PATH && content.sourceMode === "replay",
      "Artifact content endpoint preserves artifact identity and explicitly reports replay source mode",
      { artifactId: content.artifactId, path: content.path, sourceMode: content.sourceMode },
      { artifactId: ARTIFACT_ID, path: ARTIFACT_PATH, sourceMode: "replay" },
    );
    gates.check(
      "DG26",
      typeof content.content === "string" && content.content.includes(`# ${ARTIFACT_BODY_HEADING}`) && content.content.includes("arXiv:2311.18206"),
      "Replay literature artifact contains a stable rendered heading and a source identifier",
      { hasHeading: content.content?.includes(`# ${ARTIFACT_BODY_HEADING}`), hasSourceIdentifier: content.content?.includes("arXiv:2311.18206") },
      { hasHeading: true, hasSourceIdentifier: true },
    );

    await page.goto(`/tasks/${TASK_ID}`, { waitUntil: "commit" });
    await expect(page.getByTestId("result-source-tag")).toHaveText("回放结果");
    await expect(page.getByTestId("replay-disclosure")).toBeVisible();
    const detailArtifactCard = page.getByTestId(`task-detail-artifact-${ARTIFACT_ID}`);
    await expect(detailArtifactCard).toContainText(ARTIFACT_TITLE);
    await screenshot(page, testInfo, "01-detail-literature-artifact-entry");

    await detailArtifactCard.click();
    await expect(page).toHaveURL(artifactWorkbenchUrlPattern());
    const mainCard = page.getByTestId("artifact-viewer-canvas");
    await expect(page.getByTestId("result-source-tag")).toHaveText("回放结果");
    await expect(page.getByTestId("workbench-no-execution-disclosure")).toBeVisible();
    await expect(mainCard.getByTestId(`artifact-markdown-${ARTIFACT_ID}`).getByRole("heading", { name: ARTIFACT_BODY_HEADING, exact: true })).toBeVisible();
    gates.check("DG27", true, "Task detail artifact card opens the exact replay artifact and renders its hydrated Markdown body");
    await screenshot(page, testInfo, "02-workbench-literature-artifact-hydrated");

    await page.reload({ waitUntil: "commit" });
    await expect(page).toHaveURL(artifactWorkbenchUrlPattern());
    await expect(mainCard.getByTestId(`artifact-markdown-${ARTIFACT_ID}`).getByRole("heading", { name: ARTIFACT_BODY_HEADING, exact: true })).toBeVisible();
    gates.check("DG28", true, "Refresh preserves the artifact query anchor and restores the same rendered replay artifact");
    await screenshot(page, testInfo, "03-workbench-literature-artifact-after-refresh");

    await page.goto(`/tasks/${TASK_ID}`, { waitUntil: "commit" });
    const capabilityNode = page.getByTestId(`capability-call-${CAPABILITY_CALL_ID}`);
    await expect(capabilityNode).toHaveCount(1);
    await expect(capabilityNode).toContainText(CAPABILITY_LABEL);
    await capabilityNode.click();
    const capabilityArtifactLink = page.getByTestId(`capability-artifact-${CAPABILITY_CALL_ID}-${ARTIFACT_ID}`);
    await expect(capabilityArtifactLink).toBeVisible();
    await screenshot(page, testInfo, "04-detail-capability-call-linked-artifact");
    await capabilityArtifactLink.click();
    await expect(page).toHaveURL(artifactWorkbenchUrlPattern());
    await expect(mainCard.getByTestId(`artifact-markdown-${ARTIFACT_ID}`).getByRole("heading", { name: ARTIFACT_BODY_HEADING, exact: true })).toBeVisible();
    gates.check("DG29", true, "Capability-call timeline exposes the linked artifact and opens the same replay artifact");

    await expectNoVisibleSecretLeak(page);
    gates.check("DG30", evidence.pageErrors.length === 0, "P2.5a replay artifact trace flow emits no uncaught page error", evidence.pageErrors, []);
  } finally {
    await gates.attach(testInfo);
    await evidence.flush();
    await disposeDevSession(session);
  }
});
