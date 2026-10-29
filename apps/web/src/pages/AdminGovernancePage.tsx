import { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  Input,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import type { AdminAccountRow, AuditLogEntry, Contribution } from "@openair/shared";
import { ApiError } from "../api/http";
import {
  adminListAccounts,
  adminListAuditLogs,
  adminListPendingContributions,
  adminReviewContribution,
  adminSetAccountActive,
} from "../api/governance";

export default function AdminGovernancePage() {
  const { message, modal } = App.useApp();
  const [accounts, setAccounts] = useState<AdminAccountRow[]>([]);
  const [pending, setPending] = useState<Contribution[]>([]);
  const [audits, setAudits] = useState<AuditLogEntry[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);

  const reload = useCallback(() => {
    setLoading(true);
    Promise.all([
      adminListAccounts({ query: query || undefined, limit: 100 }),
      adminListPendingContributions(),
      adminListAuditLogs({ limit: 80 }),
    ])
      .then(([a, p, logs]) => {
        setAccounts(a);
        setPending(p);
        setAudits(logs);
      })
      .catch((err) => message.error(err instanceof Error ? err.message : "加载失败"))
      .finally(() => setLoading(false));
  }, [message, query]);

  useEffect(() => {
    reload();
  }, [reload]);

  const toggleActive = (row: AdminAccountRow) => {
    const next = !row.isActive;
    modal.confirm({
      title: next ? "激活用户" : "禁用用户",
      content: `确认${next ? "激活" : "禁用"} ${row.email}？${next ? "激活后可在额度管理中分配剩余 token。" : ""}`,
      onOk: async () => {
        try {
          await adminSetAccountActive(row.userId, next, next ? "admin activate" : "admin disable");
          message.success(next ? "已激活" : "已禁用");
          reload();
        } catch (err) {
          message.error(err instanceof Error ? err.message : "操作失败");
        }
      },
    });
  };

  const review = (row: Contribution, decision: "approve" | "reject") => {
    let reviewNotes = "";
    const getErrorMessage = (err: unknown) => {
      if (err instanceof ApiError && err.payload && typeof err.payload === "object") {
        const detail = (err.payload as { detail?: unknown }).detail;
        if (typeof detail === "string" && detail.trim()) return detail;
      }
      return err instanceof Error ? err.message : "审核失败";
    };

    modal.confirm({
      title: decision === "approve" ? "批准并进入资源目录" : "驳回公开发布",
      content: (
        <Space direction="vertical" className="w-full">
          <Typography.Text strong>{row.title}</Typography.Text>
          <Typography.Text type="secondary">
            {decision === "approve"
              ? "批准前系统会重新检查并冻结当前版本。未配置公共执行入口的资源将以“需要配置”进入目录。"
              : "请写明需要修改的具体问题，作者会据此继续完善并重新提交。"}
          </Typography.Text>
          <Input.TextArea
            rows={3}
            placeholder={decision === "reject" ? "必填：需要修改什么" : "可选：审核说明"}
            onChange={(event) => {
              reviewNotes = event.target.value;
            }}
          />
        </Space>
      ),
      onOk: async () => {
        if (decision === "reject" && !reviewNotes.trim()) {
          message.error("驳回时必须填写修改原因");
          throw new Error("review notes required");
        }
        try {
          await adminReviewContribution(row.contributionId, decision, reviewNotes.trim());
          message.success(decision === "approve" ? "已批准并进入资源目录" : "已驳回并反馈修改原因");
          reload();
        } catch (err) {
          message.error(getErrorMessage(err));
          throw err;
        }
      },
    });
  };

  return (
    <div className="w-full">
      <Typography.Title level={2}>用户与治理</Typography.Title>
      <Typography.Paragraph type="secondary">
        新注册账号默认为未激活。激活后用户才能登录，并在「额度管理」中被分配剩余 token。
      </Typography.Paragraph>

      <Tabs
        items={[
          {
            key: "accounts",
            label: "用户管理",
            children: (
              <Card>
                <Space className="mb-3">
                  <Input.Search
                    placeholder="邮箱 / 用户名"
                    allowClear
                    onSearch={(v) => setQuery(v)}
                    style={{ width: 260 }}
                  />
                  <Button onClick={reload} loading={loading}>
                    刷新
                  </Button>
                </Space>
                <Table
                  rowKey="userId"
                  loading={loading}
                  dataSource={accounts}
                  pagination={{ pageSize: 20 }}
                  columns={[
                    { title: "邮箱", dataIndex: "email" },
                    { title: "用户名", dataIndex: "username" },
                    {
                      title: "角色",
                      dataIndex: "role",
                      render: (v: string) =>
                        v === "admin" ? <Tag color="gold">管理员</Tag> : <Tag color="blue">用户</Tag>,
                    },
                    {
                      title: "状态",
                      dataIndex: "isActive",
                      render: (v: boolean) => (
                        <Tag color={v ? "green" : "orange"}>{v ? "正常" : "未激活"}</Tag>
                      ),
                    },
                    { title: "额度档", dataIndex: "quotaTier" },
                    {
                      title: "操作",
                      render: (_, row) => (
                        <Button size="small" danger={row.isActive} onClick={() => toggleActive(row)}>
                          {row.isActive ? "禁用" : "激活"}
                        </Button>
                      ),
                    },
                  ]}
                />
              </Card>
            ),
          },
          {
            key: "reviews",
            label: `贡献审核 (${pending.length})`,
            children: (
              <Card>
                <Table
                  rowKey="contributionId"
                  loading={loading}
                  dataSource={pending}
                  columns={[
                    { title: "公开名称", dataIndex: "title" },
                    { title: "资源", dataIndex: "resourceRef" },
                    { title: "提交者", dataIndex: "ownerId" },
                    {
                      title: "系统检查",
                      dataIndex: "systemCheckStatus",
                      render: (value: Contribution["systemCheckStatus"], row: Contribution) => (
                        <Space direction="vertical" size={0}>
                          <Tag color={value === "passed" ? "green" : "orange"}>
                            {value === "passed" ? "结构检查通过" : "需要处理"}
                          </Tag>
                          {row.systemCheckIssues.slice(0, 2).map((issue) => (
                            <Typography.Text key={issue} type="secondary" className="max-w-[280px]">
                              {issue}
                            </Typography.Text>
                          ))}
                        </Space>
                      ),
                    },
                    {
                      title: "操作",
                      render: (_, row) => (
                        <Space>
                          <Button size="small" type="primary" onClick={() => review(row, "approve")}>
                            批准公开
                          </Button>
                          <Button size="small" danger onClick={() => review(row, "reject")}>
                            驳回
                          </Button>
                        </Space>
                      ),
                    },
                  ]}
                />
              </Card>
            ),
          },
          {
            key: "audit",
            label: "审计日志",
            children: (
              <Card>
                <Table
                  rowKey="auditId"
                  loading={loading}
                  dataSource={audits}
                  pagination={{ pageSize: 30 }}
                  columns={[
                    { title: "时间", dataIndex: "createdAt", width: 200 },
                    { title: "动作", dataIndex: "action", width: 160 },
                    {
                      title: "结果",
                      dataIndex: "outcome",
                      width: 90,
                      render: (v: string) => (
                        <Tag color={v === "success" ? "green" : "red"}>{v}</Tag>
                      ),
                    },
                    { title: "操作者", dataIndex: "actorId" },
                    { title: "对象", dataIndex: "targetId" },
                    { title: "IP", dataIndex: "ip", width: 120 },
                  ]}
                />
              </Card>
            ),
          },
        ]}
      />
    </div>
  );
}
