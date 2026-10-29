import { expect, test } from "../lib/test";
import { API_BASE_URL } from "../lib/config";
import { attachJson, collectPageEvidence, snapshotTask } from "../lib/evidence";
import { expectNoVisibleSecretLeak } from "../lib/gates";

type Scenario = {
  id: string;
  input: string;
  expectedTaskType: string;
  artifactMarker: string;
};

const scenarios: Scenario[] = [
  { id: "computer-science-mapreduce-spark", expectedTaskType: "comparative_literature_analysis", artifactMarker: "MapReduce", input: "我在做分布式数据处理系统比较研究。基于 Dean 和 Ghemawat 2004 的 MapReduce 论文及 Zaharia 等 2012 的 RDD 论文，建立 MapReduce 与 Spark/RDD 在故障恢复、数据复用、调度语义和适用工作负载上的来源受限对比草案。当前不检索新来源、不运行 benchmark、不下性能结论。" },
  { id: "theoretical-materials-llzo-defect", expectedTaskType: "validation_design", artifactMarker: "LLZO", input: "规划立方石榴石 Li7La3Zr2O12（LLZO）中锂空位不同局域配位环境的 DFT 验证设计。已有 Murugan 等 2007 文献和 PBE/PBEsol、k 点、截断能、超胞、自旋及收敛阈值的候选设置。当前没有总能或 NEB 结果，不能把计算假设写成缺陷机制结论。" },
  { id: "experimental-materials-nmc811-coating", expectedTaskType: "validation_design", artifactMarker: "NMC811", input: "规划 Al2O3 表面涂层改善 LiNi0.8Mn0.1Co0.1O2（NMC811）4.3V 循环稳定性的实验验证。已有 NMC811-A/B 与未涂层对照、0.5/1.0/2.0 wt% 涂层、溶胶凝胶工艺和 XRD、SEM/EDS、XPS、半电池循环及 EIS 计划。以上均为计划，不是性能结果。" },
];

test("S32 / G01: three realistic domains execute an explicit HUIYAN Skill with durable audited artifacts", async ({ page, request }, testInfo) => {
  test.setTimeout(900_000);
  const evidence = collectPageEvidence(page, testInfo);
  const observations: Array<Record<string, unknown>> = [];
  try {
    for (const scenario of scenarios) {
      await page.goto("/", { waitUntil: "commit" });
      await page.getByTestId("research-intent-input").fill(scenario.input);
      await page.getByTestId("starter-home-main-create").click();
      await page.getByTestId("starter-home-contract-confirm").click();
      await expect(page.getByTestId("workbench-draft-start-confirmation")).toBeVisible({ timeout: 135_000 });
      const taskId = await page.evaluate(() => window.sessionStorage.getItem("openair.userRoute.currentTaskId"));
      expect(taskId, `${scenario.id} task id`).toBeTruthy();

      const confirmation = page.getByTestId("research-start-confirmation-panel");
      for (const checkbox of await confirmation.getByRole("checkbox").all()) await checkbox.check();
      await confirmation.getByRole("radio").nth(1).check();
      await page.getByTestId("workbench-draft-confirm-start").click();
      const start = page.waitForResponse(response => response.request().method() === "POST" && response.url().endsWith(`/api/tasks/${taskId}/start`) && response.status() === 200);
      await page.getByTestId("workbench-collaboration-input-zone").getByRole("button", { name: "开始任务" }).click();
      await start;

      const inputZone = page.getByTestId("workbench-collaboration-input-zone");
      await inputZone.getByRole("button", { name: "快速操作" }).click();
      const skillButton = page.getByRole("menuitem").filter({ hasText: /意图理解|问题理解/ }).first();
      await expect(skillButton).toBeVisible({ timeout: 20_000 });
      await skillButton.click();

      await expect.poll(async () => {
        const task = await snapshotTask(request, testInfo, API_BASE_URL, taskId!, `s32-${scenario.id}-poll`);
        return task.capabilityCalls ?? [];
      }, { timeout: 180_000 }).toEqual(expect.arrayContaining([
        expect.objectContaining({ runtime: "huiyan_chat_completions", status: "completed", runMode: "live", sourceMode: "live" }),
      ]));

      const task = await snapshotTask(request, testInfo, API_BASE_URL, taskId!, `s32-${scenario.id}-completed`);
      expect(task.creationPlan?.understanding?.taskType).toBe(scenario.expectedTaskType);
      const call = (task.capabilityCalls ?? []).find((item: any) => item.runtime === "huiyan_chat_completions" && item.status === "completed");
      expect(call?.sourceMetadata?.providerRunId).toBeTruthy();
      expect(call?.sourceMetadata?.inputMode).toBe("huiyan_controller");
      expect(call?.sourceMetadata?.controllerRunId).toBeTruthy();
      expect(call?.skillHash).toBeTruthy();
      const artifact = (task.artifacts ?? []).find((item: any) => item.sourceCapabilityCallId === call?.id);
      const artifactContent = artifact?.inlineContent ?? artifact?.summary ?? "";
      expect(artifactContent).toContain(scenario.artifactMarker);
      for (const otherScenario of scenarios.filter(item => item.id !== scenario.id)) {
        expect(artifactContent, `${scenario.id} artifact must not carry ${otherScenario.id} domain content`).not.toContain(otherScenario.artifactMarker);
      }
      expect(artifactContent).not.toMatch(/(?:trace|run|session|provider)[_-]?id\s*:/i);
      expect(artifactContent).not.toMatch(/(?:api[_-]?key|token|password)\s*:/i);
      await page.reload({ waitUntil: "commit" });
      await expectNoVisibleSecretLeak(page);
      const restored = await snapshotTask(request, testInfo, API_BASE_URL, taskId!, `s32-${scenario.id}-restored`);
      expect(restored.capabilityCalls ?? []).toEqual(expect.arrayContaining([expect.objectContaining({ id: call.id, status: "completed" })]));
      observations.push({ id: scenario.id, taskId, call, artifactId: artifact?.id });
    }
    await attachJson(testInfo, "s32-three-domain-live-skill-audit", observations);
    expect(evidence.pageErrors).toEqual([]);
  } finally {
    await evidence.flush();
  }
});
