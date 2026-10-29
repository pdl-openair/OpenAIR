import { Alert, Button, Spin, Tag, Typography } from "antd";
import type { TaskRecoveryProjection } from "../../hooks/useTaskRecoveryProjection";

export interface RecoveryActionPanelProps {
  projection: TaskRecoveryProjection;
  onReconcile: () => void;
}

const STATE_TONE = {
  unavailable: "default",
  creating: "processing",
  reconciling: "processing",
  external_result_unknown: "warning",
  awaiting_user_confirmation: "warning",
  confirmed: "success",
  failed: "error",
} as const;

export function RecoveryActionPanel({ projection, onReconcile }: RecoveryActionPanelProps) {
  const busy = projection.state === "creating" || projection.state === "reconciling";

  return (
    <section className="grid gap-3 border-t border-border pt-3" aria-label="恢复处理" data-testid="recovery-action-panel">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Typography.Text strong>恢复处理</Typography.Text>
        <Tag color={STATE_TONE[projection.state]} data-testid="recovery-action-panel-state">{projection.display.title}</Tag>
      </div>
      <dl className="m-0 grid gap-2 text-sm">
        <div className="grid gap-1"><dt className="text-text-secondary">未完成步骤</dt><dd className="m-0">{projection.display.failedStep}</dd></div>
        <div className="grid gap-1"><dt className="text-text-secondary">原因</dt><dd className="m-0">{projection.display.reason}</dd></div>
        <div className="grid gap-1"><dt className="text-text-secondary">已保留的工作</dt><dd className="m-0 grid gap-1">{projection.display.retainedFacts.map((fact, index) => <span key={`${fact}-${index}`}>{fact}</span>)}</dd></div>
        <div className="grid gap-1"><dt className="text-text-secondary">重试</dt><dd className="m-0">{projection.display.retryEffect}</dd></div>
        <div className="grid gap-1"><dt className="text-text-secondary">调整方案</dt><dd className="m-0">{projection.display.replanEffect}</dd></div>
        {projection.display.successorFact ? <div className="grid gap-1"><dt className="text-text-secondary">后续执行</dt><dd className="m-0">{projection.display.successorFact}</dd></div> : null}
      </dl>
      <Alert type={projection.state === "failed" ? "error" : "info"} showIcon message={projection.display.nextStep} />
      <div className="flex flex-wrap gap-2" data-testid="recovery-action-controls">
        {projection.authoritativeRefreshRequired ? <Button disabled={busy} icon={busy ? <Spin size="small" /> : undefined} onClick={onReconcile} data-testid="recovery-action-reconcile">重新核对</Button> : null}
      </div>
      <Typography.Text type="secondary" className="text-xs">历史恢复记录仅供查阅。</Typography.Text>
    </section>
  );
}
