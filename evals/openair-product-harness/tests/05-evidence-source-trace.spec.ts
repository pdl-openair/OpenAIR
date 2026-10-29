import { expect, test, type Page } from "../lib/test";
import { API_BASE_URL } from "../lib/config";
import { expectClipboardContains, expectClipboardEquals } from "../lib/clipboard";
import { collectPageEvidence, screenshot, snapshotApiResponse } from "../lib/evidence";
import { evidenceRefTestId } from "../lib/evidenceTestIds";
import { expectNoVisibleSecretLeak, GateRecorder, readTasks } from "../lib/gates";

const TASK_ID = "task_cs_ope_001";
const ARTIFACT_ID = "art-001";
const ARTIFACT_TITLE = "OPE 文献调研综述";
const EVIDENCE_REF_ID = "evidence-scope-rl-ope-protocols";
const SOURCE_URL = "https://arxiv.org/abs/2311.18206";
const SOURCE_IDENTIFIER = "arXiv:2311.18206";
const SOURCE_EXCERPT = "SCOPE-RL put particular emphasis on its OPE modules, offering a range of OPE estimators and robust evaluation-of-OPE protocols.";

function artifactWorkbenchUrlPattern(): RegExp {
  return new RegExp(`/workbench/${TASK_ID}\\?artifact=${ARTIFACT_ID}$`);
}

async function openEvidenceWorkbench(page: Page) {
  await page.goto(`/tasks/${TASK_ID}`, { waitUntil: "commit" });
  const detailArtifactCard = page.getByTestId(`task-detail-artifact-${ARTIFACT_ID}`);
  await expect(detailArtifactCard).toContainText(ARTIFACT_TITLE, { timeout: 30_000 });
  await detailArtifactCard.click();
  await expect(page).toHaveURL(artifactWorkbenchUrlPattern());
  return ensureEvidencePanelVisible(page);
}

async function ensureEvidencePanelVisible(page: Page) {
  const panel = page.getByTestId("artifact-evidence-panel");
  const alreadyVisible = await panel.isVisible().catch(() => false);
  if (alreadyVisible) return panel;

  const artifactButtons = page.getByRole("button", { name: ARTIFACT_TITLE });
  if (await artifactButtons.count()) {
    await artifactButtons.first().click();
  }
  await expect(panel).toBeVisible({ timeout: 15_000 });
  return panel;
}

test("S03 / P2.5b: structured evidence traces a replay conclusion to an external source URL and original excerpt", async ({ page, request }, testInfo) => {
  const evidence = collectPageEvidence(page, testInfo);
  const gates = new GateRecorder();
  try {
    const tasks = await readTasks(request, API_BASE_URL);
    const seeded = tasks.find((task) => task.taskId === TASK_ID);
    expect(seeded, `Replay task ${TASK_ID} should exist`).toBeTruthy();
    if (!seeded) throw new Error(`Replay task ${TASK_ID} missing`);

    const artifact = (seeded.artifacts ?? []).find((item: any) => item.id === ARTIFACT_ID);
    const evidenceRef = artifact?.evidenceRefs?.[0];
    gates.check(
      "DG31",
      Boolean(evidenceRef) && evidenceRef.sourceUrl === SOURCE_URL && evidenceRef.excerpt === SOURCE_EXCERPT,
      "Replay task artifact exposes one structured evidence reference with official source URL and original excerpt",
      evidenceRef,
      { sourceUrl: SOURCE_URL, excerpt: SOURCE_EXCERPT },
    );

    const contentResponse = await request.get(`${API_BASE_URL}/api/tasks/${TASK_ID}/artifacts/${ARTIFACT_ID}/content`);
    const { bodyText: contentBody } = await snapshotApiResponse(testInfo, "p25b-literature-artifact-content-with-evidence", contentResponse);
    expect(contentResponse.ok(), "Replay artifact content endpoint should succeed").toBeTruthy();
    const content = JSON.parse(contentBody);
    gates.check(
      "DG32",
      content.evidenceRefs?.[0]?.sourceIdentifier === SOURCE_IDENTIFIER && content.evidenceRefs?.[0]?.sourceUrl === SOURCE_URL,
      "Artifact content endpoint preserves the structured evidence reference",
      content.evidenceRefs?.[0],
      { sourceIdentifier: SOURCE_IDENTIFIER, sourceUrl: SOURCE_URL },
    );

    const panel = await openEvidenceWorkbench(page);
    await expect(panel.getByText(SOURCE_IDENTIFIER, { exact: true })).toBeVisible();
    await expect(panel.getByText(SOURCE_EXCERPT, { exact: true })).toBeVisible();
    const sourceLink = panel.getByTestId(evidenceRefTestId(EVIDENCE_REF_ID, "source-link"));
    await expect(sourceLink).toHaveAttribute("href", SOURCE_URL);
    await expect(sourceLink).toHaveAttribute("target", "_blank");
    gates.check("DG33", true, "Workbench renders structured evidence, source identifier, original excerpt, and external URL");
    await screenshot(page, testInfo, "01-workbench-structured-evidence-source-trace");

    await page.reload({ waitUntil: "load" });
    await expect(page).toHaveURL(artifactWorkbenchUrlPattern());
    await ensureEvidencePanelVisible(page);
    await expect(page.getByTestId("artifact-evidence-panel").getByText(SOURCE_EXCERPT, { exact: true })).toBeVisible();
    gates.check("DG34", true, "Refresh restores the same structured evidence and source trace for the selected artifact");

    await expectNoVisibleSecretLeak(page);
    gates.check("DG35", evidence.pageErrors.length === 0, "P2.5b evidence/source trace flow emits no uncaught page error", evidence.pageErrors, []);
  } finally {
    await gates.attach(testInfo);
    await evidence.flush();
  }
});

test("S04 / P2.6b DG37: source recovery affordance is always available when external URL may fail", async ({ page, context, request }, testInfo) => {
  const evidence = collectPageEvidence(page, testInfo);
  const gates = new GateRecorder();
  try {
    const tasks = await readTasks(request, API_BASE_URL);
    const seeded = tasks.find((task) => task.taskId === TASK_ID);
    expect(seeded, `Replay task ${TASK_ID} should exist`).toBeTruthy();
    if (!seeded) throw new Error(`Replay task ${TASK_ID} missing`);

    const panel = await openEvidenceWorkbench(page);
    const evidenceItem = panel.getByTestId(evidenceRefTestId(EVIDENCE_REF_ID, "item"));
    await expect(evidenceItem.getByText(SOURCE_IDENTIFIER, { exact: true })).toBeVisible();
    await expect(evidenceItem.getByText(SOURCE_EXCERPT, { exact: true })).toBeVisible();

    const sourceLink = evidenceItem.getByTestId(evidenceRefTestId(EVIDENCE_REF_ID, "source-link"));
    await expect(sourceLink).toBeVisible();
    await expect(sourceLink).toHaveAttribute("href", SOURCE_URL);
    await expect(sourceLink).toHaveAttribute("target", "_blank");
    await expect(sourceLink).toHaveAttribute("rel", /noopener/);
    await expect(sourceLink).toHaveAttribute("rel", /noreferrer/);
    await expect(evidenceItem.getByTestId(evidenceRefTestId(EVIDENCE_REF_ID, "source-boundary-hint"))).toContainText("不等于已保存证据片段失效");
    const copyLink = evidenceItem.getByTestId(evidenceRefTestId(EVIDENCE_REF_ID, "copy-link"));
    const copySource = evidenceItem.getByTestId(evidenceRefTestId(EVIDENCE_REF_ID, "copy-source"));
    await expect(copyLink).toBeVisible();
    await expect(copySource).toBeVisible();
    await expect(page.getByTestId("artifact-unavailable-recovery")).toHaveCount(0);
    await expect(page.getByTestId("result-source-tag")).toHaveText("回放结果");
    await expect(page.getByText("科研产物暂时无法打开")).toHaveCount(0);

    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await copyLink.click();
    await expect(evidenceItem.getByTestId(evidenceRefTestId(EVIDENCE_REF_ID, "copy-feedback"))).toContainText("链接已复制");
    await expectClipboardEquals(page, SOURCE_URL);

    await copySource.click();
    await expect(evidenceItem.getByTestId(evidenceRefTestId(EVIDENCE_REF_ID, "copy-feedback"))).toContainText("来源信息已复制");
    await expectClipboardContains(page, SOURCE_IDENTIFIER);
    await expectClipboardContains(page, SOURCE_EXCERPT);
    await expectClipboardContains(page, SOURCE_URL);

    gates.check(
      "DG37",
      true,
      "Evidence panel keeps safe external link, recovery actions, boundary hint, and working copy affordances without probe-based failure claims",
      {
        hasSafeExternalLink: await sourceLink.isVisible(),
        hasBoundaryHint: await evidenceItem.getByTestId(evidenceRefTestId(EVIDENCE_REF_ID, "source-boundary-hint")).isVisible(),
        hasClaim: await evidenceItem.getByText(SOURCE_EXCERPT, { exact: true }).isVisible(),
        hasSourceIdentifier: await evidenceItem.getByText(SOURCE_IDENTIFIER, { exact: true }).isVisible(),
        hasRecoveryAction: await copyLink.isVisible(),
        hasGlobalArtifactUnavailable: await page.getByTestId("artifact-unavailable-recovery").count(),
      },
      {
        hasSafeExternalLink: true,
        hasBoundaryHint: true,
        hasClaim: true,
        hasSourceIdentifier: true,
        hasRecoveryAction: true,
        hasGlobalArtifactUnavailable: 0,
      },
    );
    await screenshot(page, testInfo, "02-workbench-source-recovery-affordance");
    await expectNoVisibleSecretLeak(page);
    gates.check("DG38", evidence.pageErrors.length === 0, "P2.6b source recovery affordance emits no uncaught page error", evidence.pageErrors, []);
  } finally {
    await gates.attach(testInfo);
    await evidence.flush();
  }
});

test("S05 / P2.6b DG39: invalid external source URL still keeps evidence and copy recovery path", async ({ page, context, request }, testInfo) => {
  const evidence = collectPageEvidence(page, testInfo);
  const gates = new GateRecorder();
  const contentUrl = `${API_BASE_URL}/api/tasks/${TASK_ID}/artifacts/${ARTIFACT_ID}/content`;
  try {
    const tasks = await readTasks(request, API_BASE_URL);
    const seeded = tasks.find((task) => task.taskId === TASK_ID);
    expect(seeded, `Replay task ${TASK_ID} should exist`).toBeTruthy();
    if (!seeded) throw new Error(`Replay task ${TASK_ID} missing`);

    await page.route(contentUrl, async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      const nextEvidenceRefs = Array.isArray(body.evidenceRefs)
        ? body.evidenceRefs.map((ref: any, index: number) => (
          index === 0 ? { ...ref, sourceUrl: "javascript:void(0)" } : ref
        ))
        : body.evidenceRefs;
      await route.fulfill({
        status: response.status(),
        headers: response.headers(),
        contentType: "application/json",
        body: JSON.stringify({ ...body, evidenceRefs: nextEvidenceRefs }),
      });
    });

    const panel = await openEvidenceWorkbench(page);
    const evidenceItem = panel.getByTestId(evidenceRefTestId(EVIDENCE_REF_ID, "item"));
    await expect(evidenceItem.getByTestId(evidenceRefTestId(EVIDENCE_REF_ID, "source-unavailable"))).toBeVisible();
    await expect(evidenceItem.getByTestId(evidenceRefTestId(EVIDENCE_REF_ID, "source-link"))).toHaveCount(0);
    await expect(evidenceItem.getByTestId(evidenceRefTestId(EVIDENCE_REF_ID, "source-boundary-hint"))).toContainText("链接格式无效");
    await expect(evidenceItem.getByText(SOURCE_EXCERPT, { exact: true })).toBeVisible();

    const copySource = evidenceItem.getByTestId(evidenceRefTestId(EVIDENCE_REF_ID, "copy-source"));
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await copySource.click();
    await expect(evidenceItem.getByTestId(evidenceRefTestId(EVIDENCE_REF_ID, "copy-feedback"))).toContainText("来源信息已复制");
    await expectClipboardContains(page, SOURCE_IDENTIFIER);
    await expectClipboardContains(page, "来源链接：不可用");

    gates.check("DG39", true, "Invalid external source URL keeps saved evidence and copy recovery without global artifact failure");
    await screenshot(page, testInfo, "03-workbench-invalid-source-url-recovery");
    await expectNoVisibleSecretLeak(page);
    gates.check("DG40", evidence.pageErrors.length === 0, "P2.6b invalid source URL branch emits no uncaught page error", evidence.pageErrors, []);
  } finally {
    await page.unroute(contentUrl);
    await gates.attach(testInfo);
    await evidence.flush();
  }
});
