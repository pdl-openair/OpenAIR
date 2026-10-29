import { expect, type Page } from "@playwright/test";
import fs from "node:fs/promises";
import path from "node:path";
import { RUN_DIR } from "./config";
import type { FailureType, ScenarioPreconditionReport } from "./uiuxScenario";

export type ViewportSpec = { width: number; height: number; label: string };

export const S17_VIEWPORTS: ViewportSpec[] = [
  { width: 1280, height: 720, label: "1280x720" },
  { width: 1440, height: 900, label: "1440x900" },
  { width: 1920, height: 1080, label: "1920x1080" },
];

export type SidebarState = "both" | "left-only" | "right-only" | "collapsed";

export type WorkbenchMode = "current" | "history" | "history-sub" | "replay" | "completed";

export type UiuxGateResult = {
  id: string;
  passed: boolean;
  description: string;
  scenario: string;
  actual?: unknown;
  expected?: unknown;
  evidence?: Record<string, unknown>;
  reviewType: "M" | "M+A" | "M+H";
  scenario_id?: string;
  task_id?: string;
  expected_stage?: string;
  actual_stage?: string;
  expected_substage?: string;
  actual_substage?: string;
  expected_sidebar_state?: SidebarState;
  actual_sidebar_state?: SidebarState;
  viewport?: string;
  precondition_status?: "PASS" | "FAIL";
  failure_type?: FailureType;
  precondition?: ScenarioPreconditionReport;
};

export function normalizeUiText(value: string | null | undefined): string {
  return (value ?? "").replace(/\s+/g, " ").trim();
}

export function tokenSet(text: string): Set<string> {
  const normalized = normalizeUiText(text).toLowerCase();
  if (!normalized) return new Set();
  return new Set(normalized.split(/[^\p{L}\p{N}]+/u).filter((token) => token.length >= 2));
}

export function jaccardSimilarity(a: string, b: string): number {
  const left = tokenSet(a);
  const right = tokenSet(b);
  if (left.size === 0 || right.size === 0) return 0;
  let intersection = 0;
  for (const token of left) {
    if (right.has(token)) intersection += 1;
  }
  return intersection / (left.size + right.size - intersection);
}

export function isDuplicatePair(a: string, b: string, minLength = 40, threshold = 0.35): boolean {
  const na = normalizeUiText(a);
  const nb = normalizeUiText(b);
  if (na.length <= minLength || nb.length <= minLength) return false;
  if (na.length >= 20 && nb.includes(na.slice(0, Math.min(40, na.length)))) return true;
  if (nb.length >= 20 && na.includes(nb.slice(0, Math.min(40, nb.length)))) return true;
  return jaccardSimilarity(na, nb) >= threshold;
}

export type SemanticRegions = {
  leftStageInfo: string;
  centerProgress: string;
  rightJudgment: string;
};

export type DedupEvaluation = {
  passed: boolean;
  duplicatePairs: string[];
  regions: SemanticRegions;
  similarities: Record<string, number>;
};

export function evaluateSemanticDedup(regions: SemanticRegions, minLength = 40, threshold = 0.35): DedupEvaluation {
  const pairs: Array<[string, string, string]> = [
    ["left-center", regions.leftStageInfo, regions.centerProgress],
    ["left-right", regions.leftStageInfo, regions.rightJudgment],
    ["center-right", regions.centerProgress, regions.rightJudgment],
  ];
  const similarities: Record<string, number> = {};
  const duplicatePairs: string[] = [];
  for (const [label, a, b] of pairs) {
    similarities[label] = jaccardSimilarity(a, b);
    if (isDuplicatePair(a, b, minLength, threshold)) duplicatePairs.push(label);
  }
  return {
    passed: duplicatePairs.length < 2,
    duplicatePairs,
    regions,
    similarities,
  };
}

export type TimelineMetrics = {
  clientHeight: number;
  scrollHeight: number;
  overflowY: string;
  canScroll: boolean;
};

export type InputFootMetrics = {
  visible: boolean;
  bottom: number | null;
  withinViewport: boolean;
};

export type BannerMetrics = {
  count: number;
  stackHeight: number;
  items: Array<{ testId: string | null; message: string; height: number; top: number }>;
};

export type CenterArtifactMetrics = {
  clientHeight: number;
  scrollHeight: number;
  overflowY: string;
  present: boolean;
};

export type HistorySubstateMetrics = {
  hasLocalBrowsingHint: boolean;
  hasReplayDisclosure: boolean;
  localHintMessages: string[];
  bannerStackHeight: number;
  visibleBannerCount: number;
};

export async function setSidebarState(page: Page, state: SidebarState): Promise<void> {
  const leftCollapsed = await page.getByTestId("left-panel-collapsed").isVisible().catch(() => false);
  const rightCollapsed = await page.getByTestId("right-panel-collapsed").isVisible().catch(() => false);
  const wantLeftOpen = state === "both" || state === "left-only";
  const wantRightOpen = state === "both" || state === "right-only";

  if (wantLeftOpen && leftCollapsed) await page.getByTestId("toggle-left-panel").click();
  if (!wantLeftOpen && !leftCollapsed) await page.getByTestId("toggle-left-panel").click();
  if (wantRightOpen && rightCollapsed) await page.getByTestId("toggle-right-panel").click();
  if (!wantRightOpen && !rightCollapsed) await page.getByTestId("toggle-right-panel").click();
  await page.waitForTimeout(250);
}

export async function navigateWorkbenchMode(page: Page, mode: WorkbenchMode): Promise<void> {
  if (mode === "current" || mode === "replay") return;
  if (mode === "completed") return;
  if (mode === "history") {
    await page.locator(".wb-flow-phase-btn").filter({ hasText: "问题" }).click();
    await page.waitForTimeout(300);
    return;
  }
  if (mode === "history-sub") {
    throw new Error("history-sub navigation requires establishHistorySubScenario() — do not use navigateWorkbenchMode()");
  }
}

export async function collectSemanticRegions(page: Page): Promise<SemanticRegions> {
  return page.evaluate(() => {
    const text = (selector: string) => {
      const element = document.querySelector(selector);
      return element ? element.textContent?.replace(/\s+/g, " ").trim() ?? "" : "";
    };
    const leftBody = text(".wb-stage-info");
    const riskSplit = leftBody.split(/风险/);
    const nextSplit = leftBody.split(/下一步/);
    return {
      leftStageInfo: leftBody.replace(/阶段信息/g, "").trim(),
      centerProgress: text('[data-testid="workbench-center-current-progress"]'),
      rightJudgment: text(".wb-interaction-summary"),
    };
  });
}

export async function collectTimelineMetrics(page: Page): Promise<TimelineMetrics | null> {
  return page.evaluate(() => {
    const element = document.querySelector(".wb-interaction-messages") as HTMLElement | null
      ?? document.querySelector(".wb-interaction-scroll");
    if (!element) return null;
    const style = getComputedStyle(element);
    return {
      clientHeight: element.clientHeight,
      scrollHeight: element.scrollHeight,
      overflowY: style.overflowY,
      canScroll: element.scrollHeight > element.clientHeight + 2,
    };
  });
}

export async function collectInputFootMetrics(page: Page, viewportHeight: number): Promise<InputFootMetrics> {
  const selectors = [".wb-interaction-foot", ".wb-interaction-input-bar", ".wb-interaction-input"];
  for (const selector of selectors) {
    const locator = page.locator(selector).first();
    if (await locator.count()) {
      const visible = await locator.isVisible().catch(() => false);
      const box = visible ? await locator.boundingBox() : null;
      const bottom = box ? box.y + box.height : null;
      return {
        visible,
        bottom,
        withinViewport: visible && bottom !== null && bottom <= viewportHeight + 1,
      };
    }
  }
  return { visible: false, bottom: null, withinViewport: false };
}

export async function collectBannerMetrics(page: Page): Promise<BannerMetrics> {
  return page.evaluate(() => {
    const selectors = '[data-testid="replay-disclosure"], .wb-main-alert, .ant-alert';
    const elements = [...document.querySelectorAll(selectors)].filter((element) => {
      const rect = element.getBoundingClientRect();
      return rect.height > 0 && rect.width > 0;
    });
    const items = elements.map((element) => ({
      testId: element.getAttribute("data-testid"),
      message: (element.querySelector(".ant-alert-message")?.textContent ?? element.textContent ?? "")
        .replace(/\s+/g, " ")
        .trim()
        .slice(0, 160),
      height: element.getBoundingClientRect().height,
      top: element.getBoundingClientRect().top,
    }));
    return {
      count: items.length,
      stackHeight: items.reduce((sum, item) => sum + item.height, 0),
      items,
    };
  });
}

export async function collectCenterArtifactMetrics(page: Page): Promise<CenterArtifactMetrics> {
  return page.evaluate(() => {
    const element = document.querySelector(".wb-main-scroll");
    if (!element) {
      return { clientHeight: 0, scrollHeight: 0, overflowY: "none", present: false };
    }
    const style = getComputedStyle(element);
    return {
      clientHeight: element.clientHeight,
      scrollHeight: element.scrollHeight,
      overflowY: style.overflowY,
      present: true,
    };
  });
}

export async function collectHistorySubstateMetrics(page: Page): Promise<HistorySubstateMetrics> {
  return page.evaluate(() => {
    const replay = document.querySelector('[data-testid="replay-disclosure"]');
    const replayVisible = Boolean(replay && replay.getBoundingClientRect().height > 0);
    const localAlerts = [...document.querySelectorAll(".wb-main-alert, .ant-alert")].filter((element) => {
      if (element.matches('[data-testid="replay-disclosure"]')) return false;
      const rect = element.getBoundingClientRect();
      return rect.height > 0 && rect.width > 0;
    });
    const localHintMessages = localAlerts
      .map((element) => (element.querySelector(".ant-alert-message")?.textContent ?? element.textContent ?? "").replace(/\s+/g, " ").trim())
      .filter((message) => /历史阶段|历史浏览|浏览模式|所选.*节点|二级节点|子节点|局部浏览/.test(message));
    const hasLocalBrowsingHint = localHintMessages.length > 0;
    const visibleBannerCount = [...document.querySelectorAll('[data-testid="replay-disclosure"], .wb-main-alert, .ant-alert')].filter((element) => {
      const rect = element.getBoundingClientRect();
      return rect.height > 0 && rect.width > 0;
    }).length;
    const bannerStackHeight = [...document.querySelectorAll('[data-testid="replay-disclosure"], .wb-main-alert, .ant-alert')]
      .filter((element) => element.getBoundingClientRect().height > 0)
      .reduce((sum, element) => sum + element.getBoundingClientRect().height, 0);
    return {
      hasLocalBrowsingHint,
      hasReplayDisclosure: replayVisible,
      localHintMessages,
      visibleBannerCount,
      bannerStackHeight,
    };
  });
}

export function evaluateTimelineGate1280(timeline: TimelineMetrics | null, foot: InputFootMetrics): UiuxGateResult {
  const passed = Boolean(
    timeline &&
      timeline.clientHeight >= 180 &&
      foot.visible &&
      foot.withinViewport,
  );
  return {
    id: "UX-GATE-TIMELINE-1280",
    passed,
    description: "Interaction timeline keeps >=180px readable area and input foot stays visible at 1280x720",
    scenario: "workbench replay/running history-sub both sidebars 1280x720",
    actual: { timeline, foot },
    expected: { minTimelineClientHeight: 180, footVisible: true, footWithinViewport: true },
    reviewType: "M",
  };
}

export function evaluateDedupGate01(dedup: DedupEvaluation): UiuxGateResult {
  return {
    id: "UX-GATE-DEDUP-01",
    passed: dedup.passed,
    description: "Stage summary/risk/next-step must not be fully duplicated across two or more regions",
    scenario: "workbench replay/running both sidebars",
    actual: dedup,
    expected: { maxDuplicatePairs: 1, jaccardThreshold: 0.35, minTextLength: 40 },
    reviewType: "M+A",
  };
}

export function evaluateBannerStackGate(banners: BannerMetrics): UiuxGateResult {
  const passed = !(banners.count >= 2 && banners.stackHeight > 150);
  return {
    id: "UX-GATE-BANNER-STACK",
    passed,
    description: "Replay and local history banners must not stack beyond first-screen budget",
    scenario: "workbench replay history 1280x720",
    actual: banners,
    expected: { maxVisibleBannersBeforeFail: 1, maxStackHeight: 150 },
    reviewType: "M",
  };
}

export function evaluateCenterArtifactGate(artifact: CenterArtifactMetrics): UiuxGateResult {
  const passed = artifact.present && artifact.clientHeight >= 200;
  return {
    id: "UX-GATE-CENTER-ARTIFACT",
    passed,
    description: "Center artifact scroll area keeps >=200px at 1280x720 with both sidebars open",
    scenario: "workbench history-sub both sidebars 1280x720",
    actual: artifact,
    expected: { minClientHeight: 200 },
    reviewType: "M",
  };
}

function gateFieldsFromPrecondition(precondition: ScenarioPreconditionReport): Pick<
  UiuxGateResult,
  | "scenario_id"
  | "task_id"
  | "expected_stage"
  | "actual_stage"
  | "expected_substage"
  | "actual_substage"
  | "expected_sidebar_state"
  | "actual_sidebar_state"
  | "viewport"
  | "precondition_status"
  | "precondition"
> {
  return {
    scenario_id: precondition.scenario_id,
    task_id: precondition.task_id,
    expected_stage: precondition.expected_stage,
    actual_stage: precondition.actual_stage,
    expected_substage: precondition.expected_substage,
    actual_substage: precondition.actual_substage,
    expected_sidebar_state: precondition.expected_sidebar_state,
    actual_sidebar_state: precondition.actual_sidebar_state,
    viewport: precondition.viewport,
    precondition_status: precondition.precondition_status,
    precondition,
  };
}

export function evaluateHistorySubstateSetupFailure(precondition: ScenarioPreconditionReport): UiuxGateResult {
  return {
    id: "UX-GATE-HISTORY-SUBSTATE",
    passed: false,
    failure_type: "HARNESS_SETUP_FAIL",
    description: "Same-phase non-current sub-stage view exposes a local browsing hint without replay banner stacking",
    scenario: "workbench current-phase non-current substage both sidebars 1280x720",
    actual: {
      setupErrors: precondition.errors,
      hasLocalBrowsingHint: precondition.has_local_browsing_hint,
      hasHistoricalSubTag: precondition.has_historical_sub_tag,
    },
    expected: { precondition_status: "PASS" },
    reviewType: "M+H",
    ...gateFieldsFromPrecondition(precondition),
  };
}

export function evaluateHistorySubstateGate(
  metrics: HistorySubstateMetrics,
  precondition: ScenarioPreconditionReport,
): UiuxGateResult {
  if (precondition.precondition_status !== "PASS") {
    return evaluateHistorySubstateSetupFailure(precondition);
  }
  const duplicateStackWithReplay =
    metrics.hasReplayDisclosure &&
    metrics.hasLocalBrowsingHint &&
    metrics.visibleBannerCount >= 2 &&
    metrics.bannerStackHeight > 150;
  const passed = metrics.hasLocalBrowsingHint && !duplicateStackWithReplay;
  return {
    id: "UX-GATE-HISTORY-SUBSTATE",
    passed,
    failure_type: passed ? "NONE" : "PRODUCT_GATE_FAIL",
    description: "Same-phase non-current sub-stage view exposes a local browsing hint without replay banner stacking",
    scenario: `workbench ${precondition.expected_stage} phase ${precondition.expected_substage} non-current substage`,
    actual: metrics,
    expected: { hasLocalBrowsingHint: true, duplicateStackWithReplay: false },
    reviewType: "M+H",
    ...gateFieldsFromPrecondition(precondition),
  };
}

export class UiuxGateReporter {
  private readonly results: UiuxGateResult[] = [];

  record(result: UiuxGateResult): void {
    this.results.push(result);
    const failureType = result.failure_type ?? (result.passed ? "NONE" : "PRODUCT_GATE_FAIL");
    if (failureType === "HARNESS_SETUP_FAIL") {
      const setupDetail = result.precondition?.errors?.join("; ") ?? result.description;
      expect.soft(false, `HARNESS_SETUP_FAIL ${result.id}: ${setupDetail}`).toBe(true);
      return;
    }
    expect.soft(result.passed, `${result.id}: ${result.description}`).toBe(true);
  }

  list(): UiuxGateResult[] {
    return [...this.results];
  }

  summary(): Record<string, "PASS" | "FAIL" | "PENDING"> {
    const out: Record<string, "PASS" | "FAIL" | "PENDING"> = {};
    for (const result of this.results) {
      out[result.id] = result.passed ? "PASS" : "FAIL";
    }
    return out;
  }

  async writeReport(extra?: Record<string, unknown>): Promise<string> {
    const dir = path.join(RUN_DIR, "gates");
    await fs.mkdir(dir, { recursive: true });
    const file = path.join(dir, "uiux-s17-gates.json");
    const primaryPrecondition = this.results.find((result) => result.precondition)?.precondition;
    const payload = {
      generatedAt: new Date().toISOString(),
      runDir: RUN_DIR,
      gates: this.results,
      summary: this.summary(),
      redBaselineExpected: true,
      scenario_id: primaryPrecondition?.scenario_id ?? null,
      task_id: primaryPrecondition?.task_id ?? null,
      expected_stage: primaryPrecondition?.expected_stage ?? null,
      actual_stage: primaryPrecondition?.actual_stage ?? null,
      expected_substage: primaryPrecondition?.expected_substage ?? null,
      actual_substage: primaryPrecondition?.actual_substage ?? null,
      expected_sidebar_state: primaryPrecondition?.expected_sidebar_state ?? null,
      actual_sidebar_state: primaryPrecondition?.actual_sidebar_state ?? null,
      viewport: primaryPrecondition?.viewport ?? null,
      precondition_status: primaryPrecondition?.precondition_status ?? null,
      failure_type: this.results.find((result) => result.failure_type && result.failure_type !== "NONE")?.failure_type ?? "NONE",
      ...extra,
    };
    await fs.writeFile(file, JSON.stringify(payload, null, 2), "utf8");
    return file;
  }
}

export async function saveMatrixScreenshot(page: Page, slug: string): Promise<string> {
  const dir = path.join(RUN_DIR, "uiux-matrix", "screenshots");
  await fs.mkdir(dir, { recursive: true });
  const file = path.join(dir, `${slug}.png`);
  await page.screenshot({ path: file, fullPage: false });
  return file;
}

export async function writeMatrixReport(rows: unknown[], extra?: Record<string, unknown>): Promise<string> {
  const dir = path.join(RUN_DIR, "uiux-matrix");
  await fs.mkdir(dir, { recursive: true });
  const file = path.join(dir, "uiux-s17-matrix.json");
  const payload = {
    generatedAt: new Date().toISOString(),
    runDir: RUN_DIR,
    rowCount: rows.length,
    rows,
    ...extra,
  };
  await fs.writeFile(file, JSON.stringify(payload, null, 2), "utf8");
  return file;
}

export async function writeDomMetrics(label: string, value: unknown): Promise<string> {
  const dir = path.join(RUN_DIR, "dom-metrics");
  await fs.mkdir(dir, { recursive: true });
  const file = path.join(dir, `${label}.json`);
  await fs.writeFile(file, JSON.stringify(value, null, 2), "utf8");
  return file;
}

export function pickSeededCompletedTask(tasks: any[]): any | null {
  const exact = tasks.find((task) => task.taskId === "task_cs_ope_006");
  if (exact) return exact;
  return tasks.find((task) => task.status === "completed") ?? null;
}
