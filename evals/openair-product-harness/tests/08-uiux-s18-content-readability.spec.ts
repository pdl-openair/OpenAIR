import { expect, test } from "@playwright/test";
import { disposeDevSession, prepareDevHarnessAuth } from "../lib/auth";
import { API_BASE_URL, RUN_DIR } from "../lib/config";
import { attachJson, collectPageEvidence, screenshot } from "../lib/evidence";
import { pickSeededAdvancableTask, readTasks } from "../lib/gates";
import { S17_RUNNING_TASK_ID } from "../lib/uiuxScenario";
import {
  collectEmptyArtifactMetrics,
  collectPopulatedArtifactMetrics,
  collectSummaryCardMetrics,
  evaluateEmptyArtifactAudit,
  evaluateOverflowSummary,
  evaluatePopulatedArtifactAudit,
  evaluatePrimarySummaryReadabilityGate,
  prepareCurrentWorkbench,
  prepareEmptyArtifactView,
  preparePopulatedArtifactView,
  S18_SIDEBAR_STATES,
  S18_SUMMARY_CARD_IDS,
  S18_VIEWPORTS,
  S18GateReporter,
  saveContentScreenshot,
  verifyExpandCollapseInteraction,
  writeContentMatrixReport,
} from "../lib/uiuxContentMetrics";
import { setSidebarState } from "../lib/uiuxMetrics";

const RUNNING_TASK_ID = S17_RUNNING_TASK_ID;
const POPULATED_ARTIFACT_ID = "art-001";
const POPULATED_ARTIFACT_TITLE = "OPE 文献调研综述";

test.describe("S18 / UX15–UX16 UI content readability gates", () => {
  test.describe.configure({ timeout: 300_000 });

  test("UX-REG-S18-MATRIX: overflow summary, empty artifact, populated artifact capture", async ({ page, playwright }, testInfo) => {
    const evidence = collectPageEvidence(page, testInfo);
    const reporter = new S18GateReporter();
    const matrixRows: Record<string, unknown>[] = [];
    const overflowEvaluations = [];
    const emptyMetricsRows = [];
    const populatedMetricsRows = [];
    const session = await prepareDevHarnessAuth(playwright, page);

    try {
      const tasks = await readTasks(session.request, API_BASE_URL);
      const runningTask = pickSeededAdvancableTask(tasks);
      expect(runningTask, "Seeded replay/running task is required").toBeTruthy();
      if (!runningTask) throw new Error("Required seeded running task missing");
      expect(runningTask.taskId, "S18 uses stable replay fixture task").toBe(RUNNING_TASK_ID);

      const artifact = (runningTask.artifacts ?? []).find((item: any) => item.id === POPULATED_ARTIFACT_ID);
      const fixtureAvailable = Boolean(
        artifact
        && artifact.title === POPULATED_ARTIFACT_TITLE
        && Array.isArray(artifact.evidenceRefs)
        && artifact.evidenceRefs.length > 0,
      );
      await attachJson(testInfo, "s18-fixture-observation", {
        taskId: RUNNING_TASK_ID,
        populatedArtifactId: POPULATED_ARTIFACT_ID,
        fixtureAvailable,
        artifactTitle: artifact?.title ?? null,
        evidenceRefCount: artifact?.evidenceRefs?.length ?? 0,
      }, "gates");

      for (const viewport of S18_VIEWPORTS) {
        for (const sidebar of S18_SIDEBAR_STATES) {
          await prepareCurrentWorkbench(page, RUNNING_TASK_ID, viewport, sidebar, setSidebarState);
          const cards = await collectSummaryCardMetrics(page);
          const overflowEval = evaluateOverflowSummary(cards, viewport.label, sidebar);
          overflowEvaluations.push(overflowEval);
          const screenshotPath = await saveContentScreenshot(
            page,
            `overflow_summary_${sidebar}_${viewport.label}`,
          );
          matrixRows.push({
            scenario: "OVERFLOW_SUMMARY",
            viewport: viewport.label,
            sidebar,
            taskId: RUNNING_TASK_ID,
            ...overflowEval,
            screenshot: screenshotPath,
          });
        }
      }

      const overflowGate = evaluatePrimarySummaryReadabilityGate(overflowEvaluations);
      reporter.record(overflowGate);

      const expandCollapseVerifications = [];
      await prepareCurrentWorkbench(page, RUNNING_TASK_ID, S18_VIEWPORTS[0], "both", setSidebarState);
      for (const cardId of S18_SUMMARY_CARD_IDS) {
        const verification = await verifyExpandCollapseInteraction(page, cardId);
        if (verification) {
          expandCollapseVerifications.push(verification);
          expect.soft(verification.expandActionVisible, `${cardId} expand action visible`).toBe(true);
          expect.soft(verification.fullTextVisibleAfterExpand, `${cardId} full text after expand`).toBe(true);
          expect.soft(verification.collapseActionVisible, `${cardId} collapse action visible`).toBe(true);
          expect.soft(verification.collapsedStateAfterCollapse, `${cardId} collapsed after collapse`).toBe(true);
        }
      }
      await saveContentScreenshot(page, "overflow_summary_expand_collapse_1280x720_both");
      await attachJson(testInfo, "s18-expand-collapse-verifications", expandCollapseVerifications, "gates");

      await attachJson(testInfo, "s18-overflow-summary-matrix", overflowEvaluations, "gates");
      await screenshot(page, testInfo, "s18-overflow-summary-sample-1280-both");

      const emptyViewport = S18_VIEWPORTS[0];
      const emptySidebar = "both" as const;
      await prepareEmptyArtifactView(page, RUNNING_TASK_ID, emptyViewport, emptySidebar, setSidebarState);
      const emptyMetrics = await collectEmptyArtifactMetrics(page, {
        viewport: emptyViewport.label,
        sidebar: emptySidebar,
        taskId: RUNNING_TASK_ID,
        substageLabel: "结果分析",
      });
      emptyMetricsRows.push(emptyMetrics);
      const emptyScreenshot = await saveContentScreenshot(page, "empty_artifact_both_1280x720");
      matrixRows.push({ ...emptyMetrics, screenshot: emptyScreenshot });
      await screenshot(page, testInfo, "s18-empty-artifact-both-1280x720");

      const emptyAudit = evaluateEmptyArtifactAudit(emptyMetricsRows);
      await attachJson(testInfo, "s18-empty-artifact-audit", { emptyAudit, rows: emptyMetricsRows }, "gates");

      let populatedAudit: ReturnType<typeof evaluatePopulatedArtifactAudit>;
      if (!fixtureAvailable) {
        populatedAudit = { audit: "NOT_RUN", fixture: "GAP" };
        matrixRows.push({
          scenario: "POPULATED_ARTIFACT",
          fixtureAvailable: false,
          failure_type: "FIXTURE_GAP",
          note: "No stable populated artifact fixture with structured evidence",
        });
      } else {
        const populatedViewport = S18_VIEWPORTS[0];
        const populatedSidebar = "both" as const;
        await preparePopulatedArtifactView(
          page,
          RUNNING_TASK_ID,
          POPULATED_ARTIFACT_ID,
          populatedViewport,
          populatedSidebar,
          setSidebarState,
        );
        const populatedMetrics = await collectPopulatedArtifactMetrics(page, {
          viewport: populatedViewport.label,
          sidebar: populatedSidebar,
          taskId: RUNNING_TASK_ID,
          artifactId: POPULATED_ARTIFACT_ID,
          fixtureAvailable: true,
        });
        populatedMetricsRows.push(populatedMetrics);
        const populatedScreenshot = await saveContentScreenshot(page, "populated_artifact_both_1280x720");
        matrixRows.push({ ...populatedMetrics, screenshot: populatedScreenshot });
        await screenshot(page, testInfo, "s18-populated-artifact-both-1280x720");
        populatedAudit = evaluatePopulatedArtifactAudit(populatedMetricsRows, true);
        await attachJson(testInfo, "s18-populated-artifact-audit", { populatedAudit, rows: populatedMetricsRows }, "gates");
      }

      const gateReportFile = await reporter.writeReport({
        overflowSummary: {
          matrixRowCount: overflowEvaluations.length,
          overflowCardCount: overflowGate.actual && typeof overflowGate.actual === "object"
            ? (overflowGate.actual as { overflowCardCount?: number }).overflowCardCount ?? 0
            : 0,
          overflowWithoutExpandCount: overflowGate.actual && typeof overflowGate.actual === "object"
            ? (overflowGate.actual as { overflowWithoutExpandCount?: number }).overflowWithoutExpandCount ?? 0
            : 0,
        },
        emptyArtifactAudit: emptyAudit,
        populatedArtifactFixture: populatedAudit.fixture,
        populatedArtifactAudit: populatedAudit.audit,
        reviewClassification: {
          M: [
            "summary card textContent/clientWidth/scrollWidth/isOverflowing",
            "empty artifact container heights and blankSpaceRatio",
            "populated artifact body/evidence/source/scroll metrics",
          ],
          A: [
            "emptyLayoutBalance",
            "longTextReadability",
          ],
          H: [
            "emptyCopyClarity",
            "evidenceSourceTrace",
          ],
        },
        note: "S18 red/green baseline capture; empty blank-space threshold not frozen.",
      });

      const matrixFile = await writeContentMatrixReport(matrixRows, {
        scenarios: ["OVERFLOW_SUMMARY", "EMPTY_ARTIFACT", "POPULATED_ARTIFACT"],
        viewports: S18_VIEWPORTS.map((item) => item.label),
        sidebars: S18_SIDEBAR_STATES,
        populatedArtifactFixture: populatedAudit.fixture,
        expandCollapseVerifications,
      });

      await attachJson(testInfo, "uiux-s18-gates", reporter.list(), "gates");
      await attachJson(testInfo, "uiux-s18-matrix-summary", { rowCount: matrixRows.length, matrixFile }, "uiux-content-matrix");
      await attachJson(testInfo, "uiux-s18-gate-summary", reporter.summary(), "gates");

      expect(reporter.summary()["UX-GATE-PRIMARY-SUMMARY-READABILITY"]).toBeDefined();
      expect(matrixRows.length).toBeGreaterThan(0);
      expect(gateReportFile).toContain("uiux-s18-gates.json");
      expect(matrixFile).toContain("uiux-s18-matrix.json");
      expect(RUN_DIR.length).toBeGreaterThan(0);
    } finally {
      await evidence.flush();
      await disposeDevSession(session);
    }
  });
});
