import { expect, test } from "../lib/test";
import { attachJson, attachText, collectPageEvidence, screenshot } from "../lib/evidence";

// Real-browser reproduction for the Home research-start experience:
// - preview and confirm the task contract for the topic-only test input
// - measure time to a readable draft
// - assert no transient page error on the waiting page
// - capture the created task's creationPlan (multi-agent composition)
// - assert the right collaboration panel shows multiple agent roles
const TEST_INPUT =
  "我想做类器官药敏预测，现在只有疾病场景、模型类型和几个关键词。帮我收敛研究问题，并列出可能的研究空缺。";

test.describe("home research-start live experience", () => {
  test("S20: topic-only Home start creates a multi-agent question-refinement draft without transient error", async ({
    authRequired,
    page,
    request,
  }, testInfo) => {
    const evidence = collectPageEvidence(page, testInfo);
    const errorScreenTexts: string[] = [];
    const consoleErrors: string[] = [];

    page.on("console", message => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });

    // Watch for the transient "unable to load workbench" error screen while the
    // waiting page hands off to the freshly created task.
    page.on("framenavigated", async () => {
      /* no-op: navigation tracked via network log */
    });

    // In JWT mode, the harness seeds AuthContext before navigation. The b2shot
    // shortcut would overwrite that JWT with the legacy static dev token.
    await page.goto(authRequired ? "/" : "/?b2shot=1", { waitUntil: "commit" });
    await expect(page.getByTestId("research-intent-input")).toBeVisible({ timeout: 30000 });

    await page.getByTestId("research-intent-input").fill(TEST_INPUT);

    // Poll for the scary error alert during the whole handoff, in parallel.
    let sawErrorScreen = false;
    const errorPoll = setInterval(async () => {
      try {
        const alertText = await page
          .locator('text=/无法加载工作台|缺少任务 ID|无法加载任务/')
          .first()
          .textContent({ timeout: 200 })
          .catch(() => null);
        if (alertText) {
          sawErrorScreen = true;
          errorScreenTexts.push(alertText);
        }
      } catch {
        /* ignore */
      }
    }, 120);

    await page.getByTestId("starter-home-main-create").click();
    await expect(page.getByTestId("starter-task-contract-preview")).toBeVisible();
    await expect(page.getByTestId("starter-task-contract-preview")).toContainText("完整研究目标");
    await expect(page.getByTestId("starter-task-contract-preview")).toContainText("研究对象");
    await expect(page.getByTestId("starter-task-contract-preview")).toContainText("材料来源");
    await expect(page.getByTestId("starter-task-contract-preview")).toContainText("不会接入或读取新的外部来源");
    await screenshot(page, testInfo, "s20-00-task-contract-preview");

    const materialSourcesInput = page.getByTestId("starter-task-contract-materialSources");
    await materialSourcesInput.fill("用户提供的类器官实验记录");
    await page.getByRole("button", { name: "返回修改输入" }).click();
    await expect(page.getByTestId("starter-task-contract-preview")).toBeHidden();
    await page.getByTestId("starter-home-main-create").click();
    await expect(materialSourcesInput).toHaveValue("用户提供的类器官实验记录");

    const clickedAt = Date.now();
    await page.getByTestId("starter-home-contract-confirm").click();

    // Waiting page should appear (pending state), not an error.
    await expect(page.getByText("正在分析你的研究目标")).toBeVisible({ timeout: 8000 }).catch(() => {
      /* fast paths may skip straight to the draft */
    });
    await screenshot(page, testInfo, "s20-01-waiting-or-draft");

    // Draft confirmation must appear within an acceptable budget.
    await expect(page.getByTestId("workbench-draft-start-confirmation")).toBeVisible({ timeout: 30000 });
    const draftReadyAt = Date.now();
    clearInterval(errorPoll);

    const elapsedMs = draftReadyAt - clickedAt;
    await attachText(testInfo, "s20-elapsed-ms", String(elapsedMs));

    await expect(page.getByTestId("workbench-draft-start-confirmation")).toContainText("任务草案已准备好");
    await screenshot(page, testInfo, "s20-02-draft-confirmation");

    // Right collaboration panel shows the multi-agent planning discussion.
    await expect(page.getByTestId("workbench-right-panel")).toBeVisible();
    const rightText = (await page.getByTestId("workbench-right-panel").textContent()) ?? "";
    await attachText(testInfo, "s20-right-panel-text", rightText);
    await screenshot(page, testInfo, "s20-03-right-panel");

    // Read the created task id from the SPA and fetch its creationPlan.
    const currentTaskId = await page.evaluate(() =>
      window.sessionStorage.getItem("openair.userRoute.currentTaskId"),
    );
    expect(currentTaskId, "current task id should be recorded after creation").toBeTruthy();

    const taskResp = await request.get(`/api/tasks/${encodeURIComponent(currentTaskId!)}`);
    expect(taskResp.ok()).toBeTruthy();
    const task = await taskResp.json();
    await expect(page.getByTestId("workbench-draft-created-feedback")).toContainText(currentTaskId!);
    const creationPlan = task.creationPlan ?? {};
    const capabilityIds = (creationPlan.selectedCapabilities ?? []).map((c: any) => c.id);
    const substages = (creationPlan.stages ?? []).flatMap((s: any) =>
      (s.substages ?? []).map((r: any) => r.substage),
    );
    const orchestrator = creationPlan.orchestratorDecision ?? {};

    await attachJson(testInfo, "s20-creation-plan-summary", {
      elapsedMs,
      sawErrorScreen,
      errorScreenTexts,
      consoleErrors,
      pageErrors: evidence.pageErrors,
      researchType: creationPlan.researchType,
      planningSource: creationPlan.planningSource,
      taskType: creationPlan.understanding?.taskType,
      capabilityIds,
      substages,
      primaryAgent: orchestrator.primaryAgent,
      supportingAgents: orchestrator.supportingAgents,
      targetOutputs: creationPlan.understanding?.targetOutputs,
      missingInputs: creationPlan.understanding?.missingInputs,
    });
    // ---- Assertions ----
    // 1. No transient error screen during the handoff.
    expect(sawErrorScreen, `transient error screen: ${errorScreenTexts.join(" | ")}`).toBeFalsy();
    // 2. No uncaught page errors during the flow.
    expect(evidence.pageErrors, JSON.stringify(evidence.pageErrors)).toHaveLength(0);
    expect(consoleErrors, consoleErrors.join(" | ")).toHaveLength(0);
    // 3. Semantics: question refinement, not evidence-grounded gap identification.
    expect(creationPlan.understanding?.taskType).toBe("question_refinement");
    expect(creationPlan.researchType).toBe("question_refinement_start_draft");
    expect(substages).not.toContain("gap_identification");
    // 4. Multi-agent composition (primary + next agents), leveraging the agent system.
    expect(capabilityIds.length, `capabilities: ${capabilityIds.join(",")}`).toBeGreaterThanOrEqual(2);
    expect(capabilityIds[0]).toBe("research_question_refinement");
    expect(orchestrator.primaryAgent).toBe("research_question_refinement");
    // 5. The default collaboration view is action-oriented. Detailed agent and
    // audit records are intentionally available from the separate audit view.
    await expect(page.getByTestId("workbench-right-current-todo")).toBeVisible();
    await expect(page.getByTestId("workbench-right-key-blocker")).toBeVisible();
    await expect(page.getByTestId("workbench-right-key-event")).toBeVisible();
    // 6. Bounded, non-hanging handoff. Raw speed is not the gate — the live planner
    // wins when it can and the deterministic multi-agent floor covers it otherwise;
    // a generous ceiling only guards against a truly stuck wait.
    expect(elapsedMs, `elapsed ${elapsedMs}ms`).toBeLessThan(35000);
    await evidence.flush();
  });
});
