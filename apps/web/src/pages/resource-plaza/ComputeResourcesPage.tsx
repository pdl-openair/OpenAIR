import { useState } from "react";
import { Alert, Button, Empty, Input, Tag } from "antd";
import { ArrowRight, Cpu, Plus, RefreshCw, Search } from "lucide-react";
import { Link } from "react-router-dom";
import { ResourcePlazaLoadingState } from "../../components/resource-plaza/ResourcePlazaStates";
import { ConnectionModal, PlatformMark } from "./equipment-compute/common";
import {
  fallbackJccInventory,
  formatBeijingTime,
  jccMetrics,
} from "./equipment-compute/data";
import type { JccInventoryResult } from "./equipment-compute/data";
import { fetchJccInventory } from "./equipment-compute/api";
import { usePlatformInventory } from "./equipment-compute/usePlatformInventory";
import "./equipment-compute/equipment-compute.css";

const JCC_CARD_KEYWORDS =
  "云际计算平台 jcc 算力 超算 智算 gpu npu mlu vasp 训练 推理 云主机 容器";

function JccPlatformCard({ inventory }: { inventory: JccInventoryResult }) {
  const { meta, payload } = inventory;
  const usingCache = meta.source === "cache";
  const metrics = payload
    ? jccMetrics(payload)
    : { categories: 0, training: 0, hpc: 0, completed: 0 };
  return (
    <article className="ec-platform-card" data-testid="jcc-platform-card">
      <div className="ec-card-identity">
        <PlatformMark kind="jcc" />
        <div>
          <Link className="ec-card-title" to="/resources/compute/platforms/jcc">
            云际计算平台
          </Link>
          <div>
            <Tag color={usingCache ? "cyan" : "orange"}>
              {usingCache ? "实时采集缓存" : "快照兜底"}
            </Tag>
            {meta.collecting && (
              <Tag
                icon={<RefreshCw size={12} className="ec-spin-icon" />}
                color="processing"
              >
                采集中
              </Tag>
            )}
          </div>
        </div>
      </div>
      <p className="ec-card-description">
        统一查看异构训练规格、VASP
        超算集群与云资源，了解当前账号的任务状态。
      </p>
      <div className="ec-capabilities">
        <Tag>智算训练</Tag>
        <Tag>VASP 超算</Tag>
        <Tag>云主机 / 容器</Tag>
      </div>
      <div className="ec-card-metrics ec-card-metrics-four">
        <div>
          <strong>
            {metrics.categories} <small>类</small>
          </strong>
          <span>训练加速卡</span>
        </div>
        <div>
          <strong>
            {metrics.training} <small>种</small>
          </strong>
          <span>训练规格</span>
        </div>
        <div>
          <strong>
            {metrics.hpc} <small>个</small>
          </strong>
          <span>VASP 集群</span>
        </div>
        <div>
          <strong>
            {metrics.completed} <small>条</small>
          </strong>
          <span>已完成训练</span>
        </div>
      </div>
      <div className="ec-card-context">
        <span>数据范围</span>
        <strong>
          当前账号可见 ·{" "}
          {usingCache ? "后端采集缓存，可手动刷新" : "本地快照兜底"}
        </strong>
      </div>
      <div className="ec-card-actions">
        <Link to="/resources/compute/platforms/jcc">
          <Button type="primary">
            查看资源 <ArrowRight size={15} />
          </Button>
        </Link>
        <Link to="/resources/compute/platforms/jcc?tab=tasks">查看任务</Link>
      </div>
      <div className="ec-card-footnote">
        采集于 {formatBeijingTime(payload?.observedAt ?? meta.observedAt)}
        （北京时间）
        {meta.collecting ? " · 正在后台采集最新数据" : ""}
      </div>
    </article>
  );
}

export default function ComputeResourcesPage() {
  const [query, setQuery] = useState("");
  const [connection, setConnection] = useState<"device" | "compute" | null>(
    null,
  );
  const { data, loading, usingFallback, error, reload } = usePlatformInventory(
    fetchJccInventory,
    fallbackJccInventory,
  );
  const inventory = data ?? fallbackJccInventory;
  const showJcc = JCC_CARD_KEYWORDS.includes(query.trim().toLowerCase());
  return (
    <div className="ec-page" data-testid="compute-resources-page">
      <header className="ec-page-header">
        <div>
          <h1>算力资源</h1>
          <p>连接超算、智算与云计算资源，查看规格与任务</p>
        </div>
        <div className="ec-header-actions">
          <Input
            aria-label="搜索算力平台"
            allowClear
            prefix={<Search size={16} />}
            placeholder="搜索平台或能力"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <Button
            icon={<Plus size={16} />}
            onClick={() => setConnection("compute")}
          >
            接入平台
          </Button>
        </div>
      </header>
      <section className="ec-platform-column" aria-label="算力平台">
        <div className="ec-column-heading">
          <div className="ec-heading-icon">
            <Cpu size={21} />
          </div>
          <div>
            <h2>算力平台</h2>
            <p>超算、智算与云计算资源</p>
          </div>
          <span className="ec-count">{showJcc ? 1 : 0} 个平台</span>
        </div>
        {loading && !data ? (
          <ResourcePlazaLoadingState />
        ) : showJcc ? (
          <>
            {usingFallback && (
              <Alert
                type="warning"
                showIcon
                title="云际计算平台实时访问失败，当前展示本地快照兜底"
                description={`请确认后端服务可用后重试。${error ? `：${error}` : ""}`}
                action={
                  <Button size="small" onClick={reload}>
                    重试
                  </Button>
                }
                style={{ marginBottom: 12 }}
              />
            )}
            <JccPlatformCard inventory={inventory} />
          </>
        ) : (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="未找到匹配的算力平台"
          />
        )}
        <Button
          className="ec-add-platform"
          type="dashed"
          icon={<Plus size={16} />}
          onClick={() => setConnection("compute")}
        >
          添加算力平台
        </Button>
      </section>
      <p className="ec-overview-note">
        规格与集群数量为采集时点数据；提交使用前请在平台详情核对余量与权限。
      </p>
      {connection && (
        <ConnectionModal
          open
          kind={connection}
          onClose={() => setConnection(null)}
        />
      )}
    </div>
  );
}
