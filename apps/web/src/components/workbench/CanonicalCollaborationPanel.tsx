import {
  Alert,
  Button,
  Card,
  Empty,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import type { CanonicalCollaborationLoadError, CanonicalCollaborationLoadStatus } from "../../hooks/useCanonicalCollaboration";
import { hasMaterializedCanonicalCollaboration } from "../../model/workbenchCollaborationLayoutCore.mjs";
import { userFacingDynamicText, userFacingErrorText } from "../../lib/userFacingLanguage";
import type {
  CanonicalCollaborationAllowedAction,
  CanonicalCollaborationArtifactView,
  CanonicalCollaborationView,
  CanonicalCollaborationWorkUnitStatus,
} from "../../model/canonicalCollaborationView";

const { Text, Title } = Typography;

export interface CanonicalCollaborationPanelProps {
  status: CanonicalCollaborationLoadStatus;
  view: CanonicalCollaborationView | null;
  error: CanonicalCollaborationLoadError | null;
  onRetry?: () => void;
  onAction?: (action: CanonicalCollaborationAllowedAction) => void;
  actionPending?: CanonicalCollaborationAllowedAction | null;
  hideUserActions?: boolean;
  className?: string;
}

function statusColor(status: CanonicalCollaborationWorkUnitStatus): string {
  if (status === "completed") return "success";
  if (status === "running" || status === "reviewing") return "processing";
  if (status === "failed" || status === "external_result_unknown") return "error";
  if (status === "blocked") return "warning";
  return "default";
}

function artifactTags(artifacts: CanonicalCollaborationArtifactView[]) {
  if (artifacts.length === 0) return null;
  return (
    <Space size={[4, 4]} wrap>
      {artifacts.map((artifact) => (
        <Tag key={artifact.key}>
          {artifact.label} · {artifact.stateLabel}
        </Tag>
      ))}
    </Space>
  );
}

function sectionTitle(id: string, children: string) {
  return (
    <Title id={id} level={5} style={{ margin: 0, fontSize: 15 }}>
      {children}
    </Title>
  );
}

function formatOccurredAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function errorAlert(error: CanonicalCollaborationLoadError, onRetry?: () => void) {
  return (
    <Alert
      showIcon
      type={error.kind === "revision_mismatch" || error.kind === "invalid_response" ? "warning" : "error"}
      message={error.kind === "revision_mismatch" ? "协作进度需要更新" : "协作进展暂不可用"}
      description={userFacingErrorText(error.message, "暂时无法读取最新协作进度，请稍后重试。")}
      action={onRetry ? (
        <Button size="small" icon={<ReloadOutlined />} onClick={onRetry}>
          重新读取
        </Button>
      ) : undefined}
    />
  );
}

export function CanonicalCollaborationPanel({
  status,
  view,
  error,
  onRetry,
  onAction,
  actionPending = null,
  hideUserActions = false,
  className,
}: CanonicalCollaborationPanelProps) {
  if (status === "loading") {
    return (
      <Card className={className} size="small" aria-busy="true">
        <Space><Spin size="small" /><Text type="secondary">正在读取协作进度…</Text></Space>
      </Card>
    );
  }
  if (status === "error" && error) {
    return <div className={className}>{errorAlert(error, onRetry)}</div>;
  }
  if (status === "idle") {
    return (
      <Card className={className} size="small">
        <Text type="secondary">正在确认最新协作进度…</Text>
      </Card>
    );
  }
  if (status !== "ready" || !view) return null;
  if (!hasMaterializedCanonicalCollaboration(view)) return null;

  return (
    <Card
      className={className}
      size="small"
      title="协作推进"
      data-testid="canonical-collaboration-panel"
    >
      <div style={{ display: "grid", gap: 20 }}>
      {!hideUserActions ? (
      <section aria-labelledby="canonical-user-actions">
        {sectionTitle("canonical-user-actions", "需要你处理")}
        <div style={{ marginTop: 10, display: "grid", gap: 10 }}>
          {view.userActionItems.length === 0 ? (
            <Text type="secondary">当前没有需要你处理的事项。</Text>
          ) : view.userActionItems.map((item) => (
            <Alert
              key={item.key}
              showIcon
              type={item.kind === "external_result_unknown" ? "warning" : "info"}
              message={item.title}
              description={(
                <Space orientation="vertical" size={6}>
                  <Text>{userFacingDynamicText(item.reason, "当前需要你处理一项研究任务。")}</Text>
                  {item.work ? <Text type="secondary">相关工作：{item.work.role}「{item.work.label}」</Text> : null}
                  {artifactTags(item.artifacts)}
                  {item.allowedActions.length > 0 ? (
                    <Space size={6} wrap>
                      {item.allowedActions.map(({ action, label }) => (
                        <Button
                          key={action}
                          size="small"
                          type="primary"
                          loading={actionPending === action}
                          disabled={!onAction}
                          onClick={() => onAction?.(action)}
                        >
                          {label}
                        </Button>
                      ))}
                    </Space>
                  ) : null}
                </Space>
              )}
            />
          ))}
        </div>
      </section>
      ) : null}

      <section aria-labelledby="canonical-current-work">
        {sectionTitle("canonical-current-work", "当前工作")}
        <div style={{ marginTop: 10, display: "grid", gap: 10 }}>
          {view.currentWork.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未生成当前执行工作" />
          ) : view.currentWork.map((group) => (
            <div key={group.key} style={{ display: "grid", gap: 8 }}>
              {group.isParallel ? <Text strong>{group.title}</Text> : null}
              {group.items.map((item) => (
                <div
                  key={item.key}
                  style={{
                    border: "1px solid rgba(5, 5, 5, 0.08)",
                    borderRadius: 8,
                    padding: 10,
                    display: "grid",
                    gap: 7,
                  }}
                >
                  <Space size={6} wrap>
                    <Tag>{item.role}</Tag>
                    <Tag color={statusColor(item.status)}>{item.statusLabel}</Tag>
                    {item.needsReviewCount > 0 ? <Tag color="gold">{item.needsReviewCount} 项待复核</Tag> : null}
                  </Space>
                  <Text>{userFacingDynamicText(item.objective, "当前研究工作")}</Text>
                  {artifactTags(item.artifacts)}
                </div>
              ))}
            </div>
          ))}
        </div>
      </section>

      <section aria-labelledby="canonical-waiting-dependencies">
        {sectionTitle("canonical-waiting-dependencies", "等待与依赖")}
        <div style={{ marginTop: 10, display: "grid", gap: 8 }}>
          {view.waitingAndDependencies.length === 0 ? (
            <Text type="secondary">当前没有等待或未满足的依赖。</Text>
          ) : view.waitingAndDependencies.map((item) => (
            <div key={item.key} style={{ display: "grid", gap: 4 }}>
              <Space size={6} wrap>
                <Tag>{item.role}</Tag>
                <Text strong>{item.objective}</Text>
              </Space>
              <Text type="secondary">{item.reason}</Text>
              {item.dependencies.length > 0 ? (
                <Text type="secondary">
                  依赖：{item.dependencies.map((dependency) => `${dependency.role}「${dependency.label}」`).join("、")}
                </Text>
              ) : null}
              {item.reviewers.length > 0 ? (
                <Text type="secondary">
                  独立复核：{item.reviewers.map((reviewer) => `${reviewer.role}「${reviewer.label}」`).join("、")}
                </Text>
              ) : null}
            </div>
          ))}
        </div>
      </section>

      <section aria-labelledby="canonical-review-handoff">
        {sectionTitle("canonical-review-handoff", "复核与交接")}
        <div style={{ marginTop: 10, display: "grid", gap: 10 }}>
          {view.reviewAndHandoffs.length === 0 ? (
            <Text type="secondary">当前没有复核或交接记录。</Text>
          ) : view.reviewAndHandoffs.map((entry) => (
            <div key={entry.key} style={{ display: "grid", gap: 4 }}>
              {entry.type === "handoff" ? (
                <>
                  <Space size={6} wrap>
                    <Tag color={entry.state === "completed" ? "success" : "processing"}>{entry.payload.stateLabel}</Tag>
                    <Text>{entry.payload.from.role} → {entry.payload.to.role}</Text>
                    <Text type="secondary">{formatOccurredAt(entry.occurredAt)}</Text>
                  </Space>
                  <Text>{entry.payload.summary}</Text>
                  <Text type="secondary">从「{entry.payload.from.label}」交给「{entry.payload.to.label}」</Text>
                  {entry.payload.verdictLabel ? <Tag>{entry.payload.verdictLabel}</Tag> : null}
                  {artifactTags(entry.payload.artifacts)}
                </>
              ) : (
                <>
                  <Space size={6} wrap>
                    <Tag color={entry.state === "approved" ? "success" : "gold"}>{entry.payload.decisionLabel}</Tag>
                    <Text>{entry.payload.reviewer.role}</Text>
                    <Text type="secondary">{formatOccurredAt(entry.occurredAt)}</Text>
                  </Space>
                  <Text>
                    {entry.payload.target
                      ? `正在核查「${entry.payload.target.label}」`
                      : `已完成「${entry.payload.reviewer.label}」`}
                  </Text>
                  {artifactTags(entry.payload.artifacts)}
                </>
              )}
            </div>
          ))}
        </div>
      </section>


      </div>
    </Card>
  );
}

export default CanonicalCollaborationPanel;
