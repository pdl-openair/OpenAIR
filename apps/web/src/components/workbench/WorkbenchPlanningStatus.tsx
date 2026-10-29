import { Button, Spin, Typography } from "antd";
import type { ResearchStartSessionStatus } from "../../api/client";
import type { ResearchStartWaitingView } from "../../lib/researchStartWaiting";

export function WorkbenchPlanningStatus({
  planningStatus,
  planningWaitingView,
  planningElapsedLabel,
  onRecheckPlanningSession,
}: {
  planningStatus: ResearchStartSessionStatus | null;
  planningWaitingView: ResearchStartWaitingView;
  planningElapsedLabel: string | null;
  onRecheckPlanningSession?: () => void;
}) {
  return (
    <div
      className="grid gap-1 border-b border-border px-3 py-3 text-sm"
      data-testid="workbench-planning-status"
    >
      <div className="flex items-start gap-2">
        {planningStatus?.status === "failed" ? (
          <span className="mt-0.5 text-danger" aria-hidden>
            !
          </span>
        ) : planningStatus?.status === "completed" ||
          planningWaitingView.progressCode === "design_ready" ? (
          <span className="mt-0.5 text-success" aria-hidden>
            ✓
          </span>
        ) : (
          <Spin size="small" className="mt-0.5" />
        )}
        <div className="grid min-w-0 flex-1 gap-1">
          {planningStatus?.status !== "running" ? (
            <Typography.Text strong>
              {planningWaitingView.title}
            </Typography.Text>
          ) : null}
          <Typography.Text type="secondary">
            {planningWaitingView.message}
          </Typography.Text>
          {planningElapsedLabel ? (
            <Typography.Text type="secondary">
              {planningElapsedLabel}
            </Typography.Text>
          ) : null}
          {planningWaitingView.nextActionHint ? (
            <Typography.Text type="secondary">
              {planningWaitingView.nextActionHint}
            </Typography.Text>
          ) : null}
          {planningWaitingView.unknown && onRecheckPlanningSession ? (
            <Button
              size="small"
              className="mt-1 w-fit"
              onClick={onRecheckPlanningSession}
              data-testid="workbench-planning-recheck"
            >
              再检查
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
