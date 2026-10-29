import { useEffect, useRef, useState } from "react";

type Side = "left" | "right";
type Widths = { left: number; right: number };

function clamp(side: Side, width: number): number {
  const compact = window.innerWidth <= 1320;
  return side === "left"
    ? Math.min(Math.max(width, 180), compact ? 190 : 480)
    : Math.min(Math.max(width, 260), compact ? 260 : 640);
}

function readWidths(taskId: string | undefined): Widths {
  let widths = { left: 220, right: 280 };
  try {
    const raw = taskId
      ? localStorage.getItem(`openair.workbench.panel-widths.v2:${taskId}`)
      : null;
    const saved = raw ? JSON.parse(raw) : null;
    if (
      typeof saved?.leftWidth === "number" &&
      Number.isFinite(saved.leftWidth)
    )
      widths.left = saved.leftWidth;
    if (
      typeof saved?.rightWidth === "number" &&
      Number.isFinite(saved.rightWidth)
    )
      widths.right = saved.rightWidth;
  } catch {
    // Invalid preferences do not prevent opening a research task.
    widths = { left: 220, right: 280 };
  }
  return {
    left: clamp("left", widths.left),
    right: clamp("right", widths.right),
  };
}

/** Owned by a task-keyed Workbench session: initialization precedes persistence. */
export function useWorkbenchPanelWidths(taskId: string | undefined) {
  const [widths, setWidths] = useState(() => readWidths(taskId));
  const dragStateRef = useRef<null | {
    side: Side;
    startX: number;
    startWidth: number;
  }>(null);
  useEffect(() => {
    if (!taskId) return;
    localStorage.setItem(
      `openair.workbench.panel-widths.v2:${taskId}`,
      JSON.stringify({ leftWidth: widths.left, rightWidth: widths.right }),
    );
  }, [taskId, widths]);
  useEffect(() => {
    const resize = () =>
      setWidths((previous) => ({
        left: clamp("left", previous.left),
        right: clamp("right", previous.right),
      }));
    const move = (event: MouseEvent) => {
      const drag = dragStateRef.current;
      if (!drag) return;
      const delta = event.clientX - drag.startX;
      const next = clamp(
        drag.side,
        drag.startWidth + (drag.side === "left" ? delta : -delta),
      );
      setWidths((previous) => ({ ...previous, [drag.side]: next }));
    };
    const end = () => {
      if (!dragStateRef.current) return;
      dragStateRef.current = null;
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
    };
    window.addEventListener("resize", resize);
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", end);
    return () => {
      window.removeEventListener("resize", resize);
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", end);
      end();
    };
  }, []);
  return {
    leftPanelWidth: widths.left,
    rightPanelWidth: widths.right,
    dragStateRef,
  };
}
