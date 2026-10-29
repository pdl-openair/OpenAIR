import { useEffect, useState, type ReactNode } from "react";
import { Button, Spin, Typography } from "antd";
import {
  useTaskResearchStream,
  type ResearchStreamConnectionState,
} from "../../hooks/useTaskResearchStream";
import { chineseLabelForSkillPresentation } from "../../data/recordedRunAdapter";
import { userFacingDynamicText } from "../../lib/userFacingLanguage";
import { researchArtifactTitle } from "../../lib/researchArtifactTitle";
import {
  coalesceResearchActivityBlocks,
  distinctResearchActivities,
  filterResearchActivityArtifactRefs,
  foldResearchActivityPresentation,
  mergeResearchActivityHistory,
  type CanonicalResearchCurrentState,
} from "../../model/researchActivityStreamCore.mjs";

const { Text } = Typography;

function presentActivityCopy(value: string): string {
  return value.replace(/^(.+?)(\s+完成)$/, (raw, skillName, suffix) => {
    const label = chineseLabelForSkillPresentation(skillName);
    return label ? `${label}${suffix}` : raw;
  });
}

const INTERNAL_EXECUTION_OBJECTIVE =
  /\b(?:assemble|compile|consolidate|finali[sz]e|prepare)\b[\s\S]{0,100}\b(?:canonical|final|completed|deliverable|output)\b[\s\S]{0,100}\b(?:research|result|findings?|evidence|artifact)\b/i;
const REVIEW_AUDIT_NARRATIVE =
  /^\s*(?:reviewer|audit(?:or)?|compliance)\b[\s\S]{0,80}\b(?:commentary|assessment|review|check|validation|status|criteria|requirement)\b/i;

function isInternalPresentationNoise(value: string): boolean {
  return (
    INTERNAL_EXECUTION_OBJECTIVE.test(value) ||
    REVIEW_AUDIT_NARRATIVE.test(value)
  );
}

function userFacingActivityText(value: string, fallback: string): string {
  if (isInternalPresentationNoise(value)) return fallback;
  return userFacingDynamicText(value, fallback);
}
export type ResearchStreamArtifact = {
  id: string;
  title?: string | null;
  type?: string;
  artifactType?: string | null;
  relatedSkillId?: string | null;
  validatorResult?: unknown;
};

export type ResearchStreamWorkUnitContext = {
  workUnitId: string;
  objective?: string | null;
  goalKey?: string;
};

export type ResearchActivity = {
  eventId: string;
  eventType: string;
  presentationLevel?: "primary" | "detail";
  workUnitId?: string | null;
  headline: string;
  summary: string | null;
  sequence: number | null;
  revision?: number | null;
  occurredAt: string | null;
  startedAt?: string | null;
  aggregateCount: number | null;
  activityKey?: string | null;
  phase?: string | null;
  artifactRefs?: string[];
  resultRefs?: string[];
  scientificStateVersions?: number[];
};

export type WorkUnitBlock = {
  workUnitId: string;
  rows: ResearchActivity[];
  processActivities: ResearchActivity[];
  currentStatus: string | null;
  terminalActivity: ResearchActivity | null;
  artifactRefs: string[];
  resultRefs: string[];
  scientificStateVersions: number[];
  startedAt: string | null;
  endedAt: string | null;
  latestOccurredAt: string | null;
};

function activityCopy(activity: ResearchActivity | null): string | null {
  if (!activity) return null;
  for (const raw of [activity.summary?.trim(), activity.headline?.trim()]) {
    if (!raw) continue;
    const copy = userFacingActivityText(raw, "研究过程已更新");
    // Authority/transport bookkeeping is not research narration. If a summary
    // is bookkeeping, fall through to the user-facing headline instead.
    if (
      /权威产物接口|可查看、可使用仍以|产物是否可查看|权威任务接口/.test(copy)
    )
      continue;
    if (
      /^(当前步骤已经开始|获得了有意义的新进展|研究进展已更新|工具与过程正在推进|工具与过程取得阶段进展)$/.test(
        copy,
      )
    )
      continue;
    if (/^同一步骤的低价值调用已聚合/.test(copy)) continue;
    return presentActivityCopy(copy);
  }
  return null;
}

const INTERNAL_ARTIFACT_TITLE =
  /^(?:key_|sdr:|artifact[:_]|[a-z][a-z0-9]*_[a-z0-9_]+$)/i;

function artifactButtonLabel(
  title: string | null | undefined,
  index: number,
  total: number,
): string {
  const normalized = title?.trim() ?? "";
  if (normalized && !INTERNAL_ARTIFACT_TITLE.test(normalized)) {
    return userFacingDynamicText(normalized, "研究产物");
  }
  return total === 1 ? "查看研究产物" : `研究产物 ${index + 1}`;
}

function distinctRows(rows: ResearchActivity[]): ResearchActivity[] {
  return (distinctResearchActivities(rows) as ResearchActivity[]).filter(
    (activity) => Boolean(activityCopy(activity)),
  );
}

const LIVE_PHASES = new Set([
  "scheduled",
  "queued",
  "started",
  "running",
  "reviewing",
]);
const TERMINAL_PHASES = new Set([
  "completed",
  "succeeded",
  "failed",
  "blocked",
  "stopped",
  "stopped_by_user",
]);
const LIVE_CURRENT_STATUSES = new Set([
  "scheduled",
  "queued",
  "started",
  "running",
  "reviewing",
]);
const COMPLETED_CURRENT_STATUSES = new Set(["completed", "succeeded"]);
/** Process-stream incomplete execution — not a user-action demand. */
const INCOMPLETE_CURRENT_STATUSES = new Set([
  "failed",
  "blocked",
  "blocked_recoverable",
]);
const UNKNOWN_CURRENT_STATUSES = new Set(["external_result_unknown"]);
const NEEDS_INPUT_CURRENT_STATUSES = new Set(["needs_input"]);
const STOPPED_CURRENT_STATUSES = new Set([
  "cancelled",
  "stopped",
  "stopped_by_user",
]);

function currentStatusIsTerminal(status: string | null): boolean {
  return Boolean(
    status &&
    (COMPLETED_CURRENT_STATUSES.has(status) ||
      INCOMPLETE_CURRENT_STATUSES.has(status) ||
      UNKNOWN_CURRENT_STATUSES.has(status) ||
      NEEDS_INPUT_CURRENT_STATUSES.has(status) ||
      STOPPED_CURRENT_STATUSES.has(status)),
  );
}

function timestampMs(value?: string | null): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatDuration(durationMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(durationMs / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}小时${minutes}分${seconds}秒`;
  if (minutes > 0) return `${minutes}分${seconds}秒`;
  return `${seconds}秒`;
}

function formatOccurredTime(value?: string | null): string | null {
  const parsed = timestampMs(value);
  if (parsed === null) return null;
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(parsed));
}

function elapsedBetween(
  startedAt: string | null | undefined,
  endMs: number | null,
): string | null {
  const startMs = timestampMs(startedAt);
  if (startMs === null || endMs === null || endMs < startMs) return null;
  return formatDuration(endMs - startMs);
}

function activityTimeLabel(
  activity: ResearchActivity,
  nowMs: number,
  live: boolean,
): string | null {
  const startMs = timestampMs(activity.startedAt);
  const occurredMs = timestampMs(activity.occurredAt);
  if (
    startMs !== null &&
    TERMINAL_PHASES.has(activity.phase ?? "") &&
    occurredMs !== null &&
    occurredMs >= startMs
  ) {
    return `用时 ${formatDuration(occurredMs - startMs)}`;
  }
  if (startMs !== null && live && LIVE_PHASES.has(activity.phase ?? "")) {
    return `已进行 ${formatDuration(nowMs - startMs)}`;
  }
  return formatOccurredTime(activity.occurredAt);
}

function ProcessDetails({
  activities,
  defaultOpen,
  clockMs,
  live,
}: {
  activities: ResearchActivity[];
  defaultOpen: boolean;
  clockMs: number;
  live: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="grid gap-1 pl-6">
      <button
        type="button"
        className="w-fit cursor-pointer border-none bg-transparent p-0 text-left text-xs text-text-muted"
        data-testid="research-stream-process-toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? "收起过程" : `过程 · ${activities.length} 步`}
      </button>
      {open ? (
        <div
          className="grid gap-1 text-xs text-text-muted"
          data-testid="research-stream-process-preview"
        >
          {activities.map((activity) => {
            const timing = activityTimeLabel(activity, clockMs, live);
            return (
              <div key={activity.eventId} className="grid gap-0.5">
                <div className="flex min-w-0 gap-1.5">
                  <span aria-hidden>↳</span>
                  <span className="min-w-0">{activityCopy(activity)}</span>
                </div>
                {timing ? (
                  <span
                    className="pl-4 text-text-muted"
                    data-testid="research-stream-item-time"
                  >
                    {timing}
                  </span>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

function blockTitle(block: WorkUnitBlock, objective: string | null): string {
  const objectiveCopy = objective?.trim()
    ? userFacingActivityText(objective, "")
    : null;
  if (objectiveCopy) return objectiveCopy;
  const started =
    block.rows.find((activity) => activity.phase === "started") ??
    block.rows.find((activity) => activity.presentationLevel !== "detail");
  const startCopy = activityCopy(started ?? null);
  const genericLifecycleCopy = new Set([
    "研究目标已经开始",
    "研究步骤开始",
    "研究进展已更新",
    "研究步骤已进入执行队列",
    "研究步骤正在执行",
    "研究结果正在核查",
    "研究步骤已完成",
    "研究步骤执行失败",
    "研究步骤当前受阻",
    "研究执行结果仍待核查",
    "研究步骤已停止",
  ]);
  if (startCopy && !genericLifecycleCopy.has(startCopy)) return startCopy;
  const firstProcess = block.rows.find(
    (activity) => activity.presentationLevel === "detail",
  );
  const processCopy = activityCopy(firstProcess ?? null);
  return processCopy
    ? `${processCopy.replace(/^正在使用/, "").replace(/已完成$/, "")}研究步骤`
    : "研究步骤";
}

function ConnectionStatus({
  state,
  enabled,
}: {
  state: ResearchStreamConnectionState;
  enabled: boolean;
}) {
  if (!enabled || state !== "disconnected") return null;
  return (
    <div
      className="py-2 text-xs"
      data-testid="research-stream-connection"
      data-connection-state={state}
      role="status"
    >
      <Text type="secondary">研究进展暂时未更新，系统会自动同步最新状态。</Text>
    </div>
  );
}

export type ResearchActivityPresentationFact = {
  id: string;
  workUnitId: string | null;
  occurredAt: string | null;
  artifactRefs: string[];
  resultRefs: string[];
  scientificStateVersions: number[];
  rows: ResearchActivity[];
};

export type ResearchActivityPresentationApi = {
  facts: ResearchActivityPresentationFact[];
  renderFact: (
    factId: string,
    relatedContent?: ReactNode,
    options?: { needsInput?: boolean },
  ) => ReactNode;
  summary: ReactNode;
  connection: ReactNode;
};

export function ResearchActivityStream({
  taskId,
  enabled,
  active,
  activeObjective,
  nextObjective,
  workUnitContexts = [],
  artifacts = [],
  durableActivities = [],
  canonicalCurrentState = null,
  onSelectArtifact,
  onInvalidate,
  children,
  activityStartedAt = null,
  activityEndedAt = null,
  halted = false,
  haltedAt = null,
}: {
  taskId: string;
  enabled: boolean;
  active: boolean;
  activeObjective?: string | null;
  nextObjective?: string | null;
  workUnitContexts?: ResearchStreamWorkUnitContext[];
  artifacts?: ResearchStreamArtifact[];
  /** Task-GET projection rebuilt from durable canonical research facts. */
  durableActivities?: ResearchActivity[];
  /** Same Task-GET canonical snapshot; current state never comes from SSE/history. */
  canonicalCurrentState?: CanonicalResearchCurrentState | null;
  onSelectArtifact?: (artifactId: string) => void;
  onInvalidate?: (scopes: string[]) => void;
  children?: (presentation: ResearchActivityPresentationApi) => ReactNode;
  /** Planning sessions start before durable task events; use their authoritative start time when available. */
  activityStartedAt?: string | null;
  /** When planning/research has finished, freeze duration against this end time. */
  activityEndedAt?: string | null;
  /** User stop / cancelled execution. Live work-unit rows must not keep saying 正在进行. */
  halted?: boolean;
  haltedAt?: string | null;
}) {
  const { primaryActivities, collapsedDetails, connectionState } =
    useTaskResearchStream(taskId, enabled, onInvalidate);

  const [clockMs, setClockMs] = useState(() => Date.now());
  useEffect(() => {
    setClockMs(Date.now());
    if (!active) return undefined;
    const timer = window.setInterval(() => setClockMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);

  const mergedActivities = mergeResearchActivityHistory(
    durableActivities,
    primaryActivities as ResearchActivity[],
    collapsedDetails as ResearchActivity[],
    canonicalCurrentState,
  );
  const visibleMergedActivities = {
    primaryActivities: filterResearchActivityArtifactRefs(
      mergedActivities.primaryActivities as ResearchActivity[],
      artifacts.map((artifact) => artifact.id),
    ),
    collapsedDetails: filterResearchActivityArtifactRefs(
      mergedActivities.collapsedDetails as ResearchActivity[],
      artifacts.map((artifact) => artifact.id),
    ),
  };
  const folded = foldResearchActivityPresentation(
    visibleMergedActivities.primaryActivities,
    visibleMergedActivities.collapsedDetails,
    canonicalCurrentState,
  ) as {
    blocks: WorkUnitBlock[];
    unscoped: ResearchActivity[];
    startedAt: string | null;
    endedAt: string | null;
    latestOccurredAt: string | null;
  };
  const objectiveByWorkUnit = new Map(
    workUnitContexts
      .filter((item) => item.workUnitId)
      .map((item) => [item.workUnitId, item.objective?.trim() || null]),
  );
  // Retries mint new WorkUnits; researchers still see one step card per goal.
  const blocks = coalesceResearchActivityBlocks(
    folded.blocks,
    objectiveByWorkUnit,
    new Map(workUnitContexts.filter(item => item.goalKey).map(item => [item.workUnitId, item.goalKey as string])),
  ) as WorkUnitBlock[];
  const { unscoped, startedAt, endedAt } = folded;
  const currentWorkUnitId =
    [...blocks]
      .reverse()
      .find((block) => LIVE_CURRENT_STATUSES.has(block.currentStatus ?? ""))
      ?.workUnitId ?? null;
  const artifactTitles = new Map(
    artifacts.map((artifact) => [artifact.id, researchArtifactTitle(artifact)]),
  );
  const overallEndMs =
    timestampMs(activityEndedAt) ??
    timestampMs(endedAt) ??
    (active ? clockMs : null);
  const totalDuration = elapsedBetween(
    activityStartedAt ?? startedAt,
    overallEndMs,
  );
  const visibleUnscoped = distinctRows(unscoped);

  const renderMilestone = (
    activity: ResearchActivity,
    relatedContent?: ReactNode,
  ) => {
    const refs = activity.artifactRefs ?? [];
    const planningStarted =
      activity.phase === "started" &&
      activity.activityKey?.startsWith("planning-attempt:");
    const copy = planningStarted
      ? "已开始组织研究方案"
      : activityCopy(activity);
    const thinking = activity.activityKey?.startsWith("planning-thought:");
    const inProgressCopy = Boolean(copy && /^(正在|等待|准备)/.test(copy));
    const live =
      !planningStarted &&
      active &&
      (thinking
        ? activity.phase === "forming"
        : LIVE_PHASES.has(activity.phase ?? "") || inProgressCopy);
    const problem = ["failed", "blocked", "needs_input", "paused"].includes(
      activity.phase ?? "",
    );
    return (
      <div
        key={activity.eventId}
        className="grid gap-1.5 border-b border-border py-2.5"
        role="listitem"
        data-testid="research-stream-milestone"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 gap-2">
            {live ? (
              <Spin size="small" className="mt-0.5" />
            ) : (
              <span
                className={`mt-0.5 ${problem ? "text-warning" : inProgressCopy || thinking ? "text-text-muted" : "text-success"}`}
                aria-hidden
              >
                {problem ? "!" : inProgressCopy || thinking ? "●" : "✓"}
              </span>
            )}
            <Text className="whitespace-pre-wrap">{copy}</Text>
          </div>
          {formatOccurredTime(activity.occurredAt) ? (
            <Text
              type="secondary"
              className="shrink-0 text-xs"
              data-testid="research-stream-item-time"
            >
              {formatOccurredTime(activity.occurredAt)}
            </Text>
          ) : null}
        </div>
        {refs.length ? (
          <div
            className="flex flex-wrap gap-1.5 pl-6"
            data-testid="research-stream-artifacts"
          >
            {refs.map((artifactId, index) => (
              <Button
                key={artifactId}
                size="small"
                onClick={() => onSelectArtifact?.(artifactId)}
              >
                {artifactButtonLabel(
                  artifactTitles.get(artifactId),
                  index,
                  refs.length,
                )}
              </Button>
            ))}
          </div>
        ) : null}
        {relatedContent ? (
          <div
            className="grid gap-0 pl-6"
            data-testid="research-stream-related-facts"
          >
            {relatedContent}
          </div>
        ) : null}
      </div>
    );
  };

  const renderBlock = (
    block: WorkUnitBlock,
    relatedContent?: ReactNode,
    options?: { needsInput?: boolean },
  ) => {
    const isCurrent = block.workUnitId === currentWorkUnitId;
    const currentStatus = block.currentStatus;
    const terminal = currentStatusIsTerminal(currentStatus);
    const liveStatus = LIVE_CURRENT_STATUSES.has(currentStatus ?? "");
    const stoppedByHalt = halted && liveStatus;
    const spinning = enabled && active && liveStatus && !halted;
    const syncing = currentStatus === null && !halted;
    const visibleProcess = distinctRows(block.processActivities);
    const refs = block.artifactRefs;
    const canonicalObjective =
      objectiveByWorkUnit.get(block.workUnitId) ??
      (isCurrent ? activeObjective?.trim() || null : null);
    const title = blockTitle(block, canonicalObjective);
    const incomplete = INCOMPLETE_CURRENT_STATUSES.has(currentStatus ?? "");
    const resultUnknown = UNKNOWN_CURRENT_STATUSES.has(currentStatus ?? "");
    const awaitingInput = NEEDS_INPUT_CURRENT_STATUSES.has(currentStatus ?? "");
    const stopped =
      STOPPED_CURRENT_STATUSES.has(currentStatus ?? "") || stoppedByHalt;
    const completed = COMPLETED_CURRENT_STATUSES.has(currentStatus ?? "");
    // Process narration only. User responsibility / CTAs come from interruption + user-actions.
    const heading = resultUnknown
      ? `结果待核对：${title}`
      : awaitingInput
        ? `待补充：${title}`
        : incomplete
          ? `未完成：${title}`
          : stopped
            ? `已停止：${title}`
            : completed
              ? options?.needsInput
                ? "已形成初步结果"
                : `已完成：${title}`
              : spinning
                ? `正在进行：${title}`
                : syncing
                  ? `正在同步：${title}`
                  : title;
    const attention = incomplete || resultUnknown || awaitingInput;
    const terminalCopy =
      terminal && block.terminalActivity
        ? activityCopy(block.terminalActivity)
        : null;
    const blockEndMs =
      timestampMs(block.endedAt) ??
      (stoppedByHalt
        ? (timestampMs(haltedAt) ?? timestampMs(block.latestOccurredAt))
        : null) ??
      (spinning ? clockMs : null);
    const blockDuration = elapsedBetween(block.startedAt, blockEndMs);

    return (
      <div
        key={block.workUnitId}
        className="grid gap-2 border-b border-border py-3"
        role="listitem"
        data-testid="research-stream-work-unit"
        data-work-unit-id={block.workUnitId}
      >
        <div className="flex items-start gap-2">
          {spinning ? (
            <Spin size="small" className="mt-0.5" />
          ) : (
            <span
              className={`mt-0.5 ${attention ? "text-warning" : terminal ? "text-success" : "text-text-muted"}`}
              aria-hidden
            >
              {attention ? "!" : stopped ? "■" : completed ? "✓" : "●"}
            </span>
          )}
          <div className="grid min-w-0 flex-1 gap-1">
            <Text strong>{heading}</Text>
            {blockDuration ? (
              <Text
                type="secondary"
                className="text-xs"
                data-testid="research-stream-work-unit-duration"
              >
                {stopped || completed || incomplete
                  ? `用时 ${blockDuration}`
                  : blockDuration}
              </Text>
            ) : null}
            {terminalCopy ? <Text type="secondary">{terminalCopy}</Text> : null}
            {spinning && nextObjective?.trim() ? (
              <Text
                type="secondary"
                className="text-xs"
                data-testid="research-stream-next-step"
              >
                接下来计划：{nextObjective.trim()}
              </Text>
            ) : null}
          </div>
        </div>

        {visibleProcess.length ? (
          <ProcessDetails
            activities={visibleProcess}
            defaultOpen={spinning}
            clockMs={clockMs}
            live={spinning}
          />
        ) : null}

        {refs.length ? (
          <div
            className="flex flex-wrap gap-1.5 pl-6"
            data-testid="research-stream-artifacts"
          >
            {refs.map((artifactId, index) => (
              <Button
                key={artifactId}
                size="small"
                onClick={() => onSelectArtifact?.(artifactId)}
              >
                {artifactButtonLabel(
                  artifactTitles.get(artifactId),
                  index,
                  refs.length,
                )}
              </Button>
            ))}
          </div>
        ) : null}
        {relatedContent ? (
          <div
            className="grid gap-0 pl-6"
            data-testid="research-stream-related-facts"
          >
            {relatedContent}
          </div>
        ) : null}
      </div>
    );
  };

  const facts: ResearchActivityPresentationFact[] = [
    ...visibleUnscoped.map((activity) => ({
      id: `research-event:${activity.eventId}`,
      workUnitId: activity.workUnitId ?? null,
      occurredAt: activity.occurredAt ?? null,
      artifactRefs: activity.artifactRefs ?? [],
      resultRefs: activity.resultRefs ?? [],
      scientificStateVersions: activity.scientificStateVersions ?? [],
      rows: [activity],
    })),
    ...blocks.map((block) => ({
      id: `research-work-unit:${block.workUnitId}`,
      workUnitId: block.workUnitId,
      occurredAt: block.startedAt ?? block.latestOccurredAt,
      artifactRefs: block.artifactRefs,
      resultRefs: block.resultRefs,
      scientificStateVersions: block.scientificStateVersions,
      rows: block.rows,
    })),
  ];

  const renderFact = (
    factId: string,
    relatedContent?: ReactNode,
    options?: { needsInput?: boolean },
  ): ReactNode => {
    if (factId.startsWith("research-event:")) {
      const eventId = factId.slice("research-event:".length);
      const activity = visibleUnscoped.find((row) => row.eventId === eventId);
      return activity ? renderMilestone(activity, relatedContent) : null;
    }
    if (factId.startsWith("research-work-unit:")) {
      const workUnitId = factId.slice("research-work-unit:".length);
      const block = blocks.find((row) => row.workUnitId === workUnitId);
      return block ? renderBlock(block, relatedContent, options) : null;
    }
    return null;
  };

  const summary = startedAt ? (
    <div
      className="grid gap-1 border-b border-border py-2.5"
      data-testid="research-stream-activity-summary"
    >
      <div className="flex items-baseline justify-between gap-3">
        <Text strong>研究过程</Text>
        {totalDuration ? (
          <Text
            type="secondary"
            className="text-xs"
            data-testid="research-stream-total-duration"
          >
            活动 · {totalDuration}
          </Text>
        ) : null}
      </div>
    </div>
  ) : null;
  const connection = (
    <ConnectionStatus state={connectionState} enabled={enabled} />
  );

  if (children) {
    return (
      <div className="contents" data-testid="research-activity-stream">
        {children({ facts, renderFact, summary, connection })}
      </div>
    );
  }

  return (
    <div className="contents" data-testid="research-activity-stream">
      {summary}
      {facts.map((fact) => renderFact(fact.id))}
      {connection}
    </div>
  );
}
