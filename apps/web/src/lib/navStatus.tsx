import { Tag } from "antd";
import type { PhaseNavStatus } from "./pagePhases";

export function navStatusLabel(s: PhaseNavStatus): string {
  const map: Record<PhaseNavStatus, string> = {
    completed: "已完成",
    running: "进行中",
    pending: "待处理",
    blocked: "执行受阻",
    failed: "失败",
    not_started: "未开始",
  };
  return map[s];
}

export function NavStatusTag({ status }: { status: PhaseNavStatus }) {
  const color =
    status === "completed"
      ? "success"
      : status === "running"
        ? "processing"
        : status === "pending" || status === "blocked"
          ? "warning"
          : status === "failed"
            ? "error"
            : "default";
  return <Tag color={color}>{navStatusLabel(status)}</Tag>;
}
