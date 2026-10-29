import { useMemo } from "react";
import { Tooltip, Typography } from "antd";
import { CheckCircleFilled, ClockCircleOutlined, MinusCircleFilled, PlayCircleFilled } from "@ant-design/icons";
import type { TaskStage } from "@openair/shared";
import type { WorkbenchArtifact, WorkbenchStageRow } from "../model/workbenchTask";
import { cn } from "../lib/cn";

export type NodeStatus = "completed" | "running" | "not_started" | "failed" | "pending";

export interface ChainTimelineProps {
  stages: WorkbenchStageRow[];
  artifacts: WorkbenchArtifact[];
  currentStage?: TaskStage;
  onSubstageClick?: (stage: TaskStage, substage: string) => void;
  className?: string;
}

const STATUS_ICON_CLASS: Record<NodeStatus, string> = {
  completed: "text-success",
  running: "text-accent-strong",
  pending: "text-warning",
  not_started: "text-text-muted",
  failed: "text-danger",
};

const STATUS_BADGE_CLASS: Record<NodeStatus, string> = {
  completed: "bg-success-soft text-success",
  running: "bg-info-soft text-accent-strong",
  pending: "bg-warning-soft text-warning",
  not_started: "bg-surface-soft text-text-muted",
  failed: "bg-danger-soft text-danger",
};

const STATUS_HOVER_CLASS: Record<NodeStatus, string> = {
  completed: "hover:border-success hover:shadow-[0_0_0_2px_color-mix(in_srgb,var(--wb-success)_13%,transparent)]",
  running: "hover:border-accent-strong hover:shadow-[0_0_0_2px_color-mix(in_srgb,var(--wb-accent-strong)_13%,transparent)]",
  pending: "hover:border-warning hover:shadow-[0_0_0_2px_color-mix(in_srgb,var(--wb-warning)_13%,transparent)]",
  not_started: "hover:border-border",
  failed: "hover:border-danger hover:shadow-[0_0_0_2px_color-mix(in_srgb,var(--wb-danger)_13%,transparent)]",
};

const STATUS_ICON = {
  completed: CheckCircleFilled,
  running: PlayCircleFilled,
  pending: ClockCircleOutlined,
  not_started: MinusCircleFilled,
  failed: MinusCircleFilled,
} as const;

function statusOf(status: string): NodeStatus {
  if (status === "completed" || status === "running" || status === "failed" || status === "pending") {
    return status;
  }
  return "not_started";
}

export function ChainTimeline({
  stages,
  artifacts,
  currentStage,
  onSubstageClick,
  className,
}: ChainTimelineProps) {
  const artifactCountBySubstage = useMemo(() => {
    const byStage = new Map<string, number>();
    for (const a of artifacts) {
      const key = a.relatedStage ?? "_unknown";
      byStage.set(key, (byStage.get(key) ?? 0) + 1);
    }
    return byStage;
  }, [artifacts]);

  const isCompact = stages.length <= 1 || stages.every((s) => (s.subStages?.length ?? 0) === 0);

  if (isCompact) return null;

  return (
    <div
      className={cn(
        "flex items-stretch gap-0 overflow-x-auto rounded-lg border border-border bg-surface-soft px-4 py-3.5",
        className,
      )}
    >
      {stages.map((stage, stageIdx) => {
        const stageStatus = statusOf(stage.status);
        const stageArtifactCount = artifactCountBySubstage.get(stage.stage) ?? 0;
        const isCurrent = currentStage === stage.stage;
        const isLast = stageIdx === stages.length - 1;

        return (
          <div key={stage.stage} className="flex min-w-[200px] flex-1 items-stretch">
            <div
              className={cn(
                "flex flex-1 flex-col rounded px-3",
                isCurrent && "border-l-3 border-l-accent-strong bg-accent-soft-2/40",
                !isCurrent && "border-l-3 border-l-transparent",
              )}
            >
              <div className="mb-2.5">
                <Typography.Text
                  strong
                  className={cn(
                    "text-sm",
                    stageStatus === "not_started" ? "text-text-muted" : "text-text",
                  )}
                >
                  {stage.label}
                </Typography.Text>
                <div className="mt-0.5">
                  <Typography.Text type="secondary" className="text-2xs">
                    {stageArtifactCount} 个产物 · 状态:{stageStatus}
                  </Typography.Text>
                </div>
              </div>

              <div className="flex flex-col gap-1.5">
                {(stage.subStages ?? []).filter(Boolean).map((sub) => {
                  const subStatus = statusOf(sub.status);
                  const subArtifactRefs = sub.artifactRefs ?? [];
                  const Icon = STATUS_ICON[subStatus];
                  const clickable = !!onSubstageClick && subStatus !== "not_started";
                  return (
                    <Tooltip
                      key={sub.id}
                      title={
                        <div>
                          <div>{sub.label}</div>
                          <div className="text-text-muted">
                            状态:{subStatus} · 产物:{subArtifactRefs.length}
                          </div>
                        </div>
                      }
                    >
                      <div
                        onClick={() => clickable && onSubstageClick?.(stage.stage, sub.subStage)}
                        role={clickable ? "button" : undefined}
                        tabIndex={clickable ? 0 : undefined}
                        className={cn(
                          "flex items-center gap-2 rounded-md border border-border bg-surface px-2 py-1.5 transition-all duration-150",
                          clickable && "cursor-pointer",
                          clickable && STATUS_HOVER_CLASS[subStatus],
                          subStatus === "not_started" && "opacity-55",
                        )}
                      >
                        <Icon className={cn("text-sm", STATUS_ICON_CLASS[subStatus])} />
                        <span className="flex-1 text-xs text-text">{sub.label}</span>
                        {subArtifactRefs.length > 0 && (
                          <span
                            className={cn(
                              "rounded-lg px-1.5 py-px text-3xs",
                              STATUS_BADGE_CLASS[subStatus],
                            )}
                          >
                            {subArtifactRefs.length}
                          </span>
                        )}
                      </div>
                    </Tooltip>
                  );
                })}
                {(stage.subStages ?? []).length === 0 && (
                  <div className="px-2 py-1 text-2xs text-text-muted">—</div>
                )}
              </div>
            </div>

            {!isLast && (
              <div
                aria-hidden
                className="flex items-center self-start px-1 pt-5.5 text-base text-border-strong"
              >
                →
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
