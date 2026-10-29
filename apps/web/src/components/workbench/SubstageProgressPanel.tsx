import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";
import { Typography } from "antd";
import { fetchWorkUnitProgress } from "../../api/client";
import { StreamdownMarkdown } from "../artifacts/StreamdownMarkdown";

const POLL_INTERVAL_MS = 5000;

/** Content-area height limits for the drag-to-resize affordance (px). */
const HEIGHT_DEFAULT = 224; // matches the original max-h-56 (14rem)
const HEIGHT_MIN = 28;
const HEIGHT_MAX = 640;
/** Below this height the panel snaps to collapsed on drag release; dragging
 * a collapsed panel up at least this far re-opens it. */
const HEIGHT_COLLAPSE_THRESHOLD = 64;
const EXPAND_TRIGGER = 16;

export interface SubstageProgressPanelProps {
  taskId: string;
  /** Durable work units backing the selected substage (projection rows). */
  workUnitIds: string[] | undefined;
  /** Poll while the substage is queued/running; freeze the last content otherwise. */
  running: boolean;
}

/**
 * Live「执行进展」panel for one substage.
 *
 * Durable skill scripts append timestamped milestone lines to the work unit's
 * progress.md on the shared workspace volume; the API exposes a read-through
 * route and this panel polls it while the substage runs. Progress is a
 * transient projection — the durable record stays the Result/Artifact set.
 *
 * The top edge is a drag handle mirroring the workbench side-panel
 * resizers. One consistent direction model in both states: drag up to
 * expand, drag down to shrink; releasing near the minimum collapses, and a
 * short upward drag re-opens a collapsed panel.
 */
export function SubstageProgressPanel({ taskId, workUnitIds, running }: SubstageProgressPanelProps) {
  const [content, setContent] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [height, setHeight] = useState(HEIGHT_DEFAULT);
  const [collapsed, setCollapsed] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const dragStateRef = useRef<null | { startY: number; startHeight: number }>(null);
  // Latest size values for the window-level drag listeners (registered once).
  const sizeRef = useRef({ height, collapsed });
  sizeRef.current = { height, collapsed };
  const unitKey = useMemo(() => (workUnitIds ?? []).join("|"), [workUnitIds]);

  const load = useCallback(
    async (signal: AbortSignal) => {
      const units = unitKey ? unitKey.split("|") : [];
      if (!units.length) {
        setContent(null);
        return;
      }
      const parts: string[] = [];
      for (const unit of units) {
        try {
          const progress = await fetchWorkUnitProgress(taskId, unit, signal);
          if (progress?.exists && progress.content) parts.push(progress.content);
        } catch (err) {
          if ((err as Error)?.name !== "AbortError") {
            // Transient poll failure — keep the last rendered content.
          }
        }
      }
      if (!signal.aborted) setContent(parts.length ? parts.join("\n") : null);
    },
    [taskId, unitKey],
  );

  useEffect(() => {
    if (!unitKey) {
      setContent(null);
      return;
    }
    const controller = new AbortController();
    void load(controller.signal);
    if (!running) return () => controller.abort();
    const timer = window.setInterval(() => {
      void load(controller.signal);
      setTick((value) => value + 1);
    }, POLL_INTERVAL_MS);
    return () => {
      window.clearInterval(timer);
      controller.abort();
    };
  }, [load, running, unitKey]);

  // Switching substage re-opens a collapsed panel so fresh progress stays visible.
  useEffect(() => {
    setCollapsed(false);
  }, [unitKey]);

  useEffect(() => {
    const move = (event: MouseEvent) => {
      const drag = dragStateRef.current;
      if (!drag) return;
      const dy = event.clientY - drag.startY;
      if (sizeRef.current.collapsed) {
        // Up opens, mirroring "drag down collapses": the panel edge follows
        // the cursor once the gesture exceeds a small accidental-drag guard.
        const up = drag.startY - event.clientY;
        if (up >= EXPAND_TRIGGER) {
          const next = Math.min(
            Math.max(up, HEIGHT_COLLAPSE_THRESHOLD),
            HEIGHT_MAX,
          );
          setCollapsed(false);
          setHeight(next);
          // Rebase the drag anchor so continued movement stays continuous.
          dragStateRef.current = { startY: event.clientY, startHeight: next };
        }
        return;
      }
      // Handle sits on the top edge: dragging up expands, dragging down shrinks.
      setHeight(Math.min(Math.max(drag.startHeight - dy, HEIGHT_MIN), HEIGHT_MAX));
    };
    const end = () => {
      if (!dragStateRef.current) return;
      dragStateRef.current = null;
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
      const { height: current, collapsed: isCollapsed } = sizeRef.current;
      if (!isCollapsed && current < HEIGHT_COLLAPSE_THRESHOLD) {
        setCollapsed(true);
        setHeight(HEIGHT_DEFAULT);
      }
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", end);
    return () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", end);
      end();
    };
  }, []);

  const startResizeDrag = useCallback((event: ReactMouseEvent) => {
    event.preventDefault();
    dragStateRef.current = { startY: event.clientY, startHeight: sizeRef.current.height };
    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";
  }, []);

  useEffect(() => {
    // Keep the newest milestone in view while executing.
    const node = scrollRef.current;
    if (running && !collapsed && node) node.scrollTop = node.scrollHeight;
  }, [content, running, tick, collapsed, height]);

  if (!unitKey || !content) return null;
  return (
    <section
      className="mb-3 overflow-hidden rounded-md border border-border bg-surface"
      data-testid="substage-progress-panel"
      aria-label="执行进展"
    >
      <div
        className="relative z-resize h-1.5 w-full cursor-row-resize bg-surface-muted transition-colors after:absolute after:top-1/2 after:left-1/2 after:h-0.5 after:w-7 after:-translate-x-1/2 after:-translate-y-1/2 after:rounded-sm after:bg-text-secondary after:opacity-40 after:transition-all hover:after:w-12 hover:after:bg-accent-strong hover:after:opacity-100 active:after:w-12 active:after:bg-accent-strong active:after:opacity-100"
        role="separator"
        aria-orientation="horizontal"
        aria-label="上下拖动调整执行进展高度（向上拖展开，向下拖到最小松手收起）"
        onMouseDown={startResizeDrag}
        data-testid="substage-progress-resizer"
      />
      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
        <Typography.Text strong>执行进展</Typography.Text>
        {running ? (
          <Typography.Text type="secondary" className="text-xs">
            实时刷新中（每 5 秒）{collapsed ? " · 已收起" : ""}
          </Typography.Text>
        ) : (
          <Typography.Text type="secondary" className="text-xs">
            已定格{collapsed ? " · 已收起" : ""}
          </Typography.Text>
        )}
      </div>
      {!collapsed ? (
        <div ref={scrollRef} className="overflow-auto px-3 py-2" style={{ height }}>
          <StreamdownMarkdown content={content} testId="substage-progress-markdown" />
        </div>
      ) : null}
    </section>
  );
}
