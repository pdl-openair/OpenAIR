/**
 * ExecutionQueue — renders a real-time "execution process" progress queue
 * in the collaboration column. Each item is a capability call that
 * transitions running → completed/failed in real time. Per-tool events
 * (tool_use / tool_result) render as sub-items within each capability call.
 * Artifact arrival is announced here, while full artifact content stays in
 * the center workspace. The queue collapses to ~1 line when artifacts exist.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { CaretRightOutlined, CheckCircleOutlined, CloseCircleOutlined, LoadingOutlined } from "@ant-design/icons";
import { AnimatePresence, motion } from "motion/react";
import { Badge, Spin, Typography } from "antd";
import { labelForSkill } from "../../data/recordedRunAdapter";
import { cn } from "../../lib/cn";
import { userVisibleWorkbenchCalls } from "../../lib/workbenchUi";
import {
  formatInteractionTime,
  substageLabelFromCall,
  stageLabelFromKey,
  type StreamTimelineState,
  type ToolUseEvent,
} from "../../lib/workbenchInteraction";
import type { WorkbenchTaskModel } from "../../model/workbenchTask";

export interface ExecutionQueueProps {
  streamStateByCallId: Record<string, StreamTimelineState>;
  model: WorkbenchTaskModel;
}

type VisibleStreamTimelineState = StreamTimelineState & {
  call: NonNullable<StreamTimelineState["call"]>;
};

/** A single per-tool sub-item within a capability call. */
function ToolStepItem({ toolEvent }: { toolEvent: ToolUseEvent }) {
  const isPending = toolEvent.status === "pending";
  const isDone = toolEvent.status === "done";
  const isFailed = toolEvent.isError;

  return (
    <div
      className={cn(
        "flex items-start gap-2 rounded-md px-2 py-1",
        isPending && "bg-accent-tint",
        !isPending && "bg-success-tint",
      )}
    >
      <div className="shrink-0 pt-px">
        {isPending ? (
          <LoadingOutlined className="text-sm text-accent-strong" />
        ) : isFailed ? (
          <CloseCircleOutlined className="text-sm text-danger" />
        ) : (
          <CheckCircleOutlined className="text-sm text-success" />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <Typography.Text className="text-xs font-semibold text-text">
            {toolEvent.toolName || "Tool"}
          </Typography.Text>
          {isPending && <Typography.Text type="secondary">执行中…</Typography.Text>}
          {isDone && !isFailed && <Typography.Text type="secondary">完成</Typography.Text>}
          {isFailed && <Typography.Text type="danger">失败</Typography.Text>}
        </div>
        <Typography.Text type="secondary" className="text-xs">
          {isPending
            ? "正在处理当前步骤；内部参数不在普通视图展示。"
            : isFailed
              ? "步骤失败；请复核影响后再重试或调整策略。"
              : "步骤已完成；以阶段产物、证据状态和下一步为准。"}
        </Typography.Text>
        {isDone && toolEvent.content && (
          <Typography.Text type="secondary" className="text-xs" ellipsis>
            {isFailed ? "失败详情已记录在执行记录中。" : "结果摘要已沉淀到产物或阶段状态。"}
          </Typography.Text>
        )}
      </div>
    </div>
  );
}

/** A single queue item representing a capability call. */
function QueueItem({
  callId,
  state,
  model,
  isLast,
  now,
}: {
  callId: string;
  state: StreamTimelineState;
  model: WorkbenchTaskModel;
  isLast: boolean;
  now: number;
}) {
  const call = state.call;
  const isRunning = call?.status === "running";
  const isCompleted = call?.status === "completed";
  const isFailed = call?.status === "failed";
  const skillDisplay = call ? labelForSkill(call.name) : callId;
  const subLabel = call ? substageLabelFromCall(model, call) : undefined;
  const stageLabel = call?.stageKey ? stageLabelFromKey(model, call.stageKey) : undefined;
  const toolEvents = state.toolEvents ?? [];
  const elapsedSeconds = call?.startedAt
    ? Math.max(0, Math.floor((now - new Date(call.startedAt).getTime()) / 1000))
    : null;
  const elapsedLabel = elapsedSeconds == null || !Number.isFinite(elapsedSeconds)
    ? null
    : elapsedSeconds < 60
      ? `已持续 ${elapsedSeconds} 秒`
      : elapsedSeconds < 3600
        ? `已持续 ${Math.floor(elapsedSeconds / 60)} 分钟`
        : `已持续 ${Math.floor(elapsedSeconds / 3600)} 小时 ${Math.floor((elapsedSeconds % 3600) / 60)} 分钟`;

  return (
    <div className={cn("flex items-start gap-2.5 px-3.5 py-2", !isLast && "border-b-0")}>
      <div className="shrink-0 pt-0.5">
        {isRunning ? (
          <LoadingOutlined className="text-base text-accent-strong" />
        ) : isCompleted ? (
          <CheckCircleOutlined className="text-base text-success" />
        ) : isFailed ? (
          <CloseCircleOutlined className="text-base text-danger" />
        ) : (
          <CheckCircleOutlined className="text-base text-icon-disabled" />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="mb-0.5 flex items-center gap-2.5">
          <Typography.Text strong className="m-0 text-sm">
            {skillDisplay}
          </Typography.Text>
          <Badge
            status={isCompleted ? "success" : isRunning ? "processing" : isFailed ? "error" : "default"}
            text={isRunning ? "运行中" : isCompleted ? "已完成" : isFailed ? "失败" : "待处理"}
          />
        </div>
        {(subLabel || stageLabel) && (
          <div className="mb-0.5 flex gap-2">
            {stageLabel && <Typography.Text type="secondary" className="text-2xs">{stageLabel}</Typography.Text>}
            {subLabel && <Typography.Text type="secondary" className="text-2xs">{subLabel}</Typography.Text>}
          </div>
        )}
        {state.progress.length > 0 && (
          <div className="mt-1 flex flex-col gap-1">
            {state.progress.slice(-3).map((item, index) => (
              <div
                key={`${callId}-progress-${index}`}
                className="rounded-md bg-surface-soft px-2 py-1 text-xs leading-snug text-text-secondary"
              >
                {item}
              </div>
            ))}
          </div>
        )}
        {call?.startedAt && (
          <Typography.Text type="secondary" className="mt-0.5 block text-2xs">
            {formatInteractionTime(call.startedAt)}
            {call.endedAt ? ` → ${formatInteractionTime(call.endedAt)}` : ` → 进行中${elapsedLabel ? `（${elapsedLabel}）` : ""}`}
          </Typography.Text>
        )}
        {toolEvents.length > 0 && (
          <div className="mt-1.5 flex flex-col gap-1 border-l-2 border-border pl-2">
            {toolEvents.map((te) => (
              <ToolStepItem key={te.toolUseId} toolEvent={te} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export function ExecutionQueue({ streamStateByCallId, model }: ExecutionQueueProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const scrollRef = useRef<HTMLDivElement>(null);

  const sortedEntries = useMemo(() => {
    // A running task can outlive the browser stream that started it. Seed the
    // collaboration view from the persisted task snapshot, then layer the
    // current stream's finer-grained events on top.
    const byCallId = new Map<string, VisibleStreamTimelineState>(
      userVisibleWorkbenchCalls(model.capabilityCalls).map((call) => [
        call.id,
        {
          ...(streamStateByCallId[call.id] ?? {}),
          call: {
            ...call,
            ...(streamStateByCallId[call.id]?.call ?? {}),
          },
          progress: streamStateByCallId[call.id]?.progress ?? [],
          artifactRefs: streamStateByCallId[call.id]?.artifactRefs ?? [
            ...(call.outputArtifactRefs ?? []),
            ...(call.artifactRefs ?? []),
          ],
          confirmations: streamStateByCallId[call.id]?.confirmations ?? [],
          toolEvents: streamStateByCallId[call.id]?.toolEvents ?? [],
        } as VisibleStreamTimelineState,
      ]),
    );
    for (const [callId, state] of Object.entries(streamStateByCallId)) {
      if (!byCallId.has(callId) && state.call != null) {
        byCallId.set(callId, { ...state, call: state.call });
      }
    }
    const entries = [...byCallId.entries()];
    return entries
      .filter(([, state]) => state.call != null)
      .sort((a, b) => {
        const aTime = a[1].call?.startedAt ?? a[1].startedAt ?? "";
        const bTime = b[1].call?.startedAt ?? b[1].startedAt ?? "";
        return aTime.localeCompare(bTime);
      });
  }, [model.capabilityCalls, streamStateByCallId]);

  const hasAnyCall = sortedEntries.length > 0;
  const completedCount = sortedEntries.filter(([, s]) => s.call?.status === "completed").length;
  const runningCount = sortedEntries.filter(([, s]) => s.call?.status === "running").length;
  const failedCount = sortedEntries.filter(([, s]) => s.call?.status === "failed").length;

  useEffect(() => {
    if (runningCount === 0) return undefined;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [runningCount]);

  const totalToolEvents = useMemo(() => {
    let count = 0;
    for (const [, state] of sortedEntries) {
      count += (state.toolEvents ?? []).length;
    }
    return count;
  }, [sortedEntries]);

  const artifactIds = useMemo(() => {
    const ids: string[] = [];
    for (const [, state] of sortedEntries) {
      if (state.artifactRefs) {
        ids.push(...state.artifactRefs);
      }
    }
    return [...new Set(ids)];
  }, [sortedEntries]);

  const shouldCollapse = hasAnyCall && (completedCount > 0 || failedCount > 0) && artifactIds.length > 0 && !runningCount;
  const effectiveCollapsed = collapsed || shouldCollapse;

  const prevLengthRef = useRef(sortedEntries.length);
  if (sortedEntries.length > prevLengthRef.current && !effectiveCollapsed) {
    prevLengthRef.current = sortedEntries.length;
    requestAnimationFrame(() => {
      if (scrollRef.current) {
        scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      }
    });
  } else {
    prevLengthRef.current = sortedEntries.length;
  }

  if (!hasAnyCall) {
    return null;
  }

  const summaryParts: string[] = [];
  if (runningCount > 0) summaryParts.push(`${runningCount} 项运行中`);
  if (completedCount > 0) summaryParts.push(`${completedCount} 项已完成`);
  if (failedCount > 0) summaryParts.push(`${failedCount} 项失败`);
  if (totalToolEvents > 0) summaryParts.push(`${totalToolEvents} 条操作记录`);
  const summaryText = summaryParts.join("，") || "就绪";

  return (
    <motion.div
      layout
      className={cn(
        "shrink-0 overflow-hidden rounded-card border border-border bg-surface transition-[max-height] duration-250 ease-out",
        effectiveCollapsed ? "max-h-20" : "max-h-[480px]",
      )}
    >
      <motion.button
        type="button"
        whileHover={{ backgroundColor: "var(--wb-surface-muted)" }}
        className="flex w-full cursor-pointer items-center justify-between gap-3 border-none border-b border-border bg-surface-soft px-3.5 py-2 text-left font-[inherit] text-inherit focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent-strong"
        onClick={() => setCollapsed((prev) => !prev)}
        aria-expanded={effectiveCollapsed ? "false" : "true"}
      >
        <div className="inline-flex min-w-0 items-center gap-1.5">
          <motion.span animate={{ rotate: effectiveCollapsed ? 0 : 90 }} transition={{ duration: 0.18, ease: "easeOut" }}>
            <CaretRightOutlined className="text-2xs text-text-muted" />
          </motion.span>
          <Typography.Text strong>执行过程</Typography.Text>
        </div>
        <div className="inline-flex items-center gap-2">
          {runningCount > 0 && <Spin size="small" />}
          <Typography.Text type="secondary" className="text-xs">{summaryText}</Typography.Text>
          <Typography.Text type="secondary" className="text-xs">
            {effectiveCollapsed ? "展开" : "收起"}
          </Typography.Text>
        </div>
      </motion.button>

      <AnimatePresence initial={false}>
        {effectiveCollapsed ? (
          <motion.div
            key="collapsed"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
            className="overflow-hidden px-3.5 py-1.5"
          >
            <div ref={scrollRef} className="overflow-x-auto overflow-y-hidden whitespace-nowrap">
              <span className="inline-flex gap-1.5">
                {sortedEntries.map(([callId, state]) => {
                  const call = state.call;
                  const icon = call?.status === "completed" ? "✓" : call?.status === "running" ? "⟳" : call?.status === "failed" ? "✗" : "○";
                  const label = call ? labelForSkill(call.name) : callId.slice(0, 8);
                  return (
                    <span
                      key={callId}
                      className="inline-flex shrink-0 items-center gap-1 rounded-full border border-border bg-surface-soft px-2.5 py-0.5 text-xs whitespace-nowrap text-text-secondary"
                      title={`${label}: ${call?.status ?? "unknown"}`}
                    >
                      {icon} {label}
                    </span>
                  );
                })}
              </span>
            </div>
          </motion.div>
        ) : (
          <motion.div
            key="expanded"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
            className="overflow-hidden"
          >
            <div ref={scrollRef} className="max-h-[400px] overflow-x-hidden overflow-y-auto px-0 py-2">
              <div className="flex flex-col">
                {sortedEntries.map(([callId, state], index) => (
                  <QueueItem
                    key={callId}
                    callId={callId}
                    state={state}
                    model={model}
                    isLast={index === sortedEntries.length - 1}
                    now={now}
                  />
                ))}
              </div>
              {artifactIds.length > 0 && (
                <div className="mt-2 border-t border-dashed border-border px-3.5 pt-2.5 pb-1.5">
                  <Typography.Text type="secondary" className="text-xs">
                    已生成 {artifactIds.length} 个产物，已同步至中间产物区。
                  </Typography.Text>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
