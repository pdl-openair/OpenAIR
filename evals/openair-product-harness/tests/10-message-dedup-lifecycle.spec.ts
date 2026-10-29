import { expect, test } from "@playwright/test";
import { disposeDevSession, prepareDevHarnessAuth } from "../lib/auth";
import { Batch1CaseRecorder, type Batch1FailureType, type Batch1PreconditionReport } from "../lib/batch1CaseGates";
import { API_BASE_URL } from "../lib/config";
import { attachJson, collectPageEvidence, screenshot } from "../lib/evidence";
import { readTasks } from "../lib/gates";

const RUNNING_TASK_ID = "task_cs_ope_001";
const AWAITING_TASK_ID = "task_cs_ope_002";
const TODO_MESSAGE_ID_SELECTOR = "TODO_VERIFY_SELECTOR: message-id-testid";

type InteractionSnapshot = {
  userTexts: string[];
  assistantTexts: string[];
  traceTexts: string[];
  timelineItemCount: number;
  totalRows: number;
};

async function collectInteractionSnapshot(page: import("@playwright/test").Page): Promise<InteractionSnapshot> {
  return page.evaluate(() => {
    const normalize = (value: string | null | undefined) => (value ?? "").replace(/\s+/g, " ").trim();
    const userRows = Array.from(document.querySelectorAll(".wb-interaction-messages .wb-bubble-row--user"));
    const assistantRows = Array.from(document.querySelectorAll(".wb-interaction-messages .wb-bubble-row--assistant"));
    const traceRows = Array.from(document.querySelectorAll(".wb-interaction-trace > *"));
    const userTexts = userRows.map((row) => normalize((row as HTMLElement).innerText || row.textContent)).filter((text) => text.length > 0);
    const assistantTexts = assistantRows.map((row) => normalize((row as HTMLElement).innerText || row.textContent)).filter((text) => text.length > 0);
    const traceTexts = traceRows
      .map((row) => normalize((row as HTMLElement).innerText || row.textContent))
      .filter((text) => text.length > 0);
    return {
      userTexts,
      assistantTexts,
      traceTexts,
      timelineItemCount: traceRows.length,
      totalRows: userRows.length + assistantRows.length,
    };
  });
}

function countExact(texts: string[], target: string): number {
  return texts.reduce((acc, text) => (text === target ? acc + 1 : acc), 0);
}

function countContains(texts: string[], target: string): number {
  return texts.reduce((acc, text) => (text.includes(target) ? acc + 1 : acc), 0);
}

async function waitUntil(
  page: import("@playwright/test").Page,
  predicate: () => Promise<boolean>,
  timeoutMs: number,
  intervalMs = 500,
): Promise<boolean> {
  const startedAt = Date.now();
  while (Date.now() - startedAt <= timeoutMs) {
    if (await predicate()) return true;
    await page.waitForTimeout(intervalMs);
  }
  return false;
}

function productOrSetupFailure(preconditionPassed: boolean, passed: boolean): Batch1FailureType {
  if (!preconditionPassed) return "HARNESS_SETUP_FAIL";
  return passed ? "NONE" : "PRODUCT_GATE_FAIL";
}

test.describe("Batch 1 / Bug-15: message-dedup-lifecycle", () => {
  test("B1-MESSAGE-DEDUP-LIFECYCLE: unique text count + refresh persistence", async ({ page, playwright }, testInfo) => {
    const evidence = collectPageEvidence(page, testInfo);
    const recorder = new Batch1CaseRecorder();
    const session = await prepareDevHarnessAuth(playwright, page);

    try {
      const tasks = await readTasks(session.request, API_BASE_URL);
      const runningTask = tasks.find((task) => String(task.taskId ?? "") === RUNNING_TASK_ID);
      const awaitingTask = tasks.find((task) => String(task.taskId ?? "") === AWAITING_TASK_ID);

      const precondition: Batch1PreconditionReport = {
        scenario_id: "message-dedup-lifecycle",
        task_id: RUNNING_TASK_ID,
        target_url: `/workbench/${RUNNING_TASK_ID}`,
        viewport: "1280x720",
        precondition_status: "PASS",
        failure_type: "NONE",
        errors: [],
        notes: [
          TODO_MESSAGE_ID_SELECTOR,
          "using unique-text counting strategy before message-id selector is verified",
        ],
      };
      if (!runningTask || runningTask.status !== "running") {
        precondition.errors.push("task_cs_ope_001 must stay in status=running");
      }
      if (!awaitingTask || awaitingTask.status !== "awaiting_confirmation") {
        precondition.errors.push("task_cs_ope_002 must stay in status=awaiting_confirmation");
      }
      if (precondition.errors.length > 0) {
        precondition.precondition_status = "FAIL";
        precondition.failure_type = "HARNESS_SETUP_FAIL";
      }

      let gatePassed = false;
      const gateActual: Record<string, unknown> = { precondition_note: TODO_MESSAGE_ID_SELECTOR };
      let gateFailureType: Batch1FailureType = productOrSetupFailure(precondition.precondition_status === "PASS", gatePassed);

      if (precondition.precondition_status === "PASS") {
        try {
          await page.setViewportSize({ width: 1280, height: 720 });
          await page.goto(`/workbench/${RUNNING_TASK_ID}`, { waitUntil: "commit" });
          await page.getByTestId("workbench-center").waitFor({ state: "visible", timeout: 30_000 });

          const messageList = page.locator(".wb-interaction-messages");
          const input = page.locator("textarea.wb-interaction-input").first();
          const sendButton = page.locator(".wb-interaction-input-bar button").last();
          if ((await messageList.count()) < 1) precondition.errors.push(".wb-interaction-messages not found");
          if ((await input.count()) < 1) precondition.errors.push("textarea.wb-interaction-input not found");
          if ((await sendButton.count()) < 1) precondition.errors.push("send button not found");
          if (precondition.errors.length > 0) {
            precondition.precondition_status = "FAIL";
            precondition.failure_type = "HARNESS_SETUP_FAIL";
          }
          await attachJson(testInfo, "batch1-bug15-precondition", precondition, "gates");

          if (precondition.precondition_status === "PASS") {
            const uniqueUserText = `B1-DEDUP-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
            const before = await collectInteractionSnapshot(page);
            gateActual.before = before;
            gateActual.unique_user_text = uniqueUserText;

            await input.fill(uniqueUserText);
            await sendButton.click();
            await screenshot(page, testInfo, "batch1-bug15-after-send-click");

            const userAppeared = await waitUntil(
              page,
              async () => {
                const snapshot = await collectInteractionSnapshot(page);
                const containsInUserBubble = countContains(snapshot.userTexts, uniqueUserText);
                return containsInUserBubble >= 1;
              },
              40_000,
            );
            const assistantAdvanced = await waitUntil(
              page,
              async () => {
                const snapshot = await collectInteractionSnapshot(page);
                return snapshot.assistantTexts.length - before.assistantTexts.length >= 1;
              },
              90_000,
            );
            await sendButton.waitFor({ state: "visible", timeout: 90_000 });

            const afterSend = await collectInteractionSnapshot(page);
            const userUniqueCountAfterSend = countContains(afterSend.userTexts, uniqueUserText);
            const userTraceContainsAfterSend = countContains(afterSend.traceTexts, uniqueUserText);
            const assistantVerificationSource = "assistant-bubble";
            const assistantDeltaAfterSend = afterSend.assistantTexts.length - before.assistantTexts.length;
            const latestAssistantTextAfterSend = afterSend.assistantTexts.length > 0
              ? afterSend.assistantTexts[afterSend.assistantTexts.length - 1]
              : "";
            const latestAssistantTextExactCountAfterSend = latestAssistantTextAfterSend.length > 0
              ? countExact(afterSend.assistantTexts, latestAssistantTextAfterSend)
              : null;
            await screenshot(page, testInfo, "batch1-bug15-after-send-settled");

            await page.reload({ waitUntil: "commit" });
            await page.getByTestId("workbench-center").waitFor({ state: "visible", timeout: 30_000 });
            await messageList.waitFor({ state: "visible", timeout: 15_000 });
            const afterRefresh = await collectInteractionSnapshot(page);
            const userUniqueCountAfterRefresh = countContains(afterRefresh.userTexts, uniqueUserText);
            const userTraceContainsAfterRefresh = countContains(afterRefresh.traceTexts, uniqueUserText);
            const assistantDeltaAfterRefresh = afterRefresh.assistantTexts.length - before.assistantTexts.length;
            const assistantDeltaRefreshVsSend = afterRefresh.assistantTexts.length - afterSend.assistantTexts.length;
            const latestAssistantTextAfterRefresh = afterRefresh.assistantTexts.length > 0
              ? afterRefresh.assistantTexts[afterRefresh.assistantTexts.length - 1]
              : "";
            const latestAssistantTextAfterSendCountAfterRefresh = latestAssistantTextAfterSend.length > 0
              ? countExact(afterRefresh.assistantTexts, latestAssistantTextAfterSend)
              : null;
            const latestAssistantTextExactCountAfterRefresh = latestAssistantTextAfterRefresh.length > 0
              ? countExact(afterRefresh.assistantTexts, latestAssistantTextAfterRefresh)
              : null;
            await screenshot(page, testInfo, "batch1-bug15-after-refresh");

            gateActual.after_send = afterSend;
            gateActual.after_refresh = afterRefresh;
            gateActual.user_appeared_within_timeout = userAppeared;
            gateActual.assistant_advanced_within_timeout = assistantAdvanced;
            gateActual.assistant_verification_source = assistantVerificationSource;
            gateActual.user_trace_contains_after_send = userTraceContainsAfterSend;
            gateActual.user_trace_contains_after_refresh = userTraceContainsAfterRefresh;
            gateActual.assistant_count_strategy = {
              before_count: before.assistantTexts.length,
              after_send_count: afterSend.assistantTexts.length,
              after_refresh_count: afterRefresh.assistantTexts.length,
              delta_after_send: "after_send_count - before_count",
              delta_after_refresh: "after_refresh_count - before_count",
              delta_refresh_vs_send: "after_refresh_count - after_send_count",
              selector: ".wb-bubble-row--assistant",
            };
            gateActual.user_unique_count_after_send = userUniqueCountAfterSend;
            gateActual.user_unique_count_after_refresh = userUniqueCountAfterRefresh;
            gateActual.assistant_delta_after_send = assistantDeltaAfterSend;
            gateActual.assistant_delta_after_refresh = assistantDeltaAfterRefresh;
            gateActual.assistant_delta_refresh_vs_send = assistantDeltaRefreshVsSend;
            gateActual.latest_assistant_text_summary = latestAssistantTextAfterSend.slice(0, 100);
            gateActual.latest_assistant_text_after_refresh_summary = latestAssistantTextAfterRefresh.slice(0, 100);
            gateActual.latest_assistant_text_exact_count_after_send = latestAssistantTextExactCountAfterSend;
            gateActual.latest_assistant_text_after_send_count_after_refresh = latestAssistantTextAfterSendCountAfterRefresh;
            gateActual.latest_assistant_text_exact_count_after_refresh = latestAssistantTextExactCountAfterRefresh;

            if (!userAppeared || userUniqueCountAfterSend < 1) {
              gateFailureType = "HARNESS_SETUP_FAIL";
              gateActual.setup_issue = "user bubble not observable";
            } else if (
              !assistantAdvanced
              || assistantDeltaAfterSend < 1
              || latestAssistantTextAfterSend.length < 1
              || latestAssistantTextAfterRefresh.length < 1
            ) {
              gateFailureType = "HARNESS_SETUP_FAIL";
              gateActual.setup_issue = "assistant bubble not observable";
            } else {
              const assistantNoDupOnRefresh = assistantDeltaRefreshVsSend <= 0;
              const assistantSingleByTextAfterSend = latestAssistantTextAfterSend.length > 0
                ? latestAssistantTextExactCountAfterSend === 1
                : true;
              const assistantSingleByTextAfterRefresh = latestAssistantTextAfterSend.length > 0
                ? latestAssistantTextExactCountAfterRefresh === 1
                : true;

              gatePassed = (
                userUniqueCountAfterSend === 1
                && userUniqueCountAfterRefresh === 1
                && assistantNoDupOnRefresh
                && assistantSingleByTextAfterSend
                && assistantSingleByTextAfterRefresh
              );
              gateFailureType = productOrSetupFailure(true, gatePassed);
            }
          } else {
            gateFailureType = "HARNESS_SETUP_FAIL";
          }
        } catch (error) {
          gateFailureType = "INFRA_FAIL";
          gateActual.error = error instanceof Error ? error.message : String(error);
        }
      } else {
        gateFailureType = "HARNESS_SETUP_FAIL";
        await attachJson(testInfo, "batch1-bug15-precondition", precondition, "gates");
      }

      recorder.record({
        id: "B1-MESSAGE-DEDUP-LIFECYCLE",
        bug: "Bug-15",
        passed: gatePassed && precondition.precondition_status === "PASS",
        description: "User message and assistant reply remain de-duplicated after send and after refresh.",
        failure_type: gateFailureType,
        precondition,
        actual: gateActual,
        expected: {
          user_unique_count_after_send: 1,
          user_unique_count_after_refresh: 1,
          assistant_delta_after_send_gte: 1,
          assistant_delta_refresh_vs_send_lte: 0,
          latest_assistant_text_exact_count_after_send: 1,
          latest_assistant_text_exact_count_after_refresh: 1,
          latest_assistant_text_after_send_count_after_refresh_lte: 1,
          todo_verify_selector: TODO_MESSAGE_ID_SELECTOR,
        },
      });
    } finally {
      await recorder.attach(testInfo, "batch1-bug15-gates");
      await evidence.flush();
      await disposeDevSession(session);
    }
  });
});
