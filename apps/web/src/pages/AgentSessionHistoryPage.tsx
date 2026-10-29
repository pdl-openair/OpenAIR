import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Alert, Button, Empty, Space, Table, Tag, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { DownloadOutlined, ReloadOutlined } from "@ant-design/icons";
import { usePageHeader } from "../context/PageHeaderContext";
import {
  AgentSessionRequestError,
  downloadAgentSessionArtifactsZip,
  fetchAgentSessionList,
  type AgentSessionSummary,
} from "../features/agent-session/api";

function statusLabel(status: string): { label: string; color: string } {
  if (status === "running") return { label: "执行中", color: "processing" };
  if (status === "waiting_input") return { label: "等待输入", color: "warning" };
  if (status === "ready") return { label: "就绪", color: "success" };
  if (status === "completed") return { label: "已完成", color: "success" };
  if (status === "failed") return { label: "失败", color: "error" };
  return { label: status || "未知", color: "default" };
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value || "—";
  return date.toLocaleString("zh-CN", { hour12: false });
}

function truncateTitle(value: string, maxChars = 20): string {
  const text = value.trim();
  if (!text) return "未命名对话";
  if ([...text].length <= maxChars) return text;
  return `${[...text].slice(0, maxChars).join("")}...`;
}

export default function AgentSessionHistoryPage() {
  const navigate = useNavigate();
  const [items, setItems] = useState<AgentSessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const rows = await fetchAgentSessionList(100);
      setItems(rows);
    } catch (err) {
      setError(
        err instanceof AgentSessionRequestError
          ? `加载失败（${err.status}）`
          : "加载历史对话失败",
      );
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const headerAction = useMemo(
    () => (
      <Space size={8}>
        <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
          刷新
        </Button>
        <Button type="primary" onClick={() => navigate("/resources")}>
          去资源广场
        </Button>
      </Space>
    ),
    [load, loading, navigate],
  );

  usePageHeader({ action: headerAction });

  const onDownloadZip = async (row: AgentSessionSummary) => {
    if (downloadingId || (row.artifactCount ?? 0) <= 0) return;
    setDownloadingId(row.sessionId);
    try {
      await downloadAgentSessionArtifactsZip(row.sessionId);
    } catch {
      message.error("打包下载失败，请稍后重试。");
    } finally {
      setDownloadingId(null);
    }
  };

  const columns: ColumnsType<AgentSessionSummary> = [
    {
      title: "主题",
      dataIndex: "title",
      key: "title",
      width: "22%",
      align: "left",
      ellipsis: true,
      render: (value: string, row) => {
        const full = (value || row.agentDisplayName || "未命名对话").trim() || "未命名对话";
        return (
          <button
            type="button"
            title={full}
            className="max-w-full cursor-pointer truncate border-0 bg-transparent p-0 text-left text-brand shadow-none outline-none hover:text-accent-strong"
            onClick={() =>
              navigate(`/agent-sessions/${encodeURIComponent(row.sessionId)}?from=history`)
            }
          >
            {truncateTitle(full)}
          </button>
        );
      },
    },
    {
      title: "类型",
      dataIndex: "sessionKind",
      key: "sessionKind",
      width: "8%",
      align: "center",
      render: (value: string) => (value === "skill" ? "技能" : "智能体"),
    },
    {
      title: "名称",
      dataIndex: "agentDisplayName",
      key: "agentDisplayName",
      width: "14%",
      align: "center",
      ellipsis: true,
      render: (value: string) => value || "—",
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: "10%",
      align: "center",
      render: (value: string) => {
        const meta = statusLabel(value);
        return <Tag color={meta.color}>{meta.label}</Tag>;
      },
    },
    {
      title: "消息数",
      dataIndex: "messageCount",
      key: "messageCount",
      width: "10%",
      align: "center",
      render: (value: number) => value ?? 0,
    },
    {
      title: "产物数",
      dataIndex: "artifactCount",
      key: "artifactCount",
      width: "10%",
      align: "center",
      render: (value: number) => value ?? 0,
    },
    {
      title: "最近更新",
      dataIndex: "updatedAt",
      key: "updatedAt",
      width: "18%",
      align: "center",
      render: (value: string) => formatTime(value),
    },
    {
      title: "操作",
      key: "actions",
      width: "10%",
      align: "center",
      render: (_value, row) => (
        <Button
          type="link"
          size="small"
          className="px-0!"
          icon={<DownloadOutlined />}
          disabled={(row.artifactCount ?? 0) <= 0}
          loading={downloadingId === row.sessionId}
          onClick={(event) => {
            event.stopPropagation();
            void onDownloadZip(row);
          }}
        >
          下载
        </Button>
      ),
    },
  ];

  return (
    <div className="h-full w-full pt-0!" data-testid="agent-session-history-page">
      <div className="flex h-full min-h-0 w-full flex-col gap-4 overflow-hidden rounded-xl bg-surface p-6">
        {error ? (
          <Alert
            type="error"
            showIcon
            className="shrink-0"
            message={error}
            action={
              <Button size="small" onClick={() => void load()}>
                重试
              </Button>
            }
          />
        ) : null}

        <div className="min-h-0 flex-1 overflow-auto">
          <Table
            rowKey="sessionId"
            loading={loading}
            columns={columns}
            dataSource={items}
            tableLayout="fixed"
            pagination={{ pageSize: 20, hideOnSinglePage: true }}
            locale={{
              emptyText: (
                <Empty description="暂无历史对话" image={Empty.PRESENTED_IMAGE_SIMPLE}>
                  <Button type="primary" onClick={() => navigate("/resources")}>
                    去试用智能体
                  </Button>
                </Empty>
              ),
            }}
            onRow={(row) => ({
              onClick: () =>
                navigate(`/agent-sessions/${encodeURIComponent(row.sessionId)}?from=history`),
              className: "cursor-pointer",
            })}
          />
        </div>
      </div>
    </div>
  );
}
