import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { disposeDevSession, prepareDevHarnessAuth } from "../lib/auth";
import { API_BASE_URL, RUN_DIR } from "../lib/config";
import { Batch1CaseRecorder, type Batch1PreconditionReport } from "../lib/batch1CaseGates";
import { attachJson, collectPageEvidence, screenshot } from "../lib/evidence";
import { pickSeededAdvancableTask, readTasks } from "../lib/gates";
import {
  clearWorkbenchPersistedNav,
  establishHistorySubScenario,
  S17_HISTORY_SUB_SCENARIO_ID,
  S17_RUNNING_TASK_ID,
} from "../lib/uiuxScenario";
import {
  collectBannerMetrics,
  collectCenterArtifactMetrics,
  collectHistorySubstateMetrics,
  collectInputFootMetrics,
  collectSemanticRegions,
  collectTimelineMetrics,
  evaluateBannerStackGate,
  evaluateCenterArtifactGate,
  evaluateDedupGate01,
  evaluateHistorySubstateGate,
  evaluateSemanticDedup,
  evaluateTimelineGate1280,
  navigateWorkbenchMode,
  pickSeededCompletedTask,
  S17_VIEWPORTS,
  saveMatrixScreenshot,
  setSidebarState,
  type SidebarState,
  type WorkbenchMode,
  UiuxGateReporter,
  writeDomMetrics,
  writeMatrixReport,
} from "../lib/uiuxMetrics";

const HIGH_RISK_VIEWPORT = S17_VIEWPORTS[0];
const SIDEBAR_STATES: SidebarState[] = ["both", "left-only", "right-only", "collapsed"];
const MATRIX_MODES: WorkbenchMode[] = ["current", "history", "history-sub", "replay", "completed"];
const BATCH1_CASE_INTERACTION_MIN = "B1-INTERACTION-PANEL-MIN-READABLE-AREA";
const BATCH1_CASE_INTERACTION_COLLAPSE = "B1-INTERACTION-COLLAPSE-PRESERVE-CONTEXT";
const STABLE_AWAITING_TASK_ID = "task_cs_ope_002";
const BUG03_CONTEXT_SEED_TEXT = "B2-BUG03-DETERMINISTIC-CONTEXT task_cs_ope_001 collapse anchor";

type InteractionPanelContext = {
  message_count: number;
  message_texts: string[];
  latest_message_text: string;
  scroll_top: number;
  scroll_height: number;
  client_height: number;
};

type CenterOperabilitySnapshot = {
  center_visible: boolean;
  center_box: { x: number; y: number; width: number; height: number } | null;
  main_scroll_present: boolean;
  main_scroll_pointer_events: string | null;
  main_scroll_visibility: string | null;
};

type CenterOperabilityPerSidebar = CenterOperabilitySnapshot & {
  sidebar: SidebarState;
  can_operate: boolean;
};

type AgentMessage = {
  role?: string;
  text?: string;
};

type Bug03MessageSetupResult = {
  status: "PASS" | "FAIL";
  method: "agent-chat-api";
  seed_text: string;
  before_count: number | null;
  after_count: number | null;
  user_seed_present: boolean;
  assistant_present: boolean;
  errors: string[];
};

function summarizeMessageForReport(text: string, maxLength = 80): string {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (!normalized) return "EMPTY";
  if (normalized.length <= maxLength) return normalized;
  return `${normalized.slice(0, maxLength)}...`;
}

async function collectInteractionPanelContext(page: Page): Promise<InteractionPanelContext> {
  return page.evaluate(() => {
    const normalize = (value: string | null | undefined) => (value ?? "").replace(/\s+/g, " ").trim();
    const scroll = document.querySelector(".wb-interaction-scroll") as HTMLElement | null;
    const rows = Array.from(document.querySelectorAll(".wb-interaction-messages .wb-bubble-row"));
    const messageTexts = rows
      .map((row) => normalize((row as HTMLElement).innerText || row.textContent))
      .filter((text) => text.length > 0);
    const latestText = messageTexts.length > 0 ? messageTexts[messageTexts.length - 1] : "";
    return {
      message_count: rows.length,
      message_texts: messageTexts,
      latest_message_text: latestText,
      scroll_top: scroll?.scrollTop ?? 0,
      scroll_height: scroll?.scrollHeight ?? 0,
      client_height: scroll?.clientHeight ?? 0,
    };
  });
}

async function readAgentMessages(
  request: APIRequestContext,
  taskId: string,
): Promise<AgentMessage[]> {
  const response = await request.get(`${API_BASE_URL}/api/tasks/${encodeURIComponent(taskId)}/agent/messages`);
  if (!response.ok()) {
    const detail = await response.text().catch(() => "<no body>");
    throw new Error(`GET agent messages failed: HTTP ${response.status()} ${detail.slice(0, 160)}`);
  }
  const body = await response.json() as { messages?: AgentMessage[] };
  return Array.isArray(body.messages) ? body.messages : [];
}

async function ensureBug03DeterministicMessageContext(
  request: APIRequestContext,
  taskId: string,
): Promise<Bug03MessageSetupResult> {
  const result: Bug03MessageSetupResult = {
    status: "FAIL",
    method: "agent-chat-api",
    seed_text: BUG03_CONTEXT_SEED_TEXT,
    before_count: null,
    after_count: null,
    user_seed_present: false,
    assistant_present: false,
    errors: [],
  };

  try {
    const before = await readAgentMessages(request, taskId);
    result.before_count = before.length;
    const alreadySeeded = before.some(
      (message) => message.role === "user" && String(message.text ?? "").includes(BUG03_CONTEXT_SEED_TEXT),
    );

    if (!alreadySeeded) {
      const response = await request.post(`${API_BASE_URL}/api/tasks/${encodeURIComponent(taskId)}/agent/chat`, {
        data: {
          message: BUG03_CONTEXT_SEED_TEXT,
          substageKey: "experiment_run_monitor",
        },
        timeout: 90_000,
      });
      if (!response.ok()) {
        const detail = await response.text().catch(() => "<no body>");
        result.errors.push(`POST agent chat setup failed: HTTP ${response.status()} ${detail.slice(0, 160)}`);
      } else {
        const body = await response.json().catch(() => ({})) as { error?: string | null };
        if (body.error) {
          result.errors.push(`POST agent chat setup returned error: ${body.error}`);
        }
      }
    }

    const after = await readAgentMessages(request, taskId);
    result.after_count = after.length;
    result.user_seed_present = after.some(
      (message) => message.role === "user" && String(message.text ?? "").includes(BUG03_CONTEXT_SEED_TEXT),
    );
    result.assistant_present = after.some(
      (message) => message.role === "assistant" && String(message.text ?? "").trim().length > 0,
    );
    if (!result.user_seed_present) {
      result.errors.push("deterministic user message not present in agent messages");
    }
    if (!result.assistant_present) {
      result.errors.push("assistant message not present in agent messages");
    }
    if (result.user_seed_present && result.assistant_present && after.length > (result.before_count ?? 0)) {
      result.status = "PASS";
    } else if (result.user_seed_present && result.assistant_present && alreadySeededMessage(after)) {
      result.status = "PASS";
    }
  } catch (error) {
    result.errors.push(error instanceof Error ? error.message : String(error));
  }

  return result;
}

function alreadySeededMessage(messages: AgentMessage[]): boolean {
  return messages.some(
    (message) => message.role === "user" && String(message.text ?? "").includes(BUG03_CONTEXT_SEED_TEXT),
  );
}

async function collectCenterOperabilitySnapshot(page: Page): Promise<CenterOperabilitySnapshot> {
  return page.evaluate(() => {
    const center = document.querySelector('[data-testid="workbench-center"]') as HTMLElement | null;
    const mainScroll = document.querySelector(".wb-main-scroll") as HTMLElement | null;
    const centerRect = center?.getBoundingClientRect();
    const mainScrollStyle = mainScroll ? getComputedStyle(mainScroll) : null;
    return {
      center_visible: Boolean(centerRect && centerRect.width > 0 && centerRect.height > 0),
      center_box: centerRect
        ? { x: centerRect.x, y: centerRect.y, width: centerRect.width, height: centerRect.height }
        : null,
      main_scroll_present: Boolean(mainScroll),
      main_scroll_pointer_events: mainScrollStyle?.pointerEvents ?? null,
      main_scroll_visibility: mainScrollStyle?.visibility ?? null,
    };
  });
}

async function evaluateCenterOperabilityAcrossSidebarSwitch(page: Page): Promise<{
  passed: boolean;
  states: CenterOperabilityPerSidebar[];
}> {
  const checkpoints: SidebarState[] = ["both", "left-only", "right-only", "both"];
  const states: CenterOperabilityPerSidebar[] = [];
  for (const sidebar of checkpoints) {
    await setSidebarState(page, sidebar);
    await page.waitForTimeout(150);
    const snapshot = await collectCenterOperabilitySnapshot(page);
    const minHeightOk = (snapshot.center_box?.height ?? 0) >= 160;
    const canOperate =
      snapshot.center_visible &&
      snapshot.main_scroll_present &&
      snapshot.main_scroll_pointer_events !== "none" &&
      snapshot.main_scroll_visibility !== "hidden" &&
      minHeightOk;
    states.push({ sidebar, can_operate: canOperate, ...snapshot });
  }
  return {
    passed: states.every((item) => item.can_operate),
    states,
  };
}

async function prepareWorkbench(
  page: Page,
  taskId: string,
  mode: WorkbenchMode,
  sidebar: SidebarState,
  viewport = HIGH_RISK_VIEWPORT,
): Promise<void> {
  if (mode === "history-sub") {
    throw new Error("Use establishHistorySubScenario() for history-sub matrix rows");
  }
  await clearWorkbenchPersistedNav(page, taskId);
  await page.setViewportSize({ width: viewport.width, height: viewport.height });
  await page.goto(`/workbench/${taskId}`, { waitUntil: "commit" });
  await expect(page.getByTestId("workbench-center")).toBeVisible({ timeout: 30_000 });
  await navigateWorkbenchMode(page, mode);
  await setSidebarState(page, sidebar);
  await expect(page.getByTestId("workbench-center")).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(200);
}

test.describe("S17 / UX11-UX14 UI layout gates - red baseline", () => {
  test.describe.configure({ timeout: 420_000 });

  test("UX-REG-S17-MATRIX: deterministic gates + matrix capture", async ({ page, playwright }, testInfo) => {
    const evidence = collectPageEvidence(page, testInfo);
    const reporter = new UiuxGateReporter();
    const batch1Recorder = new Batch1CaseRecorder();
    const matrixRows: Record<string, unknown>[] = [];
    const session = await prepareDevHarnessAuth(playwright, page);

    try {
      const tasks = await readTasks(session.request, API_BASE_URL);
      const runningTask = pickSeededAdvancableTask(tasks);
      const completedTask = pickSeededCompletedTask(tasks);
      expect(runningTask, "Seeded replay/running task is required").toBeTruthy();
      expect(completedTask, "Seeded completed task is required").toBeTruthy();
      if (!runningTask || !completedTask) throw new Error("Required seeded tasks missing");

      const fixtureErrors: string[] = [];
      const stableRunningTask = tasks.find((task) => String(task.taskId ?? "") === S17_RUNNING_TASK_ID);
      const stableAwaitingTask = tasks.find((task) => String(task.taskId ?? "") === STABLE_AWAITING_TASK_ID);
      if (!stableRunningTask || stableRunningTask.status !== "running") {
        fixtureErrors.push("task_cs_ope_001 must stay in status=running");
      }
      if (!stableAwaitingTask || stableAwaitingTask.status !== "awaiting_confirmation") {
        fixtureErrors.push("task_cs_ope_002 must stay in status=awaiting_confirmation");
      }

      const runningTaskId = runningTask.taskId as string;
      expect(runningTaskId, "S17 running task must remain the stable replay fixture").toBe(S17_RUNNING_TASK_ID);
      const completedTaskId = completedTask.taskId as string;
      const collapseTaskId = runningTaskId;
      const collapseTaskSnapshot = tasks.find((task) => String(task.taskId ?? "") === collapseTaskId) ?? runningTask;

      const historySubPrecondition = await establishHistorySubScenario(page, session.request, API_BASE_URL, {
        scenarioId: S17_HISTORY_SUB_SCENARIO_ID,
        taskId: runningTaskId,
        viewport: HIGH_RISK_VIEWPORT,
        sidebar: "both",
        taskSnapshot: runningTask,
      });
      await attachJson(testInfo, "history-sub-scenario-preconditions", historySubPrecondition, "gates");
      expect(
        historySubPrecondition.precondition_status,
        historySubPrecondition.errors.length > 0
          ? `HARNESS_SETUP_FAIL: ${historySubPrecondition.errors.join("; ")}`
          : "history-sub scenario preconditions must pass before product gates",
      ).toBe("PASS");

      const timeline = await collectTimelineMetrics(page);
      const foot = await collectInputFootMetrics(page, HIGH_RISK_VIEWPORT.height);
      const dedupRegions = await collectSemanticRegions(page);
      const dedup = evaluateSemanticDedup(dedupRegions);
      const centerArtifact = await collectCenterArtifactMetrics(page);
      const historySubstate = await collectHistorySubstateMetrics(page);
      const gateDomMetrics = {
        timeline,
        foot,
        dedup,
        centerArtifact,
        historySubstate,
        viewport: HIGH_RISK_VIEWPORT.label,
        taskId: runningTaskId,
        mode: "history-sub",
        sidebar: "both",
        scenario: historySubPrecondition,
      };
      const domMetricsFile = await writeDomMetrics("ux-gates-high-risk", gateDomMetrics);
      await attachJson(testInfo, "ux-gates-high-risk-dom-metrics", gateDomMetrics, "dom-metrics");
      await screenshot(page, testInfo, "ux-gate-history-sub-both-1280x720");

      const bug02Precondition: Batch1PreconditionReport = {
        scenario_id: "interaction-panel-min-readable-area",
        task_id: runningTaskId,
        target_url: `/workbench/${runningTaskId}`,
        viewport: HIGH_RISK_VIEWPORT.label,
        precondition_status: historySubPrecondition.precondition_status,
        failure_type: historySubPrecondition.precondition_status === "PASS" ? "NONE" : "HARNESS_SETUP_FAIL",
        errors: [...historySubPrecondition.errors, ...fixtureErrors],
        notes: [
          `scenario_source=${historySubPrecondition.scenario_id}`,
          "validate .wb-interaction-messages readable height",
          "validate .wb-interaction-foot remains in viewport",
          "validate center area operable after three-column switch",
        ],
      };
      if (bug02Precondition.errors.length > 0) {
        bug02Precondition.precondition_status = "FAIL";
        bug02Precondition.failure_type = "HARNESS_SETUP_FAIL";
      }
      await attachJson(testInfo, "batch1-bug02-precondition", bug02Precondition, "gates");

      const timelineGate = evaluateTimelineGate1280(timeline, foot);
      reporter.record(timelineGate);
      let bug02FailureType: "NONE" | "HARNESS_SETUP_FAIL" | "PRODUCT_GATE_FAIL" | "INFRA_FAIL" =
        bug02Precondition.precondition_status === "PASS" ? "NONE" : "HARNESS_SETUP_FAIL";
      let bug02Passed = false;
      const bug02Actual: Record<string, unknown> = { timeline_gate: timelineGate };
      if (bug02Precondition.precondition_status === "PASS") {
        try {
          const centerOperability = await evaluateCenterOperabilityAcrossSidebarSwitch(page);
          bug02Actual.center_operability_after_sidebar_switch = centerOperability;
          bug02Passed = timelineGate.passed && centerOperability.passed;
          bug02FailureType = bug02Passed ? "NONE" : "PRODUCT_GATE_FAIL";
          await screenshot(page, testInfo, "batch1-bug02-after-three-column-switch");
        } catch (error) {
          bug02FailureType = "INFRA_FAIL";
          bug02Actual.error = error instanceof Error ? error.message : String(error);
        }
      }
      batch1Recorder.record({
        id: BATCH1_CASE_INTERACTION_MIN,
        bug: "Bug-02",
        passed: bug02Passed && bug02Precondition.precondition_status === "PASS",
        description: "Interaction panel keeps readable area and center remains operable after sidebar switches.",
        failure_type: bug02FailureType,
        precondition: bug02Precondition,
        actual: bug02Actual,
        expected: {
          ...timelineGate.expected,
          center_operable_after_sidebar_switch: true,
        },
      });

      const bug03Precondition: Batch1PreconditionReport = {
        scenario_id: "interaction-collapse-preserve-context",
        task_id: collapseTaskId,
        target_url: `/workbench/${collapseTaskId}`,
        viewport: HIGH_RISK_VIEWPORT.label,
        precondition_status: historySubPrecondition.precondition_status,
        failure_type: historySubPrecondition.precondition_status === "PASS" ? "NONE" : "HARNESS_SETUP_FAIL",
        errors: [...historySubPrecondition.errors, ...fixtureErrors],
        notes: ["switch to current mode to validate interactive collapse context"],
      };
      const bug03MessageSetup = await ensureBug03DeterministicMessageContext(session.request, collapseTaskId);
      await attachJson(testInfo, "batch1-bug03-message-setup", bug03MessageSetup, "gates");
      if (bug03MessageSetup.status !== "PASS") {
        bug03Precondition.errors.push(
          `deterministic message setup failed via ${bug03MessageSetup.method}: ${bug03MessageSetup.errors.join("; ")}`,
        );
      }
      await prepareWorkbench(page, collapseTaskId, "current", "both", HIGH_RISK_VIEWPORT);
      await page.evaluate(() => {
        const panel = document.querySelector(".wb-interaction-scroll") as HTMLElement | null;
        if (panel) panel.scrollTop = panel.scrollHeight;
      });
      await page.waitForTimeout(150);
      let beforeCollapse = await collectInteractionPanelContext(page);
      const rightPanelStateBefore = await page.evaluate(() => {
        const rightPanel = document.querySelector('[data-testid="workbench-right"]') as HTMLElement | null;
        const rightRail = document.querySelector('[data-testid="right-panel-collapsed"]') as HTMLElement | null;
        const isVisible = (element: HTMLElement | null) =>
          Boolean(element && element.offsetParent !== null && getComputedStyle(element).visibility !== "hidden");
        if (isVisible(rightRail)) return "collapsed";
        if (isVisible(rightPanel)) return "expanded";
        return "hidden";
      });
      if (beforeCollapse.message_count < 1 || beforeCollapse.latest_message_text.length === 0) {
        const inputCount = await page.locator("textarea.wb-interaction-input").count();
        const sendButtonCount = await page.locator(".wb-interaction-input-bar button").count();
        bug03Precondition.errors.push(
          `deterministic agent message not observable as .wb-bubble-row before collapse; input_count=${inputCount}; send_button_count=${sendButtonCount}`,
        );
      }
      if (beforeCollapse.message_count < 1 || beforeCollapse.latest_message_text.length === 0) {
        bug03Precondition.errors.push("no interaction messages found before collapse");
      }
      const collapseStage = String(
        (collapseTaskSnapshot as Record<string, unknown>).currentStage ??
        (collapseTaskSnapshot as Record<string, unknown>).current_stage ??
        "UNKNOWN",
      );
      const collapseSubstage = String(
        (collapseTaskSnapshot as Record<string, unknown>).currentSubStage ??
        (collapseTaskSnapshot as Record<string, unknown>).currentSubstage ??
        (collapseTaskSnapshot as Record<string, unknown>).current_substage ??
        "UNKNOWN",
      );
      bug03Precondition.notes = [
        ...(bug03Precondition.notes ?? []),
        `url=${page.url()}`,
        `task_id=${collapseTaskId}`,
        `stage=${collapseStage}`,
        `substage=${collapseSubstage}`,
        `right_panel_state=${rightPanelStateBefore}`,
        `message_setup_status=${bug03MessageSetup.status}`,
        `message_setup_method=${bug03MessageSetup.method}`,
        `message_setup_before_count=${bug03MessageSetup.before_count ?? "UNKNOWN"}`,
        `message_setup_after_count=${bug03MessageSetup.after_count ?? "UNKNOWN"}`,
        `message_setup_user_seed_present=${bug03MessageSetup.user_seed_present}`,
        `message_setup_assistant_present=${bug03MessageSetup.assistant_present}`,
        `message_count=${beforeCollapse.message_count}`,
        `latest_message_text_summary=${summarizeMessageForReport(beforeCollapse.latest_message_text)}`,
      ];
      if (bug03Precondition.errors.length > 0) {
        bug03Precondition.precondition_status = "FAIL";
        bug03Precondition.failure_type = "HARNESS_SETUP_FAIL";
      }
      await attachJson(
        testInfo,
        "batch1-bug03-precondition",
        {
          ...bug03Precondition,
          precondition_report: {
            url: page.url(),
            task_id: collapseTaskId,
            stage: collapseStage,
            substage: collapseSubstage,
            right_panel_state: rightPanelStateBefore,
            message_count: beforeCollapse.message_count,
            latest_message_text_summary: summarizeMessageForReport(beforeCollapse.latest_message_text),
          },
        },
        "gates",
      );
      await screenshot(page, testInfo, "batch1-bug03-before-collapse");

      if (bug03Precondition.precondition_status !== "PASS") {
        batch1Recorder.record({
          id: BATCH1_CASE_INTERACTION_COLLAPSE,
          bug: "Bug-03",
          passed: false,
          description: "Preconditions failed, cannot verify context preservation after collapse/expand.",
          failure_type: "HARNESS_SETUP_FAIL",
          precondition: bug03Precondition,
          actual: beforeCollapse,
          expected: { message_count: ">0", latest_message_text: "non-empty" },
        });
      } else {
        try {
          await page.getByTestId("toggle-right-panel").click();
          let railVisible = false;
          try {
            await page.getByTestId("right-panel-collapsed").waitFor({ state: "visible", timeout: 5_000 });
            railVisible = await page.getByTestId("right-panel-collapsed").isVisible();
          } catch {
            railVisible = false;
          }
          await screenshot(page, testInfo, "batch1-bug03-collapsed-rail");

          await page.getByTestId("toggle-right-panel").click();
          let expandedVisible = false;
          try {
            await page.locator(".wb-interaction-messages").waitFor({ state: "visible", timeout: 5_000 });
            expandedVisible = (await page.locator(".wb-interaction-messages").count()) > 0;
          } catch {
            expandedVisible = false;
          }
          await page.waitForTimeout(250);

          const afterCollapse = await collectInteractionPanelContext(page);
          const latestMessageStillExists = afterCollapse.message_texts.includes(beforeCollapse.latest_message_text);
          const messageOrderPreserved =
            beforeCollapse.message_texts.length === afterCollapse.message_texts.length &&
            beforeCollapse.message_texts.every((text, index) => afterCollapse.message_texts[index] === text);
          const scrollDelta = Math.abs(afterCollapse.scroll_top - beforeCollapse.scroll_top);
          const scrollPreserved = scrollDelta <= 140;
          const passed = railVisible && expandedVisible && latestMessageStillExists && messageOrderPreserved && scrollPreserved;

          batch1Recorder.record({
            id: BATCH1_CASE_INTERACTION_COLLAPSE,
            bug: "Bug-03",
            passed,
            description:
              "Right panel collapse shows rail; after expand, latest message exists, order is preserved, and scroll offset stays within threshold.",
            failure_type: passed ? "NONE" : "PRODUCT_GATE_FAIL",
            precondition: bug03Precondition,
            actual: {
              rail_visible: railVisible,
              expanded_visible: expandedVisible,
              latest_message_still_exists: latestMessageStillExists,
              message_order_preserved: messageOrderPreserved,
              scroll_delta: scrollDelta,
              beforeCollapse,
              afterCollapse,
            },
            expected: {
              rail_visible: true,
              expanded_visible: true,
              latest_message_still_exists: true,
              message_order_preserved: true,
              scroll_delta_lte: 140,
            },
          });
        } catch (error) {
          batch1Recorder.record({
            id: BATCH1_CASE_INTERACTION_COLLAPSE,
            bug: "Bug-03",
            passed: false,
            description: "Collapse/expand interaction failed due to runtime or infra instability.",
            failure_type: "INFRA_FAIL",
            precondition: bug03Precondition,
            actual: { error: error instanceof Error ? error.message : String(error) },
            expected: { collapse_expand_flow: "stable" },
          });
        }
      }

      reporter.record(evaluateDedupGate01(dedup));
      reporter.record(evaluateCenterArtifactGate(centerArtifact));
      reporter.record(evaluateHistorySubstateGate(historySubstate, historySubPrecondition));

      await prepareWorkbench(page, runningTaskId, "history", "both", HIGH_RISK_VIEWPORT);
      const bannerMetrics = await collectBannerMetrics(page);
      await attachJson(testInfo, "ux-gate-banner-stack-metrics", bannerMetrics, "dom-metrics");
      await screenshot(page, testInfo, "ux-gate-history-both-1280x720");
      reporter.record(evaluateBannerStackGate(bannerMetrics));

      for (const viewport of S17_VIEWPORTS) {
        for (const mode of MATRIX_MODES) {
          for (const sidebar of SIDEBAR_STATES) {
            const taskId = mode === "completed" ? completedTaskId : runningTaskId;
            const taskSnapshot = mode === "completed" ? completedTask : runningTask;
            const effectiveMode: WorkbenchMode = mode === "replay" ? "current" : mode;
            if (effectiveMode === "history-sub") {
              const rowPrecondition = await establishHistorySubScenario(page, session.request, API_BASE_URL, {
                scenarioId: `${S17_HISTORY_SUB_SCENARIO_ID}-matrix`,
                taskId,
                viewport,
                sidebar,
                taskSnapshot,
              });
              matrixRows.push({
                viewport: viewport.label,
                mode,
                sidebar,
                taskId,
                scenario: rowPrecondition,
                timeline: sidebar === "both" || sidebar === "right-only" ? await collectTimelineMetrics(page) : null,
                foot: sidebar === "both" || sidebar === "right-only"
                  ? await collectInputFootMetrics(page, viewport.height)
                  : null,
                banners: await collectBannerMetrics(page),
                centerArtifact: await collectCenterArtifactMetrics(page),
                semantic: sidebar === "both" ? evaluateSemanticDedup(await collectSemanticRegions(page)) : null,
                historySubstate: await collectHistorySubstateMetrics(page),
                screenshot: await saveMatrixScreenshot(
                  page,
                  `workbench_${mode}_${sidebar}_${viewport.label}`,
                ),
              });
              continue;
            }

            await prepareWorkbench(page, taskId, effectiveMode, sidebar, viewport);

            const row = {
              viewport: viewport.label,
              mode,
              sidebar,
              taskId,
              timeline: await collectTimelineMetrics(page),
              foot: sidebar === "both" || sidebar === "right-only"
                ? await collectInputFootMetrics(page, viewport.height)
                : null,
              banners: await collectBannerMetrics(page),
              centerArtifact: await collectCenterArtifactMetrics(page),
              semantic: sidebar === "both" ? evaluateSemanticDedup(await collectSemanticRegions(page)) : null,
              historySubstate: null,
              screenshot: await saveMatrixScreenshot(
                page,
                `workbench_${mode}_${sidebar}_${viewport.label}`,
              ),
            };
            matrixRows.push(row);
          }
        }
      }

      const gateReportFile = await reporter.writeReport({
        domMetricsFile,
        matrixRowCount: matrixRows.length,
        note: "S17 gates require explicit history-sub scenario setup; failures are attributed by failure_type.",
      });
      const matrixFile = await writeMatrixReport(matrixRows, {
        viewports: S17_VIEWPORTS.map((item) => item.label),
        modes: MATRIX_MODES,
        sidebars: SIDEBAR_STATES,
        highRiskGateDomMetrics: domMetricsFile,
        historySubScenarioId: S17_HISTORY_SUB_SCENARIO_ID,
      });

      await attachJson(testInfo, "uiux-s17-gates", reporter.list(), "gates");
      await attachJson(testInfo, "uiux-s17-matrix-summary", { rowCount: matrixRows.length, matrixFile }, "uiux-matrix");
      await attachJson(testInfo, "uiux-s17-gate-summary", reporter.summary(), "gates");
      await batch1Recorder.attach(testInfo, "batch1-s17-cases");

      expect(reporter.summary()["UX-GATE-TIMELINE-1280"], "S17 gate snapshot").toBeDefined();
      expect(reporter.summary()["UX-GATE-HISTORY-SUBSTATE"]).toBe("PASS");
      expect(batch1Recorder.summary()[BATCH1_CASE_INTERACTION_MIN]).toBeDefined();
      expect(batch1Recorder.summary()[BATCH1_CASE_INTERACTION_COLLAPSE]).toBeDefined();
      expect(matrixRows.length).toBeGreaterThan(0);
      expect(gateReportFile).toContain("uiux-s17-gates.json");
      expect(matrixFile).toContain("uiux-s17-matrix.json");
      expect(RUN_DIR.length).toBeGreaterThan(0);
    } finally {
      await evidence.flush();
      await disposeDevSession(session);
    }
  });
});
