import { expect, test } from "@playwright/test";
import { disposeDevSession, prepareDevHarnessAuth } from "../lib/auth";
import { API_BASE_URL } from "../lib/config";
import { attachJson, collectPageEvidence, screenshot, snapshotTask } from "../lib/evidence";
import { GateRecorder } from "../lib/gates";

test.describe("research-start ResearchDesign confirmation", () => {
  test("S11: draft opens with a visible ResearchDesign and explicit revision and confirmation actions", async ({ page, playwright }, testInfo) => {
    const evidence = collectPageEvidence(page, testInfo);
    const gates = new GateRecorder();
    const session = await prepareDevHarnessAuth(playwright, page);
    let taskId: string | null = null;

    try {
      const draftResponse = await session.request.post(`${API_BASE_URL}/api/tasks/draft`, {
        data: { goalSummary: `ResearchDesign confirmation smoke ${Date.now()}` },
        timeout: 125_000,
      });
      expect(draftResponse.ok(), "Draft task creation should succeed").toBeTruthy();
      const draft = await draftResponse.json();
      taskId = draft.taskId;
      await attachJson(testInfo, "created-research-design-draft", { taskId, status: draft.status, creationStatus: draft.creationStatus });
      gates.check("DG30", draft.status === "draft", "draft is created before execution", draft.status, "draft");
      gates.check("DG31", draft.creationStatus === "awaiting_confirmation", "draft requires confirmation", draft.creationStatus, "awaiting_confirmation");

      await page.goto(`/workbench/${taskId}`, { waitUntil: "commit" });
      await expect(page.getByTestId("workbench-center")).toBeVisible();
      const design = page.getByTestId("workbench-draft-start-confirmation");
      await expect(design).toBeVisible();
      await expect(design).toContainText("研究方案");
      const edit = page.getByTestId("workbench-stream-edit-plan");
      await expect(edit).toBeVisible();
      await expect(edit).toBeEnabled();
      const confirmStart = page.getByTestId("workbench-stream-confirm-start");
      await expect(confirmStart).toBeVisible();
      await expect(confirmStart).toBeEnabled();

      const persisted = await snapshotTask(session.request, testInfo, API_BASE_URL, taskId, "draft-after-workbench-open");
      gates.check("DG33", persisted.status === "draft", "opening does not start execution", persisted.status, "draft");
      gates.check("DG34", persisted.creationStatus === "awaiting_confirmation", "confirmation gate remains", persisted.creationStatus, "awaiting_confirmation");
      gates.check("DG35", evidence.pageErrors.length === 0, "opening emits no page error", evidence.pageErrors, []);
      await screenshot(page, testInfo, "research-design-confirmation");
    } finally {
      await gates.attach(testInfo);
      await evidence.flush();
      if (taskId) await session.request.delete(`${API_BASE_URL}/api/tasks/${taskId}`);
      await disposeDevSession(session);
    }
  });

  test("S11: the current plan revision editor is available before confirmation without starting research", async ({ page, playwright }, testInfo) => {
    const session = await prepareDevHarnessAuth(playwright, page);
    let taskId: string | null = null;
    try {
      const created = await session.request.post(`${API_BASE_URL}/api/tasks/draft`, {
        data: { goalSummary: `plan revision availability ${Date.now()}` },
        timeout: 125_000,
      });
      expect(created.ok()).toBeTruthy();
      const draft = await created.json();
      taskId = draft.taskId;

      await page.goto(`/workbench/${taskId}`, { waitUntil: "commit" });
      await page.getByTestId("workbench-stream-edit-plan").click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await expect(page.getByRole("dialog").getByText("编辑研究草案")).toBeVisible();
      const persisted = await snapshotTask(session.request, testInfo, API_BASE_URL, taskId, "research-design-edit-opened");
      expect(persisted.status).toBe("draft");
      expect(persisted.creationStatus).toBe("awaiting_confirmation");
    } finally {
      if (taskId) await session.request.delete(`${API_BASE_URL}/api/tasks/${taskId}`);
      await disposeDevSession(session);
    }
  });
});
