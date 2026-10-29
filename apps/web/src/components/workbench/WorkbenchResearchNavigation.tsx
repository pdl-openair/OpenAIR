import { Button, Typography } from "antd";
import { cn } from "../../lib/cn";
import {
  WorkbenchFlowNav,
  type WorkbenchFlowNavProps,
} from "./WorkbenchFlowNav";

interface Props {
  collapsed: boolean;
  mobile: boolean;
  width?: number;
  revision: number;
  executionStatus: string;
  planAvailable: boolean;
  planSelected: boolean;
  onViewPlan: () => void;
  flow: WorkbenchFlowNavProps;
}

export function WorkbenchResearchNavigation({
  collapsed,
  mobile,
  width,
  revision,
  executionStatus,
  planAvailable,
  planSelected,
  onViewPlan,
  flow,
}: Props) {
  return (
    <aside
      className={cn(
        "flex min-h-0 flex-col overflow-hidden bg-surface border-r border-border",
        collapsed ? "w-0 shrink-0" : "w-[220px] shrink-0",
        mobile && "w-full flex-1 shrink border-r-0",
      )}
      data-testid="workbench-left-panel"
      data-revision={revision}
      data-execution-status={executionStatus}
      style={
        !mobile && width !== undefined ? { width, flex: "0 0 auto" } : undefined
      }
    >
      {collapsed ? (
        <div
          className="flex h-full w-full items-center justify-center py-3 text-xs tracking-sidebar text-text-muted [writing-mode:vertical-rl]"
          data-testid="left-panel-collapsed"
        >
          研究阶段
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-x-hidden px-3 overflow-y-auto pb-2">
          <Typography.Title level={5} style={{ margin: "12px 0 4px" }}>
            研究阶段
          </Typography.Title>
          {planAvailable ? (
            <Button
              block
              type={planSelected ? "primary" : "default"}
              className="mb-3"
              aria-pressed={planSelected}
              onClick={onViewPlan}
              data-testid="workbench-view-confirmed-plan"
            >
              研究方案 · 已确认
            </Button>
          ) : null}
          <WorkbenchFlowNav {...flow} />
        </div>
      )}
    </aside>
  );
}
