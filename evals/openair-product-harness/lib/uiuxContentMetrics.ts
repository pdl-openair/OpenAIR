import { expect, type Page } from "@playwright/test";
import fs from "node:fs/promises";
import path from "node:path";
import { RUN_DIR } from "./config";
import type { SidebarState, ViewportSpec } from "./uiuxMetrics";

export type ContentFailureType = "NONE" | "HARNESS_SETUP_FAIL" | "PRODUCT_GATE_FAIL" | "FIXTURE_GAP" | "INFRA_FAIL";

export type ReviewClass = "M" | "A" | "H";

export const S18_VIEWPORTS: ViewportSpec[] = [
  { width: 1280, height: 720, label: "1280x720" },
  { width: 1440, height: 900, label: "1440x900" },
  { width: 1920, height: 1080, label: "1920x1080" },
];

export const S18_SIDEBAR_STATES: SidebarState[] = ["both", "left-only", "right-only", "collapsed"];

export const S18_SUMMARY_CARD_IDS = [
  "workbench-center-current-progress",
  "workbench-center-pending-confirmation",
  "workbench-center-risks",
  "workbench-center-next-step",
] as const;

/** Keep aligned with WorkbenchFocusSummaryItem overflow hint (line-clamp under-report). */
export const FOCUS_SUMMARY_LINE_CLAMP_CHAR_HINT = 18;

export type SummaryCardMetrics = {
  id: string;
  label: string;
  textContent: string;
  clientWidth: number;
  scrollWidth: number;
  clientHeight: number;
  scrollHeight: number;
  isOverflowing: boolean;
  hasAccessibleExpandAction: boolean;
  hasTooltipOrDetailEntry: boolean;
  reviewType: ReviewClass;
};

export type OverflowSummaryEvaluation = {
  scenario: "OVERFLOW_SUMMARY";
  viewport: string;
  sidebar: SidebarState;
  cards: SummaryCardMetrics[];
  overflowCardCount: number;
  overflowWithoutExpandCount: number;
  passed: boolean;
};

export type EmptyArtifactMetrics = {
  scenario: "EMPTY_ARTIFACT";
  viewport: string;
  sidebar: SidebarState;
  taskId: string;
  substageLabel: string;
  artifactContainerHeight: number;
  emptyStateHeight: number;
  viewportHeight: number;
  blankSpaceRatio: number;
  hasExplicitReason: boolean;
  hasNextStepHint: boolean;
  hasAlternativeEntry: boolean;
  emptyStateText: string;
  reviewFields: Array<{ field: string; reviewType: ReviewClass; value: unknown; note?: string }>;
};

export type PopulatedArtifactMetrics = {
  scenario: "POPULATED_ARTIFACT";
  viewport: string;
  sidebar: SidebarState;
  taskId: string;
  artifactId: string;
  artifactType: string | null;
  hasBody: boolean;
  hasStructuredEvidence: boolean;
  hasSourceEntry: boolean;
  mainScrollClientHeight: number;
  mainScrollScrollHeight: number;
  canScroll: boolean;
  bodyPreview: string;
  fixtureAvailable: boolean;
  reviewFields: Array<{ field: string; reviewType: ReviewClass; value: unknown; note?: string }>;
};

export type S18GateResult = {
  id: string;
  passed: boolean;
  failure_type: ContentFailureType;
  description: string;
  scenario: string;
  actual?: unknown;
  expected?: unknown;
  reviewType: ReviewClass;
};

export type ExpandCollapseVerification = {
  cardKey: string;
  isOverflowingBeforeExpand: boolean;
  hasAccessibleExpandAction: boolean;
  expandActionVisible: boolean;
  expandedState: boolean;
  fullTextVisibleAfterExpand: boolean;
  collapseActionVisible: boolean;
  collapsedStateAfterCollapse: boolean;
};

export async function verifyExpandCollapseInteraction(
  page: Page,
  cardId: string,
): Promise<ExpandCollapseVerification | null> {
  const section = page.locator(`[data-testid="${cardId}"]`);
  if (!(await section.count())) return null;

  const before = (await collectSummaryCardMetrics(page)).find((card) => card.id === cardId);
  if (!before?.isOverflowing || before.textContent.length < 16) return null;

  const expandBtn = section.getByRole("button", { name: new RegExp(`展开${before.label}|展开`) });
  const expandActionVisible = await expandBtn.isVisible().catch(() => false);
  if (!expandActionVisible) {
    return {
      cardKey: cardId,
      isOverflowingBeforeExpand: before.isOverflowing,
      hasAccessibleExpandAction: before.hasAccessibleExpandAction,
      expandActionVisible: false,
      expandedState: false,
      fullTextVisibleAfterExpand: false,
      collapseActionVisible: false,
      collapsedStateAfterCollapse: false,
    };
  }

  await expandBtn.click();
  await page.waitForTimeout(150);

  const expandedMetrics = await page.evaluate(({ id, expectedText }) => {
    const cardSection = document.querySelector(`[data-testid="${id}"]`);
    const value = cardSection?.querySelector(".wb-focus-value") as HTMLElement | null;
    const collapseBtn = cardSection?.querySelector(`[data-testid="${id}-collapse"]`) as HTMLElement | null;
    const textContent = (value?.textContent ?? "").replace(/\s+/g, " ").trim();
    const normalizedExpected = expectedText.replace(/\s+/g, " ").trim();
    const hasExpandedClass = Boolean(value?.classList.contains("wb-focus-value--expanded"));
    const fullTextVisibleAfterExpand = Boolean(
      value
      && hasExpandedClass
      && textContent.length >= Math.min(normalizedExpected.length, 16)
      && normalizedExpected.slice(0, 16) === textContent.slice(0, 16),
    );
    return {
      expandedState: Boolean(collapseBtn && collapseBtn.getAttribute("aria-expanded") === "true"),
      fullTextVisibleAfterExpand,
      collapseActionVisible: Boolean(collapseBtn && collapseBtn.offsetParent !== null),
    };
  }, { id: cardId, expectedText: before.textContent });

  const collapseBtn = section.getByRole("button", { name: new RegExp(`收起${before.label}|收起`) });
  await collapseBtn.click();
  await page.waitForTimeout(150);

  const afterCollapse = await page.evaluate(({ id }) => {
    const cardSection = document.querySelector(`[data-testid="${id}"]`);
    const value = cardSection?.querySelector(".wb-focus-value") as HTMLElement | null;
    const style = value ? getComputedStyle(value) : null;
    const lineClamp = style
      ? Number.parseInt(style.webkitLineClamp || style.getPropertyValue("-webkit-line-clamp") || "0", 10)
      : 0;
    const collapsedStateAfterCollapse = Boolean(value && lineClamp > 0);
    return { collapsedStateAfterCollapse };
  }, { id: cardId });

  return {
    cardKey: cardId,
    isOverflowingBeforeExpand: before.isOverflowing,
    hasAccessibleExpandAction: before.hasAccessibleExpandAction,
    expandActionVisible,
    expandedState: expandedMetrics.expandedState,
    fullTextVisibleAfterExpand: expandedMetrics.fullTextVisibleAfterExpand,
    collapseActionVisible: expandedMetrics.collapseActionVisible,
    collapsedStateAfterCollapse: afterCollapse.collapsedStateAfterCollapse,
  };
}

export async function collectSummaryCardMetrics(page: Page): Promise<SummaryCardMetrics[]> {
  return page.evaluate(({ cardIds, charHint }) => {
    const expandPattern = /展开|查看详情|更多|详情|expand/i;
    const cards = cardIds.map((id) => {
      const section = document.querySelector(`[data-testid="${id}"]`);
      const value = section?.querySelector(".wb-focus-value") as HTMLElement | null;
      const label = section?.querySelector(".wb-focus-label")?.textContent?.trim() ?? id;
      if (!section || !value) {
        return {
          id,
          label,
          textContent: "",
          clientWidth: 0,
          scrollWidth: 0,
          clientHeight: 0,
          scrollHeight: 0,
          isOverflowing: false,
          hasAccessibleExpandAction: false,
          hasTooltipOrDetailEntry: false,
          reviewType: "M" as const,
        };
      }
      const textContent = (value.textContent ?? "").replace(/\s+/g, " ").trim();
      const clientWidth = value.clientWidth;
      const scrollWidth = value.scrollWidth;
      const clientHeight = value.clientHeight;
      const scrollHeight = value.scrollHeight;
      const style = getComputedStyle(value);
      const lineClamp = Number.parseInt(style.webkitLineClamp || style.getPropertyValue("-webkit-line-clamp") || "0", 10);
      const isOverflowing =
        scrollHeight > clientHeight + 1
        || scrollWidth > clientWidth + 1
        || (lineClamp > 0 && textContent.length >= charHint);
      const sectionButtons = [...section.querySelectorAll("button, a, [role='button']")]
        .map((node) => (node.textContent ?? "").replace(/\s+/g, " ").trim())
        .filter(Boolean);
      const hasAccessibleExpandAction = sectionButtons.some((text) => expandPattern.test(text))
        || Boolean(section.querySelector(".ant-typography-expand, [aria-expanded], .ant-typography-copy-expand"));
      const hasTooltipOrDetailEntry = Boolean(
        value.getAttribute("title")
        || value.getAttribute("aria-label")
        || section.querySelector("[aria-describedby], .ant-tooltip-open, [data-testid*='detail']"),
      );
      return {
        id,
        label,
        textContent,
        clientWidth,
        scrollWidth,
        clientHeight,
        scrollHeight,
        isOverflowing,
        hasAccessibleExpandAction,
        hasTooltipOrDetailEntry,
        reviewType: "M" as const,
      };
    });
    return cards;
  }, { cardIds: [...S18_SUMMARY_CARD_IDS], charHint: FOCUS_SUMMARY_LINE_CLAMP_CHAR_HINT });
}

export function evaluateOverflowSummary(
  cards: SummaryCardMetrics[],
  viewport: string,
  sidebar: SidebarState,
): OverflowSummaryEvaluation {
  const overflowCards = cards.filter((card) => card.isOverflowing && card.textContent.length >= 16);
  const overflowWithoutExpand = overflowCards.filter(
    (card) => !card.hasAccessibleExpandAction && !card.hasTooltipOrDetailEntry,
  );
  return {
    scenario: "OVERFLOW_SUMMARY",
    viewport,
    sidebar,
    cards,
    overflowCardCount: overflowCards.length,
    overflowWithoutExpandCount: overflowWithoutExpand.length,
    passed: overflowWithoutExpand.length === 0,
  };
}

export async function collectEmptyArtifactMetrics(
  page: Page,
  options: { viewport: string; sidebar: SidebarState; taskId: string; substageLabel: string },
): Promise<EmptyArtifactMetrics> {
  return page.evaluate(({ viewportHeight, substageLabel, taskId }) => {
    const mainScroll = document.querySelector(".wb-main-scroll") as HTMLElement | null;
    const emptyRoot = document.querySelector(".wb-main-scroll .ant-empty") as HTMLElement | null;
    const emptyText = (emptyRoot?.textContent ?? document.querySelector(".wb-main-scroll")?.textContent ?? "")
      .replace(/\s+/g, " ")
      .trim();
    const artifactContainerHeight = mainScroll?.clientHeight ?? 0;
    const emptyStateHeight = emptyRoot?.getBoundingClientRect().height ?? 0;
    const blankSpaceRatio = viewportHeight > 0
      ? Math.max(0, (artifactContainerHeight - emptyStateHeight) / viewportHeight)
      : 0;
    const hasExplicitReason = /暂无挂载产物|暂无产物|本步骤成果可能已并入/.test(emptyText);
    const hasNextStepHint = /下一步|阶段摘要|任务推进概览|继续推进|继续执行/.test(emptyText);
    const hasAlternativeEntry = Boolean(
      document.querySelector('[data-testid="workbench-center-primary-action"]')
      || document.querySelector(".wb-output-chip")
      || document.querySelector('[data-testid="workbench-center-return-current"]'),
    );
    return {
      scenario: "EMPTY_ARTIFACT" as const,
      viewport: "",
      sidebar: "both" as const,
      taskId,
      substageLabel,
      artifactContainerHeight,
      emptyStateHeight,
      viewportHeight,
      blankSpaceRatio,
      hasExplicitReason,
      hasNextStepHint,
      hasAlternativeEntry,
      emptyStateText: emptyText.slice(0, 400),
      reviewFields: [
        { field: "artifactContainerHeight", reviewType: "M" as const, value: artifactContainerHeight },
        { field: "emptyStateHeight", reviewType: "M" as const, value: emptyStateHeight },
        { field: "blankSpaceRatio", reviewType: "M" as const, value: blankSpaceRatio, note: "Ratio not frozen this round" },
        { field: "hasExplicitReason", reviewType: "M" as const, value: hasExplicitReason },
        { field: "hasNextStepHint", reviewType: "M" as const, value: hasNextStepHint },
        { field: "hasAlternativeEntry", reviewType: "M" as const, value: hasAlternativeEntry },
        { field: "emptyLayoutBalance", reviewType: "A" as const, value: blankSpaceRatio, note: "Agent visual: empty state vs wasted viewport" },
        { field: "emptyCopyClarity", reviewType: "H" as const, value: emptyText.slice(0, 160), note: "Human 10s read" },
      ],
    };
  }, {
    viewportHeight: page.viewportSize()?.height ?? 720,
    substageLabel: options.substageLabel,
    taskId: options.taskId,
  }).then((metrics) => ({
    ...metrics,
    viewport: options.viewport,
    sidebar: options.sidebar,
  }));
}

export async function collectPopulatedArtifactMetrics(
  page: Page,
  options: {
    viewport: string;
    sidebar: SidebarState;
    taskId: string;
    artifactId: string;
    fixtureAvailable: boolean;
  },
): Promise<PopulatedArtifactMetrics> {
  return page.evaluate(({ taskId, artifactId, fixtureAvailable }) => {
    const mainScroll = document.querySelector(".wb-main-scroll") as HTMLElement | null;
    const body = document.querySelector(".wb-artifact-md");
    const evidencePanel = document.querySelector('[data-testid="artifact-evidence-panel"]');
    const sourceLink = document.querySelector('[data-testid^="artifact-evidence-source-link-"]');
    const artifactCard = document.querySelector(".wb-main-card");
    const artifactType = artifactCard?.querySelector(".wb-output-chip--active span:last-child")?.textContent?.trim()
      ?? artifactCard?.textContent?.match(/PDF|文|表|链|件/)?.[0]
      ?? null;
    const bodyPreview = (body?.textContent ?? "").replace(/\s+/g, " ").trim().slice(0, 240);
    return {
      scenario: "POPULATED_ARTIFACT" as const,
      viewport: "",
      sidebar: "both" as const,
      taskId,
      artifactId,
      artifactType,
      hasBody: Boolean(body && bodyPreview.length > 20),
      hasStructuredEvidence: Boolean(evidencePanel && evidencePanel.textContent && evidencePanel.textContent.trim().length > 20),
      hasSourceEntry: Boolean(sourceLink && sourceLink.getAttribute("href")),
      mainScrollClientHeight: mainScroll?.clientHeight ?? 0,
      mainScrollScrollHeight: mainScroll?.scrollHeight ?? 0,
      canScroll: Boolean(mainScroll && mainScroll.scrollHeight > mainScroll.clientHeight + 2),
      bodyPreview,
      fixtureAvailable,
      reviewFields: [
        { field: "hasBody", reviewType: "M" as const, value: Boolean(body && bodyPreview.length > 20) },
        { field: "hasStructuredEvidence", reviewType: "M" as const, value: Boolean(evidencePanel) },
        { field: "hasSourceEntry", reviewType: "M" as const, value: Boolean(sourceLink) },
        { field: "mainScrollClientHeight", reviewType: "M" as const, value: mainScroll?.clientHeight ?? 0 },
        { field: "mainScrollScrollHeight", reviewType: "M" as const, value: mainScroll?.scrollHeight ?? 0 },
        { field: "canScroll", reviewType: "M" as const, value: Boolean(mainScroll && mainScroll.scrollHeight > mainScroll.clientHeight + 2) },
        { field: "longTextReadability", reviewType: "A" as const, value: bodyPreview.slice(0, 120), note: "Agent visual reading flow" },
        { field: "evidenceSourceTrace", reviewType: "H" as const, value: Boolean(sourceLink), note: "Human verifies source trace" },
      ],
    };
  }, options).then((metrics) => ({
    ...metrics,
    viewport: options.viewport,
    sidebar: options.sidebar,
  }));
}

export function evaluatePrimarySummaryReadabilityGate(
  evaluations: OverflowSummaryEvaluation[],
): S18GateResult {
  const failingRows = evaluations.filter((row) => !row.passed);
  const overflowWithoutExpandCount = evaluations.reduce((sum, row) => sum + row.overflowWithoutExpandCount, 0);
  const overflowCardCount = evaluations.reduce((sum, row) => sum + row.overflowCardCount, 0);
  const passed = failingRows.length === 0;
  return {
    id: "UX-GATE-PRIMARY-SUMMARY-READABILITY",
    passed,
    failure_type: passed ? "NONE" : "PRODUCT_GATE_FAIL",
    description: "Deduped primary summary cards must remain understandable; overflow requires expand/detail entry",
    scenario: "OVERFLOW_SUMMARY matrix on current running workbench",
    actual: {
      matrixRowCount: evaluations.length,
      overflowCardCount,
      overflowWithoutExpandCount,
      failingRows: failingRows.map((row) => ({
        viewport: row.viewport,
        sidebar: row.sidebar,
        overflowWithoutExpandCount: row.overflowWithoutExpandCount,
        cards: row.cards.filter((card) => card.isOverflowing && !card.hasAccessibleExpandAction && !card.hasTooltipOrDetailEntry),
      })),
    },
    expected: { overflowWithoutExpandCount: 0 },
    reviewType: "M",
  };
}

export function evaluateEmptyArtifactAudit(metrics: EmptyArtifactMetrics[]): {
  audit: "PASS" | "FAIL" | "REVIEW_NEEDED";
  averageBlankSpaceRatio: number;
  note: string;
} {
  if (metrics.length === 0) {
    return { audit: "REVIEW_NEEDED", averageBlankSpaceRatio: 0, note: "No empty-artifact samples captured" };
  }
  const averageBlankSpaceRatio =
    metrics.reduce((sum, row) => sum + row.blankSpaceRatio, 0) / metrics.length;
  const allHaveReason = metrics.every((row) => row.hasExplicitReason);
  const allHaveNextStep = metrics.every((row) => row.hasNextStepHint || row.hasAlternativeEntry);
  if (!allHaveReason) {
    return { audit: "FAIL", averageBlankSpaceRatio, note: "Missing explicit empty-state reason copy" };
  }
  if (!allHaveNextStep) {
    return { audit: "REVIEW_NEEDED", averageBlankSpaceRatio, note: "Reason present; next-step/alternative entry needs human review" };
  }
  return { audit: "REVIEW_NEEDED", averageBlankSpaceRatio, note: "Quantitative blank-space threshold not frozen; human review required" };
}

export function evaluatePopulatedArtifactAudit(
  metrics: PopulatedArtifactMetrics[],
  fixtureAvailable: boolean,
): { audit: "PASS" | "FAIL" | "NOT_RUN"; fixture: "PASS" | "GAP" } {
  if (!fixtureAvailable) {
    return { audit: "NOT_RUN", fixture: "GAP" };
  }
  if (metrics.length === 0) {
    return { audit: "NOT_RUN", fixture: "PASS" };
  }
  const sample = metrics[0];
  const passed = sample.hasBody && sample.hasStructuredEvidence && sample.hasSourceEntry && sample.canScroll;
  return { audit: passed ? "PASS" : "FAIL", fixture: "PASS" };
}

export class S18GateReporter {
  private readonly results: S18GateResult[] = [];

  record(result: S18GateResult): void {
    this.results.push(result);
    if (result.failure_type === "HARNESS_SETUP_FAIL" || result.failure_type === "FIXTURE_GAP") {
      expect.soft(false, `${result.failure_type} ${result.id}: ${result.description}`).toBe(true);
      return;
    }
    expect.soft(result.passed, `${result.id}: ${result.description}`).toBe(true);
  }

  list(): S18GateResult[] {
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
    const file = path.join(dir, "uiux-s18-gates.json");
    const payload = {
      generatedAt: new Date().toISOString(),
      runDir: RUN_DIR,
      gates: this.results,
      summary: this.summary(),
      ...extra,
    };
    await fs.writeFile(file, JSON.stringify(payload, null, 2), "utf8");
    return file;
  }
}

export async function writeContentMatrixReport(rows: unknown[], extra?: Record<string, unknown>): Promise<string> {
  const dir = path.join(RUN_DIR, "uiux-content-matrix");
  await fs.mkdir(dir, { recursive: true });
  const file = path.join(dir, "uiux-s18-matrix.json");
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

export async function saveContentScreenshot(page: Page, slug: string): Promise<string> {
  const dir = path.join(RUN_DIR, "uiux-content-matrix", "screenshots");
  await fs.mkdir(dir, { recursive: true });
  const file = path.join(dir, `${slug}.png`);
  await page.screenshot({ path: file, fullPage: false });
  return file;
}

export async function prepareCurrentWorkbench(
  page: Page,
  taskId: string,
  viewport: ViewportSpec,
  sidebar: SidebarState,
  setSidebarState: (page: Page, state: SidebarState) => Promise<void>,
): Promise<void> {
  await page.setViewportSize({ width: viewport.width, height: viewport.height });
  await page.goto(`/workbench/${taskId}`, { waitUntil: "commit" });
  await expect(page.getByTestId("workbench-center")).toBeVisible({ timeout: 20_000 });
  await setSidebarState(page, sidebar);
  await page.waitForTimeout(250);
}

export async function prepareEmptyArtifactView(
  page: Page,
  taskId: string,
  viewport: ViewportSpec,
  sidebar: SidebarState,
  setSidebarState: (page: Page, state: SidebarState) => Promise<void>,
): Promise<void> {
  await prepareCurrentWorkbench(page, taskId, viewport, sidebar, setSidebarState);
  const reportPhase = page.locator(".wb-flow-phase-btn").filter({ hasText: "报告" });
  await expect(reportPhase).toBeVisible({ timeout: 15_000 });
  await reportPhase.click();
  const resultAnalysis = page.locator(".wb-flow-sub-btn").filter({ hasText: "结果分析" });
  await expect(resultAnalysis).toBeVisible({ timeout: 10_000 });
  await resultAnalysis.click();
  await page.waitForTimeout(300);
}

export async function preparePopulatedArtifactView(
  page: Page,
  taskId: string,
  artifactId: string,
  viewport: ViewportSpec,
  sidebar: SidebarState,
  setSidebarState: (page: Page, state: SidebarState) => Promise<void>,
): Promise<void> {
  await page.setViewportSize({ width: viewport.width, height: viewport.height });
  await page.goto(`/workbench/${taskId}?artifact=${artifactId}`, { waitUntil: "commit" });
  await expect(page.getByTestId("workbench-center")).toBeVisible({ timeout: 20_000 });
  await expect(page.locator(".wb-artifact-md")).toBeVisible({ timeout: 30_000 });
  await setSidebarState(page, sidebar);
  await page.waitForTimeout(300);
}
