import { test } from "@playwright/test";
import { disposeDevSession, prepareDevHarnessAuth } from "../lib/auth";
import {
  Batch1CaseRecorder,
  type Batch1FailureType,
  type Batch1PreconditionReport
} from "../lib/batch1CaseGates";
import { API_BASE_URL } from "../lib/config";
import { attachJson, collectPageEvidence, screenshot } from "../lib/evidence";
import { readTasks } from "../lib/gates";

const VIEWPORTS = [
  { width: 1280, height: 720, label: "1280x720" },
  { width: 1440, height: 900, label: "1440x900" }
] as const;

const STABLE_RUNNING_TASK_ID = "task_cs_ope_001";
const STABLE_AWAITING_TASK_ID = "task_cs_ope_002";
const TODO_BOARD_EMPTY_PRIMARY_CTA =
  "TODO_VERIFY_SELECTOR: board-empty-primary-cta-testid";
const NEW_TASK_BUTTON_NAME = /\u65b0\u5efa\u4efb\u52a1/;
const NEW_TASK_DIALOG_NAME = /\u65b0\u5efa\u4efb\u52a1/;
const CANCEL_BUTTON_NAME = /\u53d6\u6d88/;
const EMPTY_HINT_TEXT = /\u6682\u65e0\u4efb\u52a1/;

function productOrSetupFailure(
  preconditionPassed: boolean,
  passed: boolean
): Batch1FailureType {
  if (!preconditionPassed) return "HARNESS_SETUP_FAIL";
  return passed ? "NONE" : "PRODUCT_GATE_FAIL";
}

async function closeCreateTaskModal(
  page: import("@playwright/test").Page
): Promise<boolean> {
  const modalWrap = page.locator(".ant-modal-wrap").first();
  const hasModal = (await modalWrap.count()) > 0;
  if (!hasModal) return true;

  const cancelButton = page
    .getByRole("button", { name: CANCEL_BUTTON_NAME })
    .first();
  if (
    (await cancelButton.count()) > 0 &&
    (await cancelButton.isVisible().catch(() => false))
  ) {
    await cancelButton.click().catch(() => undefined);
  } else {
    await page.keyboard.press("Escape").catch(() => undefined);
    const closeButton = page.locator(".ant-modal-close").first();
    if (
      (await closeButton.count()) > 0 &&
      (await closeButton.isVisible().catch(() => false))
    ) {
      await closeButton.click().catch(() => undefined);
    }
  }
  await modalWrap
    .waitFor({ state: "hidden", timeout: 10_000 })
    .catch(() => undefined);
  return !(await modalWrap.isVisible().catch(() => false));
}

test.describe("Batch 1 / Bug-04: new-task-cta-discoverability", () => {
  test("B1-NEW-TASK-CTA-DISCOVERABILITY: board first screen + filtered empty state", async ({
    page,
    playwright
  }, testInfo) => {
    const evidence = collectPageEvidence(page, testInfo);
    const recorder = new Batch1CaseRecorder();
    const session = await prepareDevHarnessAuth(playwright, page);

    try {
      const tasks = await readTasks(session.request, API_BASE_URL);
      const fixtureErrors: string[] = [];
      const runningTask = tasks.find(
        task => String(task.taskId ?? "") === STABLE_RUNNING_TASK_ID
      );
      const awaitingTask = tasks.find(
        task => String(task.taskId ?? "") === STABLE_AWAITING_TASK_ID
      );
      if (!runningTask || runningTask.status !== "running") {
        fixtureErrors.push("task_cs_ope_001 must stay in status=running");
      }
      if (!awaitingTask || awaitingTask.status !== "awaiting_confirmation") {
        fixtureErrors.push(
          "task_cs_ope_002 must stay in status=awaiting_confirmation"
        );
      }

      for (const viewport of VIEWPORTS) {
        await page.setViewportSize({
          width: viewport.width,
          height: viewport.height
        });
        await page.goto("/workbench", { waitUntil: "commit" });
        await page
          .getByTestId("app-shell-scroll")
          .waitFor({ state: "visible", timeout: 30_000 });

        const cta = page
          .getByRole("button", { name: NEW_TASK_BUTTON_NAME })
          .first();
        const firstScreenPrecondition: Batch1PreconditionReport = {
          scenario_id: "new-task-cta-discoverability-first-screen",
          target_url: "/workbench",
          viewport: viewport.label,
          precondition_status: "PASS",
          failure_type: "NONE",
          errors: [...fixtureErrors]
        };
        if ((await cta.count()) < 1) {
          firstScreenPrecondition.errors.push(
            "board first-screen CTA button[name='新建任务'] not found"
          );
        }
        if (firstScreenPrecondition.errors.length > 0) {
          firstScreenPrecondition.precondition_status = "FAIL";
          firstScreenPrecondition.failure_type = "HARNESS_SETUP_FAIL";
        }
        await attachJson(
          testInfo,
          `batch1-bug04-precondition-first-screen-${viewport.label}`,
          firstScreenPrecondition,
          "gates"
        );

        let firstScreenPassed = false;
        let firstScreenFailureType: Batch1FailureType = productOrSetupFailure(
          firstScreenPrecondition.precondition_status === "PASS",
          firstScreenPassed
        );
        const firstScreenActual: Record<string, unknown> = {};
        if (firstScreenPrecondition.precondition_status === "PASS") {
          try {
            const visible = await cta.isVisible();
            const box = visible ? await cta.boundingBox() : null;
            const enabled = await cta.isEnabled();
            const withinViewport = Boolean(
              box && box.y >= 0 && box.y + box.height <= viewport.height
            );
            let createModalOpened = false;
            let createModalClosed = true;
            if (visible && enabled) {
              await cta.click();
              await page
                .getByRole("dialog", { name: NEW_TASK_DIALOG_NAME })
                .waitFor({ state: "visible", timeout: 10_000 });
              createModalOpened = true;
              createModalClosed = await closeCreateTaskModal(page);
            }
            firstScreenActual.visible = visible;
            firstScreenActual.box = box;
            firstScreenActual.enabled = enabled;
            firstScreenActual.within_viewport = withinViewport;
            firstScreenActual.create_modal_opened = createModalOpened;
            firstScreenActual.create_modal_closed = createModalClosed;
            firstScreenPassed =
              visible &&
              enabled &&
              withinViewport &&
              createModalOpened &&
              createModalClosed;
            firstScreenFailureType = productOrSetupFailure(
              true,
              firstScreenPassed
            );
            await screenshot(
              page,
              testInfo,
              `batch1-bug04-board-first-screen-${viewport.label}`
            );
          } catch (error) {
            firstScreenFailureType = "INFRA_FAIL";
            firstScreenActual.error =
              error instanceof Error ? error.message : String(error);
          }
        }

        recorder.record({
          id: `B1-NEW-TASK-CTA-FIRST-SCREEN-${viewport.label}`,
          bug: "Bug-04",
          passed:
            firstScreenPassed &&
            firstScreenPrecondition.precondition_status === "PASS",
          description:
            "On /workbench first screen, button[name='新建任务'] is visible and clickable.",
          failure_type: firstScreenFailureType,
          precondition: firstScreenPrecondition,
          actual: firstScreenActual,
          expected: {
            visible: true,
            enabled: true,
            within_viewport: true,
            create_modal_opened: true,
            create_modal_closed: true
          }
        });

        const emptyStatePrecondition: Batch1PreconditionReport = {
          scenario_id: "new-task-cta-discoverability-filtered-empty-state",
          target_url: "/workbench",
          viewport: viewport.label,
          precondition_status: "PASS",
          failure_type: "NONE",
          errors: [...fixtureErrors],
          notes: []
        };
        const tasksApiPattern = "**/api/tasks*";
        const routedTasksPayload = runningTask ? [runningTask] : [];
        let emptyStatePassed = false;
        let emptyStateFailureType: Batch1FailureType = productOrSetupFailure(
          emptyStatePrecondition.precondition_status === "PASS",
          emptyStatePassed
        );
        const emptyStateActual: Record<string, unknown> = {};
        let routeInstalled = false;
        try {
          await closeCreateTaskModal(page);
          await page.route(tasksApiPattern, async route => {
            await route.fulfill({
              status: 200,
              contentType: "application/json",
              body: JSON.stringify(routedTasksPayload)
            });
          });
          routeInstalled = true;
          await page.goto("/workbench", { waitUntil: "commit" });
          await page
            .getByTestId("app-shell-scroll")
            .waitFor({ state: "visible", timeout: 30_000 });

          const statCards = await page.evaluate(() =>
            Array.from(document.querySelectorAll(".board-stat-card")).map(
              (card, index) => {
                const numText =
                  card.querySelector(".board-stat-num")?.textContent?.trim() ??
                  "";
                const title =
                  card
                    .querySelector(".board-stat-head span")
                    ?.textContent?.trim() ?? `card-${index}`;
                const count = Number.parseInt(numText, 10);
                return { index, title, count };
              }
            )
          );
          const zeroStatCard =
            statCards.find(
              item =>
                item.index > 0 &&
                Number.isFinite(item.count) &&
                item.count === 0
            ) ?? null;
          if (!zeroStatCard) {
            emptyStatePrecondition.errors.push(
              "no zero-count board-stat-card under deterministic routed tasks payload"
            );
          }
          const emptyStatePrimaryCta = page
            .getByTestId("board-empty-primary-cta-testid")
            .first();
          const hasEmptyStatePrimaryCta =
            (await emptyStatePrimaryCta.count()) > 0;
          if (!hasEmptyStatePrimaryCta) {
            emptyStatePrecondition.notes?.push(TODO_BOARD_EMPTY_PRIMARY_CTA);
          }
          if (emptyStatePrecondition.errors.length > 0) {
            emptyStatePrecondition.precondition_status = "FAIL";
            emptyStatePrecondition.failure_type = "HARNESS_SETUP_FAIL";
          }
          await attachJson(
            testInfo,
            `batch1-bug04-precondition-empty-${viewport.label}`,
            {
              emptyStatePrecondition,
              routed_tasks_count: routedTasksPayload.length,
              stat_cards: statCards,
              zeroStatCard,
              hasEmptyStatePrimaryCta
            },
            "gates"
          );

          emptyStateActual.routed_tasks_count = routedTasksPayload.length;
          emptyStateActual.stat_cards = statCards;
          emptyStateActual.zeroStatCard = zeroStatCard;
          emptyStateActual.has_empty_state_primary_cta =
            hasEmptyStatePrimaryCta;
          if (
            emptyStatePrecondition.precondition_status === "PASS" &&
            zeroStatCard
          ) {
            await page
              .locator(".board-stat-card")
              .nth(zeroStatCard.index)
              .click();
            const emptyHint = page.getByText(EMPTY_HINT_TEXT).first();
            await emptyHint.waitFor({ state: "visible", timeout: 10_000 });
            const effectiveEmptyCta = hasEmptyStatePrimaryCta
              ? emptyStatePrimaryCta
              : cta;
            const ctaVisible = await effectiveEmptyCta.isVisible();
            const ctaEnabled = await effectiveEmptyCta.isEnabled();
            let createModalOpened = false;
            let createModalClosed = true;
            if (ctaVisible && ctaEnabled) {
              await effectiveEmptyCta.click();
              await page
                .getByRole("dialog", { name: NEW_TASK_DIALOG_NAME })
                .waitFor({ state: "visible", timeout: 10_000 });
              createModalOpened = true;
              createModalClosed = await closeCreateTaskModal(page);
            }
            emptyStateActual.empty_hint_visible = await emptyHint.isVisible();
            emptyStateActual.cta_visible = ctaVisible;
            emptyStateActual.cta_enabled = ctaEnabled;
            emptyStateActual.create_modal_opened = createModalOpened;
            emptyStateActual.create_modal_closed = createModalClosed;
            emptyStateActual.todo_verify_selector = hasEmptyStatePrimaryCta
              ? null
              : TODO_BOARD_EMPTY_PRIMARY_CTA;
            emptyStatePassed =
              Boolean(emptyStateActual.empty_hint_visible) &&
              ctaVisible &&
              ctaEnabled &&
              createModalOpened &&
              createModalClosed;
            emptyStateFailureType = productOrSetupFailure(
              true,
              emptyStatePassed
            );
            await screenshot(
              page,
              testInfo,
              `batch1-bug04-filter-empty-state-${viewport.label}`
            );
          } else if (emptyStatePrecondition.precondition_status !== "PASS") {
            emptyStateFailureType = "HARNESS_SETUP_FAIL";
          }
        } catch (error) {
          emptyStateFailureType = "INFRA_FAIL";
          emptyStateActual.error =
            error instanceof Error ? error.message : String(error);
        } finally {
          if (routeInstalled) {
            await page.unroute(tasksApiPattern);
          }
        }

        recorder.record({
          id: `B1-NEW-TASK-CTA-FILTER-EMPTY-${viewport.label}`,
          bug: "Bug-04",
          passed:
            emptyStatePassed &&
            emptyStatePrecondition.precondition_status === "PASS",
          description:
            "After filtering to empty-state, create-entry remains visible and clickable.",
          failure_type: emptyStateFailureType,
          precondition: emptyStatePrecondition,
          actual: emptyStateActual,
          expected: {
            empty_hint_visible: true,
            cta_visible: true,
            cta_enabled: true,
            create_modal_opened: true,
            create_modal_closed: true,
            todo_verify_selector_when_missing: TODO_BOARD_EMPTY_PRIMARY_CTA
          }
        });
      }
    } finally {
      await recorder.attach(testInfo, "batch1-bug04-gates");
      await evidence.flush();
      await disposeDevSession(session);
    }
  });
});
