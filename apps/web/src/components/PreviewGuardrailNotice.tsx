import { Alert, Tag, Typography } from "antd";
import type { ResultSource } from "@openair/shared";
import { normalizeResultSource } from "../lib/resultSource";
import { cn } from "../lib/cn";

type PreviewGuardrailNoticeProps = {
  resultSource?: ResultSource;
  compact?: boolean;
  scope?: "task" | "global";
};

const MODE_COPY: Record<ResultSource, { label: string; detail: string; color?: string }> = {
  live: {
    label: "Live · 已生成",
    detail: "本任务已有新生成的运行记录。使用结论前请查看来源、边界和产物关联。",
    color: "success",
  },
  replay: {
    label: "回放结果",
    detail: "这是已保存的历史记录，可用于复现与核对，不代表本次新执行。",
    color: "processing",
  },
  demo: {
    label: "参考内容",
    detail: "该内容只用于了解流程结构，不会写入真实任务，也不能作为科研结论。",
    color: "default",
  },
  none: {
    label: "待生成",
    detail: "当前还没有可审阅结果。继续推进任务后会生成产物或明确缺口。",
  },
};

const GLOBAL_COPY = {
  label: "科研任务",
  detail: "任务结果会保留来源、边界和下一步。使用结论前请查看对应产物与来源说明。",
  color: "processing",
};

export function PreviewGuardrailNotice({ resultSource, compact = false, scope = "task" }: PreviewGuardrailNoticeProps) {
  const normalized = normalizeResultSource(resultSource);
  const mode = scope === "global" ? GLOBAL_COPY : MODE_COPY[normalized];
  return (
    <Alert
      className={cn(
        "border-info-border bg-info-soft",
        compact ? "my-2 mb-3" : "my-3",
      )}
      type="info"
      showIcon
      data-testid="preview-guardrail-notice"
      data-result-source={normalized}
      message={
        <div className="flex flex-wrap items-center gap-2">
          <Typography.Text strong>科研任务边界</Typography.Text>
          <Tag color={mode.color}>{mode.label}</Tag>
        </div>
      }
      description={
        <div className="flex flex-col gap-1 text-xs leading-normal text-text-secondary">
          <span>{mode.detail}</span>
          <span>AI 辅助科研输出需要结合来源和上下文判断后再用于结论、引用或实验决策。</span>
        </div>
      }
    />
  );
}
