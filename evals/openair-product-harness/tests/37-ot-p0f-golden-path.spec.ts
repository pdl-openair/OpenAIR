import { randomUUID } from "node:crypto";
import type { APIRequestContext, Page, TestInfo } from "@playwright/test";
import { expect, test } from "../lib/test";
import { API_BASE_URL } from "../lib/config";
import { attachJson, collectPageEvidence, snapshotTask } from "../lib/evidence";

const QUERY = "调研 2024 年以来 GraphRAG 在科研文献问答中的代表性方法与公开实现，比较它与混合检索 RAG 在答案准确性、证据覆盖、引用可靠性和检索成本上的差异，并设计一个可复现实验，给出数据集、基线、评价指标和消融方案。";
const TARGET_BLOCKERS = new Set([
  "canonical_skill_composition_unavailable",
  "runtime_unavailable",
  "dispatch_review_contract_mismatch",
  "dispatch_resolver_internal_error",
]);
const TARGET_SUBSTAGES = [
  "structured_requirement_generation",
  "constraint_interpretation_skill",
  "retrieval_strategy_design",
  "evidence_schema_design",
];
const SOURCE_STATES = new Set(["dispatched", "queued", "running", "reviewing", "completed"]);

type Task = Record<string, any>;

function substage(task: Task, key: string): Record<string, any> | undefined {
  return task.progressProjection?.substageProgress?.find(
    (row: Record<string, any>) => row.substageDefinitionId === key,
  );
}

function diagnostic(task: Task): Record<string, unknown> {
  return {
    revision: task.revision ?? null,
    taskStatus: task.status ?? null,
    currentSubstage: task.progressProjection?.substageProgress?.find(
      (row: Record<string, any>) => ["running", "ready", "needs_input", "failed"].includes(row.status),
    )?.substageDefinitionId ?? null,
    substageStates: (task.progressProjection?.substageProgress ?? []).map((row: Record<string, any>) => ({
      key: row.substageDefinitionId,
      status: row.status,
      reason: row.statusReasonCode ?? null,
      decisionId: row.activeExecutionDecisionId ?? null,
    })),
    workUnits: (task.workUnits ?? []).map((row: Record<string, any>) => ({
      workUnitId: row.workUnitId,
      decisionId: row.executionDecisionId,
      status: row.status,
      terminalReasonCode: row.terminalReasonCode ?? null,
    })),
  };
}

function firstBlocker(task: Task): string | null {
  const reasons = [
    ...(task.progressProjection?.substageProgress ?? []).map((row: Record<string, any>) => row.statusReasonCode),
    ...(task.workUnits ?? []).map((row: Record<string, any>) => row.terminalReasonCode),
  ];
  return reasons.find((value) => typeof value === "string" && value.trim()) ?? null;
}

function blockerCategory(blocker: string | null): "target" | "other" | null {
  if (!blocker) return null;
  return TARGET_BLOCKERS.has(blocker) ? "target" : "other";
}

async function readTask(request: APIRequestContext, taskId: string, testInfo: TestInfo, label: string): Promise<Task> {
  return snapshotTask(request, testInfo, API_BASE_URL, taskId, label) as Promise<Task>;
}

async function waitFor(
  request: APIRequestContext,
  taskId: string,
  testInfo: TestInfo,
  label: string,
  predicate: (task: Task) => boolean,
): Promise<Task> {
  let latest: Task | null = null;
  try {
    await expect.poll(async () => {
      latest = await readTask(request, taskId, testInfo, `${label}-poll`);
      const blocker = firstBlocker(latest);
      if (blocker) throw new Error(`first_blocker:${blockerCategory(blocker)}:${blocker}`);
      return predicate(latest);
    }, { timeout: 720_000, intervals: [500, 1_000, 2_000, 5_000] }).toBe(true);
  } catch (error) {
    if (latest) await attachJson(testInfo, `${label}-last-observed`, diagnostic(latest));
    throw error;
  }
  return latest!;
}

async function continueAtStageGate(page: Page, request: APIRequestContext, taskId: string, testInfo: TestInfo): Promise<void> {
  const task = await readTask(request, taskId, testInfo, "stage-gate-before-continue");
  const pending = (task.progressProjection?.substageProgress ?? []).find(
    (row: Record<string, any>) => row.status === "completed",
  );
  if (task.status !== "awaiting_confirmation" || !pending) return;
  await expect(page.getByTestId("workbench-continue-research")).toBeVisible({ timeout: 30_000 });
  await page.getByTestId("workbench-continue-research").click();
}

test("OT-P0F: fresh live task reaches evidence schema and starts source acquisition", async ({ page, request }, testInfo) => {
  test.setTimeout(900_000);
  const evidence = collectPageEvidence(page, testInfo);
  let taskId: string | null = null;

  try {
    await page.goto("/", { waitUntil: "commit" });
    await page.getByRole("button", { name: "科研线索与假说" }).click();
    await page.getByTestId("research-intent-input").fill(`${QUERY}\n\nGate run: ${randomUUID()}`);
    const submitRequest = page.waitForRequest(request => (
      request.method() === "POST" && request.url().includes("/api/research-start/submit")
    ));
    await page.getByTestId("starter-home-main-create").click();
    const submittedPayload = (await submitRequest).postDataJSON() as Record<string, unknown>;
    await attachJson(testInfo, "ot-p0f-research-start-request", submittedPayload);
    expect(submittedPayload.requestedWorkflow, "OT Golden Path must use the explicit production workflow contract")
      .toBe("opentrajectory_research_workflow");
    await expect(page.getByTestId("workbench-draft-start-confirmation")).toBeVisible({ timeout: 420_000 });

    taskId = await page.evaluate(() => window.sessionStorage.getItem("openair.userRoute.currentTaskId"));
    expect(taskId, "fresh task id must be created by the normal Home flow").toBeTruthy();
    const draft = await readTask(request, taskId!, testInfo, "fresh-draft");
    expect(draft.researchDesignVersions?.at(-1)?.status).toBe("draft");

    const confirmation = page.getByTestId("research-start-confirmation-panel");
    for (const checkbox of await confirmation.getByRole("checkbox").all()) await checkbox.check();
    const usableMaterialsDecision = confirmation.getByRole("radio").nth(1);
    if (await usableMaterialsDecision.isVisible()) await usableMaterialsDecision.check();
    const confirmStart = page.getByTestId("workbench-draft-confirm-start").or(
      page.getByRole("button", { name: "确认并开始" }),
    );
    await expect(confirmStart).toBeEnabled({ timeout: 30_000 });
    await confirmStart.click();
    await expect.poll(async () => (await readTask(request, taskId!, testInfo, "research-design-confirmation-poll"))
      .researchDesignVersions?.at(-1)?.status, { timeout: 60_000 }).toBe("confirmed");

    await waitFor(request, taskId!, testInfo, "initial-dispatch", (task) => (
      (task.workUnits ?? []).some((work: Record<string, any>) => SOURCE_STATES.has(work.status))
    ));

    for (const key of TARGET_SUBSTAGES) {
      const completed = await waitFor(request, taskId!, testInfo, `${key}-completion`, (task) => substage(task, key)?.status === "completed");
      const row = substage(completed, key)!;
      const workUnits = (completed.workUnits ?? []).filter((work: Record<string, any>) => row.workUnitIds?.includes(work.workUnitId));
      expect(workUnits.length, `${key} must have a durable work unit`).toBeGreaterThan(0);
      expect(workUnits.some((work: Record<string, any>) => Boolean(work.executionDecisionId)), `${key} must have an execution decision`).toBe(true);

      if (key === "evidence_schema_design") {
        const artifact = (completed.artifacts ?? []).find((item: Record<string, any>) => (
          row.artifactRefs?.includes(item.id) && item.artifactState === "usable"
        ));
        expect(artifact, "evidence schema must persist a usable canonical artifact").toBeTruthy();
        const content = await request.get(`${API_BASE_URL}/api/tasks/${encodeURIComponent(taskId!)}/artifacts/${encodeURIComponent(artifact.id)}/content`);
        expect(content.ok(), "evidence schema artifact content must be readable").toBeTruthy();
        expect(String((await content.json() as { content?: unknown }).content ?? "").trim()).not.toBe("");
      }

      if (key !== "evidence_schema_design") {
        await continueAtStageGate(page, request, taskId!, testInfo);
      }
    }

    const sourceTask = await waitFor(request, taskId!, testInfo, "source-acquisition-start", (task) => {
      const row = substage(task, "source_acquisition");
      return row?.status !== "planned" && (row?.workUnitIds ?? []).some((id: string) => (
        (task.workUnits ?? []).some((work: Record<string, any>) => work.workUnitId === id && SOURCE_STATES.has(work.status))
      ));
    });
    await attachJson(testInfo, "ot-p0f-result", { taskId, ...diagnostic(sourceTask) });
    expect(evidence.pageErrors).toEqual([]);
  } finally {
    await evidence.flush();
  }
});
