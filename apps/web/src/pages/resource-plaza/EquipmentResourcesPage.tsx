import { useState } from "react";
import { Alert, Button, Empty, Input, Tag } from "antd";
import { ArrowRight, FlaskConical, Plus, Search } from "lucide-react";
import { Link } from "react-router-dom";
import { ResourcePlazaLoadingState } from "../../components/resource-plaza/ResourcePlazaStates";
import { ConnectionModal, PlatformMark } from "./equipment-compute/common";
import {
  fallbackFluxInventory,
  fluxMetrics,
  formatBeijingTime,
  isNoiseTemplateName,
} from "./equipment-compute/data";
import type { FluxSnapshot } from "./equipment-compute/data";
import { fetchFluxInventory } from "./equipment-compute/api";
import { usePlatformInventory } from "./equipment-compute/usePlatformInventory";
import "./equipment-compute/equipment-compute.css";

const FLUX_CARD_KEYWORDS =
  "云实验平台 flux crystalflux 实验平台 实验 晶体 助熔剂 生长 pvd aicvd 太赫兹 模板 工艺 设备";

/** 纯数字命名（如测试模板 "11221212"）作能力标签没有信息量，过滤掉。 */
function FluxCapabilityTags({ snapshot }: { snapshot: FluxSnapshot }) {
  const names = snapshot.templates
    .map((t) => t.templateName)
    .filter((name): name is string => Boolean(name?.trim()) && !isNoiseTemplateName(name));
  if (!names.length) return <Tag>实验模板目录</Tag>;
  return (
    <>
      {names.slice(0, 4).map((name) => (
        <Tag key={name}>{name}</Tag>
      ))}
    </>
  );
}

function FluxPlatformCard({ snapshot, usingFallback }: { snapshot: FluxSnapshot; usingFallback: boolean }) {
  const metrics = fluxMetrics(snapshot);
  return (
    <article className="ec-platform-card" data-testid="flux-platform-card">
      <div className="ec-card-identity">
        <PlatformMark kind="flux" />
        <div>
          <Link
            className="ec-card-title"
            to="/resources/equipment/platforms/flux"
          >
            云实验平台
          </Link>
          <div>
            <Tag color={usingFallback ? "orange" : "green"}>
              {usingFallback ? "快照兜底" : "实时接入"}
            </Tag>
          </div>
        </div>
      </div>
      <p className="ec-card-description">
        覆盖助熔剂晶体生长、PVD、AICVD 等实验能力目录与工艺字典，实时读取平台开放数据。
      </p>
      <div className="ec-capabilities">
        <FluxCapabilityTags snapshot={snapshot} />
      </div>
      <div className="ec-card-metrics ec-card-metrics-three">
        <div>
          <strong>
            {metrics.templates} <small>个</small>
          </strong>
          <span>实验模板</span>
        </div>
        <div>
          <strong>
            {metrics.labs} <small>个</small>
          </strong>
          <span>所属实验室</span>
        </div>
        <div>
          <strong>
            {metrics.recipes} <small>条</small>
          </strong>
          <span>PVD 工艺步骤</span>
        </div>
      </div>
      <div className="ec-card-context">
        <span>数据来源</span>
        <strong>{usingFallback ? "本地快照兜底" : "平台开放 API 实时读取"}</strong>
      </div>
      <div className="ec-card-actions">
        <Link to="/resources/equipment/platforms/flux">
          <Button type="primary">
            查看实验能力 <ArrowRight size={15} />
          </Button>
        </Link>
        <Link to="/resources/equipment/platforms/flux?tab=recipes">
          工艺字典
        </Link>
      </div>
      <div className="ec-card-footnote">
        获取于 {formatBeijingTime(snapshot.observedAt)}（北京时间）
        {usingFallback ? " · 实时访问失败，快照兜底" : " · 实时读取"}
      </div>
    </article>
  );
}

export default function EquipmentResourcesPage() {
  const [query, setQuery] = useState("");
  const [connection, setConnection] = useState<"device" | "compute" | null>(
    null,
  );
  const { data, loading, usingFallback, error, reload } = usePlatformInventory(
    fetchFluxInventory,
    fallbackFluxInventory,
  );
  const snapshot = data ?? fallbackFluxInventory;
  const showFlux = FLUX_CARD_KEYWORDS.includes(query.trim().toLowerCase());
  return (
    <div className="ec-page" data-testid="equipment-resources-page">
      <header className="ec-page-header">
        <div>
          <h1>设备资源</h1>
          <p>连接实验设备与实验平台，查看实验能力与使用条件</p>
        </div>
        <div className="ec-header-actions">
          <Input
            aria-label="搜索设备平台"
            allowClear
            prefix={<Search size={16} />}
            placeholder="搜索平台或能力"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <Button
            icon={<Plus size={16} />}
            onClick={() => setConnection("device")}
          >
            接入平台
          </Button>
        </div>
      </header>
      <section className="ec-platform-column" aria-label="设备平台">
        <div className="ec-column-heading">
          <div className="ec-heading-icon">
            <FlaskConical size={21} />
          </div>
          <div>
            <h2>实验平台</h2>
            <p>实验能力目录、工艺字典与使用条件</p>
          </div>
          <span className="ec-count">{showFlux ? 1 : 0} 个平台</span>
        </div>
        {loading && !data ? (
          <ResourcePlazaLoadingState />
        ) : showFlux ? (
          <>
            {usingFallback && (
              <Alert
                type="warning"
                showIcon
                title="云实验平台实时访问失败，当前展示本地快照兜底"
                description={`请确认后端服务与平台网络可达后重试。${error ? `：${error}` : ""}`}
                action={
                  <Button size="small" onClick={reload}>
                    重试
                  </Button>
                }
                style={{ marginBottom: 12 }}
              />
            )}
            <FluxPlatformCard snapshot={snapshot} usingFallback={usingFallback} />
          </>
        ) : (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="未找到匹配的设备平台"
          />
        )}
        <Button
          className="ec-add-platform"
          type="dashed"
          icon={<Plus size={16} />}
          onClick={() => setConnection("device")}
        >
          添加设备平台
        </Button>
      </section>
      <p className="ec-overview-note">
        平台按能力分别接入，实验能力与使用条件以平台详情为准。
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
