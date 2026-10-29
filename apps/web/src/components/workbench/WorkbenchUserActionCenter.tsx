import { Alert, Button, Popconfirm, Space, Typography } from "antd";
import type {
  WorkbenchActionOperationState,
  WorkbenchUserActionItem,
  WorkbenchUserActionName,
} from "../../api/workbenchUserActions";

export interface WorkbenchUserActionCenterProps {
  items: WorkbenchUserActionItem[];
  unrecoverableMessage?: string | null;
  onSupplement: () => void;
  onExplicitRetry: (item: WorkbenchUserActionItem) => void;
  operationState: (item: WorkbenchUserActionItem, action: WorkbenchUserActionName) => WorkbenchActionOperationState;
}

const ACTION_LABELS: Record<WorkbenchUserActionName, string> = {
  supplement_input: "补充信息",
  explicit_retry: "重试",
  continue_research: "继续研究",
  stop: "停止执行",
};

function stateMessage(
  action: WorkbenchUserActionName,
  state: WorkbenchActionOperationState,
): string | null {
  if (state === "submitting") return "正在提交…";
  if (state === "response_unknown") return "提交响应未送达，正在按权威状态核对。";
  if (action === "supplement_input" && state === "pending") return "已提交，等待在安全节点生效。";
  if (action === "supplement_input" && state === "confirmed") return "补充已生效。";
  if (state === "pending") return "服务端正在处理，最终结果以重新读取为准。";
  if (state === "confirmed") return "服务端已确认。";
  if (state === "rejected") return "服务端已拒绝该操作。";
  if (state === "failed") return "操作未完成，请先刷新权威状态。";
  return null;
}

export function WorkbenchUserActionCenter({
  items,
  unrecoverableMessage,
  onSupplement,
  onExplicitRetry,
  operationState,
}: WorkbenchUserActionCenterProps) {
  const hasUnrecoverableState = unrecoverableMessage != null;
  if (!items.length && !hasUnrecoverableState) return null;
  const hasAction = items.some((item) => item.allowedActions.length > 0);
  const hasWaiting = items.some((item) => item.allowedActions.length === 0);
  const heading = hasUnrecoverableState
    ? "研究已经停止"
    : hasAction && hasWaiting ? "操作与等待" : hasAction ? "需要你处理" : "当前等待";
  return (
    <section title={heading} aria-label={heading} className="grid gap-2">
      <Typography.Text strong>{heading}</Typography.Text>
      {items.map((item) => (
        <Alert
          key={item.itemId}
          type="warning"
          message={item.title}
          description={(
            <Space direction="vertical">
              <Typography.Text>{item.reason}</Typography.Text>
              <Space wrap>
                {item.allowedActions.map((action) => {
                  const state = operationState(item, action);
                  const busy = state === "submitting" || state === "pending" || state === "response_unknown";
                  if (action === "supplement_input") {
                    return (
                      <Button key={action} size="small" type="primary" disabled={busy} onClick={onSupplement}>
                        {ACTION_LABELS[action]}
                      </Button>
                    );
                  }
                  if (action === "explicit_retry") {
                    return (
                      <Popconfirm
                        key={action}
                        title="重试这一步？"
                        description="上次执行未成功完成。重试会创建一次新的执行。"
                        okText="重试"
                        cancelText="取消"
                        onConfirm={() => onExplicitRetry(item)}
                      >
                        <Button
                          size="small"
                          type="primary"
                          danger
                          loading={state === "submitting"}
                          disabled={busy}
                          data-testid={`workbench-explicit-retry-${item.workUnitId ?? item.itemId}`}
                        >
                          {ACTION_LABELS[action]}
                        </Button>
                      </Popconfirm>
                    );
                  }
                  return null;
                })}
              </Space>
              {item.allowedActions.map((action) => {
                const message = stateMessage(action, operationState(item, action));
                return message ? <Typography.Text key={`${action}-state`} type="secondary">{message}</Typography.Text> : null;
              })}
            </Space>
          )}
        />
      ))}
      {hasUnrecoverableState ? (
        <Alert
          type="warning"
          showIcon
          message={unrecoverableMessage}
          data-testid="workbench-unrecoverable-state"
        />
      ) : null}
    </section>
  );
}
