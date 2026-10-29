import { STATUS_LABELS, type Artifact, type Task, type TaskStatus } from "@openair/shared";
import { CS_RESEARCH_OPE_FIXTURE_TASK_ID, loadCsResearchOpeWorkbenchFixture } from "./csResearchOpeFixture";
import { taskFromWorkbenchModel, type TaskWithExecutionDisplayStatus } from "../model/taskFromFixture";
import { flowLabelForTask } from "./flowLabels";
import { pagePhaseForUnderlying, PAGE_PHASE_LABEL } from "../lib/pagePhases";
import { stageRowForPagePhase, workbenchModelFromApiTask, type WorkbenchTaskModel } from "../model/workbenchTask";
import { userFacingDynamicText } from "../lib/userFacingLanguage";

export type BoardFilter = "all" | "active" | "draft" | "running" | "awaiting_confirmation" | "completed" | "failed" | "cancelled";

export interface BoardTaskRow {
  task: TaskWithExecutionDisplayStatus;
  flowLabel: string;
  currentPhaseLabel: string;
  keyProgress: string;
  updatedAt: string;
  artifactKinds: string[];
  riskOrNext?: string;
  creationHint?: string;
}

const BOARD_EXECUTION_STATUS_LABELS: Readonly<Record<string, string>> = {
  needs_input: "待用户补充",
  blocked_recoverable: "执行受阻",
  blocked: "执行受阻",
  external_result_unknown: "结果待核查",
  research_design_incomplete: "研究方案未形成",
  stopped: "已停止",
  projection_unavailable: "进度暂不可确认",
};

export function boardStatusPresentation(task: TaskWithExecutionDisplayStatus): {
  status: string;
  label: string;
} {
  const executionDisplayStatus = task.executionDisplayStatus?.trim();
  if (executionDisplayStatus) {
    return {
      status: executionDisplayStatus,
      label: BOARD_EXECUTION_STATUS_LABELS[executionDisplayStatus]
        ?? STATUS_LABELS[executionDisplayStatus as TaskStatus]
        ?? "状态待确认",
    };
  }
  return { status: task.status, label: STATUS_LABELS[task.status] };
}

function statusMatchesFilter(task: Task, f: BoardFilter): boolean {
  if (f === "all") return true;
  if (f === "active") return !["completed", "failed", "cancelled"].includes(task.status);
  return task.status === f;
}

export function boardTopCounts(rows: BoardTaskRow[]): Record<BoardFilter, number> {
  return {
    all: rows.length,
    active: rows.filter((r) => !["completed", "failed", "cancelled"].includes(r.task.status)).length,
    draft: rows.filter((r) => r.task.status === "draft").length,
    running: rows.filter((r) => r.task.status === "running").length,
    awaiting_confirmation: rows.filter((r) => r.task.status === "awaiting_confirmation").length,
    completed: rows.filter((r) => r.task.status === "completed").length,
    failed: rows.filter((r) => r.task.status === "failed").length,
    cancelled: rows.filter((r) => r.task.status === "cancelled").length,
  };
}

function artifactKinds(arts: Artifact[], limit = 6): string[] {
  const kinds = arts.map((a) => a.kind);
  return [...new Set(kinds)].slice(0, limit);
}

function rowFromModel(m: WorkbenchTaskModel): BoardTaskRow {
  const t = taskFromWorkbenchModel(m);
  const phase = pagePhaseForUnderlying(m.currentUnderlyingStage);
  const cur = stageRowForPagePhase(m, phase);
  const riskOrNext = cur?.risks
    ? userFacingDynamicText(cur.risks.slice(0, 80), "当前步骤有一项需要关注的事项。")
    : (m.capabilityCalls.some((c) => c.status === "running") ? "研究工作仍在进行，请关注最新产出。" : undefined);
  return {
    task: t,
    flowLabel: flowLabelForTask(m.skillPack, m.domain),
    currentPhaseLabel: PAGE_PHASE_LABEL[phase],
    keyProgress: cur?.summary ? userFacingDynamicText(cur.summary.slice(0, 120), "研究进展已更新。") : "—",
    updatedAt: m.updatedAt,
    artifactKinds: artifactKinds(t.artifacts),
    riskOrNext,
  };
}

function demoSiblingRows(base: BoardTaskRow): BoardTaskRow[] {
  const mk = (taskId: string, title: string, status: TaskStatus, progress: string): BoardTaskRow => ({
    task: {
      ...base.task,
      taskId,
      title,
      status,
      executionDisplayStatus: undefined,
      currentStage: base.task.currentStage,
      goalSummary: undefined,
      artifacts: base.task.artifacts.slice(0, 1),
      capabilityCalls: [],
      confirmations: [],
    },
    flowLabel: base.flowLabel,
    currentPhaseLabel: base.currentPhaseLabel,
    keyProgress: progress,
    updatedAt: base.updatedAt,
    artifactKinds: base.artifactKinds.slice(0, 3),
    riskOrNext: status === "awaiting_confirmation" ? "等待确认后继续推进更安全。" : undefined,
  });

  const baseTime = new Date(base.updatedAt).getTime();
  const ago = (hours: number) => new Date(baseTime - hours * 3_600_000).toISOString();

  const siblings: BoardTaskRow[] = [
    mk(
      "task_cs_ope_002",
      "OPE 指标对比实验（待确认）",
      "awaiting_confirmation",
      "阶段性实验报告已生成，等待确认后进入下一阶段。",
    ),
    mk("task_cs_rl_baseline_003", "RL 训练基线日志采集", "running", "正在汇总多 seed 训练曲线与 checkpoint。"),
    mk("task_cs_policy_eval_004", "策略评估切片数据集构建", "running", "已完成 3/5 个评估窗口的数据对齐。"),
    mk("task_cs_survey_005", "离线 RL 文献结构化整理", "completed", "综述与引用表已归档，可供后续任务引用。"),
    mk("task_cs_ablation_006", "OPE 消融实验批次 A", "completed", "消融结果已写入产物库。"),
    mk("task_cs_pipeline_007", "实验流水线配置校验", "running", "校验能力依赖与资源配额。"),
    mk(
      "task_cs_import_fail_008",
      "外部轨迹数据导入",
      "failed",
      "导入阶段校验失败，需在详情页查看错误说明。",
    ),
  ];
  siblings[0] = { ...siblings[0], updatedAt: ago(5) };
  siblings[1] = { ...siblings[1], updatedAt: ago(2) };
  siblings[2] = { ...siblings[2], updatedAt: ago(8) };
  siblings[3] = { ...siblings[3], updatedAt: ago(48) };
  siblings[4] = { ...siblings[4], updatedAt: ago(72) };
  siblings[5] = { ...siblings[5], updatedAt: ago(3) };
  siblings[6] = { ...siblings[6], updatedAt: ago(12) };
  return siblings;
}

function keyProgressFromTask(task: Task): string {
  const cur = task.stageDetails?.find((s) => s.stageKey === task.currentStage);
  if (cur?.summary) return userFacingDynamicText(cur.summary.slice(0, 120), "研究进展已更新。");
  return task.goalSummary?.slice(0, 120) ?? "—";
}

function creationHint(task: Task): string | undefined {
  if (task.status !== "draft") return undefined;
  if (task.creationStatus === "awaiting_confirmation") return "待确认计划";
  if (task.creationStatus === "planning") return "计划生成中";
  if (task.creationStatus === "revision_requested") return "计划修改中";
  return undefined;
}

function rowFromTask(task: Task): BoardTaskRow {
  if (task.stageDetails && task.stageDetails.length > 0) {
    const row = rowFromModel(workbenchModelFromApiTask(task));
    return { ...row, creationHint: creationHint(task) };
  }
  const phase = pagePhaseForUnderlying(task.currentStage);
  return {
    task,
    flowLabel: flowLabelForTask(task.skillPack, task.domain),
    currentPhaseLabel: PAGE_PHASE_LABEL[phase],
    keyProgress: keyProgressFromTask(task),
    updatedAt: task.updatedAt ?? task.createdAt ?? new Date().toISOString(),
    artifactKinds: artifactKinds(task.artifacts),
    riskOrNext: undefined,
    creationHint: creationHint(task),
  };
}

export function buildBoardRowsFromTasks(tasks: Task[], injectDemo: boolean): BoardTaskRow[] {
  const fromApi: BoardTaskRow[] = tasks.map((task) => rowFromTask(task));

  if (!injectDemo) return fromApi;

  if (fromApi.length === 1 && fromApi[0].task.taskId === CS_RESEARCH_OPE_FIXTURE_TASK_ID) {
    const m = loadCsResearchOpeWorkbenchFixture();
    const primary = rowFromModel(m);
    return [primary, ...demoSiblingRows(primary)];
  }

  return fromApi;
}

export function filterBoardRows(rows: BoardTaskRow[], f: BoardFilter): BoardTaskRow[] {
  return rows.filter((r) => statusMatchesFilter(r.task, f));
}

export type BoardTimeScope = "7d" | "all";

export interface BoardPackOption {
  value: string;
  label: string;
}

const SEVEN_DAYS_MS = 7 * 24 * 60 * 60 * 1_000;

function effectivePackKey(row: BoardTaskRow): string {
  return row.task.skillPack ?? row.task.domain ?? "";
}

/**
 * Build filter options from authoritative pack metadata plus pack values already
 * present on returned tasks. This keeps newly deployed packs reachable without
 * a frontend release and preserves legacy task values missing from metadata.
 */
export function buildBoardPackOptions(
  rows: readonly BoardTaskRow[],
  packs: readonly { key: string; label?: string }[],
): BoardPackOption[] {
  const labels = new Map<string, string>();
  for (const pack of packs) {
    const key = pack.key.trim();
    if (key) labels.set(key, pack.label?.trim() || key);
  }
  for (const row of rows) {
    const key = effectivePackKey(row).trim();
    if (!key || labels.has(key)) continue;
    const projected = row.flowLabel.trim();
    labels.set(key, projected && projected !== key ? `${projected}（${key}）` : key);
  }
  return [
    { value: "all", label: "全部流程" },
    ...[...labels.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([value, label]) => ({ value, label })),
  ];
}

/** Status, pack and time filtering with an injectable clock for boundary tests. */
export function filterBoardRowsForView(
  rows: readonly BoardTaskRow[],
  status: BoardFilter,
  packScope: string,
  timeScope: BoardTimeScope,
  nowMs = Date.now(),
): BoardTaskRow[] {
  const lowerBound = nowMs - SEVEN_DAYS_MS;
  return filterBoardRows([...rows], status)
    .filter((row) => packScope === "all" || effectivePackKey(row) === packScope)
    .filter((row) => {
      if (timeScope === "all") return true;
      const updatedAtMs = Date.parse(row.updatedAt);
      return Number.isFinite(updatedAtMs) && updatedAtMs >= lowerBound;
    })
    .sort((a, b) => {
      const aTime = Date.parse(a.updatedAt);
      const bTime = Date.parse(b.updatedAt);
      return (Number.isFinite(bTime) ? bTime : 0) - (Number.isFinite(aTime) ? aTime : 0);
    });
}
