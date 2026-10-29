import {
  CheckCircleFilled,
  ExclamationCircleFilled,
  LoadingOutlined,
  MinusCircleOutlined,
} from "@ant-design/icons";
import type { UiLanguage } from "@openair/shared";
import { AnimatePresence, motion } from "motion/react";
import type { PagePhaseId, PhaseNavStatus } from "../../lib/pagePhases";
import type { WorkbenchStageRow } from "../../model/workbenchTask";
import type { WorkbenchProgressStep } from "../../lib/workbenchProgress";
import { flowProjectionStatusLabel } from "../../lib/workbenchProgress";
import { cn } from "../../lib/cn";
import { researchWorkProgressTitle } from "../../lib/researchDesignPresentation";

function phaseIcon(status: PhaseNavStatus) {
  if (status === "completed") {
    return <CheckCircleFilled className="text-success" />;
  }
  if (status === "running") {
    return <LoadingOutlined className="text-accent-strong" />;
  }
  if (status === "pending" || status === "blocked") {
    return <ExclamationCircleFilled className="text-warning" />;
  }
  if (status === "failed") {
    return <MinusCircleOutlined className="text-danger" />;
  }
  return (
    <span
      className="inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-text-muted"
      aria-hidden
    />
  );
}

function phaseStatusClass(status: PhaseNavStatus): string {
  switch (status) {
    case "completed":
      return "text-success";
    case "running":
      return "text-accent-strong";
    case "pending":
    case "blocked":
      return "text-warning";
    case "failed":
      return "text-danger";
    default:
      return "text-text-muted";
  }
}

function userFacingFlowLabel(label: string): string {
  return label
    .replace(/\bworker\b/gi, "执行")
    .replace(/\bworkflow\b/gi, "流程")
    .replace(/\bschema\b/gi, "字段")
    .replace(/\braw\s*log\b/gi, "记录")
    .replace(/\btrace\b/gi, "记录")
    .replace(/[_-]+/g, " ")
    .trim();
}

export interface WorkbenchFlowNavProps {
  language: UiLanguage;
  phaseAgg: Record<PagePhaseId, PhaseNavStatus>;
  stagesByPhase: Record<PagePhaseId, WorkbenchStageRow | undefined>;
  progressSteps: WorkbenchProgressStep[];
  viewingPhase: PagePhaseId;
  currentPagePhase: PagePhaseId | null;
  currentPagePhases?: PagePhaseId[];
  currentSubStageIds?: string[];
  selectedSubStage: string | null;
  onSelectPhase: (p: PagePhaseId) => void;
  onSelectSubStage: (subStageId: string) => void;
}

export function WorkbenchFlowNav({
  language,
  stagesByPhase,
  progressSteps,
  viewingPhase,
  currentPagePhase,
  currentPagePhases = [],
  currentSubStageIds = [],
  selectedSubStage,
  onSelectPhase,
  onSelectSubStage,
}: WorkbenchFlowNavProps) {
  return (
    <nav
      className="pb-2"
      aria-label={language === "en-US" ? "Research stages" : "研究阶段"}
    >
      {progressSteps.length === 0 ? (
        <div
          className="rounded-lg border border-dashed border-border px-3 py-4 text-sm text-text-muted"
          data-testid="workbench-research-design-empty"
        >
          {language === "en-US"
            ? "No research work is currently included in this design."
            : "当前研究设计尚未包含研究节点。"}
        </div>
      ) : null}
      {progressSteps.map((step, idx) => {
        const isView = viewingPhase === step.id;
        const isCurrent =
          currentPagePhases.length > 0
            ? currentPagePhases.includes(step.id)
            : currentPagePhase === step.id;
        const subStages = isView
          ? (stagesByPhase[step.id]?.subStages ?? [])
          : [];

        return (
          <div key={step.id} className="mb-1">
            <motion.button
              type="button"
              whileHover={{ backgroundColor: "var(--wb-surface-muted)" }}
              className={cn(
                "flex w-full cursor-pointer items-center gap-2 rounded-lg border border-transparent bg-surface px-2.5 py-2 text-left text-sm text-text",
                "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-strong",
                isView && "border-info-border bg-info-soft",
                isCurrent && "shadow-[inset_3px_0_0_var(--wb-accent-strong)]",
              )}
              aria-current={isCurrent ? "step" : undefined}
              onClick={() => onSelectPhase(step.id)}
            >
              <span className="min-w-4 font-semibold text-text-muted">
                {idx + 1}
              </span>
              {phaseIcon(step.status)}
              <span className="grid min-w-0 flex-1 gap-0.5">
                <span className="font-semibold">
                  {userFacingFlowLabel(step.label)}
                </span>
                <span
                  className={cn(
                    "text-xs leading-tight",
                    phaseStatusClass(step.status),
                  )}
                  data-testid={`workbench-left-stage-status-${step.id}`}
                >
                  {(() => {
                    const projectionStatus =
                      (step as { projectionStatus?: string })
                        .projectionStatus ??
                      stagesByPhase[step.id]?.projectionStatus;
                    if (
                      projectionStatus &&
                      projectionStatus !== "not_started" &&
                      projectionStatus !== "planned" &&
                      step.status === "not_started"
                    ) {
                      return "执行中";
                    }
                    return flowProjectionStatusLabel(
                      projectionStatus,
                      step.status,
                    );
                  })()}
                </span>
              </span>
            </motion.button>
            <AnimatePresence initial={false}>
              {subStages.length > 0 ? (
                <motion.div
                  key={`${step.id}-subs`}
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.18, ease: "easeOut" }}
                  className="overflow-hidden"
                >
                  <div className="mb-2 mt-1 pl-7">
                    {subStages.map((sub, subIdx) => (
                      <motion.button
                        key={sub.id}
                        type="button"
                        whileHover={{
                          backgroundColor: "var(--wb-surface-muted)",
                        }}
                        className={cn(
                          "mb-0.5 flex w-full cursor-pointer items-center gap-2 rounded-md border-none bg-transparent px-2.5 py-1.5 text-left text-sm text-text-secondary",
                          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-strong",
                          selectedSubStage === sub.id &&
                            "bg-info-soft font-semibold text-accent-strong",
                        )}
                        aria-current={
                          currentSubStageIds.includes(sub.id)
                            ? "step"
                            : undefined
                        }
                        onClick={() => onSelectSubStage(sub.id)}
                      >
                        <span className="min-w-3.5 text-xs text-text-muted">
                          {idx + 1}.{subIdx + 1}
                        </span>
                        {phaseIcon(sub.status)}
                        <span className="grid min-w-0 flex-1 gap-0.5">
                          <span
                            className="line-clamp-2 font-semibold"
                            title={researchWorkProgressTitle(sub)}
                          >
                            {userFacingFlowLabel(
                              researchWorkProgressTitle(sub),
                            )}
                          </span>
                          <span
                            className={cn(
                              "text-xs leading-tight",
                              phaseStatusClass(sub.status),
                            )}
                            data-testid={`workbench-left-substage-status-${sub.id}`}
                          >
                            {flowProjectionStatusLabel(
                              sub.projectionStatus,
                              sub.status,
                            )}
                          </span>
                        </span>
                      </motion.button>
                    ))}
                  </div>
                </motion.div>
              ) : null}
            </AnimatePresence>
          </div>
        );
      })}
    </nav>
  );
}
