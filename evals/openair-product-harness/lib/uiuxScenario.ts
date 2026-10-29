import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { readTasks } from "./gates";
import { setSidebarState, type SidebarState, type ViewportSpec } from "./uiuxMetrics";

export const S17_HISTORY_SUB_SCENARIO_ID = "S17-UX-GATE-HISTORY-SUB-EXPERIMENT-NONCURRENT";
export const S17_RUNNING_TASK_ID = "task_cs_ope_001";

export type FailureType = "NONE" | "HARNESS_SETUP_FAIL" | "PRODUCT_GATE_FAIL" | "INFRA_FAIL";

export type ScenarioPreconditionReport = {
  scenario_id: string;
  task_id: string;
  expected_stage: string;
  actual_stage: string;
  expected_substage: string;
  actual_substage: string;
  expected_sidebar_state: SidebarState;
  actual_sidebar_state: SidebarState;
  viewport: string;
  precondition_status: "PASS" | "FAIL";
  failure_type: FailureType;
  errors: string[];
  target_page_phase_label: string;
  target_substage_key: string;
  target_substage_label: string;
  active_substage_key: string;
  active_substage_label: string;
  has_local_browsing_hint: boolean;
  has_historical_sub_tag: boolean;
};

const UNDERLYING_TO_PAGE_PHASE: Record<string, string> = {
  problem_modeling: "问题",
  solution_design: "方法",
  execution_validation: "实验",
  insight_generation: "报告",
};

const SUB_STAGE_UI_LABELS: Record<string, string> = {
  literature_review: "文献调研",
  question_formulation: "问题凝练",
  hypothesis_construction: "假说构建",
  experiment_design: "实验设计",
  task_planning: "任务规划",
  code_implementation: "代码实现",
  experiment_execution: "实验执行",
  result_monitoring: "结果监控",
  result_analysis: "结果分析",
  insight_synthesis: "结论提炼",
  report_writing: "报告生成",
};

export type HistorySubTarget = {
  scenarioId: string;
  taskId: string;
  stageKey: string;
  pagePhaseLabel: string;
  targetSubStageKey: string;
  targetSubStageLabel: string;
  activeSubStageKey: string;
  activeSubStageLabel: string;
};

export function subStageUiLabel(subStageKey: string): string {
  return SUB_STAGE_UI_LABELS[subStageKey] ?? subStageKey;
}

export function pagePhaseLabelForStage(stageKey: string): string {
  return UNDERLYING_TO_PAGE_PHASE[stageKey] ?? stageKey;
}

export function resolveHistorySubTargetFromTask(task: Record<string, unknown>, scenarioId = S17_HISTORY_SUB_SCENARIO_ID): HistorySubTarget | null {
  const taskId = String(task.taskId ?? "");
  const currentStage = String(task.currentStage ?? "");
  const stageDetails = (task.stageDetails ?? []) as Array<Record<string, unknown>>;
  const stageDetail = stageDetails.find((row) => row.stageKey === currentStage);
  const subStages = (stageDetail?.subStages ?? []) as Array<Record<string, unknown>>;
  if (!taskId || !currentStage || subStages.length < 2) return null;

  const active =
    subStages.find((sub) => sub.status === "running")
    ?? subStages.filter((sub) => sub.status === "completed").at(-1)
    ?? subStages[0];
  const activeSubStageKey = String(active.subStage ?? "");
  const target = subStages.find((sub) => String(sub.subStage ?? "") !== activeSubStageKey);
  if (!target) return null;

  const targetSubStageKey = String(target.subStage ?? "");
  return {
    scenarioId,
    taskId,
    stageKey: currentStage,
    pagePhaseLabel: pagePhaseLabelForStage(currentStage),
    targetSubStageKey,
    targetSubStageLabel: subStageUiLabel(targetSubStageKey),
    activeSubStageKey,
    activeSubStageLabel: subStageUiLabel(activeSubStageKey),
  };
}

export async function clearWorkbenchPersistedNav(page: Page, taskId: string): Promise<void> {
  const storageKey = `openair.workbench.v1:${taskId}`;
  await page.addInitScript((key) => {
    localStorage.removeItem(key);
  }, storageKey);
}

export async function collectWorkbenchScenarioState(page: Page): Promise<{
  currentStageLabel: string;
  viewingPhaseLabel: string;
  selectedSubStageLabel: string;
  sidebarState: SidebarState;
  hasHistoricalSubTag: boolean;
  hasLocalBrowsingHint: boolean;
}> {
  return page.evaluate(() => {
    const metaLabels = [...document.querySelectorAll(".wb-meta-label")];
    const currentStageLabel = metaLabels
      .find((node) => node.textContent?.trim() === "当前阶段")
      ?.nextElementSibling?.textContent?.replace(/\s+/g, " ").trim() ?? "";
    const viewingPhaseLabel =
      document.querySelector(".wb-flow-phase-btn.wb-flow-phase-btn--active .wb-flow-phase-label")?.textContent?.replace(/\s+/g, " ").trim() ?? "";
    const selectedSubStageLabel =
      document.querySelector(".wb-flow-sub-btn.wb-flow-sub-btn--active .wb-flow-sub-label")?.textContent?.replace(/\s+/g, " ").trim() ?? "";
    const leftCollapsed = Boolean(document.querySelector('[data-testid="left-panel-collapsed"]'));
    const rightCollapsed = Boolean(document.querySelector('[data-testid="right-panel-collapsed"]'));
    let sidebarState: SidebarState = "both";
    if (leftCollapsed && rightCollapsed) sidebarState = "collapsed";
    else if (leftCollapsed) sidebarState = "right-only";
    else if (rightCollapsed) sidebarState = "left-only";

    const bodyText = document.body.textContent ?? "";
    const hasHistoricalSubTag = bodyText.includes("二级节点浏览");
    const localAlerts = [...document.querySelectorAll(".wb-main-alert, .ant-alert")].filter((element) => {
      if (element.matches('[data-testid="replay-disclosure"]')) return false;
      const rect = element.getBoundingClientRect();
      return rect.height > 0 && rect.width > 0;
    });
    const hasLocalBrowsingHint = localAlerts.some((element) => {
      const message = (element.querySelector(".ant-alert-message")?.textContent ?? element.textContent ?? "").replace(/\s+/g, " ").trim();
      return /历史阶段|历史浏览|浏览模式|所选.*节点|二级节点|子节点|局部浏览/.test(message);
    });

    return {
      currentStageLabel,
      viewingPhaseLabel,
      selectedSubStageLabel,
      sidebarState,
      hasHistoricalSubTag,
      hasLocalBrowsingHint,
    };
  });
}

export async function navigateToHistorySubStage(page: Page, target: HistorySubTarget): Promise<void> {
  const phaseButton = page.locator(".wb-flow-phase-btn").filter({ hasText: target.pagePhaseLabel });
  await expect(phaseButton, `Phase button "${target.pagePhaseLabel}" must exist`).toBeVisible({ timeout: 15_000 });
  await phaseButton.click();
  const subList = page.locator(".wb-flow-phase.wb-flow-phase--open .wb-flow-sub-list").first();
  await expect(subList, `Sub-stage list for "${target.pagePhaseLabel}" must open`).toBeVisible({ timeout: 10_000 });
  const subButton = subList.locator(".wb-flow-sub-btn").filter({ hasText: target.targetSubStageLabel });
  await expect(subButton, `Sub-stage "${target.targetSubStageLabel}" must exist`).toBeVisible({ timeout: 10_000 });
  await subButton.click();
  await expect(page.getByText("二级节点浏览", { exact: true })).toBeVisible({ timeout: 15_000 });
}

async function waitForWorkbenchStable(page: Page): Promise<void> {
  await expect(page.getByTestId("workbench-center")).toBeVisible({ timeout: 20_000 });
  await page.waitForTimeout(300);
}

export async function establishHistorySubScenario(
  page: Page,
  request: APIRequestContext,
  apiBaseUrl: string,
  options: {
    scenarioId?: string;
    taskId?: string;
    viewport: ViewportSpec;
    sidebar: SidebarState;
    taskSnapshot?: Record<string, unknown>;
  },
): Promise<ScenarioPreconditionReport> {
  const scenarioId = options.scenarioId ?? S17_HISTORY_SUB_SCENARIO_ID;
  const taskId = options.taskId ?? S17_RUNNING_TASK_ID;
  const errors: string[] = [];

  const task = options.taskSnapshot
    ?? (await readTasks(request, apiBaseUrl)).find((item) => item.taskId === taskId);
  if (!task) {
    return {
      scenario_id: scenarioId,
      task_id: taskId,
      expected_stage: "experiment/current running phase",
      actual_stage: "missing-task",
      expected_substage: "non-current substage in current phase",
      actual_substage: "missing-task",
      expected_sidebar_state: options.sidebar,
      actual_sidebar_state: "collapsed",
      viewport: options.viewport.label,
      precondition_status: "FAIL",
      failure_type: "HARNESS_SETUP_FAIL",
      errors: [`Task ${taskId} not found in API`],
      target_page_phase_label: "",
      target_substage_key: "",
      target_substage_label: "",
      active_substage_key: "",
      active_substage_label: "",
      has_local_browsing_hint: false,
      has_historical_sub_tag: false,
    };
  }

  const target = resolveHistorySubTargetFromTask(task, scenarioId);
  if (!target) {
    return {
      scenario_id: scenarioId,
      task_id: taskId,
      expected_stage: pagePhaseLabelForStage(String(task.currentStage ?? "")),
      actual_stage: String(task.currentStage ?? ""),
      expected_substage: "non-current substage in current phase",
      actual_substage: "unresolved",
      expected_sidebar_state: options.sidebar,
      actual_sidebar_state: "collapsed",
      viewport: options.viewport.label,
      precondition_status: "FAIL",
      failure_type: "HARNESS_SETUP_FAIL",
      errors: [`Unable to resolve a non-current substage for task ${taskId}`],
      target_page_phase_label: "",
      target_substage_key: "",
      target_substage_label: "",
      active_substage_key: "",
      active_substage_label: "",
      has_local_browsing_hint: false,
      has_historical_sub_tag: false,
    };
  }

  await clearWorkbenchPersistedNav(page, taskId);
  await page.setViewportSize({ width: options.viewport.width, height: options.viewport.height });
  await page.goto(`/workbench/${taskId}`, { waitUntil: "commit" });
  await waitForWorkbenchStable(page);

  try {
    await navigateToHistorySubStage(page, target);
  } catch (error) {
    errors.push(`Navigation failed: ${error instanceof Error ? error.message : String(error)}`);
  }

  await setSidebarState(page, options.sidebar);
  await waitForWorkbenchStable(page);

  const state = await collectWorkbenchScenarioState(page);
  if (state.sidebarState !== options.sidebar) {
    errors.push(`Sidebar state expected ${options.sidebar}, actual ${state.sidebarState}`);
  }
  if (state.viewingPhaseLabel !== target.pagePhaseLabel) {
    errors.push(`Viewing phase expected "${target.pagePhaseLabel}", actual "${state.viewingPhaseLabel}"`);
  }
  if (state.selectedSubStageLabel !== target.targetSubStageLabel) {
    errors.push(`Selected substage expected "${target.targetSubStageLabel}", actual "${state.selectedSubStageLabel}"`);
  }
  if (!state.hasHistoricalSubTag) {
    errors.push('Historical sub-stage tag "二级节点浏览" not visible');
  }
  if (!state.hasLocalBrowsingHint) {
    errors.push("Local browsing hint banner not visible");
  }

  const preconditionStatus = errors.length === 0 ? "PASS" : "FAIL";
  return {
    scenario_id: scenarioId,
    task_id: taskId,
    expected_stage: target.pagePhaseLabel,
    actual_stage: state.viewingPhaseLabel || state.currentStageLabel,
    expected_substage: target.targetSubStageLabel,
    actual_substage: state.selectedSubStageLabel,
    expected_sidebar_state: options.sidebar,
    actual_sidebar_state: state.sidebarState,
    viewport: options.viewport.label,
    precondition_status: preconditionStatus,
    failure_type: preconditionStatus === "PASS" ? "NONE" : "HARNESS_SETUP_FAIL",
    errors,
    target_page_phase_label: target.pagePhaseLabel,
    target_substage_key: target.targetSubStageKey,
    target_substage_label: target.targetSubStageLabel,
    active_substage_key: target.activeSubStageKey,
    active_substage_label: target.activeSubStageLabel,
    has_local_browsing_hint: state.hasLocalBrowsingHint,
    has_historical_sub_tag: state.hasHistoricalSubTag,
  };
}
