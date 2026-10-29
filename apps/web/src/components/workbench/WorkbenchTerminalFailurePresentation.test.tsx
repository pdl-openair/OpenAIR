import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { WorkbenchUserActionItem } from "../../api/workbenchUserActions";
import { UserActionStreamItems } from "./WorkbenchInteractionPanel";
import { WorkbenchUserActionCenter } from "./WorkbenchUserActionCenter";

const authoritativeUnrecoverableMessage = "研究已经停止，当前无法继续恢复。";
const noOp = () => {};
const idleOperationState = () => "idle" as const;

const retryItem: WorkbenchUserActionItem = {
  itemId: "retry-1",
  kind: "explicit_retry_ready",
  title: "确认重新执行",
  reason: "上一次调用没有实际发出。",
  workUnitId: "unit-1",
  objective: null,
  artifactRefs: [],
  allowedActions: ["explicit_retry"],
  commandContexts: {},
};

describe("terminal unrecoverable failure presentation", () => {
  it("shows the backend message and stopped heading instead of waiting when no action items remain", () => {
    render(
      <WorkbenchUserActionCenter
        items={[]}
        unrecoverableMessage={authoritativeUnrecoverableMessage}
        onSupplement={noOp}
        onExplicitRetry={noOp}
        operationState={idleOperationState}
      />,
    );

    expect(screen.getByText("研究已经停止")).toBeInTheDocument();
    expect(screen.getByTestId("workbench-unrecoverable-state")).toHaveTextContent(authoritativeUnrecoverableMessage);
    expect(screen.queryByText("当前等待")).not.toBeInTheDocument();
    expect(screen.queryByText("当前状态暂时无法安全恢复。")).not.toBeInTheDocument();
  });

  it("renders the same authoritative terminal message in the interaction stream", () => {
    render(
      <UserActionStreamItems
        items={[]}
        unrecoverableMessage={authoritativeUnrecoverableMessage}
        onSupplement={noOp}
        onExplicitRetry={noOp}
        operationState={idleOperationState}
      />,
    );

    expect(screen.getByTestId("workbench-unrecoverable-state")).toHaveTextContent(authoritativeUnrecoverableMessage);
    expect(screen.queryByText("现有记录和产物已经保留。")).not.toBeInTheDocument();
  });

  it("keeps ordinary action-required, waiting, and recoverable retry presentations", () => {
    const { rerender } = render(
      <WorkbenchUserActionCenter
        items={[retryItem]}
        onSupplement={noOp}
        onExplicitRetry={noOp}
        operationState={idleOperationState}
      />,
    );

    expect(screen.getByText("需要你处理")).toBeInTheDocument();

    rerender(
      <WorkbenchUserActionCenter
        items={[{ ...retryItem, allowedActions: [] }]}
        onSupplement={noOp}
        onExplicitRetry={noOp}
        operationState={idleOperationState}
      />,
    );

    expect(screen.getByText("当前等待")).toBeInTheDocument();

    render(
      <UserActionStreamItems
        items={[retryItem]}
        onSupplement={noOp}
        onExplicitRetry={noOp}
        operationState={idleOperationState}
      />,
    );

    expect(screen.getByTestId("workbench-explicit-retry-unit-1")).toBeInTheDocument();
  });
});
