import { expect, test } from "../lib/test";
import { API_BASE_URL } from "../lib/config";
import { attachJson, collectPageEvidence, snapshotTask } from "../lib/evidence";

test("S25 / G04: an explicitly selected Skill records a complete HUIYAN Live audit trail", async ({ page, request }, testInfo) => {
  const evidence = collectPageEvidence(page, testInfo);
  let taskId: string | null = null;
  try {
    const created = await request.post(`${API_BASE_URL}/api/tasks`, {
      data: {
        title: `Bounded live audit ${Date.now()}`,
        goalSummary: "Prepare a confirmation-only research note from the supplied scope. Do not retrieve sources, run experiments, or claim results.",
        domain: "computer_science",
        currentStage: "problem_modeling",
        status: "running",
      },
    });
    expect(created.ok()).toBeTruthy();
    taskId = (await created.json()).taskId as string;

    const response = await request.post(`${API_BASE_URL}/api/tasks/${taskId}/agent/chat`, {
      data: {
        message: "Restate the boundary as three concise confirmation bullets. Do not retrieve, execute, or make scientific claims.",
        skillId: "intent_understanding",
        substageKey: "intent_understanding",
      },
      timeout: 120_000,
    });
    expect(response.ok(), await response.text()).toBeTruthy();
    const result = await response.json();
    expect(result.runtime).toBe("huiyan_chat_completions");
    expect(result.reply).not.toContain("[回放模式]");
    expect(result.error).toBeNull();

    const task = await snapshotTask(request, testInfo, API_BASE_URL, taskId, "s25-live-task");
    const call = (task.capabilityCalls ?? []).find((item: { id?: string }) => item.id === result.capabilityCallId);
    expect(call).toBeTruthy();
    expect(call.runtime).toBe("huiyan_chat_completions");
    expect(call.status).toBe("completed");
    expect(call.runMode).toBe("live");
    expect(call.sourceMode).toBe("live");
    expect(call.targetAgent).toBeTruthy();
    expect(call.runId).toBeTruthy();
    expect(call.provider).toBe("huiyan_chat_completions");
    expect(call.model).toBeTruthy();
    expect(call.sourceMetadata?.providerRunId).toBeTruthy();
    await attachJson(testInfo, "s25-live-audit", { taskId, runtime: result.runtime, capabilityCall: call });
    await page.goto(`/workbench/${taskId}`, { waitUntil: "commit" });
    await expect(page.getByTestId("result-source-tag")).toHaveText(/实时|Live/);
    await page.getByRole("button", { name: /协作记录/ }).click();
    const collaboration = page.getByRole("region", { name: "协作记录" });
    await expect(collaboration).toContainText(/总控/);
    await expect(collaboration).not.toContainText(/AI 助手/);
    expect(evidence.pageErrors).toEqual([]);
  } finally {
    await evidence.flush();
    if (taskId) await request.delete(`${API_BASE_URL}/api/tasks/${taskId}`);
  }
});
