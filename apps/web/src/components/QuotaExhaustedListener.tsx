import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Modal } from "antd";
import {
  QUOTA_EXCEEDED_CODE,
  QUOTA_TIER_LABELS,
  type QuotaExceededError,
} from "@openair/shared";
import { ApiError, registerQuotaExhaustedHandler, unregisterQuotaExhaustedHandler } from "../api/http";

/**
 * Global listener for HTTP 402 quota_exhausted (S03-14-05).
 *
 * Mounted once inside AppLayout. When any API call returns 402 + code
 * quota_exhausted, http.ts fires the handler; this component shows a Modal with
 * tier / remaining / resetAt and a link to /settings. Local action handlers can
 * recognise ApiError.code === quota_exhausted to avoid double-reporting.
 */
export function QuotaExhaustedListener() {
  const [open, setOpen] = useState(false);
  const [payload, setPayload] = useState<QuotaExceededError | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    const handler = (p: QuotaExceededError) => {
      if (p?.code !== QUOTA_EXCEEDED_CODE) return;
      setPayload(p);
      setOpen(true);
    };
    registerQuotaExhaustedHandler(handler);
    return () => unregisterQuotaExhaustedHandler();
  }, []);

  const tierLabel = payload ? (QUOTA_TIER_LABELS[payload.tier] ?? payload.tier) : "";
  const resetHint = payload?.resetAt
    ? `将于 ${new Date(payload.resetAt).toLocaleString()} 重置`
    : "一次性额度已用尽";

  return (
    <Modal
      open={open}
      title="额度不足"
      okText="去查看额度"
      cancelText="关闭"
      onOk={() => {
        setOpen(false);
        navigate("/settings");
      }}
      onCancel={() => setOpen(false)}
    >
      <p>{payload?.message ?? "当前账户额度不足，无法发起本次 AI 调用。"}</p>
      <p style={{ color: "rgba(0,0,0,0.45)", marginTop: 8 }}>
        等级：{tierLabel} · 剩余 {payload?.remaining ?? 0} tokens · {resetHint}
      </p>
    </Modal>
  );
}

/** Convenience predicate for action handlers to suppress duplicate toasts. */
export function isQuotaExhaustedError(err: unknown): boolean {
  return err instanceof ApiError && err.code === QUOTA_EXCEEDED_CODE;
}
