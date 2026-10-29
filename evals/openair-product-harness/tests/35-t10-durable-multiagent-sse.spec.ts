import { expect, test } from "../lib/test";
import type { APIRequestContext, TestInfo } from "@playwright/test";
import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";
import { API_BASE_URL } from "../lib/config";
import { attachJson, collectPageEvidence, screenshot, snapshotTask } from "../lib/evidence";

const execFileAsync = promisify(execFile);
const TERMINAL_TASK_STATUSES = new Set(["completed", "failed", "cancelled", "awaiting_confirmation"]);
const EXPECTED_ARTIFACT_FIELDS = [
  "assumptions",
  "handoff",
  "next_evidence_needs",
  "question_candidates",
  "scope_boundary",
  "verification_readiness",
];

const RESEARCH_INPUT = [
  "I am planning a source-bounded study of reliability evaluation for code-generating language models.",
  "First refine two or three research-question candidates, explicitly separate included and excluded scope,",
  "record assumptions as unverified, and identify the minimum evidence needed before any literature claims.",
  "Do not retrieve external sources or claim that any scientific result has already been verified.",
].join(" ");

function decodeSseFrame(frame: string): Record<string, any> | null {
  const fields = new Map<string, string[]>();
  for (const rawLine of frame.split(/\r?\n/)) {
    if (!rawLine || rawLine.startsWith(":")) continue;
    const separator = rawLine.indexOf(":");
    const key = separator >= 0 ? rawLine.slice(0, separator) : rawLine;
    const rawValue = separator >= 0 ? rawLine.slice(separator + 1) : "";
    const value = rawValue.startsWith(" ") ? rawValue.slice(1) : rawValue;
    fields.set(key, [...(fields.get(key) ?? []), value]);
  }
  const data = fields.get("data")?.join("\n");
  if (!data) return null;
  const event = JSON.parse(data) as Record<string, any>;
  expect(event.id).toBe(fields.get("id")?.[0]);
  expect(event.type).toBe(fields.get("event")?.[0]);
  return event;
}

async function readTaskEvents(
  taskId: string,
  token: string,
  stopWhen: (event: Record<string, any>, events: Array<Record<string, any>>) => boolean,
): Promise<Array<Record<string, any>>> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30_000);
  try {
    const response = await fetch(`${API_BASE_URL}/api/tasks/${encodeURIComponent(taskId)}/events`, {
      headers: { Authorization: `Bearer ${token}`, Accept: "text/event-stream" },
      signal: controller.signal,
    });
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("text/event-stream");
    expect(response.body).toBeTruthy();
    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    let buffered = "";
    const events: Array<Record<string, any>> = [];
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffered += decoder.decode(value, { stream: true });
      const frames = buffered.split(/\r?\n\r?\n/);
      buffered = frames.pop() ?? "";
      for (const frame of frames) {
        const event = decodeSseFrame(frame);
        if (!event) continue;
        events.push(event);
        if (stopWhen(event, events)) return events;
      }
    }
    throw new Error("task event stream ended before the requested lifecycle evidence");
  } finally {
    clearTimeout(timeout);
    controller.abort();
  }
}

async function readFirstTaskEvent(taskId: string, token: string): Promise<Record<string, any>> {
  return (await readTaskEvents(taskId, token, () => true))[0];
}

async function waitForTerminalTask(
  request: APIRequestContext,
  testInfo: TestInfo,
  taskId: string,
  capturePage: Parameters<typeof screenshot>[0],
): Promise<Record<string, any>> {
  const deadline = Date.now() + 600_000;
  let latest: Record<string, any> | null = null;
  let reviewFrameCaptured = false;
  while (Date.now() < deadline) {
    const response = await request.get(`${API_BASE_URL}/api/tasks/${encodeURIComponent(taskId)}`);
    const bodyText = await response.text();
    expect(response.ok(), `task polling failed with HTTP ${response.status()}`).toBeTruthy();
    latest = JSON.parse(bodyText) as Record<string, any>;
    if (
      !reviewFrameCaptured
      && latest.status === "running"
      && Array.isArray(latest.artifacts)
      && latest.artifacts.length > 0
      && Array.isArray(latest.workUnits)
      && latest.workUnits.some((work: Record<string, any>) => work.status === "queued")
    ) {
      reviewFrameCaptured = true;
      await capturePage.waitForTimeout(250);
      await screenshot(capturePage, testInfo, "t10-mandatory-review-pending");
    }
    if (TERMINAL_TASK_STATUSES.has(String(latest.status))) {
      await attachJson(testInfo, "t10-terminal-task", {
        taskId,
        status: latest.status,
        revision: latest.revision,
        projectionRevision: latest.progressProjection?.revision ?? null,
        projectionStatus: latest.progressProjection?.taskProgress?.status ?? null,
        artifactCount: Array.isArray(latest.artifacts) ? latest.artifacts.length : null,
        capabilityCallCount: Array.isArray(latest.capabilityCalls) ? latest.capabilityCalls.length : null,
      });
      return latest;
    }
    await new Promise((resolve) => setTimeout(resolve, 1_000));
  }
  throw new Error(`task_terminal_timeout:last_status=${latest?.status ?? "unknown"}`);
}

async function assertDurableDatabaseFacts(taskId: string): Promise<Record<string, any>> {
  const python = process.env.OPENAIR_PRODUCT_HARNESS_PYTHON?.trim() || "python";
  const artifactRoot = process.env.OPENAIR_PRODUCT_HARNESS_ARTIFACT_ROOT?.trim()
    || path.join(process.cwd(), "workspace");
  const { stdout } = await execFileAsync(
    python,
    [
      path.join(process.cwd(), "scripts", "t10_assert_durable_task.py"),
      taskId,
      "--artifact-root",
      artifactRoot,
    ],
    { cwd: process.cwd(), env: process.env, timeout: 60_000, windowsHide: true },
  );
  const line = stdout.trim().split(/\r?\n/).at(-1);
  if (!line) throw new Error("durable_database_evidence_missing");
  return JSON.parse(line) as Record<string, any>;
}

test("T10: flag-on Web starts the durable multi-Agent flow from the same revision", async ({
  devSession,
  page,
  request,
}, testInfo) => {
  test.setTimeout(900_000);
  const evidence = collectPageEvidence(page, testInfo);
  let taskId: string | null = null;

  try {
    expect(devSession, "T10 requires authenticated dev execution").toBeTruthy();
    await page.goto("/", { waitUntil: "commit" });
    await page.getByTestId("research-intent-input").fill(RESEARCH_INPUT);
    await page.getByTestId("starter-home-main-create").click();
    await expect(page.getByTestId("workbench-draft-start-confirmation")).toBeVisible({ timeout: 420_000 });
    await screenshot(page, testInfo, "t10-plan-awaiting-confirmation");

    taskId = await page.evaluate(() => window.sessionStorage.getItem("openair.userRoute.currentTaskId"));
    expect(taskId).toBeTruthy();
    const draft = await snapshotTask(request, testInfo, API_BASE_URL, taskId!, "t10-draft");
    expect(draft.creationPlan?.planningSource).toBe("research_design_v1_2");
    const designs = draft.researchDesignVersions ?? [];
    expect(designs.length).toBeGreaterThan(0);
    const latestDesign = designs.at(-1)!;
    expect(latestDesign.status).toBe("draft");
    expect(latestDesign.coverageReport?.status).toMatch(/^passed_/);
    expect(process.env.HUIYAN_LLM_MODEL_TERRA).toBe("gpt-5.6-terra");

    const firstEvent = await readFirstTaskEvent(taskId!, devSession!.token);
    expect(firstEvent.type).toBe("snapshot");
    expect(firstEvent.payload?.revision).toBe(draft.revision);
    expect(firstEvent.payload?.projection?.revision).toBe(draft.revision);

    const confirmation = page.getByTestId("research-start-confirmation-panel");
    for (const checkbox of await confirmation.getByRole("checkbox").all()) await checkbox.check();
    const limitationDecision = confirmation.getByRole("radio").nth(1);
    if (await limitationDecision.isVisible()) await limitationDecision.check();

    const startRequestedAt = Date.now();
    const startResponsePromise = page.waitForResponse((response) => (
      response.request().method() === "POST"
      && response.url() === `${API_BASE_URL}/api/tasks/${encodeURIComponent(taskId!)}/start`
    ), { timeout: 30_000 });
    await page.getByTestId("workbench-draft-confirm-start").click();
    const startResponse = await startResponsePromise;
    await attachJson(testInfo, "t10-start-contract", {
      taskId,
      status: startResponse.status(),
      revisionBeforeConfirmation: draft.revision,
      durationMs: Date.now() - startRequestedAt,
    });
    expect(startResponse.status(), "flag-on Web must satisfy the frozen /start contract").toBe(202);

    await expect(page.getByTestId("workbench-canonical-execution-status")).toBeVisible({ timeout: 30_000 });
    await screenshot(page, testInfo, "t10-started");

    const terminal = await waitForTerminalTask(request, testInfo, taskId!, page);
    expect(terminal.status, "the real specialist and mandatory reviewer must close the task").toBe("completed");
    expect(terminal.progressProjection?.revision).toBe(terminal.revision);
    expect(terminal.progressProjection?.taskProgress?.status).toBe("completed");

    const finalEvent = await readFirstTaskEvent(taskId!, devSession!.token);
    expect(finalEvent.type).toBe("snapshot");
    expect(finalEvent.payload?.revision).toBe(terminal.revision);
    expect(finalEvent.payload?.projection?.revision).toBe(terminal.revision);
    expect(finalEvent.payload?.projection?.taskProgress?.status).toBe("completed");

    const lifecycleEvents = await readTaskEvents(
      taskId!,
      devSession!.token,
      (event) => event.type === "execution_terminal",
    );
    const lifecycleTypes = new Set(lifecycleEvents.map((event) => event.type));
    for (const requiredType of [
      "snapshot",
      "orchestrator_lifecycle",
      "agent_lifecycle",
      "mandatory_review_lifecycle",
      "artifact_persisted",
      "execution_terminal",
    ]) expect(lifecycleTypes.has(requiredType), `missing lifecycle event ${requiredType}`).toBeTruthy();
    const persistentEvents = lifecycleEvents.filter((event) => event.type !== "snapshot" && event.type !== "heartbeat");
    expect(persistentEvents.every((event) => event.revision === terminal.revision)).toBeTruthy();
    expect(new Set(persistentEvents.map((event) => event.id)).size).toBe(persistentEvents.length);
    await attachJson(testInfo, "t10-lifecycle-summary", {
      eventCount: lifecycleEvents.length,
      types: [...lifecycleTypes].sort(),
      revision: terminal.revision,
    });

    const catalogResponse = await request.get(
      `${API_BASE_URL}/api/artifacts?sourceTaskId=${encodeURIComponent(taskId!)}&pageSize=100`,
    );
    expect(catalogResponse.ok(), "completed producer Artifact must be publicly visible").toBeTruthy();
    const catalog = await catalogResponse.json() as { artifacts?: Array<Record<string, any>>; total?: number };
    expect(catalog.total).toBeGreaterThanOrEqual(1);
    const artifactSummaries: Array<Record<string, unknown>> = [];
    for (const artifact of catalog.artifacts ?? []) {
      const artifactId = String(artifact.id ?? "");
      expect(artifactId).toBeTruthy();
      expect(artifact.sourceCapabilityCallId).toBeTruthy();
      expect(artifact.artifactState).toBe("usable");
      const contentResponse = await request.get(
        `${API_BASE_URL}/api/artifacts/${encodeURIComponent(artifactId)}/content`,
      );
      expect(contentResponse.ok(), `Artifact ${artifactId} content must be readable`).toBeTruthy();
      const contentPayload = await contentResponse.json() as { content?: string; sourceMode?: string };
      const body = JSON.parse(String(contentPayload.content ?? "")) as Record<string, unknown>;
      expect(Object.keys(body).sort()).toEqual(EXPECTED_ARTIFACT_FIELDS);
      expect(contentPayload.sourceMode).toBe("live");
      artifactSummaries.push({
        artifactId,
        sourceCapabilityCallId: artifact.sourceCapabilityCallId,
        artifactState: artifact.artifactState,
        sourceMode: contentPayload.sourceMode,
        fieldNames: Object.keys(body).sort(),
      });
    }
    await attachJson(testInfo, "t10-public-artifacts", artifactSummaries);

    const durableFacts = await assertDurableDatabaseFacts(taskId!);
    expect(durableFacts.workUnitCount).toBeGreaterThanOrEqual(2);
    expect(durableFacts.capabilityCallCount).toBe(durableFacts.workUnitCount);
    expect(durableFacts.producerArtifactCount).toBeGreaterThanOrEqual(1);
    expect(durableFacts.approvedReviewCount).toBeGreaterThanOrEqual(1);
    expect(durableFacts.lineageCount).toBe(durableFacts.producerArtifactCount);
    await attachJson(testInfo, "t10-durable-database-facts", durableFacts);

    await page.reload({ waitUntil: "commit" });
    const canonicalStatus = page.getByTestId("workbench-canonical-execution-status");
    await expect(canonicalStatus).toBeVisible({ timeout: 30_000 });
    await expect(canonicalStatus).toHaveAttribute("data-revision", String(terminal.revision));
    await expect(page.locator('[data-execution-status="completed"]').first()).toBeVisible();
    await expect(page.getByTestId("workbench-durable-artifact-projection")).toHaveAttribute(
      "data-artifact-id",
      String(catalog.artifacts?.[0]?.id),
    );
    await screenshot(page, testInfo, "t10-completed");
    expect(evidence.pageErrors).toEqual([]);
  } finally {
    await evidence.flush();
  }
});
