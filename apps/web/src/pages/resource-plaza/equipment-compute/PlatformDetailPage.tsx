import { useEffect, useState } from "react";
import { Alert, Button, Descriptions, Empty, Tabs, Tag } from "antd";
import { ArrowLeft, ExternalLink, RefreshCw } from "lucide-react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { DemoRequestModal, PlatformMark, SnapshotNote } from "./common";
import { fallbackJccInventory, formatBeijingTime, jccMetrics } from "./data";
import type { DemoRecord, JccInventoryResult, JccSnapshot } from "./data";
import { fetchJccInventory, requestJccRefresh } from "./api";
import { usePlatformInventory } from "./usePlatformInventory";
import JccDirectory from "./JccDirectory";
import JccTasks, { DemoRecords } from "./JccTasks";
import FluxPlatform from "./FluxPlatform";
import "./equipment-compute.css";

function JccPlatformHeader({
  inventory,
  onRefresh,
}: {
  inventory: JccInventoryResult;
  onRefresh: () => void;
}) {
  const { meta, payload } = inventory;
  const usingSeed = meta.source !== "cache";
  return (
    <>
      <Link className="ec-back" to="/resources/compute">
        <ArrowLeft size={15} />
        返回算力资源
      </Link>
      <header className="ec-detail-header">
        <div className="ec-detail-identity">
          <PlatformMark kind="jcc" />
          <div>
            <h1>云际计算平台</h1>
            <Tag color={usingSeed ? "orange" : "cyan"}>
              {usingSeed ? "快照兜底" : "实时采集缓存"}
            </Tag>
            {meta.collecting && (
              <Tag icon={<RefreshCw size={12} className="ec-spin-icon" />} color="processing">
                采集中
              </Tag>
            )}
            <p className="ec-detail-meta">
              当前账号可见 · 采集于{" "}
              {formatBeijingTime(payload?.observedAt ?? meta.observedAt)}
              （北京时间）
            </p>
          </div>
        </div>
        <div className="ec-header-actions">
          <Button
            icon={<RefreshCw size={14} />}
            loading={meta.collecting}
            onClick={onRefresh}
          >
            刷新数据
          </Button>
          <Button
            href="https://jcc.jointcloud.net"
            target="_blank"
            rel="noopener noreferrer"
            icon={<ExternalLink size={14} />}
          >
            前往云际平台
          </Button>
        </div>
      </header>
    </>
  );
}

function JccInfo({ inventory }: { inventory: JccInventoryResult }) {
  const payload = inventory.payload;
  const metrics = payload ? jccMetrics(payload) : { categories: 0, training: 0, hpc: 0, completed: 0 };
  return (
    <>
      <div className="ec-info-grid">
        <section className="ec-info-panel">
          <h3>平台能力</h3>
          <div className="ec-capabilities">
            <Tag>超算</Tag>
            <Tag>智算训练</Tag>
            <Tag>推理</Tag>
            <Tag>云主机</Tag>
            <Tag>容器</Tag>
          </div>
          <p>
            通过 JCC CLI
            查询当前账号可见的资源目录、资源范围、任务与镜像。当前展示{" "}
            {metrics.categories} 类训练加速卡、{metrics.training} 种训练规格和{" "}
            {metrics.hpc} 个支持 VASP 的超算集群。
          </p>
        </section>
        <section className="ec-info-panel">
          <h3>查询范围</h3>
          <p>CPU 云主机规格 7 种，CPU 容器规格 6 种；未遍历全部云资源类型。</p>
          <p>
            镜像列表报告共 {payload?.imageTotal ?? "未取得"} 条，本次仅采集前{" "}
            {payload?.images.length ?? 0} 条；未验证镜像与资源兼容性。
          </p>
        </section>
      </div>
      <Descriptions
        column={1}
        bordered
        size="small"
        items={[
          {
            key: "source",
            label: "数据来源",
            children:
              inventory.meta.source === "cache"
                ? "JCC CLI 只读采集（后端缓存，可手动刷新）"
                : "JCC CLI 一次性采集快照（实时采集不可用时的兜底）",
          },
          {
            key: "time",
            label: "采集时间",
            children: `${formatBeijingTime(
              payload?.observedAt ?? inventory.meta.observedAt,
            )}（北京时间）`,
          },
          {
            key: "sync",
            label: "更新方式",
            children:
              "详情页「刷新数据」触发后台采集（约 1-3 分钟）；缓存超过 24 小时后访问页面会自动补采",
          },
          {
            key: "manage",
            label: "账号与授权",
            children: "本页不提供账号切换或授权修改，请在平台管理",
          },
          {
            key: "hpc",
            label: "超算任务",
            children: "全量列表查询暂不支持；可通过 JCC 按已知任务名查询",
          },
          {
            key: "count",
            label: "数量口径",
            children: "规格数、集群数和物理设备数分别统计，不能加总为总算力",
          },
        ]}
      />
      <p className="ec-section-note">
        当前页面支持查看采集信息、手动刷新和生成演示草稿。真实资源分配、任务提交、费用与平台授权需在接入执行链后处理。
      </p>
    </>
  );
}

function JccPlatform() {
  const [params, setParams] = useSearchParams();
  const [resource, setResource] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<DemoRecord[]>([]);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const { data, loading, usingFallback, error, reload } = usePlatformInventory(
    fetchJccInventory,
    fallbackJccInventory,
  );
  const inventory = data ?? fallbackJccInventory;
  const collecting = inventory.meta.collecting;

  // 采集中轮询：5s 静默拉取；collecting 翻假时的最后一次响应已带新采集结果。
  useEffect(() => {
    if (!collecting) return;
    const timer = setInterval(reload, 5000);
    return () => clearInterval(timer);
  }, [collecting, reload]);

  async function handleRefresh() {
    setRefreshError(null);
    try {
      await requestJccRefresh();
      reload();
    } catch (err) {
      setRefreshError(err instanceof Error ? err.message : String(err));
    }
  }

  const tab = params.get("tab") ?? "directory";
  const payload: JccSnapshot | null = inventory.payload;
  if (loading && !data) {
    return (
      <div className="ec-page">
        <Link className="ec-back" to="/resources/compute">
          <ArrowLeft size={15} />
          返回算力资源
        </Link>
        <p className="ec-muted">正在读取云际计算平台数据…</p>
      </div>
    );
  }
  return (
    <div className="ec-page" data-testid="jcc-platform-detail">
      <JccPlatformHeader inventory={inventory} onRefresh={handleRefresh} />
      {refreshError && (
        <Alert
          type="error"
          showIcon
          title="刷新请求失败"
          description={`${refreshError}。请确认后端已配置 JCC 采集条件（jcc.exe 与账号凭据）。`}
          style={{ marginBottom: 12 }}
        />
      )}
      {usingFallback && (
        <Alert
          type="warning"
          showIcon
          title="实时访问失败，当前展示本地快照兜底"
          description={`请确认后端服务可用后重试。${error ? `：${error}` : ""}`}
          style={{ marginBottom: 12 }}
          action={
            <Button size="small" onClick={reload}>
              重试
            </Button>
          }
        />
      )}
      {inventory.meta.lastError && !usingFallback && (
        <Alert
          type="info"
          showIcon
          title="最近一次采集存在异常"
          description={inventory.meta.lastError}
          style={{ marginBottom: 12 }}
        />
      )}
      <SnapshotNote />
      <Tabs
        className="ec-detail-tabs"
        activeKey={
          ["directory", "tasks", "drafts", "info"].includes(tab)
            ? tab
            : "directory"
        }
        onChange={(value) => setParams({ tab: value })}
        items={[
          {
            key: "directory",
            label: "资源目录",
            children: payload ? (
              <JccDirectory payload={payload} onUse={setResource} />
            ) : (
              <Empty description="暂无采集数据" />
            ),
          },
          {
            key: "tasks",
            label: "任务与实例",
            children: payload ? <JccTasks payload={payload} /> : <Empty description="暂无采集数据" />,
          },
          {
            key: "drafts",
            label: `使用草稿（演示${drafts.length ? ` · ${drafts.length}` : ""}）`,
            children: (
              <>
                <Alert
                  type="info"
                  showIcon
                  title="草稿仅保存在当前页面"
                  description="刷新或离开页面后清空，不写入真实研究项目，也不提交到云际平台。"
                />
                <DemoRecords records={drafts} />
              </>
            ),
          },
          { key: "info", label: "平台信息", children: <JccInfo inventory={inventory} /> },
        ]}
      />
      {resource && (
        <DemoRequestModal
          resource={resource}
          onClose={() => setResource(null)}
          onCreate={(record) => {
            setDrafts((current) => [record, ...current]);
            setParams({ tab: "drafts" });
          }}
        />
      )}
    </div>
  );
}

export default function PlatformDetailPage() {
  const { platformId } = useParams();
  if (platformId === "jcc") return <JccPlatform />;
  if (platformId === "flux") return <FluxPlatform />;
  return (
    <div className="ec-page">
      <Link className="ec-back" to="/resources/equipment">
        <ArrowLeft size={15} />
        返回设备资源
      </Link>
      <Empty description="未找到此平台" />
    </div>
  );
}
