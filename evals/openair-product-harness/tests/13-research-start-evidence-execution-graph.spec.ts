import fs from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";
import { disposeDevSession, prepareDevHarnessAuth } from "../lib/auth";
import { API_BASE_URL, RUN_DIR } from "../lib/config";

const FULL_SDR_EXTRA_SUBSTAGES = [
  "constraints_criteria",
  "hypothesis_formation",
  "topic_briefing",
  "ablation_design",
  "full_execution",
  "report_generation",
];

function flattenTaskSubstages(task: any): string[] {
  return (task.stageDetails ?? []).flatMap((stage: any) =>
    (stage.subStages ?? []).map((sub: any) => String(sub.subStage)),
  );
}

test.describe("research-start evidence execution graph", () => {
  test("S13: evidence organizing draft uses a task-specific execution graph", async ({ page, playwright }) => {
    const session = await prepareDevHarnessAuth(playwright, page);
    const outDir = RUN_DIR;
    fs.mkdirSync(outDir, { recursive: true });
    const submitResponses: Array<{ url: string; status: number; body: any }> = [];

    page.on("response", async (response) => {
      if (!response.url().includes("/api/research-start/submit")) return;
      let body: any = null;
      try {
        body = await response.json();
      } catch {
        body = await response.text().catch(() => null);
      }
      submitResponses.push({ url: response.url(), status: response.status(), body });
    });

    try {
      await page.goto("/", { waitUntil: "networkidle" });
      await page.screenshot({ path: path.join(outDir, "01-home.png"), fullPage: true });

      const evidenceCard = page.getByRole("button", { name: /证据整理|整证据|梳理论文证据/ }).first();
      await expect(evidenceCard).toBeVisible();
      await evidenceCard.click();
      const input = page.getByTestId("research-intent-input");
      await expect(input).toBeFocused();
      await expect(input).not.toHaveValue("");
      await page.screenshot({ path: path.join(outDir, "02-card-click-prefills-input.png"), fullPage: true });

      const richInput = [
        "我要启动一个证据整理研究任务。",
        "材料A：8 篇 SEI 论文摘录和两段实验记录，几篇都声称界面稳定性改善，但表征手段和测试条件不一致。",
        "请先整理成证据表，区分直接证据、作者推断、冲突点和仍缺材料的地方，确认前不要生成最终科研结论。",
      ].join("\n");
      await input.fill(richInput);
      await page.screenshot({ path: path.join(outDir, "03-rich-input-before-start.png"), fullPage: true });

      await page.getByTestId("starter-home-main-create").click();
      await expect(page).toHaveURL(/\/workbench\/current/);
      await page.screenshot({ path: path.join(outDir, "04-workbench-waiting-state.png"), fullPage: true });

      await expect(page.getByTestId("workbench-draft-start-confirmation")).toBeVisible({ timeout: 30_000 });
      await page.screenshot({ path: path.join(outDir, "05-workbench-final-state.png"), fullPage: true });

      const finalSubmit = submitResponses.at(-1)?.body ?? {};
      const taskId = finalSubmit.taskId;
      expect(taskId, "research-start submit should return a taskId").toBeTruthy();

      const taskResponse = await session.request.get(`${API_BASE_URL}/api/tasks/${encodeURIComponent(taskId)}`);
      expect(taskResponse.ok()).toBeTruthy();
      const task = await taskResponse.json();
      const graphSubstages = flattenTaskSubstages(task);
      const visibleNavText = await page.getByTestId("workbench-left-panel").innerText();
      const hasFullSdrExtraSubstages = FULL_SDR_EXTRA_SUBSTAGES.some((key) => graphSubstages.includes(key));
      const hasFailure = submitResponses.some((item) => item.status >= 400) || /error|failed|失败|错误/i.test(await page.locator("body").innerText());
      const summary = {
        url: page.url(),
        taskId,
        creationPlanResearchType: task.creationPlan?.researchType ?? null,
        planningSource: task.creationPlan?.planningSource ?? null,
        understanding: task.creationPlan?.understanding ?? null,
        visibleNavText,
        visibleStages: (task.stageDetails ?? []).map((stage: any) => ({
          stageKey: stage.stageKey,
          substages: (stage.subStages ?? []).map((sub: any) => sub.subStage),
        })),
        hasDraft: task.status === "draft" && task.creationStatus === "awaiting_confirmation",
        hasFailure,
        hasFullSdrExtraSubstages,
        submitResponses,
      };
      fs.writeFileSync(path.join(outDir, "summary.json"), JSON.stringify(summary, null, 2), "utf8");

      expect(summary.hasDraft).toBe(true);
      expect(summary.hasFailure).toBe(false);
      expect(summary.creationPlanResearchType).toBe("evidence_organizing_start_draft");
      const plannerMode = (process.env.OPENAIR_RESEARCH_START_PLANNER_MODE ?? "").toLowerCase();
      const expectedPlanningSource = ["live", "required"].includes(plannerMode) ? "llm" : "deterministic_fallback";
      expect(summary.planningSource).toBe(expectedPlanningSource);
      expect(summary.understanding?.taskType).toBe("evidence_organizing");
      expect(summary.understanding?.userIntent).not.toBe(task.creationPlan?.goalSummary);
      expect(graphSubstages).toContain("evidence_extraction");
      expect(graphSubstages).not.toContain("literature_search");
      expect(graphSubstages.length).toBeLessThan(10);
      expect(summary.hasFullSdrExtraSubstages).toBe(false);
      expect(visibleNavText).not.toContain("EGFR");
    } finally {
      await disposeDevSession(session);
    }
  });
});
