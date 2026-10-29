import { expect, type APIRequestContext, type Page, type TestInfo } from "@playwright/test";
import { attachJson } from "./evidence";

type GateResult = {
  id: string;
  passed: boolean;
  description: string;
  actual?: unknown;
  expected?: unknown;
};

export class GateRecorder {
  private readonly results: GateResult[] = [];

  check(id: string, passed: boolean, description: string, actual?: unknown, expected?: unknown): void {
    this.results.push({ id, passed, description, actual, expected });
    expect.soft(passed, `${id}: ${description}`).toBe(true);
  }

  async attach(testInfo: TestInfo): Promise<void> {
    await attachJson(testInfo, "deterministic-gates", this.results, "gates");
  }
}

export async function readTasks(request: APIRequestContext, apiBaseUrl: string): Promise<any[]> {
  const response = await request.get(`${apiBaseUrl}/api/tasks`);
  expect(response.ok(), `GET ${apiBaseUrl}/api/tasks should succeed`).toBeTruthy();
  const body = await response.json();
  return body.tasks ?? [];
}

export function pickSeededAdvancableTask(tasks: any[]): any | null {
  const isRunningTaskWithActiveSubstage = (task: any) => task.status === "running"
    && Array.isArray(task.stageDetails)
    && task.stageDetails.some((stage: any) => Array.isArray(stage.subStages) && stage.subStages.some((sub: any) => sub.status === "running"));
  const exact = tasks.find((task) => task.taskId === "task_cs_ope_001");
  if (exact && isRunningTaskWithActiveSubstage(exact)) return exact;
  return tasks.find(isRunningTaskWithActiveSubstage) ?? null;
}

export function taskDigest(task: any): Record<string, unknown> {
  return {
    taskId: task.taskId,
    title: task.title,
    status: task.status,
    creationStatus: task.creationStatus ?? null,
    currentStage: task.currentStage,
    updatedAt: task.updatedAt,
    stageDetails: (task.stageDetails ?? []).map((stage: any) => ({
      stageKey: stage.stageKey,
      status: stage.status,
      subStages: (stage.subStages ?? []).map((sub: any) => ({
        subStage: sub.subStage,
        status: sub.status,
      })),
    })),
  };
}

export async function expectNoVisibleSecretLeak(page: Page): Promise<void> {
  const body = await page.locator("body").innerText();
  const patterns: RegExp[] = [
    /sk-[a-zA-Z0-9_-]{20,}/,
    /Bearer\s+[a-zA-Z0-9._-]{16,}/i,
    /(api[_-]?key|token|password)\s*[:=]\s*["']?[a-zA-Z0-9._-]{12,}/i,
  ];
  for (const pattern of patterns) {
    expect(body, `Visible page text must not leak secrets matching ${pattern}`).not.toMatch(pattern);
  }
}

export async function hasVisibleFixtureDisclosure(page: Page): Promise<boolean> {
  const body = await page.locator("body").innerText();
  return /(fixture|replay|回放|演示数据|本地样例|离线样例|降级|后端未连接|未连接后端|任务服务)/i.test(body);
}

const INTERNAL_NAME_PATTERNS: RegExp[] = [
  /result_monitoring/i,
  /experiment_execution/i,
  /code_implementation/i,
  /insight_generation/i,
  /Claude Agent/i,
  /\bAgent\b/i,
  /\bSkill\b/i,
  /CS Research/i,
  /v0\.1\s*·\s*local/i,
  /已连接后端/i,
];

export async function visibleInternalNameLeaks(page: Page): Promise<string[]> {
  const body = await page.locator("body").innerText();
  return INTERNAL_NAME_PATTERNS.filter((pattern) => pattern.test(body)).map((pattern) => pattern.source);
}

export type AppShellScrollFitMetrics = {
  found: boolean;
  viewportHeight: number;
  scrollTop: number;
  scrollBottom: number;
  scrollContainerHeight: number;
  bottomGap: number;
  topGap: number;
  headerBottom: number;
  topGapFromHeader: number;
  bodyScrollable: boolean;
  documentScrollHeight: number;
};

const SCROLL_FIT_TOLERANCE_PX = 2;

export async function measureAppShellScrollFit(page: Page): Promise<AppShellScrollFitMetrics> {
  return page.evaluate(() => {
    const scrollEl =
      document.querySelector<HTMLElement>('[data-testid="app-shell-scroll"]') ??
      document.querySelector<HTMLElement>(".app-shell-content");
    const headerEl = document.querySelector<HTMLElement>('[data-testid="app-shell-header"]');
    const viewportHeight = window.innerHeight;
    const documentScrollHeight = document.documentElement.scrollHeight;
    const bodyScrollable = documentScrollHeight > viewportHeight + 1;
    const headerBottom = headerEl?.getBoundingClientRect().bottom ?? 0;

    if (!scrollEl) {
      return {
        found: false,
        viewportHeight,
        scrollTop: 0,
        scrollBottom: 0,
        scrollContainerHeight: 0,
        bottomGap: viewportHeight,
        topGap: viewportHeight,
        headerBottom,
        topGapFromHeader: viewportHeight,
        bodyScrollable,
        documentScrollHeight,
      };
    }

    const rect = scrollEl.getBoundingClientRect();
    const topGap = rect.top;
    return {
      found: true,
      viewportHeight,
      scrollTop: topGap,
      scrollBottom: rect.bottom,
      scrollContainerHeight: rect.height,
      bottomGap: viewportHeight - rect.bottom,
      topGap,
      headerBottom,
      topGapFromHeader: topGap - headerBottom,
      bodyScrollable,
      documentScrollHeight,
    };
  });
}

export function appShellScrollFitPassed(metrics: AppShellScrollFitMetrics): boolean {
  if (!metrics.found) return false;
  if (metrics.bodyScrollable) return false;
  return (
    Math.abs(metrics.bottomGap) <= SCROLL_FIT_TOLERANCE_PX &&
    Math.abs(metrics.topGapFromHeader) <= SCROLL_FIT_TOLERANCE_PX
  );
}
