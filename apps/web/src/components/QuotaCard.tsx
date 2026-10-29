import { useEffect, useState } from "react";
import { WalletOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Descriptions, InputNumber, Progress, Space, Table, Tag, Typography, App } from "antd";
import {
  QUOTA_PERIOD_TYPE_LABELS,
  QUOTA_TIER_LABELS,
  TOKEN_USAGE_SOURCE_LABELS,
  type QuotaSummary,
  type TokenUsageEntry,
} from "@openair/shared";
import { fetchMyPrefs, updateMyPrefs } from "../api/governance";
import { fetchMyQuota, fetchMyUsage } from "../api/quota";
import { cn } from "../lib/cn";

const cardClass = cn(
  "rounded-card border border-border! bg-surface! shadow-sm",
  "[&_.ant-card-head]:border-border",
);

function formatTokens(n: number | null | undefined): string {
  if (n === null || n === undefined) return "不限量";
  return n.toLocaleString("zh-CN");
}

const STATUS_TAG: Record<string, { color: string; label: string }> = {
  success: { color: "green", label: "成功" },
  failed: { color: "red", label: "失败" },
  retried: { color: "orange", label: "重试" },
};

/** User-facing quota summary + recent consumption (S03-14-05) + alert pref (S04-01). */
export default function QuotaCard() {
  const { message } = App.useApp();
  const [quota, setQuota] = useState<QuotaSummary | null>(null);
  const [usage, setUsage] = useState<TokenUsageEntry[]>([]);
  const [error, setError] = useState(false);
  const [alertPct, setAlertPct] = useState<number>(80);
  const [savingPref, setSavingPref] = useState(false);

  useEffect(() => {
    let disposed = false;
    Promise.all([fetchMyQuota(), fetchMyUsage({ limit: 10 }), fetchMyPrefs().catch(() => null)])
      .then(([q, page, prefs]) => {
        if (disposed) return;
        setQuota(q);
        setUsage(page.items);
        if (prefs) setAlertPct(prefs.quotaAlertThresholdPct);
        setError(false);
      })
      .catch(() => {
        if (disposed) return;
        setError(true);
      });
    return () => {
      disposed = true;
    };
  }, []);

  const saveAlertPref = async () => {
    setSavingPref(true);
    try {
      const prefs = await updateMyPrefs({ quotaAlertThresholdPct: alertPct });
      setAlertPct(prefs.quotaAlertThresholdPct);
      message.success("预警阈值已保存（额度档位仍由管理员配置）");
    } catch (err) {
      message.error(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSavingPref(false);
    }
  };

  const periodPercent =
    quota && !quota.unlimited && quota.periodLimit
      ? Math.min(100, Math.round((quota.periodUsed / quota.periodLimit) * 100))
      : 0;

  return (
    <Card className={cn(cardClass, "mt-4")} title={<Space><WalletOutlined />账户额度</Space>}>
      <Typography.Paragraph type="secondary">
        查看当前账户的 token 额度与近期消耗。额度不足时将无法发起新的 AI 调用。
      </Typography.Paragraph>
      {error ? (
        <Alert className="mb-4" type="warning" showIcon message="暂时无法读取额度信息，请确认后端服务是否启动。" />
      ) : null}
      {quota ? (
        <>
          <Descriptions bordered size="small" column={1} className="mb-4" items={[
            {
              key: "tier",
              label: "等级",
              children: (
                <Space>
                  <Tag color="blue">{QUOTA_TIER_LABELS[quota.tier] ?? quota.tier}</Tag>
                  <Typography.Text type="secondary">{QUOTA_PERIOD_TYPE_LABELS[quota.periodType] ?? quota.periodType}</Typography.Text>
                </Space>
              ),
            },
            { key: "total", label: "周期总额度", children: formatTokens(quota.periodLimit) },
            { key: "used", label: "已用", children: formatTokens(quota.periodUsed) },
            { key: "bonus", label: "额外额度", children: formatTokens(quota.bonusQuota) },
            {
              key: "remaining",
              label: "剩余",
              children: <Typography.Text strong>{formatTokens(quota.remaining)}</Typography.Text>,
            },
            {
              key: "reset",
              label: "重置时间",
              children:
                quota.periodType === "permanent"
                  ? "一次性额度，不自动重置"
                  : quota.periodType === "unlimited"
                    ? "不限量"
                    : quota.resetAt
                      ? new Date(quota.resetAt).toLocaleString()
                      : "—",
            },
          ]} />
          {!quota.unlimited ? (
            <Progress
              percent={periodPercent}
              status={
                periodPercent >= 100
                  ? "exception"
                  : periodPercent >= alertPct
                    ? "exception"
                    : "active"
              }
              format={() => `${periodPercent}%`}
            />
          ) : null}
          {!quota.unlimited && periodPercent >= alertPct ? (
            <Alert
              className="mt-3"
              type="warning"
              showIcon
              message={`用量已达到预警阈值（${alertPct}%）`}
            />
          ) : null}
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Typography.Text>用量预警阈值（%）</Typography.Text>
            <InputNumber min={1} max={100} value={alertPct} onChange={(v) => setAlertPct(Number(v || 80))} />
            <Button size="small" loading={savingPref} onClick={() => void saveAlertPref()}>
              保存偏好
            </Button>
            <Typography.Text type="secondary">不可自助提升额度档位</Typography.Text>
          </div>
        </>
      ) : null}

      <Typography.Title level={5} className="mt-4 mb-2">近期消耗</Typography.Title>
      <Typography.Paragraph type="secondary" className="mb-2 text-xs">
        平台提供的调用不展示上游模型名（企业侧私密）；自带 API Key 的用量将显示具体模型。
      </Typography.Paragraph>
      <Table<TokenUsageEntry>
        size="small"
        rowKey="ledgerId"
        dataSource={usage}
        pagination={false}
        scroll={{ x: 560 }}
        columns={[
          {
            title: "时间",
            dataIndex: "createdAt",
            width: 160,
            render: (v: string) => (v ? new Date(v).toLocaleString() : "—"),
          },
          {
            title: "来源",
            dataIndex: "source",
            width: 120,
            render: (s: string) => TOKEN_USAGE_SOURCE_LABELS[s as keyof typeof TOKEN_USAGE_SOURCE_LABELS] ?? s,
          },
          {
            title: "额度来源",
            dataIndex: "keySource",
            width: 100,
            render: (v: string | undefined) => (v === "user" ? "自带 Key" : "平台额度"),
          },
          {
            title: "模型",
            dataIndex: "model",
            width: 140,
            render: (v: string | null, row) => {
              if ((row.keySource ?? "platform") === "platform") return "平台托管";
              return v ?? "—";
            },
          },
          { title: "输入", dataIndex: "inputTokens", width: 80 },
          { title: "输出", dataIndex: "outputTokens", width: 80 },
          { title: "合计", dataIndex: "totalTokens", width: 80 },
          {
            title: "状态",
            dataIndex: "status",
            width: 80,
            render: (s: string) => {
              const tag = STATUS_TAG[s] ?? { color: "default", label: s };
              return <Tag color={tag.color}>{tag.label}</Tag>;
            },
          },
        ]}
      />
    </Card>
  );
}
