import { Tag, Typography } from "antd";
import type { ResultSource } from "@openair/shared";
import { normalizeResultSource, RESULT_SOURCE_LABELS } from "../lib/resultSource";

export function ResultSourceTag({ source }: { source?: ResultSource }) {
  const normalized = normalizeResultSource(source);
  const color = normalized === "live" ? "success" : normalized === "replay" ? "processing" : normalized === "demo" ? "default" : undefined;
  return (
    <Tag color={color} data-testid="result-source-tag">
      {RESULT_SOURCE_LABELS[normalized]}
    </Tag>
  );
}

/** 紧凑全局来源提示：保留 testid，不占大 Banner 首屏。 */
export function ReplayResultDisclosure() {
  return (
    <div className="wb-replay-disclosure" data-testid="replay-disclosure" role="status" aria-live="polite">
      <Tag color="processing">历史结果</Tag>
      <Typography.Text type="secondary" className="wb-replay-disclosure-text">
        该结果来自历史记录，用于复现与验证，不代表本次新执行。
      </Typography.Text>
    </div>
  );
}
