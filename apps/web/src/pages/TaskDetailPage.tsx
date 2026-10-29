import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Alert,
  Button,
  Collapse,
  Input,
  Modal,
  Radio,
  Spin,
  Typography,
  message,
} from "antd";
import {
  ArrowRightOutlined,
  CheckCircleFilled,
  ClockCircleOutlined,
  DownOutlined,
  EditOutlined,
  ExclamationCircleFilled,
  LoadingOutlined,
  RightOutlined,
  ShareAltOutlined,
} from "@ant-design/icons";
import type { Task, TaskStage, TaskStatus, UiLanguage } from "@openair/shared";
import { getStatusLabels } from "@openair/shared";
import { useAppPreferences } from "../context/AppPreferencesContext";
import { useAuth } from "../context/AuthContext";
import { fetchTaskTokenUsage, renameTask } from "../api/client";
import { publishToCommunity } from "../api/community";
import { loadTaskForPages } from "../lib/loadTask";
import {
  TASK_LOAD_EN,
  TASK_LOAD_ZH,
  classifyTaskLoadFailure,
  userFacingTaskLoadError,
} from "../lib/taskLoadError.mjs";
import { formatBeijingDate } from "../lib/beijingTime";
import { readCurrentTask, rememberCurrentArtifact, rememberCurrentTask } from "../lib/userRouteState";
import { PAGE_PHASE_ORDER, type PagePhaseId, type PhaseNavStatus } from "../lib/pagePhases";
import { labelForSkill } from "../data/recordedRunAdapter";
import {
  workbenchModelFromApiTask,
  type WorkbenchSubStageRow,
  type WorkbenchTaskModel,
} from "../model/workbenchTask";
import { cn } from "../lib/cn";
import { researchArtifactTitle } from "../lib/researchArtifactTitle";
import { FinalResultPanel } from "../components/finalResult/FinalResultPanel";

const STAGE_LABELS_ZH: Record<PagePhaseId, string> = {
  problem: "问题建模",
  method: "方案设计",
  experiment: "执行验证",
  report: "洞察生成",
};

const STAGE_LABELS_EN: Record<PagePhaseId, string> = {
  problem: "Problem Modeling",
  method: "Solution Design",
  experiment: "Execution & Validation",
  report: "Insight Generation",
};

const STAGE_NUMBERS: Record<PagePhaseId, number> = {
  problem: 1,
  method: 2,
  experiment: 3,
  report: 4,
};

function stageDisplayLabel(phase: PagePhaseId, language: UiLanguage): string {
  const label = language === "en-US" ? STAGE_LABELS_EN[phase] : STAGE_LABELS_ZH[phase];
  return `${STAGE_NUMBERS[phase]} ${label}`;
}

function statusLabel(status: PhaseNavStatus, language: UiLanguage): string {
  if (language === "en-US") {
    if (status === "completed") return "Completed";
    if (status === "running") return "In progress";
    if (status === "blocked") return "Blocked";
    if (status === "pending") return "Needs attention";
    if (status === "failed") return "Blocked";
    return "Not started";
  }
  if (status === "completed") return "已完成";
  if (status === "running") return "进行中";
  if (status === "blocked") return "执行受阻";
  if (status === "pending") return "待处理";
  if (status === "failed") return "受阻";
  return "未开始";
}

function statusTone(status: PhaseNavStatus): string {
  return cn(
    "inline-flex items-center gap-1.5 text-sm font-medium",
    status === "completed" && "text-success",
    status === "running" && "text-accent-strong",
    (status === "pending" || status === "blocked") && "text-warning",
    status === "failed" && "text-danger",
    status === "not_started" && "text-text-muted",
  );
}

function StatusIcon({ status }: { status: PhaseNavStatus }) {
  if (status === "completed") return <CheckCircleFilled />;
  if (status === "running") return <LoadingOutlined spin />;
  if (status === "pending" || status === "blocked") return <ClockCircleOutlined />;
  if (status === "failed") return <ExclamationCircleFilled />;
  return <span className="inline-block h-2.5 w-2.5 rounded-full border border-text-muted" aria-hidden />;
}

function artifactsForSubstage(bench: WorkbenchTaskModel, substage: WorkbenchSubStageRow) {
  return bench.artifacts.filter((artifact) => (
    substage.artifactRefs.includes(artifact.id)
    || artifact.subStageInstanceId === substage.id
  ));
}

function taskStatusClass(status: TaskStatus): string {
  return cn(
    "inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-sm font-medium",
    status === "completed" && "bg-success-soft text-success",
    status === "running" && "bg-info-soft text-accent-strong",
    status === "awaiting_confirmation" && "bg-warning-soft text-warning",
    status !== "completed" && status !== "running" && status !== "awaiting_confirmation" && "bg-surface-soft text-text-secondary",
  );
}

function formatClock(value: string): string {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function formatDuration(startedAt: string, finishedAt: string): string {
  const start = Date.parse(startedAt);
  const end = Date.parse(finishedAt);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return "—";
  const minutes = Math.max(1, Math.round((end - start) / 60_000));
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return remainder ? `${hours} 小时 ${remainder} 分钟` : `${hours} 小时`;
}

function substageTimingLabel(task: Task, subStageInstanceId: string): string | null {
  const substage = (task.stageDetails ?? [])
    .flatMap((detail) => detail.subStages ?? [])
    .find((item) => item.subStageInstanceId === subStageInstanceId);
  if (!substage?.startedAt) return null;
  const started = formatClock(substage.startedAt);
  if (!substage.finishedAt) return started ? `${started} 开始` : null;
  const finished = formatClock(substage.finishedAt);
  const duration = formatDuration(substage.startedAt, substage.finishedAt);
  return [started && finished ? `${started}–${finished}` : "", duration !== "—" ? `用时 ${duration}` : ""]
    .filter(Boolean)
    .join(" · ") || null;
}

function stageDurationLabel(task: Task, stageKey?: TaskStage): string {
  if (!stageKey) return "—";
  const detail = (task.stageDetails ?? []).find((item) => item.stageKey === stageKey);
  const substages = detail?.subStages ?? [];
  const starts = substages.map((item) => item.startedAt).filter((value): value is string => Boolean(value));
  const ends = substages.map((item) => item.finishedAt).filter((value): value is string => Boolean(value));
  if (!starts.length) return "—";
  if (ends.length < starts.length) return "进行中";
  const startedAt = starts.reduce((earliest, value) => Date.parse(value) < Date.parse(earliest) ? value : earliest);
  const finishedAt = ends.reduce((latest, value) => Date.parse(value) > Date.parse(latest) ? value : latest);
  return formatDuration(startedAt, finishedAt);
}

function formatTokenCount(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

const statCardClass = cn(
  "relative flex min-h-11 min-w-0 flex-col justify-center gap-1 overflow-hidden rounded-xl border border-border bg-surface px-4 py-3.5 text-left",
);

const statLinkClass = cn(
  statCardClass,
  "group font-inherit transition-[background-color,border-color,box-shadow] duration-150",
  "hover:border-border-strong hover:bg-surface-soft hover:shadow-sm",
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-strong",
);

function StatLinkCard({
  testId,
  value,
  label,
  ariaLabel,
  onClick,
}: {
  testId: string;
  value: number;
  label: string;
  ariaLabel: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={statLinkClass}
      onClick={onClick}
      data-testid={testId}
      aria-label={ariaLabel}
    >
      <span
        className="absolute inset-y-0 left-0 w-1 rounded-l-xl bg-accent-strong opacity-0 transition-opacity duration-150 group-hover:opacity-100"
        aria-hidden
      />
      <span className="text-xl font-semibold tabular-nums leading-tight text-accent-strong">{value}</span>
      <span className="flex items-center gap-1 truncate text-sm font-medium text-text-secondary group-hover:text-accent-strong">
        {label}
        <RightOutlined className="text-3xs text-text-muted transition-colors duration-150 group-hover:text-accent-strong" aria-hidden />
      </span>
    </button>
  );
}

export default function TaskDetailPage() {
  const { taskId } = useParams<{ taskId: string }>();
  const navigate = useNavigate();
  const { preferences } = useAppPreferences();
  const { logout } = useAuth();
  const language: UiLanguage = preferences.language;
  const effectiveTaskId = taskId === "current" ? readCurrentTask() : taskId;
  const labels = getStatusLabels(language);
  const copy = language === "en-US"
    ? {
        loading: "Loading project…",
        notFound: "Project not found",
        invalidTask: "Please select a valid project from the Project Board.",
        missingRemote: TASK_LOAD_EN.notFound,
        board: "Project Board",
        openWorkbench: "Open Workbench",
        overview: "Project Overview",
        current: "Current stage",
        updated: "Updated",
        workflow: "Workflow",
        statistics: "Project Statistics",
        artifacts: "Artifacts",
        completedStages: "Completed stages",
        completedSteps: "Completed steps",
        activities: "Research activities",
        noStageRecord: "No stage record yet",
        noSubstages: "No research steps yet",
        progressUnavailable: "Research progress is temporarily unavailable.",
        statusPending: "Status pending",
      }
    : {
        loading: "加载项目…",
        notFound: "未找到项目",
        invalidTask: "请从项目看板选择有效项目。",
        missingRemote: TASK_LOAD_ZH.notFound,
        board: "项目看板",
        openWorkbench: "进入工作台",
        overview: "项目概览",
        current: "当前阶段",
        updated: "最近更新",
        workflow: "工作流",
        statistics: "项目统计",
        artifacts: "产物",
        completedStages: "已完成阶段",
        completedSteps: "已完成步骤",
        activities: "研究活动",
        noStageRecord: "暂无阶段记录",
        noSubstages: "暂无研究步骤",
        progressUnavailable: "当前研究进展暂不可用，请稍后刷新。",
        statusPending: "状态待确认",
      };

  const [task, setTask] = useState<Task | null | undefined>(undefined);
  const [bench, setBench] = useState<WorkbenchTaskModel | null>(null);
  const [loadError, setLoadError] = useState<{ kind: string; message: string } | null>(null);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [expandedStages, setExpandedStages] = useState<string[]>([]);
  const [totalTokens, setTotalTokens] = useState<number | null>(null);
  const [statsModal, setStatsModal] = useState<"skills" | "agents" | null>(null);
  const [publishingCommunity, setPublishingCommunity] = useState(false);
  // debug.md #6: publish-side granularity choice (full vs template-only).
  const [publishModalOpen, setPublishModalOpen] = useState(false);
  const [publishGranularity, setPublishGranularity] = useState<"full" | "template">("full");
  const renameInFlightRef = useRef<Promise<unknown> | null>(null);
  const editCancelledRef = useRef(false);
  const activeTaskRef = useRef(effectiveTaskId);

  useEffect(() => {
    activeTaskRef.current = effectiveTaskId;
    if (!effectiveTaskId) {
      setTask(null);
      setBench(null);
      setLoadError({ kind: "loadFailed", message: copy.invalidTask });
      return;
    }
    let cancelled = false;
    setTask(undefined);
    setBench(null);
    setTotalTokens(null);
    setLoadError(null);
    loadTaskForPages(effectiveTaskId, language, { allowFixtureFallback: false })
      .then((loaded) => {
        if (cancelled || activeTaskRef.current !== effectiveTaskId) return;
        if (!loaded) {
          setTask(null);
          setLoadError({ kind: "notFound", message: copy.missingRemote });
          return;
        }
        rememberCurrentTask(loaded.task.taskId);
        setTask(loaded.task);
        setBench(loaded.model);
        void fetchTaskTokenUsage(effectiveTaskId)
          .then((usage) => {
            if (!cancelled && activeTaskRef.current === effectiveTaskId) setTotalTokens(usage.totalTokens);
          })
          .catch(() => {
            if (!cancelled && activeTaskRef.current === effectiveTaskId) setTotalTokens(null);
          });
      })
      .catch((error) => {
        if (cancelled || activeTaskRef.current !== effectiveTaskId) return;
        setTask(null);
        setLoadError({ kind: classifyTaskLoadFailure(error), message: userFacingTaskLoadError(error, language) });
      });
    return () => {
      cancelled = true;
    };
  }, [copy.invalidTask, copy.missingRemote, effectiveTaskId, language]);

  const currentStage = useMemo(() => {
    if (!bench) return undefined;
    return bench.stages.find((stage) => stage.stage === bench.currentUnderlyingStage)
      ?? [...bench.stages].reverse().find((stage) => stage.status !== "not_started");
  }, [bench]);

  const phaseRows = useMemo(() => (
    PAGE_PHASE_ORDER.map((phase) => ({
      phase,
      stage: bench?.stages.find((stage) => stage.pagePhase === phase),
    }))
  ), [bench]);

  useEffect(() => {
    if (!bench || bench.stages.length === 0) {
      setExpandedStages([]);
      return;
    }
    const preferred = currentStage
      ?? [...bench.stages].reverse().find((stage) => stage.status === "completed")
      ?? bench.stages[0];
    setExpandedStages(preferred ? [preferred.pagePhase] : []);
  }, [bench?.taskId, currentStage?.pagePhase]);

  const handleOpenWorkbench = useCallback(() => {
    if (!effectiveTaskId) return;
    rememberCurrentTask(effectiveTaskId);
    rememberCurrentArtifact(null);
    navigate(`/workbench/${encodeURIComponent(effectiveTaskId)}`);
  }, [effectiveTaskId, navigate]);

  const handlePublishToCommunity = useCallback(async () => {
    if (!task || publishingCommunity) return;
    setPublishingCommunity(true);
    try {
      await publishToCommunity({
        resourceType: "task",
        resourceId: task.taskId,
        recommendedGranularity: publishGranularity,
      });
      message.success(language === "en-US" ? "Published to community." : "已发布到社区");
      setPublishModalOpen(false);
    } catch {
      message.error(language === "en-US" ? "Failed to publish to community." : "发布到社区失败");
    } finally {
      setPublishingCommunity(false);
    }
  }, [language, publishingCommunity, task, publishGranularity]);

  const handleStartEditTitle = () => {
    if (!task) return;
    editCancelledRef.current = false;
    setTitleDraft(task.title);
    setEditingTitle(true);
  };

  const handleCancelEditTitle = () => {
    editCancelledRef.current = true;
    setEditingTitle(false);
  };

  const handleSaveTitle = async () => {
    if (!task || editCancelledRef.current || renameInFlightRef.current) return;
    const trimmed = titleDraft.trim();
    if (!trimmed) {
      message.error(language === "en-US" ? "Title cannot be empty." : "标题不能为空");
      return;
    }
    if (trimmed === task.title) {
      setEditingTitle(false);
      return;
    }
    const originalTask = task;
    setTask({ ...task, title: trimmed });
    setBench((value) => value ? { ...value, title: trimmed } : value);
    setEditingTitle(false);
    const request = renameTask(task.taskId, trimmed);
    renameInFlightRef.current = request;
    try {
      const updated = await request;
      if (activeTaskRef.current !== originalTask.taskId) return;
      setTask(updated);
      setBench(workbenchModelFromApiTask(updated));
    } catch {
      if (activeTaskRef.current !== originalTask.taskId) return;
      setTask(originalTask);
      setBench(workbenchModelFromApiTask(originalTask));
      message.error(language === "en-US" ? "Failed to rename project." : "修改标题失败");
    } finally {
      if (renameInFlightRef.current === request) renameInFlightRef.current = null;
    }
  };

  if (task === undefined) {
    return (
      <div className="px-12 py-12 text-center">
        <Spin size="large" />
        <Typography.Paragraph type="secondary">{copy.loading}</Typography.Paragraph>
      </div>
    );
  }

  if (!task || !bench) {
    const isAuthError = loadError?.kind === "unauthorized";
    return (
      <div>
        <Alert type={isAuthError ? "warning" : "error"} showIcon message={loadError?.message ?? copy.notFound} />
        <div className="mt-4 flex gap-2">
          <Link to="/workbench"><Button type="primary">{copy.board}</Button></Link>
          {isAuthError ? (
            <Button onClick={() => { logout(); window.location.href = "/login"; }}>重新登录</Button>
          ) : null}
        </div>
      </div>
    );
  }

  const displayStatus = bench.taskStatus;
  const topStatusLabel = labels[displayStatus] ?? copy.statusPending;
  const skillSummaries = Array.from(new Set(bench.capabilityCalls.map((call) => call.name).filter(Boolean))).map((name) => ({
    key: name,
    label: labelForSkill(name),
    count: bench.capabilityCalls.filter((call) => call.name === name).length,
  }));
  const agentSummaries = Array.from(new Set(bench.capabilityCalls.map((call) => call.targetAgent).filter((value): value is string => Boolean(value)))).map((targetAgent, index) => {
    const calls = bench.capabilityCalls.filter((call) => call.targetAgent === targetAgent);
    const primarySkill = calls[0]?.name;
    return {
      key: targetAgent,
      label: primarySkill ? `${labelForSkill(primarySkill)} 智能体` : `研究智能体 ${index + 1}`,
      count: calls.length,
    };
  });
  const skillCategoryCount = skillSummaries.length;
  const agentCategoryCount = agentSummaries.length;

  return (
    <div>
      <header className="mb-8 border-b border-border pb-6" aria-labelledby="project-overview-heading">
        <div className="flex items-start justify-between gap-6 max-md:flex-col">
          <div className="min-w-0 flex-1">
            {editingTitle ? (
              <Input
                data-testid="task-detail-title-input"
                value={titleDraft}
                onChange={(event) => setTitleDraft(event.target.value)}
                onPressEnter={() => void handleSaveTitle()}
                onKeyDown={(event) => {
                  if (event.key === "Escape") handleCancelEditTitle();
                }}
                onBlur={() => void handleSaveTitle()}
                autoFocus
                maxLength={200}
                size="large"
                className="max-w-3xl"
              />
            ) : (
              <div className="flex items-center gap-2">
                <Typography.Title level={2} className="m-0! text-2xl! font-semibold! text-text!">
                  {task.title}
                </Typography.Title>
                <Button
                  data-testid="task-detail-edit-title"
                  type="text"
                  size="small"
                  icon={<EditOutlined />}
                  aria-label={language === "en-US" ? "Edit title" : "编辑标题"}
                  onClick={handleStartEditTitle}
                />
              </div>
            )}
            <Typography.Paragraph className="mb-0! mt-2! max-w-4xl text-sm! leading-relaxed! text-text-secondary!" ellipsis={{ rows: 2 }}>
              {task.goalSummary || bench.goalSummary || "—"}
            </Typography.Paragraph>
            <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm text-text-muted">
              <span className={taskStatusClass(displayStatus)} data-testid="task-detail-execution-status">
                <span className={cn("h-2 w-2 rounded-full", displayStatus === "completed" ? "bg-success" : "bg-accent-strong")} />
                {topStatusLabel}
              </span>
              <span>
                {copy.current}：<strong className="font-medium text-text-secondary">{currentStage ? stageDisplayLabel(currentStage.pagePhase, language) : "—"}</strong>
              </span>
              <span>{copy.updated}：{formatBeijingDate(bench.updatedAt)}</span>
            </div>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <Button
              icon={<ShareAltOutlined />}
              onClick={() => {
                setPublishGranularity("full");
                setPublishModalOpen(true);
              }}
              data-testid="task-detail-publish-community"
            >
              {language === "en-US" ? "Publish to community" : "发布到社区"}
            </Button>
            <Button type="primary" icon={<ArrowRightOutlined />} iconPosition="end" onClick={handleOpenWorkbench}>
              {copy.openWorkbench}
            </Button>
          </div>
        </div>
      </header>

      {bench.finalResult ? (
        <FinalResultPanel
          result={bench.finalResult}
          artifacts={bench.artifacts}
          language={language}
          className="mb-8"
        />
      ) : null}

      <section className="mb-8" aria-labelledby="task-workflow-heading">
        <Typography.Title id="task-workflow-heading" level={4} className="mb-3! text-lg! font-semibold! text-text!">
          {copy.workflow}
        </Typography.Title>

        {bench.stages.length === 0 ? (
          <Alert type="warning" showIcon message={copy.progressUnavailable} data-testid="task-detail-projection-fail-visible" />
        ) : (
          <Collapse
            activeKey={expandedStages}
            onChange={(keys) => setExpandedStages(Array.isArray(keys) ? keys.map(String) : [String(keys)])}
            expandIconPosition="end"
            ghost
            className="grid gap-3 [&_.ant-collapse-item]:overflow-hidden [&_.ant-collapse-item]:rounded-xl [&_.ant-collapse-item]:border! [&_.ant-collapse-item]:border-border! [&_.ant-collapse-item]:bg-surface! [&_.ant-collapse-header]:items-center! [&_.ant-collapse-header]:px-4! [&_.ant-collapse-header]:py-3.5! [&_.ant-collapse-content]:border-t! [&_.ant-collapse-content]:border-border! [&_.ant-collapse-content-box]:p-0!"
            expandIcon={({ isActive }) => isActive ? <DownOutlined /> : <RightOutlined />}
            items={phaseRows.map(({ phase, stage }) => ({
              key: phase,
              label: (
                <div className="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-5 pr-2">
                  <span className="truncate text-base font-semibold text-text">{stageDisplayLabel(phase, language)}</span>
                  {stage ? (
                    <span className={statusTone(stage.status)} data-testid={`task-detail-stage-status-${stage.stage}`}>
                      <StatusIcon status={stage.status} />
                      {statusLabel(stage.status, language)}
                      <span className="font-normal text-text-muted">· {stageDurationLabel(task, stage.stage)}</span>
                    </span>
                  ) : (
                    <span className="text-sm text-text-muted">{copy.noStageRecord}</span>
                  )}
                </div>
              ),
              children: (
                <div className="divide-y divide-border">
                  {stage?.subStages.length ? stage.subStages.map((substage) => {
                    const artifacts = artifactsForSubstage(bench, substage);
                    return (
                      <div
                        key={substage.id}
                        className="grid min-h-14 grid-cols-[24px_minmax(0,1fr)_minmax(180px,auto)] items-center gap-3 px-4 py-2.5 max-sm:grid-cols-[24px_minmax(0,1fr)]"
                        data-testid={`task-detail-substage-${substage.id}`}
                      >
                        <span className={statusTone(substage.status)} aria-label={statusLabel(substage.status, language)}>
                          <StatusIcon status={substage.status} />
                        </span>
                        <div className="min-w-0">
                          <div className="truncate text-sm font-medium text-text" title={substage.label}>{substage.label}</div>
                          {substageTimingLabel(task, substage.id) ? (
                            <div className="mt-0.5 text-xs text-text-muted">{substageTimingLabel(task, substage.id)}</div>
                          ) : null}
                        </div>
                        <div className="flex min-w-0 flex-wrap justify-end gap-1.5 max-sm:col-span-2 max-sm:col-start-2 max-sm:justify-start">
                          {artifacts.length > 0 ? artifacts.map((artifact) => (
                            <Button
                              key={artifact.id}
                              size="small"
                              className="h-7! max-w-56 rounded-md! px-2! text-xs!"
                              title={researchArtifactTitle(artifact)}
                              data-testid={`task-detail-artifact-${artifact.id}`}
                              onClick={(event) => {
                                event.stopPropagation();
                                rememberCurrentTask(task.taskId);
                                rememberCurrentArtifact(artifact.id);
                                navigate(`/artifacts?sourceTaskId=${encodeURIComponent(task.taskId)}&artifactId=${encodeURIComponent(artifact.id)}`);
                              }}
                            >
                              <span className="truncate">{researchArtifactTitle(artifact)}</span>
                            </Button>
                          )) : <span className="text-sm text-text-muted">—</span>}
                        </div>
                      </div>
                    );
                  }) : (
                    <div className="px-4 py-4 text-sm text-text-muted">{copy.noSubstages}</div>
                  )}
                </div>
              ),
            }))}
          />
        )}
      </section>

      <section aria-labelledby="task-stats-heading">
        <Typography.Title id="task-stats-heading" level={4} className="mb-3! text-lg! font-semibold! text-text!">
          {copy.statistics}
        </Typography.Title>
        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <div className={statCardClass} data-testid="task-detail-total-tokens">
              <span className="text-xl font-semibold tabular-nums leading-tight text-text">
                {formatTokenCount(totalTokens)}
              </span>
              <span className="truncate text-sm font-medium text-text-secondary">总 Tokens</span>
            </div>
            <StatLinkCard
              testId="task-detail-all-artifacts"
              value={bench.artifacts.length}
              label="总产物数"
              ariaLabel={`总产物数，${bench.artifacts.length}，查看全部产物`}
              onClick={() => navigate(`/artifacts?sourceTaskId=${encodeURIComponent(task.taskId)}`)}
            />
            <StatLinkCard
              testId="task-detail-skills-summary"
              value={skillCategoryCount}
              label="技能类别数"
              ariaLabel={`技能类别数，${skillCategoryCount}，查看明细`}
              onClick={() => setStatsModal("skills")}
            />
            <StatLinkCard
              testId="task-detail-agents-summary"
              value={agentCategoryCount}
              label="智能体类别数"
              ariaLabel={`智能体类别数，${agentCategoryCount}，查看明细`}
              onClick={() => setStatsModal("agents")}
            />
          </div>

          <div className="rounded-xl border border-border bg-surface px-4 py-3.5" data-testid="task-detail-stage-durations">
            <div className="text-sm font-medium text-text">阶段耗时</div>
            <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
              {phaseRows.map(({ phase, stage }) => {
                const duration = stageDurationLabel(task, stage?.stage);
                return (
                  <div key={phase} className="min-w-0">
                    <div className="truncate text-xs text-text-muted">
                      {language === "en-US" ? STAGE_LABELS_EN[phase] : STAGE_LABELS_ZH[phase]}
                    </div>
                    <div
                      className={cn(
                        "mt-0.5 text-sm font-medium tabular-nums",
                        duration === "进行中" && "text-accent-strong",
                        duration === "—" && "text-text-muted",
                        duration !== "进行中" && duration !== "—" && "text-text",
                      )}
                    >
                      {duration}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </section>

      <Modal
        open={statsModal !== null}
        title={statsModal === "skills" ? "技能使用明细" : "智能体使用明细"}
        footer={null}
        width={520}
        onCancel={() => setStatsModal(null)}
      >
        <div className="divide-y divide-border">
          {(statsModal === "skills" ? skillSummaries : agentSummaries).map((item) => (
            <div key={item.key} className="flex items-center justify-between gap-4 py-3">
              <span className="text-sm font-medium text-text">{item.label}</span>
              <span className="shrink-0 text-xs text-text-muted">使用 {item.count} 次</span>
            </div>
          ))}
          {(statsModal === "skills" ? skillSummaries : agentSummaries).length === 0 ? (
            <div className="py-6 text-center text-sm text-text-muted">暂无记录</div>
          ) : null}
        </div>
      </Modal>

      {/* debug.md #6: publish granularity choice */}
      <Modal
        open={publishModalOpen}
        title={language === "en-US" ? "Publish to community" : "发布到社区"}
        okText={language === "en-US" ? "Publish" : "发布"}
        cancelText={language === "en-US" ? "Cancel" : "取消"}
        confirmLoading={publishingCommunity}
        okButtonProps={{ loading: publishingCommunity }}
        onCancel={() => (publishingCommunity ? null : setPublishModalOpen(false))}
        onOk={handlePublishToCommunity}
        destroyOnClose
      >
        <div className="flex flex-col gap-3">
          <Typography.Paragraph className="mb-0! text-sm text-text-secondary">
            选择发布粒度（作为给其他用户的推荐；克隆者仍可自行选择）：
          </Typography.Paragraph>
          <Radio.Group
            value={publishGranularity}
            onChange={(event) => setPublishGranularity(event.target.value)}
            className="flex flex-col gap-2"
          >
            <Radio value="full">
              <span className="text-sm font-medium text-text">完整项目</span>
              <div className="text-xs text-text-muted">推荐克隆时带入全部产物</div>
            </Radio>
            <Radio value="template">
              <span className="text-sm font-medium text-text">工作流模板</span>
              <div className="text-xs text-text-muted">推荐克隆时仅带研究设计，从空开始</div>
            </Radio>
          </Radio.Group>
        </div>
      </Modal>
    </div>
  );
}
