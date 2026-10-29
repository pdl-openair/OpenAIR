import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Modal,
  Progress,
  Segmented,
  Select,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  App,
} from "antd";
import {
  QUOTA_PERIOD_TYPE_LABELS,
  QUOTA_TIER_LABELS,
  TOKEN_USAGE_SOURCE_LABELS,
  type AdminUserQuotaDetail,
  type AdminUserQuotaRow,
  type QuotaAdjustmentRecord,
  type QuotaAdjustType,
  type QuotaPeriodType,
  type QuotaTier,
  type QuotaTierCatalogEntry,
  type TokenUsageEntry,
  type UsageTimeseriesBundle,
} from "@openair/shared";
import {
  adminCreateAdjustment,
  adminGetUserQuota,
  adminListAdjustments,
  adminListQuotaTiers,
  adminListUsers,
} from "../api/adminQuota";
import { isQuotaExhaustedError } from "../components/QuotaExhaustedListener";

function formatTokens(n: number | null | undefined): string {
  return n === null || n === undefined ? "不限量" : n.toLocaleString("zh-CN");
}

function formatCompactTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n % 1_000_000 === 0 ? 0 : 1)}M`;
  if (n >= 10_000) return `${(n / 1_000).toFixed(n % 1000 === 0 ? 0 : 1)}k`;
  return n.toLocaleString("zh-CN");
}

function formatTierLabel(entry: QuotaTierCatalogEntry): string {
  const name = QUOTA_TIER_LABELS[entry.tier] ?? entry.tier;
  const period = QUOTA_PERIOD_TYPE_LABELS[entry.periodType as QuotaPeriodType] ?? entry.periodType;
  if (entry.periodLimit == null) {
    return `${name} · ${period}`;
  }
  return `${name} · ${period} ${formatTokens(entry.periodLimit)} tokens`;
}

function tierTag(tier: string, catalog: QuotaTierCatalogEntry[]) {
  const entry = catalog.find((c) => c.tier === tier);
  const label = entry ? formatTierLabel(entry) : (QUOTA_TIER_LABELS[tier as QuotaTier] ?? tier);
  return <Tag color="blue">{label}</Tag>;
}

function keySourceLabel(keySource: string | undefined): string {
  return keySource === "user" ? "用户自带" : "平台托管";
}

function MetricCell({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="min-w-0 rounded-lg border border-neutral-200 bg-neutral-50/80 px-3 py-2.5">
      <div className="text-xs text-neutral-500">{label}</div>
      <div className="mt-0.5 truncate text-base font-semibold text-neutral-900" title={value}>
        {value}
      </div>
      {hint ? <div className="mt-0.5 truncate text-[11px] text-neutral-400">{hint}</div> : null}
    </div>
  );
}

type TrendGranularity = "day" | "week" | "month";

function CallTrendPanel({ detail }: { detail: AdminUserQuotaDetail }) {
  const [granularity, setGranularity] = useState<TrendGranularity>("day");

  const bundle: UsageTimeseriesBundle | null | undefined =
    granularity === "day"
      ? detail.timeseriesDaily
      : granularity === "week"
        ? detail.timeseriesWeekly
        : detail.timeseriesMonthly;

  const activePoints = useMemo(
    () => (bundle?.points ?? []).filter((p) => p.calls > 0),
    [bundle],
  );
  const max = Math.max(bundle?.peakTokens ?? 0, 1);
  const totalCalls = activePoints.reduce((s, p) => s + p.calls, 0);
  const showTrough = activePoints.length > 1;

  return (
    <div className="rounded-xl border border-neutral-200 bg-white">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-neutral-100 px-4 py-3">
        <div>
          <Typography.Text strong>调用态势</Typography.Text>
          <Typography.Text type="secondary" className="ml-2 text-xs">
            切换粒度查看峰谷周期
          </Typography.Text>
        </div>
        <Segmented<TrendGranularity>
          size="small"
          value={granularity}
          onChange={setGranularity}
          options={[
            { label: "近 30 天", value: "day" },
            { label: "近 12 周", value: "week" },
            { label: "近 12 月", value: "month" },
          ]}
        />
      </div>

      {!bundle || bundle.points.length === 0 ? (
        <div className="px-4 py-10 text-center text-sm text-neutral-400">暂无调用数据</div>
      ) : (
        <div className="space-y-3 px-4 py-3">
          <div className="flex flex-wrap gap-2">
            <Tag className="m-0! px-2.5! py-0.5! text-xs">
              峰值 <span className="font-semibold">{formatCompactTokens(bundle.peakTokens)}</span>
              {bundle.peakPeriod ? ` · ${bundle.peakPeriod}` : ""}
            </Tag>
            {showTrough ? (
              <Tag className="m-0! px-2.5! py-0.5! text-xs">
                谷值 <span className="font-semibold">{formatCompactTokens(bundle.troughTokens)}</span>
                {bundle.troughPeriod ? ` · ${bundle.troughPeriod}` : ""}
              </Tag>
            ) : null}
            <Tag className="m-0! px-2.5! py-0.5! text-xs">
              均值 <span className="font-semibold">{formatCompactTokens(Math.round(bundle.avgTokens))}</span>
            </Tag>
            <Tag className="m-0! px-2.5! py-0.5! text-xs">
              活跃桶 <span className="font-semibold">{activePoints.length}</span>
              {` / ${bundle.points.length}`}
            </Tag>
            <Tag className="m-0! px-2.5! py-0.5! text-xs">
              调用 <span className="font-semibold">{totalCalls}</span> 次
            </Tag>
          </div>

          <div className="flex h-36 items-end gap-px rounded-lg bg-neutral-50 px-1 pt-3 pb-1">
            {bundle.points.map((p) => {
              const h = p.totalTokens > 0 ? Math.max(8, Math.round((p.totalTokens / max) * 100)) : 2;
              const isPeak = p.totalTokens === bundle.peakTokens && p.calls > 0;
              return (
                <div
                  key={p.periodStart}
                  title={`${p.periodLabel}\n${formatTokens(p.totalTokens)} tokens · ${p.calls} 次`}
                  className="group relative flex min-w-0 flex-1 flex-col justify-end"
                  style={{ height: "100%" }}
                >
                  <div
                    className={
                      p.calls > 0
                        ? isPeak
                          ? "rounded-t bg-sky-600 transition-colors"
                          : "rounded-t bg-sky-400/85 transition-colors group-hover:bg-sky-500"
                        : "rounded-t bg-neutral-200/80"
                    }
                    style={{ height: `${h}%` }}
                  />
                </div>
              );
            })}
          </div>
          <div className="flex justify-between text-[11px] text-neutral-400">
            <span>{bundle.points[0]?.periodLabel}</span>
            <span>{bundle.points[bundle.points.length - 1]?.periodLabel}</span>
          </div>
        </div>
      )}
    </div>
  );
}

function ModelSharePanel({ detail }: { detail: AdminUserQuotaDetail }) {
  const models = detail.usageByModel ?? [];
  const total = models.reduce((s, m) => s + m.totalTokens, 0) || 1;
  const top = models.slice(0, 5);

  if (top.length === 0) {
    return (
      <div className="rounded-xl border border-neutral-200 bg-white px-4 py-8 text-center text-sm text-neutral-400">
        近 30 天暂无按模型汇总
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-neutral-200 bg-white">
      <div className="border-b border-neutral-100 px-4 py-3">
        <Typography.Text strong>模型占比（近 30 天 Top 5）</Typography.Text>
      </div>
      <div className="space-y-3 px-4 py-3">
        {top.map((m) => {
          const pct = Math.round((m.totalTokens / total) * 1000) / 10;
          const name = m.model || "未知模型";
          return (
            <div key={`${m.provider}|${m.model}|${m.keySource}`}>
              <div className="mb-1 flex items-baseline justify-between gap-2 text-sm">
                <div className="min-w-0 truncate">
                  <span className="font-medium text-neutral-800">{name}</span>
                  <span className="ml-2 text-xs text-neutral-400">
                    {m.provider || "—"} · {keySourceLabel(m.keySource)} · {m.calls} 次
                  </span>
                </div>
                <span className="shrink-0 tabular-nums text-neutral-700">
                  {formatCompactTokens(m.totalTokens)}
                  <span className="ml-1 text-xs text-neutral-400">{pct}%</span>
                </span>
              </div>
              <Progress percent={pct} showInfo={false} size="small" strokeColor="#0ea5e9" />
            </div>
          );
        })}
      </div>
    </div>
  );
}

function QuotaDetailExpand({
  detail,
  adjustments,
  loading,
  tierCatalog,
}: {
  detail: AdminUserQuotaDetail | null;
  adjustments: QuotaAdjustmentRecord[];
  loading: boolean;
  tierCatalog: QuotaTierCatalogEntry[];
}) {
  if (loading && !detail) {
    return (
      <div className="flex justify-center py-10">
        <Spin tip="加载消耗详情…" />
      </div>
    );
  }
  if (!detail) {
    return <div className="py-6 text-center text-sm text-neutral-400">暂无详情数据</div>;
  }

  const periodHint = detail.user.quota.resetAt
    ? `重置 ${new Date(detail.user.quota.resetAt).toLocaleString()}`
    : detail.user.quota.periodType === "permanent"
      ? "一次性额度"
      : detail.user.quota.unlimited
        ? "不限量"
        : undefined;

  return (
    <div className="rounded-lg bg-neutral-50/70 px-3 py-3 sm:px-4">
      <Tabs
        size="small"
        items={[
          {
            key: "overview",
            label: "概览",
            children: (
              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
                  <MetricCell
                    label="等级"
                    value={QUOTA_TIER_LABELS[detail.user.quota.tier as QuotaTier] ?? detail.user.quota.tier}
                    hint={QUOTA_PERIOD_TYPE_LABELS[detail.user.quota.periodType as QuotaPeriodType]}
                  />
                  <MetricCell label="近 30 天" value={formatTokens(detail.totalTokens30d)} />
                  <MetricCell label="周期已用" value={formatTokens(detail.user.quota.periodUsed)} hint={periodHint} />
                  <MetricCell label="剩余" value={formatTokens(detail.user.quota.remaining)} />
                  <MetricCell label="额外额度" value={formatTokens(detail.user.quota.bonusQuota)} />
                </div>
                <div className="grid gap-4 lg:grid-cols-2">
                  <CallTrendPanel detail={detail} />
                  <ModelSharePanel detail={detail} />
                </div>
              </div>
            ),
          },
          {
            key: "models",
            label: "按模型",
            children: (
              <div className="space-y-4">
                <Table
                  size="small"
                  rowKey={(r) => `${r.provider ?? ""}|${r.model ?? ""}|${r.keySource}`}
                  pagination={false}
                  dataSource={detail.usageByModel ?? []}
                  scroll={{ x: 640 }}
                  locale={{ emptyText: "近 30 天暂无模型调用" }}
                  columns={[
                    { title: "模型", dataIndex: "model", ellipsis: true, render: (v: string | null) => v || "—" },
                    { title: "Provider", dataIndex: "provider", width: 110, render: (v: string | null) => v || "—" },
                    {
                      title: "密钥",
                      dataIndex: "keySource",
                      width: 88,
                      render: (v: string) => keySourceLabel(v),
                    },
                    { title: "次数", dataIndex: "calls", width: 64 },
                    { title: "合计", dataIndex: "totalTokens", width: 96, render: (v: number) => formatTokens(v) },
                  ]}
                />
                <Typography.Text type="secondary" className="text-xs">
                  按来源汇总（近 30 天）
                </Typography.Text>
                <Table
                  size="small"
                  rowKey="source"
                  pagination={false}
                  dataSource={detail.usageBySource}
                  columns={[
                    {
                      title: "来源",
                      dataIndex: "source",
                      render: (s: string) =>
                        TOKEN_USAGE_SOURCE_LABELS[s as keyof typeof TOKEN_USAGE_SOURCE_LABELS] ?? s,
                    },
                    { title: "次数", dataIndex: "calls", width: 72 },
                    { title: "输入", dataIndex: "inputTokens", width: 88, render: (v: number) => formatTokens(v) },
                    { title: "输出", dataIndex: "outputTokens", width: 88, render: (v: number) => formatTokens(v) },
                    { title: "合计", dataIndex: "totalTokens", width: 96, render: (v: number) => formatTokens(v) },
                  ]}
                />
              </div>
            ),
          },
          {
            key: "calls",
            label: "调用明细",
            children: (
              <Table<TokenUsageEntry>
                size="small"
                rowKey="ledgerId"
                pagination={{ pageSize: 10, size: "small" }}
                dataSource={detail.recentUsage ?? []}
                scroll={{ x: 760 }}
                locale={{ emptyText: "近 30 天暂无调用记录" }}
                columns={[
                  {
                    title: "时间",
                    dataIndex: "createdAt",
                    width: 148,
                    render: (v: string) => (v ? new Date(v).toLocaleString() : "—"),
                  },
                  {
                    title: "来源",
                    dataIndex: "source",
                    width: 100,
                    ellipsis: true,
                    render: (s: string) =>
                      TOKEN_USAGE_SOURCE_LABELS[s as keyof typeof TOKEN_USAGE_SOURCE_LABELS] ?? s,
                  },
                  { title: "模型", dataIndex: "model", ellipsis: true, render: (v: string | null) => v || "—" },
                  {
                    title: "密钥",
                    dataIndex: "keySource",
                    width: 80,
                    render: (v: string | undefined) => keySourceLabel(v),
                  },
                  { title: "合计", dataIndex: "totalTokens", width: 88, render: (v: number) => formatTokens(v) },
                  {
                    title: "状态",
                    dataIndex: "status",
                    width: 72,
                    render: (s: string) => (
                      <Tag color={s === "success" ? "green" : s === "failed" ? "red" : "orange"}>{s}</Tag>
                    ),
                  },
                ]}
              />
            ),
          },
          {
            key: "adjustments",
            label: "调整记录",
            children: (
              <Table<QuotaAdjustmentRecord>
                size="small"
                rowKey="adjustmentId"
                pagination={false}
                dataSource={adjustments}
                locale={{ emptyText: "暂无调整记录" }}
                columns={[
                  {
                    title: "时间",
                    dataIndex: "createdAt",
                    width: 148,
                    render: (v: string) => (v ? new Date(v).toLocaleString() : "—"),
                  },
                  {
                    title: "类型",
                    dataIndex: "adjustType",
                    width: 80,
                    render: (t: string) => (t === "bonus_quota" ? "加额度" : "改等级"),
                  },
                  {
                    title: "内容",
                    width: 160,
                    render: (_, r) => {
                      if (r.adjustType === "bonus_quota") {
                        return `+${formatTokens(r.delta ?? 0)}`;
                      }
                      const next = tierCatalog.find((c) => c.tier === r.newTier);
                      const nextLabel = next
                        ? formatTierLabel(next)
                        : (QUOTA_TIER_LABELS[r.newTier as QuotaTier] ?? r.newTier ?? "—");
                      return `${r.oldTier ?? "—"} → ${nextLabel}`;
                    },
                  },
                  { title: "原因", dataIndex: "reason", ellipsis: true },
                ]}
              />
            ),
          },
        ]}
      />
    </div>
  );
}

interface AdjustForm {
  type: QuotaAdjustType;
  delta?: number;
  newTier?: QuotaTier;
  reason: string;
}

export default function AdminQuotasPage() {
  const { message } = App.useApp();
  const [users, setUsers] = useState<AdminUserQuotaRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [expandedRowKeys, setExpandedRowKeys] = useState<string[]>([]);
  const [detailByUserId, setDetailByUserId] = useState<Record<string, AdminUserQuotaDetail>>({});
  const [adjustmentsByUserId, setAdjustmentsByUserId] = useState<Record<string, QuotaAdjustmentRecord[]>>({});
  const [detailLoadingIds, setDetailLoadingIds] = useState<Record<string, boolean>>({});
  const [adjustTarget, setAdjustTarget] = useState<AdminUserQuotaRow | null>(null);
  const [tierCatalog, setTierCatalog] = useState<QuotaTierCatalogEntry[]>([]);
  const [form] = Form.useForm<AdjustForm>();

  const assignableTier = useMemo(
    () => tierCatalog.filter((t) => t.tier !== "admin"),
    [tierCatalog],
  );

  const tierSelectOptions = useMemo(
    () => assignableTier.map((t) => ({ value: t.tier, label: formatTierLabel(t) })),
    [assignableTier],
  );

  const reload = useCallback(
    (q: string) => {
      setLoading(true);
      adminListUsers({ query: q || undefined, limit: 100 })
        .then(setUsers)
        .catch((err) => {
          if (!isQuotaExhaustedError(err)) message.error(err instanceof Error ? err.message : "用户列表加载失败");
        })
        .finally(() => setLoading(false));
    },
    [message]
  );

  useEffect(() => {
    reload("");
  }, [reload]);

  useEffect(() => {
    adminListQuotaTiers()
      .then(setTierCatalog)
      .catch((err) => {
        if (!isQuotaExhaustedError(err)) {
          message.error(err instanceof Error ? err.message : "等级额度目录加载失败");
        }
      });
  }, [message]);

  const loadDetail = useCallback(
    async (userId: string) => {
      setDetailLoadingIds((prev) => ({ ...prev, [userId]: true }));
      try {
        const [d, adjs] = await Promise.all([
          adminGetUserQuota(userId),
          adminListAdjustments(userId, 20),
        ]);
        setDetailByUserId((prev) => ({ ...prev, [userId]: d }));
        setAdjustmentsByUserId((prev) => ({ ...prev, [userId]: adjs }));
      } catch (err) {
        if (!isQuotaExhaustedError(err)) message.error(err instanceof Error ? err.message : "详情加载失败");
      } finally {
        setDetailLoadingIds((prev) => ({ ...prev, [userId]: false }));
      }
    },
    [message],
  );

  const setExpanded = useCallback(
    (userId: string | null) => {
      if (!userId) {
        setExpandedRowKeys([]);
        return;
      }
      setExpandedRowKeys([userId]);
      void loadDetail(userId);
    },
    [loadDetail],
  );

  const toggleExpand = useCallback(
    (row: AdminUserQuotaRow) => {
      const open = expandedRowKeys[0] === row.userId;
      setExpanded(open ? null : row.userId);
    },
    [expandedRowKeys, setExpanded],
  );

  const submitAdjust = async () => {
    if (!adjustTarget) return;
    const values = await form.validateFields();
    try {
      await adminCreateAdjustment(adjustTarget.userId, values);
      message.success("额度已调整");
      setAdjustTarget(null);
      form.resetFields();
      reload(query);
      if (expandedRowKeys[0] === adjustTarget.userId) {
        await loadDetail(adjustTarget.userId);
      } else {
        setDetailByUserId((prev) => {
          const next = { ...prev };
          delete next[adjustTarget.userId];
          return next;
        });
      }
    } catch (err) {
      if (!isQuotaExhaustedError(err)) message.error(err instanceof Error ? err.message : "调整失败");
    }
  };

  return (
    <div className="w-full">
      <Typography.Title level={2}>额度管理</Typography.Title>
      <Typography.Paragraph type="secondary">
        查看用户 token 消耗、调整等级与一次性额度。未激活账号不能分配额度，请先在「用户与治理」中激活。
      </Typography.Paragraph>

      {assignableTier.length > 0 ? (
        <Alert
          className="mb-4"
          type="info"
          showIcon
          message="当前各等级额度"
          description={
            <Space wrap size={[8, 8]}>
              {assignableTier.map((t) => (
                <Tag key={t.tier}>{formatTierLabel(t)}</Tag>
              ))}
            </Space>
          }
        />
      ) : null}

      <Card className="mb-4">
        <Space>
          <Input.Search
            placeholder="搜索邮箱或用户名"
            allowClear
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onSearch={(v) => reload(v)}
            style={{ width: 280 }}
          />
          <Button onClick={() => reload(query)}>刷新</Button>
        </Space>
      </Card>

      <Table<AdminUserQuotaRow>
        rowKey="userId"
        loading={loading}
        dataSource={users}
        pagination={{ pageSize: 20 }}
        scroll={{ x: 900 }}
        expandable={{
          expandedRowKeys,
          onExpand: (expanded, record) => {
            setExpanded(expanded ? record.userId : null);
          },
          expandedRowRender: (record) => (
            <QuotaDetailExpand
              detail={detailByUserId[record.userId] ?? null}
              adjustments={adjustmentsByUserId[record.userId] ?? []}
              loading={!!detailLoadingIds[record.userId]}
              tierCatalog={tierCatalog}
            />
          ),
          rowExpandable: () => true,
        }}
        columns={[
          { title: "邮箱", dataIndex: "email", width: 200 },
          { title: "用户名", dataIndex: "username", width: 120 },
          {
            title: "角色",
            dataIndex: "role",
            width: 80,
            render: (r: string) =>
              r === "admin" ? <Tag color="gold">管理员</Tag> : <Tag color="blue">用户</Tag>,
          },
          {
            title: "状态",
            dataIndex: "isActive",
            width: 80,
            render: (v: boolean | undefined) => (
              <Tag color={v === false ? "orange" : "green"}>{v === false ? "未激活" : "正常"}</Tag>
            ),
          },
          {
            title: "等级",
            width: 220,
            render: (_, r) => tierTag(r.quota.tier, tierCatalog),
          },
          { title: "已用", width: 100, render: (_, r) => formatTokens(r.quota.periodUsed) },
          { title: "剩余", width: 100, render: (_, r) => formatTokens(r.quota.remaining) },
          {
            title: "额外额度",
            dataIndex: ["quota", "bonusQuota"],
            width: 100,
            render: (v: number) => formatTokens(v),
          },
          {
            title: "操作",
            width: 160,
            fixed: "right",
            render: (_, r) => {
              const open = expandedRowKeys[0] === r.userId;
              return (
                <Space>
                  <Button size="small" type={open ? "default" : "link"} onClick={() => toggleExpand(r)}>
                    {open ? "收起" : "详情"}
                  </Button>
                  <Tooltip title={r.isActive === false ? "请先在用户与治理中激活该账号" : undefined}>
                    <Button
                      size="small"
                      type="primary"
                      disabled={r.isActive === false}
                      onClick={() => {
                        setAdjustTarget(r);
                        form.resetFields();
                        form.setFieldsValue({ type: "bonus_quota" });
                      }}
                    >
                      调整
                    </Button>
                  </Tooltip>
                </Space>
              );
            },
          },
        ]}
      />

      <Modal
        open={!!adjustTarget}
        title={adjustTarget ? `调整额度 · ${adjustTarget.email}` : "调整额度"}
        onCancel={() => setAdjustTarget(null)}
        onOk={submitAdjust}
        okText="提交"
        cancelText="取消"
        destroyOnClose
        width={520}
      >
        <Form form={form} layout="vertical" initialValues={{ type: "bonus_quota" }}>
          <Form.Item name="type" label="操作类型" rules={[{ required: true }]}>
            <Select
              options={[
                { value: "bonus_quota", label: "加一次性额度" },
                { value: "tier_change", label: "调整等级" },
              ]}
            />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(prev, cur) => prev.type !== cur.type}>
            {({ getFieldValue }) =>
              getFieldValue("type") === "bonus_quota" ? (
                <Form.Item name="delta" label="额度数量（tokens）" rules={[{ required: true, type: "number", min: 1 }]}>
                  <InputNumber min={1} style={{ width: "100%" }} />
                </Form.Item>
              ) : (
                <Form.Item
                  name="newTier"
                  label="新等级"
                  rules={[{ required: true }]}
                  extra="选项已包含周期类型与额度上限，与当前服务端配置一致。"
                >
                  <Select
                    options={tierSelectOptions}
                    placeholder={tierSelectOptions.length ? "请选择等级" : "正在加载等级额度…"}
                    optionLabelProp="label"
                  />
                </Form.Item>
              )
            }
          </Form.Item>
          <Form.Item name="reason" label="原因" rules={[{ required: true, min: 1, max: 500 }]}>
            <Input.TextArea rows={2} placeholder="说明本次调整原因（必填）" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
