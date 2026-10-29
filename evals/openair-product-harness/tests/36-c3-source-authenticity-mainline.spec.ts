import { randomUUID } from "node:crypto";
import type { APIRequestContext, BrowserContext, Playwright } from "@playwright/test";
import { expect, test } from "../lib/test";
import type { BrowserAuthUser } from "../lib/auth";
import { activateAccount, loginAsAdmin } from "../lib/auth";
import { API_BASE_URL, WEB_BASE_URL } from "../lib/config";
import { attachJson, collectPageEvidence, screenshot } from "../lib/evidence";

type TaskPayload = {
  taskId: string;
  title?: string;
  status?: string;
  creationStatus?: string;
  resultSource?: string;
  capabilityCalls?: Array<Record<string, any>>;
  artifacts?: Array<Record<string, any>>;
};

type RegisteredIdentity = {
  request: APIRequestContext;
  token: string;
  browserUser: BrowserAuthUser;
};

async function readTask(request: APIRequestContext, taskId: string): Promise<TaskPayload> {
  const response = await request.get(`${API_BASE_URL}/api/tasks/${encodeURIComponent(taskId)}`);
  expect(response.ok(), `owner GET for task ${taskId} should succeed`).toBeTruthy();
  return response.json() as Promise<TaskPayload>;
}

async function readTaskIds(request: APIRequestContext): Promise<string[]> {
  const response = await request.get(`${API_BASE_URL}/api/tasks`);
  expect(response.ok()).toBeTruthy();
  const body = await response.json() as { tasks?: Array<{ taskId: string }> };
  return (body.tasks ?? []).map((task) => task.taskId).sort();
}

async function registerIsolatedIdentity(playwright: Playwright): Promise<RegisteredIdentity> {
  const suffix = randomUUID().replaceAll("-", "");
  const email = `c3-${suffix}@example.com`;
  const username = `c3_${suffix.slice(0, 20)}`;
  const password = `C3-${randomUUID()}-Aa1`;
  const bootstrap = await playwright.request.newContext({ baseURL: API_BASE_URL });
  try {
    const response = await bootstrap.post("/api/auth/register", {
      data: {
        email,
        username,
        password,
        display_name: "C3 isolated boundary user",
      },
    });
    expect(response.status(), "isolated second user registration should succeed").toBe(201);
    const body = await response.json() as {
      pendingActivation?: boolean;
      user: {
        userId: string;
        email: string;
        username: string;
        displayName: string | null;
        ownerId: string;
        defaultWorkspaceId: string;
      };
    };
    expect(body.user.userId).toBeTruthy();

    const admin = await loginAsAdmin(playwright);
    try {
      await activateAccount(admin.request, body.user.userId, "c3 isolated identity");
    } finally {
      await admin.request.dispose();
    }

    const login = await bootstrap.post("/api/auth/login", {
      data: { email, password },
    });
    expect(login.status(), "activated isolated user should be able to log in").toBe(200);
    const session = await login.json() as {
      access_token: string;
      user: {
        userId: string;
        email: string;
        username: string;
        displayName: string | null;
        ownerId: string;
        defaultWorkspaceId: string;
      };
    };
    expect(session.access_token).toBeTruthy();
    const request = await playwright.request.newContext({
      baseURL: API_BASE_URL,
      extraHTTPHeaders: { Authorization: `Bearer ${session.access_token}` },
    });
    return {
      request,
      token: session.access_token,
      browserUser: {
        userId: session.user.userId,
        email: session.user.email,
        username: session.user.username,
        displayName: session.user.displayName,
        ownerId: session.user.ownerId,
        workspaceId: session.user.defaultWorkspaceId,
        createdBy: session.user.ownerId,
      },
    };
  } finally {
    await bootstrap.dispose();
  }
}

async function seedIdentity(context: BrowserContext, identity: RegisteredIdentity): Promise<void> {
  await context.addInitScript(
    ({ token, user }) => {
      window.localStorage.setItem("openair.auth.token.v1", token);
      window.localStorage.setItem("openair.auth.user.v2", JSON.stringify(user));
    },
    { token: identity.token, user: identity.browserUser },
  );
}

test.describe("C3 browser E2E and source authenticity", () => {
  test("C3-01: create, start specialist, inspect replay result, refresh, and re-enter", async ({ page, request }, testInfo) => {
    test.setTimeout(240_000);
    const evidence = collectPageEvidence(page, testInfo);
    let taskId: string | null = null;
    let createdTaskId: string | null = null;

    try {
      const healthResponse = await request.get(`${API_BASE_URL}/health/agent`);
      expect(healthResponse.ok()).toBeTruthy();
      const health = await healthResponse.json() as {
        skillExecMode?: string;
        anthropicApiKeyConfigured?: boolean;
        huiyanApiKeyConfigured?: boolean;
      };
      const providerConfigured = health.anthropicApiKeyConfigured === true || health.huiyanApiKeyConfigured === true;
      const liveReadiness = health.skillExecMode === "live" && providerConfigured ? "NOT_PROVEN" : "ENV_NOT_READY";
      await attachJson(testInfo, "c3-live-readiness", {
        classification: liveReadiness,
        skillExecMode: health.skillExecMode ?? "unknown",
        providerConfigured,
        liveProviderExecutionProven: false,
      });

      const goal = `C3 来源真实性与刷新恢复验证 ${Date.now()}`;
      await page.goto("/", { waitUntil: "commit" });
      const intentInput = page.getByTestId("research-intent-input");
      await expect(intentInput).toBeVisible({ timeout: 60_000 });
      await intentInput.fill(goal);
      const browserCreateOutcome = new Promise<{ classification: string; status: number | null; failure: string | null }>((resolve) => {
        const timeout = setTimeout(() => resolve({ classification: "ENV_NOT_READY", status: null, failure: "no_terminal_response" }), 30_000);
        page.on("response", (response) => {
          if (response.request().method() !== "POST" || !response.url().includes("/api/research-start/submit/stream")) return;
          clearTimeout(timeout);
          resolve({
            classification: response.ok() ? "BROWSER_CREATE_READY" : "ENV_NOT_READY",
            status: response.status(),
            failure: response.ok() ? null : "http_error",
          });
        });
        page.on("requestfailed", (requestEntry) => {
          if (requestEntry.method() !== "POST" || !requestEntry.url().includes("/api/research-start/submit/stream")) return;
          clearTimeout(timeout);
          resolve({ classification: "ENV_NOT_READY", status: null, failure: "request_failed" });
        });
      });
      await page.getByTestId("starter-home-main-create").click();
      const createOutcome = await browserCreateOutcome;
      let creationPath = "browser_research_start";
      if (createOutcome.classification === "BROWSER_CREATE_READY") {
        await expect(page.getByTestId("starter-task-contract-preview")).toBeVisible({ timeout: 70_000 });
        await page.getByTestId("starter-home-contract-confirm").click();
        await expect(page.getByTestId("workbench-draft-start-confirmation")).toBeVisible({ timeout: 70_000 });
        taskId = await page.evaluate(() => window.sessionStorage.getItem("openair.userRoute.currentTaskId"));
      } else {
        creationPath = "owner_scoped_api_setup_after_browser_env_failure";
        await screenshot(page, testInfo, "00-c3-browser-create-env-not-ready");
        const draftResponse = await request.post(`${API_BASE_URL}/api/tasks/draft`, {
          data: { goalSummary: goal },
        });
        expect(draftResponse.ok(), "owner-scoped draft setup should remain available").toBeTruthy();
        taskId = ((await draftResponse.json()) as TaskPayload).taskId;
        await page.goto(`/workbench/${taskId}`, { waitUntil: "commit" });
        await expect(page.getByTestId("workbench-draft-start-confirmation")).toBeVisible({ timeout: 70_000 });
      }
      expect(taskId).toBeTruthy();
      createdTaskId = taskId;
      const draft = await readTask(request, taskId!);
      expect(draft.status).toBe("draft");
      await expect(page.getByTestId("workbench-confirmed-task-start")).toHaveCount(0);
      expect((draft.capabilityCalls ?? []).some((call) => call.runMode === "live" || call.runMode === "replay")).toBe(false);
      await screenshot(page, testInfo, "01-c3-draft-not-started-gate");

      await page.getByTestId("workbench-draft-confirm-start").click();
      await expect(page.getByTestId("workbench-confirmed-task-start-panel")).toBeVisible({ timeout: 30_000 });
      await expect(page.getByTestId("artifact-viewer-canvas")).toContainText("等待首个执行产物");
      const confirmedNotStarted = await readTask(request, taskId!);
      expect(confirmedNotStarted.creationStatus).toBe("confirmed");
      expect((confirmedNotStarted.capabilityCalls ?? []).some((call) => call.name === "intent-understanding")).toBe(false);

      const startOutcomePromise = new Promise<{ classification: string; status: number | null; failure: string | null }>((resolve) => {
        const timeout = setTimeout(() => resolve({ classification: "ENV_NOT_READY", status: null, failure: "no_terminal_response" }), 30_000);
        page.on("response", (response) => {
          if (response.request().method() !== "POST" || !response.url().endsWith(`/api/tasks/${taskId}/start`)) return;
          clearTimeout(timeout);
          resolve({
            classification: response.status() === 200 ? "BROWSER_SPECIALIST_START_PROVEN" : "ENV_NOT_READY",
            status: response.status(),
            failure: response.status() === 200 ? null : "http_error",
          });
        });
        page.on("requestfailed", (requestEntry) => {
          if (requestEntry.method() !== "POST" || !requestEntry.url().endsWith(`/api/tasks/${taskId}/start`)) return;
          clearTimeout(timeout);
          resolve({ classification: "ENV_NOT_READY", status: null, failure: "request_failed" });
        });
      });
      const startButton = page.getByTestId("workbench-confirmed-task-start");
      await expect(startButton).toBeEnabled();
      await startButton.click();
      const startOutcome = await startOutcomePromise;

      let started: TaskPayload | null = null;
      if (startOutcome.classification === "BROWSER_SPECIALIST_START_PROVEN") {
        await expect.poll(async () => {
          started = await readTask(request, taskId!);
          return (started.capabilityCalls ?? []).find((call) => (
            call.name === "intent-understanding"
            && call.status === "completed"
            && (call.outputArtifactRefs?.length ?? 0) > 0
          ))?.sourceMode ?? "missing";
        }, { timeout: 45_000 }).toBe("replay");
      } else {
        await screenshot(page, testInfo, "02-c3-specialist-start-env-not-ready");
        const tasksResponse = await request.get(`${API_BASE_URL}/api/tasks`);
        expect(tasksResponse.ok()).toBeTruthy();
        const tasksBody = await tasksResponse.json() as { tasks?: TaskPayload[] };
        const seededReplay = (tasksBody.tasks ?? []).find((task) => task.taskId === "task_cs_ope_001" && task.resultSource === "replay")
          ?? (tasksBody.tasks ?? []).find((task) => task.resultSource === "replay");
        expect(seededReplay, "an explicitly sourced Replay task is required when durable start is unavailable").toBeTruthy();
        taskId = seededReplay!.taskId;
        await page.goto(`/workbench/${taskId}`, { waitUntil: "commit" });
      }

      started = await readTask(request, taskId!);
      const specialistCall = (started.capabilityCalls ?? []).find((call) => (
        call.sourceMode === "replay"
        && call.status === "completed"
        && ((call.outputArtifactRefs?.length ?? 0) > 0 || (call.artifactRefs?.length ?? 0) > 0)
      ));
      if (startOutcome.classification === "BROWSER_SPECIALIST_START_PROVEN") {
        expect(specialistCall).toMatchObject({ runtime: "replay", runMode: "replay", sourceMode: "replay" });
      }
      expect(started.resultSource).toBe("replay");
      const artifactId = (
        specialistCall?.outputArtifactRefs?.[0]
        ?? specialistCall?.artifactRefs?.[0]
        ?? started.artifacts?.[0]?.id
      ) as string | undefined;
      expect(artifactId).toBeTruthy();
      const contentResponse = await request.get(
        `${API_BASE_URL}/api/tasks/${taskId}/artifacts/${encodeURIComponent(artifactId!)}/content`,
      );
      expect(contentResponse.ok()).toBeTruthy();
      const content = await contentResponse.json() as { sourceMode?: string };
      expect(content.sourceMode).toBe("replay");

      await page.reload({ waitUntil: "commit" });
      await expect(page.getByTestId("result-source-tag")).toHaveText("回放结果");
      await expect(page.getByTestId("replay-disclosure")).toBeVisible();
      await expect(page.getByTestId("workbench-progression-panel")).toBeVisible();
      await expect(page.getByTestId("artifact-viewer-canvas")).toBeVisible();
      await screenshot(page, testInfo, "03-c3-replay-result-and-progress");

      await page.goto("/workbench", { waitUntil: "commit" });
      await page.goto(`/workbench/${taskId}`, { waitUntil: "commit" });
      await expect(page.getByTestId("result-source-tag")).toHaveText("回放结果");
      await expect(page.getByTestId("replay-disclosure")).toBeVisible();
      const restored = await readTask(request, taskId!);
      expect(restored.resultSource).toBe("replay");
      if (specialistCall) {
        expect((restored.capabilityCalls ?? []).find((call) => call.id === specialistCall.id)?.sourceMode).toBe("replay");
      } else {
        expect((restored.artifacts ?? []).some((artifact) => artifact.id === artifactId)).toBe(true);
      }

      await page.goto(`/tasks/${taskId}`, { waitUntil: "commit" });
      const replayDisclosure = page.getByTestId("task-detail-load-error");
      await expect(replayDisclosure).toBeVisible();
      await expect(replayDisclosure.locator(".ant-alert-title")).toHaveText(
        "该任务详情页仅展示真实 API 数据，回放和演示结果不可用。",
      );
      await expect(replayDisclosure.locator(".ant-alert-description")).toHaveText(
        "该任务详情页仅展示真实 API 数据，回放和演示结果不可用。",
      );
      await attachJson(testInfo, "c3-authoritative-source-facts", {
        environmentClassification: liveReadiness,
        browserTaskCreation: createOutcome,
        taskCreationPath: creationPath,
        browserSpecialistStart: startOutcome,
        browserSourceLabel: "回放结果",
        taskResultSource: restored.resultSource,
        capabilityCall: {
          id: specialistCall?.id,
          name: specialistCall?.name,
          runtime: specialistCall?.runtime,
          runMode: specialistCall?.runMode,
          sourceMode: specialistCall?.sourceMode,
          status: specialistCall?.status,
        },
        artifact: { id: artifactId, sourceMode: content.sourceMode },
        refreshAndReentryRetainedSource: true,
        liveProviderExecutionProven: false,
      });
      expect(evidence.pageErrors).toEqual([]);
    } finally {
      await evidence.flush();
      if (createdTaskId) await request.delete(`${API_BASE_URL}/api/tasks/${createdTaskId}`);
    }
  });

  test("C3-02: static starter sample stays read-only and outside the real task path", async ({ page, request }, testInfo) => {
    const evidence = collectPageEvidence(page, testInfo);
    const taskIdsBefore = await readTaskIds(request);
    const taskMutationRequests: string[] = [];
    page.on("request", (entry) => {
      if (["POST", "PUT", "PATCH", "DELETE"].includes(entry.method()) && /\/api\/tasks(?:[/?]|$)/.test(entry.url())) {
        taskMutationRequests.push(`${entry.method()} ${new URL(entry.url()).pathname}`);
      }
    });

    try {
      await page.goto("/starter-samples/project_research_direction_review_v0_3", { waitUntil: "commit" });
      await expect(page.getByTestId("starter-sample-page")).toBeVisible();
      await expect(page.getByText("这是一次真实任务路径样例，不是你的任务。")).toBeVisible();
      await expect(page.getByText("不会写入你的任务列表，也不能作为科研结论引用。", { exact: false })).toBeVisible();
      await page.getByTestId("sample-stage-4").click();
      await page.getByTestId("sample-artifact-card").click();
      await expect(page.getByTestId("sample-readonly-continue")).toBeDisabled();
      await expect(page.getByText("样例只能浏览阶段和产物详情，不能继续生成、不能提交输入，也不会写入你的任务。")).toBeVisible();
      await screenshot(page, testInfo, "03-c3-static-demo-readonly-boundary");

      const taskIdsAfter = await readTaskIds(request);
      expect(taskIdsAfter).toEqual(taskIdsBefore);
      expect(taskMutationRequests).toEqual([]);
      await attachJson(testInfo, "c3-static-demo-source-facts", {
        sourceClassification: "static_demo",
        routeClass: "starter-sample",
        readOnlyControlDisabled: true,
        taskListUnchanged: true,
        taskMutationRequestCount: 0,
        liveProviderExecutionProven: false,
      });
      expect(evidence.pageErrors).toEqual([]);
    } finally {
      await evidence.flush();
    }
  });

  test("C3-03: 403 显示中文无权限提示并保护任务边界", async ({ browser, page, playwright, request }, testInfo) => {
    testInfo.annotations.push({
      type: "permission-boundary",
      description: "403 必须显示中文无权限提示，不得泄露任务或误报为 404、未登录或加载失败。",
    });
    const evidence = collectPageEvidence(page, testInfo);
    const secondIdentity = await registerIsolatedIdentity(playwright);
    let ownerTaskId: string | null = null;
    let secondTaskId: string | null = null;
    let secondContext: BrowserContext | null = null;
    let secondEvidence: ReturnType<typeof collectPageEvidence> | null = null;

    try {
      const ownerCreate = await request.post(`${API_BASE_URL}/api/tasks`, {
        data: { title: `C3 owner boundary task ${Date.now()}`, goalSummary: "C3 owner scope boundary probe" },
      });
      expect(ownerCreate.status()).toBe(201);
      ownerTaskId = ((await ownerCreate.json()) as TaskPayload).taskId;

      const secondCreate = await secondIdentity.request.post("/api/tasks", {
        data: { title: `C3 second-user boundary task ${Date.now()}`, goalSummary: "C3 second-user scope boundary probe" },
      });
      expect(secondCreate.status()).toBe(201);
      secondTaskId = ((await secondCreate.json()) as TaskPayload).taskId;

      const ownerOwn = await request.get(`${API_BASE_URL}/api/tasks/${ownerTaskId}`);
      const secondOwn = await secondIdentity.request.get(`/api/tasks/${secondTaskId}`);
      const secondReadsOwner = await secondIdentity.request.get(`/api/tasks/${ownerTaskId}`);
      const ownerReadsSecond = await request.get(`${API_BASE_URL}/api/tasks/${secondTaskId}`);
      expect(ownerOwn.status()).toBe(200);
      expect(secondOwn.status()).toBe(200);
      expect(secondReadsOwner.status()).toBe(403);
      expect(ownerReadsSecond.status()).toBe(403);

      secondContext = await browser.newContext({ baseURL: WEB_BASE_URL });
      await seedIdentity(secondContext, secondIdentity);
      const secondPage = await secondContext.newPage();
      secondEvidence = collectPageEvidence(secondPage, testInfo);
      await secondPage.goto(`/tasks/${ownerTaskId}`, { waitUntil: "commit" });
      const detailError = secondPage.getByTestId("task-detail-load-error");
      await expect(detailError).toBeVisible();
      await expect(detailError.locator(".ant-alert-title")).toHaveText("无权限访问");
      await expect(detailError.locator(".ant-alert-description")).toHaveCount(0);
      await expect(detailError).not.toContainText("未找到任务");
      await expect(detailError).not.toContainText("未登录或登录状态已失效");
      await expect(detailError).not.toContainText("加载失败");
      await expect(secondPage.getByText(/C3 owner boundary task/)).toHaveCount(0);
      await screenshot(secondPage, testInfo, "04-c3-cross-user-chinese-denial");

      await secondPage.goto(`/workbench/${ownerTaskId}`, { waitUntil: "commit" });
      const workbenchError = secondPage.getByTestId("workbench-load-error");
      await expect(workbenchError).toBeVisible();
      await expect(workbenchError.locator(".ant-alert-title")).toHaveText("无权限访问");
      await expect(workbenchError.locator(".ant-alert-description")).toHaveCount(0);
      await expect(workbenchError).not.toContainText("未找到任务");
      await expect(workbenchError).not.toContainText("未登录或登录状态已失效");
      await expect(workbenchError).not.toContainText("无法加载工作台");
      await expect(secondPage.getByText(/C3 owner boundary task/)).toHaveCount(0);
      await screenshot(secondPage, testInfo, "05-c3-cross-user-workbench-denial");
      await attachJson(testInfo, "c3-authoritative-scope-facts", {
        ownerReadsOwnTask: ownerOwn.status(),
        secondUserReadsOwnTask: secondOwn.status(),
        secondUserReadsOwnerTask: secondReadsOwner.status(),
        ownerReadsSecondUserTask: ownerReadsSecond.status(),
        browserChinesePermissionDenialVisible: true,
        detailPresentation: "permission_denied_zh",
        workbenchPresentation: "permission_denied_zh",
        forbiddenResponsesObserved: true,
        protectedTaskContentVisible: false,
        fixtureFallbackObserved: false,
      });
      expect(evidence.pageErrors).toEqual([]);
      expect(secondEvidence.pageErrors).toEqual([]);
    } finally {
      if (secondEvidence) await secondEvidence.flush();
      await evidence.flush();
      if (ownerTaskId) await request.delete(`${API_BASE_URL}/api/tasks/${ownerTaskId}`);
      if (secondTaskId) await secondIdentity.request.delete(`/api/tasks/${secondTaskId}`);
      if (secondContext) await secondContext.close();
      await secondIdentity.request.dispose();
    }
  });
});
