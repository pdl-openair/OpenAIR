import { useMemo, useState } from "react";
import { DownOutlined, FileTextOutlined, RightOutlined, TeamOutlined } from "@ant-design/icons";
import { Button, Tag, Typography } from "antd";
import type {
  CollaborationAgentProjection,
  CollaborationHandoffProjection,
  CollaborationProjection,
  CollaborationReference,
} from "../../api/client";
import type { WorkbenchScreenViewModel } from "../../model/workbenchScreenView";
import type { WorkbenchTaskModel } from "../../model/workbenchTask";

type StatusCategory = "idle" | "active" | "waiting" | "completed" | "failed" | "unknown";

type CollaborationStatusPresentation = {
  label: string;
  color: "default" | "processing" | "warning" | "success" | "error";
  category: StatusCategory;
};

type AgentWithWorkUnit = CollaborationAgentProjection & { workUnitId?: string | null };

type VisibleAgent = {
  key: string;
  workUnitId: string | null;
  agentId: string;
  name: string;
  responsibility: string;
  objective: string;
  status: CollaborationStatusPresentation;
  summary: string;
  outputArtifactRefs: CollaborationReference[] | null;
  handoff: CollaborationHandoffProjection | null;
  assignedAt: string | null;
  startedAt: string | null;
  completedAt: string | null;
  supportingActivities: CollaborationAgentProjection["supportingActivities"];
};

type TimelineEntry =
  | { kind: "agent"; key: string; occurredAt: string | null; sortAt: number; agent: VisibleAgent }
  | { kind: "event"; key: string; occurredAt: string | null; sortAt: number; title: string; summary: string }
  | { kind: "handoff"; key: string; occurredAt: string | null; sortAt: number; handoff: CollaborationHandoffProjection }
  | { kind: "decision"; key: string; occurredAt: string | null; sortAt: number; reason: string }
  | { kind: "artifact"; key: string; occurredAt: string | null; sortAt: number; artifact: CollaborationReference }
  | { kind: "failure"; key: string; occurredAt: null; sortAt: number };

export interface AgentCollaborationOverviewProps {
  taskId: string;
  model: WorkbenchTaskModel;
  screen: WorkbenchScreenViewModel;
  /** The parent must pass screen.matchedCollaboration, never an unfenced response. */
  collaboration?: CollaborationProjection | null;
  onSelectArtifact?: (artifactId: string) => void;
}

const AGENT_NAME_LABELS: Record<string, string> = {
  research_question_refinement: "研究问题凝练 智能体",
  key_literature_positioning: "关键文献定位 智能体",
  paper_evidence_extraction: "论文证据提取 智能体",
  research_gap_analysis: "研究缺口分析 智能体",
  result_reliability_audit: "结果可靠性核查 智能体",
  validation_design: "验证方案设计 智能体",
  general_research_agent: "通用研究 智能体",
  openair_summary_agent: "研究总结 智能体",
  openair_review_agent: "结果核查 智能体",
  openair_presentation_guidance_agent: "研究引导 智能体",
};

const AGENT_RESPONSIBILITY_LABELS: Record<string, string> = {
  research_question_refinement: "凝练研究问题、边界与成功标准",
  key_literature_positioning: "定位、筛选并组织关键文献",
  paper_evidence_extraction: "从已有材料中提取可追溯证据",
  research_gap_analysis: "分析证据缺口与待验证问题",
  result_reliability_audit: "核查结果可信度与可复现性",
  validation_design: "设计验证方案、对照与停止条件",
  general_research_agent: "承接通用研究工作并整理结果",
  openair_summary_agent: "汇总阶段进展与结论",
  openair_review_agent: "核查产物质量与使用边界",
  openair_presentation_guidance_agent: "整理用户可见说明与下一步",
};

const UNAVAILABLE = "暂不可用";

function statusPresentation(status?: string | null): CollaborationStatusPresentation {
  switch (status) {
    case "not_started":
    case "created":
      return { label: "尚未开始", color: "default", category: "idle" };
    case "planned":
    case "queued":
    case "assigned":
    case "preparing":
    case "dispatched":
      return { label: "准备中", color: "default", category: "idle" };
    case "executing":
    case "running":
    case "in_progress":
    case "started":
    case "system_reviewing":
      return { label: status === "system_reviewing" ? "系统核查中" : "进行中", color: "processing", category: "active" };
    case "awaiting_user_confirmation":
    case "awaiting_confirmation":
      return { label: "等待确认", color: "warning", category: "waiting" };
    case "research_design_incomplete":
      return { label: "研究方案未形成", color: "warning", category: "waiting" };
    case "needs_input":
      return { label: "等待用户补充", color: "warning", category: "waiting" };
    case "blocked":
    case "blocked_recoverable":
      return { label: "执行受阻", color: "warning", category: "waiting" };
    case "completed":
    case "succeeded":
    case "success":
      return { label: "已完成", color: "success", category: "completed" };
    case "failed":
    case "rejected":
    case "external_result_unknown":
      return { label: status === "external_result_unknown" ? "外部结果未知" : "未成功", color: "error", category: "failed" };
    case "cancelled":
    case "stopped":
    case "stopped_by_user":
      return { label: "已停止", color: "default", category: "idle" };
    case "skipped":
      return { label: "已跳过", color: "default", category: "idle" };
    default:
      return { label: "状态暂不可用", color: "default", category: "unknown" };
  }
}

function safeNarrative(value?: string | null, fallback = UNAVAILABLE): string {
  const text = value?.replace(/\s+/g, " ").trim();
  if (!text) return fallback;
  if (/^(\{|\[)|traceback|stack trace|\bat\s+\S+\s*\(|provider\s*[=:]|exception\s*:/i.test(text)) {
    return fallback;
  }
  return text.length > 180 ? `${text.slice(0, 180)}...` : text;
}

function containsChinese(value?: string | null): boolean {
  return Boolean(value && /[\u3400-\u9fff]/.test(value));
}

function agentName(agentId: string, value?: string | null): string {
  if (containsChinese(value)) return value!.trim();
  return AGENT_NAME_LABELS[agentId] ?? "研究协作 智能体";
}

function agentResponsibility(agentId: string, title?: string | null, objective?: string | null): string {
  return AGENT_RESPONSIBILITY_LABELS[agentId] ?? safeNarrative(title, safeNarrative(objective));
}

function timestamp(value?: string | null): number {
  if (!value) return 0;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function timelineTime(value?: string | null): string {
  const parsed = timestamp(value);
  if (!parsed) return "时间暂不可用";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

function timelineDotClass(entry: TimelineEntry): string {
  if (entry.kind === "failure") return "border-danger bg-danger";
  if (entry.kind === "decision") return "border-warning bg-warning";
  if (entry.kind === "artifact") return "border-success bg-success";
  if (entry.kind === "handoff") return "border-accent-strong bg-accent-strong";
  if (entry.kind === "agent") {
    if (entry.agent.status.category === "failed") return "border-danger bg-danger";
    if (entry.agent.status.category === "waiting") return "border-warning bg-warning";
    if (entry.agent.status.category === "completed") return "border-success bg-success";
    if (entry.agent.status.category === "active") return "border-accent-strong bg-accent-strong";
  }
  return "border-border-strong bg-surface";
}

function dedupeReferences(refs: CollaborationReference[]): CollaborationReference[] {
  const seen = new Set<string>();
  return refs.filter((ref) => {
    if (!ref.id || seen.has(ref.id)) return false;
    seen.add(ref.id);
    return true;
  });
}

function dedupeHandoffs(handoffs: CollaborationHandoffProjection[]): CollaborationHandoffProjection[] {
  const seen = new Set<string>();
  return handoffs.filter((handoff) => {
    const key = [handoff.fromAgentId, handoff.toAgentId, handoff.summary, handoff.occurredAt].join("|");
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function ArtifactLinks({
  refs,
  onSelectArtifact,
  unavailableWhenMissing = false,
}: {
  refs: CollaborationReference[] | null;
  onSelectArtifact?: (artifactId: string) => void;
  unavailableWhenMissing?: boolean;
}) {
  if (refs === null) return <span className="text-text-muted">{UNAVAILABLE}</span>;
  if (!refs.length) {
    return <span className="text-text-muted">{unavailableWhenMissing ? UNAVAILABLE : "暂无已产出 Artifact"}</span>;
  }
  return (
    <span className="inline-flex flex-wrap gap-x-1 gap-y-0.5">
      {refs.map((ref) => (
        <Button
          key={ref.id}
          type="link"
          size="small"
          className="h-auto px-0! py-0! text-left text-xs"
          icon={<FileTextOutlined />}
          disabled={!onSelectArtifact}
          onClick={() => onSelectArtifact?.(ref.id)}
          data-testid="agent-collaboration-artifact"
        >
          {safeNarrative(ref.title, "研究产物")}
        </Button>
      ))}
    </span>
  );
}

function LabeledValue({ label, value, testId }: { label: string; value: string; testId?: string }) {
  return (
    <div className="grid min-w-0 grid-cols-[6.25rem_minmax(0,1fr)] gap-2 text-xs leading-snug max-[980px]:grid-cols-1 max-[980px]:gap-0.5">
      <span className="text-text-muted">{label}</span>
      <span className="min-w-0 wrap-anywhere text-text" data-testid={testId}>{value}</span>
    </div>
  );
}

export function AgentCollaborationOverview({
  taskId,
  model,
  screen,
  collaboration,
  onSelectArtifact,
}: AgentCollaborationOverviewProps) {
  const [expanded, setExpanded] = useState(true);
  const expectedRevision = model.progressRevision;
  const durableFenceValid = model.progressTrust.kind === "canonical"
    && expectedRevision !== null
    && model.taskId === taskId
    && screen.taskId === taskId
    && screen.revision === expectedRevision;
  const projectionFenceValid = Boolean(
    durableFenceValid
      && collaboration
      && collaboration.taskId === taskId
      && collaboration.revision === expectedRevision,
  );
  const projection = projectionFenceValid ? collaboration ?? null : null;

  const agents = useMemo<VisibleAgent[]>(() => {
    if (!durableFenceValid) return [];
    const rows: VisibleAgent[] = [];
    const seenWorkUnits = new Set<string>();
    const seenAgentOnly = new Set<string>();

    for (const raw of projection?.collaboration.actual ?? []) {
      if (!raw.isMember) continue;
      const agent = raw as AgentWithWorkUnit;
      const workUnitId = agent.workUnitId?.trim() || null;
      if (workUnitId) seenWorkUnits.add(workUnitId);
      else seenAgentOnly.add(agent.agentId);
      rows.push({
        key: workUnitId ?? `agent:${agent.agentId}:${agent.sequence}`,
        workUnitId,
        agentId: agent.agentId,
        name: agentName(agent.agentId, agent.agentName),
        responsibility: agentResponsibility(agent.agentId, agent.title, agent.objective),
        objective: safeNarrative(agent.objective, safeNarrative(agent.title)),
        status: statusPresentation(agent.status),
        summary: safeNarrative(agent.summary),
        outputArtifactRefs: dedupeReferences(agent.outputArtifactRefs),
        handoff: agent.handoff,
        assignedAt: agent.assignedAt,
        startedAt: agent.startedAt,
        completedAt: agent.completedAt,
        supportingActivities: agent.supportingActivities,
      });
    }

    model.collaborationNodes.forEach((node, index) => {
      const workUnitId = node.workUnitId?.trim() || null;
      const id = node.agentId?.trim() || "";
      if (!workUnitId && !id) return;
      if ((workUnitId && seenWorkUnits.has(workUnitId)) || (!workUnitId && seenAgentOnly.has(id))) return;
      rows.push({
        key: workUnitId ?? `durable-agent:${id}:${index}`,
        workUnitId,
        agentId: id,
        name: agentName(id, node.agentName),
        responsibility: agentResponsibility(id, node.title, node.objective),
        objective: safeNarrative(node.objective, safeNarrative(node.title)),
        status: statusPresentation(node.status),
        summary: UNAVAILABLE,
        outputArtifactRefs: null,
        handoff: null,
        assignedAt: node.startedAt ?? null,
        startedAt: node.startedAt ?? null,
        completedAt: node.completedAt ?? null,
        supportingActivities: [],
      });
    });

    return rows;
  }, [durableFenceValid, model.collaborationNodes, projection]);

  const handoffs = useMemo(() => {
    if (!projection) return [];
    return dedupeHandoffs([
      ...projection.collaboration.handoffs,
      ...agents.flatMap((agent) => agent.handoff ? [agent.handoff] : []),
    ]).sort((a, b) => timestamp(b.occurredAt) - timestamp(a.occurredAt));
  }, [agents, projection]);

  const globalArtifacts = useMemo(() => {
    if (!projection) return null;
    return dedupeReferences([
      ...projection.artifacts.items.map((artifact) => ({ id: artifact.artifactId, title: artifact.title })),
      ...agents.flatMap((agent) => agent.outputArtifactRefs ?? []),
      ...handoffs.flatMap((handoff) => handoff.artifactRefs),
    ]);
  }, [agents, handoffs, projection]);

  const pendingDecisions = projection?.collaboration.pendingDecisions ?? [];
  const needsUser = projection ? projection.collaboration.needsUserDecision || pendingDecisions.length > 0 : null;
  const activeAgents = agents.filter((agent) => agent.status.category === "active");
  const waitingAgents = agents.filter((agent) => agent.status.category === "waiting");
  const latestEvent = durableFenceValid
    ? [...model.collaborationEvents]
      .filter((event) => safeNarrative(event.summary, "") !== "")
      .sort((a, b) => timestamp(b.createdAt) - timestamp(a.createdAt))[0]
    : undefined;
  const completedAgents = agents
    .filter((agent) => agent.status.category === "completed" && agent.summary !== UNAVAILABLE)
    .sort((a, b) => timestamp(b.completedAt) - timestamp(a.completedAt));
  const currentPhase = durableFenceValid
    ? screen.activeSubstage
      ? `${screen.activeSubstage.stageLabel} / ${screen.activeSubstage.label}`
      : screen.leftStages.find((stage) => ["running", "executing", "in_progress", "needs_input", "awaiting_confirmation", "failed"].includes(stage.status))?.label
        ?? [...screen.leftStages].reverse().find((stage) => stage.status === "completed")?.label
        ?? UNAVAILABLE
    : UNAVAILABLE;
  const coordinating = activeAgents.length
    ? activeAgents.map((agent) => `${agent.name}：${agent.objective}`).join("；")
    : waitingAgents.length
      ? waitingAgents.map((agent) => `${agent.name}：${agent.status.label}`).join("；")
      : UNAVAILABLE;
  const latestProgress = latestEvent
    ? safeNarrative(latestEvent.summary)
    : completedAgents[0]?.summary ?? UNAVAILABLE;
  const conclusion = projection?.failure
    ? safeNarrative(projection.failure.resultTrustAndRetention)
    : completedAgents[0]?.summary ?? UNAVAILABLE;
  const overviewStatus = !durableFenceValid
    ? statusPresentation()
    : projection?.failure
      ? statusPresentation("failed")
      : needsUser
        ? statusPresentation("awaiting_user_confirmation")
        : activeAgents.length
          ? statusPresentation("running")
          : statusPresentation(projection?.status ?? screen.executionDisplayStatus);

  const timelineEntries = useMemo<TimelineEntry[]>(() => {
    if (!durableFenceValid) return [];
    const entries: TimelineEntry[] = agents.map((agent) => {
      const occurredAt = agent.completedAt ?? agent.startedAt ?? agent.assignedAt;
      return {
        kind: "agent",
        key: `agent:${agent.key}`,
        occurredAt,
        sortAt: timestamp(occurredAt),
        agent,
      };
    });
    const agentByWorkUnit = new Map(
      agents.filter((agent) => agent.workUnitId).map((agent) => [agent.workUnitId!, agent]),
    );

    model.collaborationEvents.forEach((event, index) => {
      const summary = safeNarrative(event.summary, "");
      if (!summary) return;
      const source = event.sourceWorkUnitId ? agentByWorkUnit.get(event.sourceWorkUnitId) : undefined;
      const target = event.targetWorkUnitId ? agentByWorkUnit.get(event.targetWorkUnitId) : undefined;
      const title = source && target
        ? `${source.name} 向 ${target.name} 更新协作进展`
        : source
          ? `${source.name} 更新协作进展`
          : "协作进展更新";
      entries.push({
        kind: "event",
        key: `event:${event.interactionId ?? index}`,
        occurredAt: event.createdAt ?? null,
        sortAt: timestamp(event.createdAt),
        title,
        summary,
      });
    });

    handoffs.forEach((handoff, index) => entries.push({
      kind: "handoff",
      key: `handoff:${handoff.fromAgentId}:${handoff.toAgentId}:${index}`,
      occurredAt: handoff.occurredAt,
      sortAt: timestamp(handoff.occurredAt),
      handoff,
    }));
    pendingDecisions.forEach((decision) => entries.push({
      kind: "decision",
      key: `decision:${decision.gateId}`,
      occurredAt: decision.createdAt,
      sortAt: timestamp(decision.createdAt) || Number.MAX_SAFE_INTEGER - 1,
      reason: safeNarrative(decision.reason, "需要你确认后继续"),
    }));
    projection?.artifacts.items.forEach((artifact) => entries.push({
      kind: "artifact",
      key: `artifact:${artifact.artifactId}`,
      occurredAt: artifact.createdAt,
      sortAt: timestamp(artifact.createdAt),
      artifact: { id: artifact.artifactId, title: artifact.title },
    }));
    if (projection?.failure) {
      entries.push({ kind: "failure", key: "current-failure", occurredAt: null, sortAt: Number.MAX_SAFE_INTEGER });
    }
    return entries.sort((a, b) => b.sortAt - a.sortAt);
  }, [agents, durableFenceValid, handoffs, model.collaborationEvents, pendingDecisions, projection]);

  return (
    <section
      className="shrink-0 overflow-hidden rounded-md border border-border bg-surface"
      data-testid="agent-collaboration-overview"
      data-task-id={taskId}
      data-revision={screen.revision}
      data-fence-valid={durableFenceValid ? "true" : "false"}
      aria-label="智能体协作概览"
    >
      <button
        type="button"
        className="grid w-full cursor-pointer grid-cols-[auto_minmax(0,1fr)_auto_auto] items-center gap-2 border-0 bg-surface-soft px-3 py-2 text-left text-inherit max-[760px]:grid-cols-[auto_auto_auto_auto] max-[760px]:justify-start"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        data-testid="agent-collaboration-overview-toggle"
      >
        <TeamOutlined className="text-accent-strong" />
        <span className="min-w-0">
          <Typography.Text strong>智能体协作概览</Typography.Text>
          <Typography.Text type="secondary" className="ml-2 text-xs max-[760px]:hidden">
            {agents.length ? `${agents.length} 个真实协作成员` : "尚无可核验的协作成员"}
          </Typography.Text>
        </span>
        <Tag color={overviewStatus.color} className="m-0!" data-testid="agent-collaboration-overview-status">
          {needsUser === true ? "需要你处理" : overviewStatus.label}
        </Tag>
        {expanded ? <DownOutlined /> : <RightOutlined />}
      </button>

      {expanded ? (
        <div className="max-h-[10rem] overflow-y-auto" data-testid="agent-collaboration-overview-content">
          <div className="border-b border-border px-3 py-2.5" data-testid="main-agent-summary">
            <div className="mb-2 flex items-center justify-between gap-2">
              <Typography.Text strong>主智能体 · 研究总控</Typography.Text>
              <Tag color={overviewStatus.color} className="m-0!">{overviewStatus.label}</Tag>
            </div>
            <div className="grid min-w-0 grid-cols-3 gap-x-5 gap-y-1.5 max-[1100px]:grid-cols-2 max-[760px]:grid-cols-1">
              <LabeledValue label="当前目标" value={durableFenceValid ? safeNarrative(model.goalSummary) : UNAVAILABLE} testId="main-agent-goal" />
              <LabeledValue label="当前阶段" value={currentPhase} testId="main-agent-phase" />
              <LabeledValue label="正在协调" value={coordinating} testId="main-agent-coordination" />
              <LabeledValue label="最近实质推进" value={latestProgress} testId="main-agent-latest-progress" />
              <LabeledValue
                label="是否等待用户"
                value={needsUser === null ? UNAVAILABLE : needsUser ? pendingDecisions.map((item) => safeNarrative(item.reason, "需要你确认后继续")).join("；") || "是" : "否"}
                testId="main-agent-needs-user"
              />
              <LabeledValue label="汇总结论" value={conclusion} testId="main-agent-conclusion" />
            </div>
          </div>

          <div className="min-w-0 px-3 py-2.5" data-testid="sub-agent-list">
            <div className="mb-2 flex items-center justify-between gap-2">
              <Typography.Text strong>协作时间流</Typography.Text>
              <Typography.Text type="secondary" className="text-xs">最近优先 · 仅同版本真实运行记录</Typography.Text>
            </div>
            {!durableFenceValid || timelineEntries.length === 0 ? (
              <Typography.Text type="secondary" data-testid="sub-agents-unavailable">
                真实智能体时间流暂不可用；规划内容不会在这里显示为已运行。
              </Typography.Text>
            ) : (
              <div data-testid="agent-collaboration-timeline">
                {timelineEntries.map((entry, index) => (
                  <div className="relative grid min-w-0 grid-cols-[5.25rem_1.25rem_minmax(0,1fr)] gap-1.5 pb-2.5 last:pb-0 max-[760px]:grid-cols-[4.5rem_1rem_minmax(0,1fr)]" key={entry.key} data-testid={`collaboration-timeline-${entry.kind}`}>
                    <time className="pt-0.5 text-right text-[11px] leading-snug text-text-muted">{entry.kind === "failure" ? "当前" : timelineTime(entry.occurredAt)}</time>
                    <div className="relative flex justify-center pt-1.5" aria-hidden="true">
                      {index < timelineEntries.length - 1 ? <span className="absolute top-3 bottom-[-0.75rem] w-px bg-border" /> : null}
                      <span className={`relative z-1 h-2.5 w-2.5 shrink-0 rounded-full border-2 ${timelineDotClass(entry)}`} />
                    </div>
                    {entry.kind === "agent" ? (() => {
                      const agent = entry.agent;
                      const incomingHandoff = handoffs.find((handoff) => handoff.toAgentId === agent.agentId) ?? agent.handoff;
                      const activityLine = agent.status.category === "active"
                        ? `当前动作：${agent.objective}`
                        : agent.status.category === "completed"
                          ? `最近完成：${agent.summary}`
                          : agent.status.category === "waiting"
                            ? "当前动作：等待中"
                            : agent.status.category === "failed"
                              ? `最近状态：${agent.summary}`
                              : agent.status.category === "idle"
                                ? `当前动作：${agent.status.label}`
                                : `当前动作：${UNAVAILABLE}`;
                      return (
                        <article className="min-w-0 border-b border-border pb-2" data-testid="sub-agent-row">
                          <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                            <Typography.Text strong className="min-w-0 wrap-anywhere">{agent.name}</Typography.Text>
                            <Tag color={agent.status.color} className="m-0!">{agent.status.label}</Tag>
                            <Typography.Text type="secondary" className="min-w-0 text-xs wrap-anywhere">{agent.responsibility}</Typography.Text>
                          </div>
                          <Typography.Text className="mt-0.5 block text-xs leading-snug wrap-anywhere">
                            {activityLine}
                          </Typography.Text>
                          {agent.status.category === "waiting" ? (
                            <Typography.Text type="warning" className="block text-xs">
                              等待原因：{UNAVAILABLE}；等待对象：{needsUser ? "用户（任务级）" : UNAVAILABLE}
                            </Typography.Text>
                          ) : null}
                          <div className="mt-0.5 text-xs text-text-secondary">
                            Artifact：<ArtifactLinks refs={agent.outputArtifactRefs} onSelectArtifact={onSelectArtifact} />
                          </div>
                          {incomingHandoff || agent.supportingActivities.length ? (
                            <details className="mt-1 text-xs text-text-secondary">
                              <summary className="cursor-pointer text-accent-strong">
                                {incomingHandoff
                                  ? `Handoff：${agentName(incomingHandoff.fromAgentId, incomingHandoff.fromAgentName)} → ${agentName(incomingHandoff.toAgentId, incomingHandoff.toAgentName)}`
                                  : "查看协助动作"}
                              </summary>
                              {incomingHandoff ? <div className="mt-1">{agentName(incomingHandoff.fromAgentId, incomingHandoff.fromAgentName)} → {agentName(incomingHandoff.toAgentId, incomingHandoff.toAgentName)}：{safeNarrative(incomingHandoff.summary)}</div> : null}
                              {agent.supportingActivities.map((activity, activityIndex) => <div className="mt-1" key={`${activity.title}-${activityIndex}`}>协助：{containsChinese(activity.agentName) ? activity.agentName : "协助 智能体"} · {safeNarrative(activity.title)} · {statusPresentation(activity.status).label}</div>)}
                            </details>
                          ) : <Typography.Text type="secondary" className="block text-xs">Handoff：{projection ? "暂无已记录交接" : UNAVAILABLE}</Typography.Text>}
                        </article>
                      );
                    })() : entry.kind === "event" ? (
                      <article className="min-w-0 border-b border-border pb-2">
                        <Typography.Text strong className="text-xs">{entry.title}</Typography.Text>
                        <Typography.Text className="block text-xs leading-snug wrap-anywhere">{entry.summary}</Typography.Text>
                      </article>
                    ) : entry.kind === "handoff" ? (
                      <article className="min-w-0 border-b border-border pb-2">
                        <Typography.Text strong className="text-xs">{agentName(entry.handoff.fromAgentId, entry.handoff.fromAgentName)} → {agentName(entry.handoff.toAgentId, entry.handoff.toAgentName)}</Typography.Text>
                        <Typography.Text className="block text-xs leading-snug wrap-anywhere">{safeNarrative(entry.handoff.summary)}</Typography.Text>
                        {(entry.handoff.artifactRefs ?? []).length ? <details className="mt-1 text-xs"><summary className="cursor-pointer text-accent-strong">查看交接产物</summary><ArtifactLinks refs={entry.handoff.artifactRefs ?? []} onSelectArtifact={onSelectArtifact} /></details> : null}
                      </article>
                    ) : entry.kind === "decision" ? (
                      <article className="min-w-0 border-b border-border pb-2">
                        <Typography.Text strong className="text-xs text-warning">等待用户处理</Typography.Text>
                        <Typography.Text className="block text-xs leading-snug wrap-anywhere">{entry.reason}</Typography.Text>
                      </article>
                    ) : entry.kind === "artifact" ? (
                      <article className="min-w-0 border-b border-border pb-2">
                        <Typography.Text strong className="text-xs">已产出 Artifact</Typography.Text>
                        <div><ArtifactLinks refs={[entry.artifact]} onSelectArtifact={onSelectArtifact} /></div>
                      </article>
                    ) : (
                      <article className="min-w-0 border-b border-border pb-2">
                        <Typography.Text strong className="text-xs text-danger">协作未成功完成</Typography.Text>
                        <Typography.Text className="block text-xs leading-snug wrap-anywhere">{safeNarrative(projection?.failure?.resultTrustAndRetention)}</Typography.Text>
                        <details className="mt-1 text-xs" data-testid="agent-collaboration-failure-details">
                          <summary className="cursor-pointer text-danger">查看失败说明</summary>
                          <div className="mt-1 grid gap-1 text-text-secondary">
                            <span>未完成环节：{safeNarrative(projection?.failure?.failedStep)}</span>
                            <span>外部动作：{safeNarrative(projection?.failure?.externalAction)}</span>
                            <span>结果保留：{safeNarrative(projection?.failure?.resultTrustAndRetention)}</span>
                            <span>安全下一步：{safeNarrative(projection?.failure?.safeNextStep)}</span>
                          </div>
                        </details>
                      </article>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="grid grid-cols-2 gap-0 border-t border-border max-[980px]:grid-cols-1" data-testid="dependency-and-output-summary">
            <div className="grid content-start gap-1 px-3 py-2 max-[980px]:border-b max-[980px]:border-border">
              <Typography.Text strong className="text-xs">依赖等待与交接</Typography.Text>
              {!projection ? (
                <Typography.Text type="secondary" className="text-xs">{UNAVAILABLE}</Typography.Text>
              ) : handoffs.length === 0 && pendingDecisions.length === 0 ? (
                <Typography.Text type="secondary" className="text-xs">暂无已记录的依赖等待或交接。</Typography.Text>
              ) : (
                <ul className="m-0 grid gap-1 pl-4 text-xs text-text-secondary">
                  {pendingDecisions.map((decision) => <li key={decision.gateId}>等待用户：{safeNarrative(decision.reason)}</li>)}
                  {handoffs.map((handoff, index) => (
                    <li key={`${handoff.fromAgentId}-${handoff.toAgentId}-${index}`}>
                      {agentName(handoff.fromAgentId, handoff.fromAgentName)} → {agentName(handoff.toAgentId, handoff.toAgentName)}：{safeNarrative(handoff.summary)}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div className="grid content-start gap-1 border-l border-border px-3 py-2 max-[980px]:border-l-0">
              <Typography.Text strong className="text-xs">已产出 Artifact</Typography.Text>
              <div className="text-xs"><ArtifactLinks refs={globalArtifacts} onSelectArtifact={onSelectArtifact} /></div>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
