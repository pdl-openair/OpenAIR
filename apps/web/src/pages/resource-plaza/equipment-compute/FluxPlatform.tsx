import { useMemo, useState } from "react";
import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  Input,
  Select,
  Table,
  Tabs,
  Tag,
} from "antd";
import { ArrowLeft, Search } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { PlatformMark } from "./common";
import {
  fallbackFluxInventory,
  fluxMetrics,
  formatBeijingTime,
  isNoiseTemplateName,
} from "./data";
import { fetchFluxInventory } from "./api";
import { usePlatformInventory } from "./usePlatformInventory";
import type { FluxSnapshot, FluxTemplate, FluxTemplateParam } from "./types";
import "./equipment-compute.css";

function ParamNodes({
  params,
  depth = 0,
}: {
  params: FluxTemplateParam[];
  depth?: number;
}) {
  if (!params.length)
    return <p className="ec-muted">该模板未返回参数定义。</p>;
  return (
    <div className="ec-param-list">
      {params.map((param, index) => (
        <div
          className="ec-param-item"
          key={`${param.paramName ?? "param"}-${index}`}
          style={{ marginLeft: depth * 16 }}
        >
          <div className="ec-param-item-head">
            <strong>{param.displayName || param.paramName || `参数 ${index + 1}`}</strong>
            <span className="ec-param-tags">
              {param.paramName && <Tag>{param.paramName}</Tag>}
              {param.dataType && <Tag color="blue">{param.dataType}</Tag>}
              {param.isRequired === "1" && <Tag color="red">必填</Tag>}
              {param.valueSource && <Tag>{param.valueSource}</Tag>}
            </span>
          </div>
          {param.defaultValue && (
            <pre className="ec-param-pre">{param.defaultValue}</pre>
          )}
          {param.children.length > 0 && (
            <ParamNodes params={param.children} depth={depth + 1} />
          )}
        </div>
      ))}
    </div>
  );
}

function FluxDirectory({
  snapshot,
  usingFallback,
}: {
  snapshot: FluxSnapshot;
  usingFallback: boolean;
}) {
  const [type, setType] = useState("all");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<FluxTemplate | null>(null);
  const types = useMemo(
    () => [...new Set(snapshot.templates.map((t) => String(t.taskType ?? "")))],
    [snapshot.templates],
  );
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return snapshot.templates.filter(
      (t) =>
        (type === "all" || String(t.taskType ?? "") === type) &&
        `${t.templateName ?? ""} ${t.id} ${t.labId ?? ""} ${t.experimentDesc ?? ""}`
          .toLowerCase()
          .includes(needle),
    );
  }, [snapshot.templates, type, query]);
  const params = selected ? (snapshot.templateParams[selected.id] ?? []) : [];
  return (
    <>
      <div className="ec-directory-toolbar">
        <Select
          aria-label="任务类型"
          value={type}
          onChange={setType}
          options={[
            { value: "all", label: "全部类型" },
            ...types.map((value) => ({ value, label: `类型 ${value}` })),
          ]}
        />
        <Input
          aria-label="搜索实验模板"
          prefix={<Search size={15} />}
          allowClear
          placeholder="搜索模板名称、ID 或实验室"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <span className="ec-directory-count">实验模板 {filtered.length} 项</span>
      </div>
      <Table<FluxTemplate>
        rowKey="id"
        size="middle"
        dataSource={filtered}
        scroll={{ x: 760 }}
        pagination={{ pageSize: 6, showSizeChanger: false, hideOnSinglePage: true }}
        locale={{ emptyText: "没有匹配的实验模板，请调整筛选条件" }}
        columns={[
          {
            title: "实验模板",
            key: "name",
            width: 300,
            render: (_, row) => (
              <>
                <Button
                  type="link"
                  className="ec-table-title"
                  style={{ padding: 0, height: "auto" }}
                  onClick={() => setSelected(row)}
                >
                  {row.templateName || "未命名模板"}
                </Button>
                <div className="ec-table-meta">
                  模板 ID：{row.id}
                  {row.experimentDesc ? ` · ${row.experimentDesc}` : ""}
                </div>
              </>
            ),
          },
          {
            title: "任务类型（原值）",
            dataIndex: "taskType",
            width: 130,
            render: (value) => <Tag>{value ?? "未取得"}</Tag>,
          },
          {
            title: "所属实验室",
            key: "lab",
            width: 190,
            render: (_, row) => (
              <span className="ec-table-meta">
                {row.labName || row.labId || "未取得"}
              </span>
            ),
          },
          {
            title: "预计周期",
            key: "days",
            width: 100,
            render: (_, row) =>
              row.estimateDays == null ? "未取得" : `${row.estimateDays} 天`,
          },
          {
            title: "优先级（原值）",
            dataIndex: "priority",
            width: 110,
            render: (value) => value || "未取得",
          },
          {
            title: "参数定义",
            key: "params",
            width: 100,
            render: (_, row) => (
              <Button size="small" onClick={() => setSelected(row)}>
                {(snapshot.templateParams[row.id] ?? []).length} 项
              </Button>
            ),
          },
        ]}
      />
      <p className="ec-section-note">
        模板目录与参数结构来自云实验平台开放 API
        {usingFallback ? "（当前为本地快照兜底）" : "（实时读取）"}
        ；任务类型枚举语义平台未提供，此处按原值展示。
      </p>
      <Drawer
        title="实验模板详情 · 云实验平台"
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        size={640}
      >
        {selected && (
          <>
            <Descriptions
              column={1}
              bordered
              size="small"
              items={[
                { key: "name", label: "模板名称", children: selected.templateName || "未命名模板" },
                { key: "id", label: "模板 ID", children: selected.id },
                { key: "type", label: "任务类型（原值）", children: String(selected.taskType ?? "未取得") },
                { key: "lab", label: "所属实验室", children: selected.labName || selected.labId || "未取得" },
                {
                  key: "days",
                  label: "预计周期",
                  children: selected.estimateDays == null ? "未取得" : `${selected.estimateDays} 天`,
                },
                { key: "priority", label: "优先级（原值）", children: selected.priority || "未取得" },
                {
                  key: "desc",
                  label: "说明",
                  children: selected.experimentDesc || "未提供",
                },
              ]}
            />
            <h3 style={{ marginTop: 24 }}>参数定义</h3>
            <ParamNodes params={params} />
            <p className="ec-section-note">
              参数结构用于下发试验任务时的字段校验；默认值示例来自平台配置，不代表必填内容。
            </p>
          </>
        )}
      </Drawer>
    </>
  );
}

function FluxRecipes({ snapshot }: { snapshot: FluxSnapshot }) {
  const [query, setQuery] = useState("");
  const needle = query.trim().toLowerCase();
  const filtered = snapshot.pvdSubRecipes.filter((r) =>
    `${r.subRecipeName ?? ""} ${r.groupName ?? ""} ${r.remark ?? ""}`
      .toLowerCase()
      .includes(needle),
  );
  return (
    <>
      <div className="ec-directory-toolbar">
        <Input
          aria-label="搜索工艺步骤"
          prefix={<Search size={15} />}
          allowClear
          placeholder="搜索工艺步骤名称、分组或备注"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ maxWidth: 320 }}
        />
        <span className="ec-directory-count">工艺步骤 {filtered.length} 项</span>
      </div>
      <Table
        rowKey={(row) => String(row.id)}
        size="middle"
        dataSource={filtered}
        scroll={{ x: 720 }}
        pagination={{ pageSize: 8, showSizeChanger: false, hideOnSinglePage: true }}
        locale={{ emptyText: "没有匹配的工艺步骤" }}
        columns={[
          {
            title: "工艺步骤",
            dataIndex: "subRecipeName",
            render: (value) => (
              <span style={{ overflowWrap: "anywhere" }}>{value || "未取得"}</span>
            ),
          },
          { title: "分组", dataIndex: "groupName", width: 140, render: (v) => v || "未取得" },
          {
            title: "启用状态（原值）",
            dataIndex: "enabled",
            width: 130,
            render: (v) => <Tag>{v ?? "未取得"}</Tag>,
          },
          { title: "排序", dataIndex: "sortOrder", width: 80, render: (v) => v ?? "—" },
          {
            title: "备注",
            dataIndex: "remark",
            width: 200,
            render: (v) => <span className="ec-table-meta">{v || "—"}</span>,
          },
        ]}
      />
      <p className="ec-section-note">
        PVD 工艺子配方字典来自平台开放 API；.dat 文件本体与设备绑定，本页只读展示目录。
      </p>
    </>
  );
}

function FluxInfo({
  snapshot,
  usingFallback,
}: {
  snapshot: FluxSnapshot;
  usingFallback: boolean;
}) {
  const metrics = fluxMetrics(snapshot);
  return (
    <>
      <div className="ec-info-grid">
        <section className="ec-info-panel">
          <h3>平台能力</h3>
          <div className="ec-capabilities">
            {snapshot.templates
              .filter((t) => !isNoiseTemplateName(t.templateName))
              .map((t) => (
                <Tag key={t.id}>{t.templateName}</Tag>
              ))}
          </div>
          <p>
            云实验平台（CrystalFlux）提供实验模板目录、参数结构与 PVD
            工艺字典的开放查询。当前账号可见 {metrics.templates} 个实验模板，覆盖{" "}
            {metrics.labs} 个实验室。
          </p>
        </section>
        <section className="ec-info-panel">
          <h3>数据范围</h3>
          <p>
            实验模板 {metrics.templates} 个；PVD 工艺步骤 {metrics.recipes} 条。
            实验室名称字段平台未回填，展示实验室 ID。
          </p>
          <p>
            任务与实例列表、设备实时状态等管理接口需平台用户授权，暂未接入开放
            API。
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
              "云实验平台开放 API（X-Api-Key 只读查询），经 OpenAIR 后端代理访问",
          },
          {
            key: "time",
            label: "获取时间",
            children: `${formatBeijingTime(snapshot.observedAt)}（北京时间）`,
          },
          {
            key: "sync",
            label: "更新方式",
            children: usingFallback
              ? "实时访问失败，当前展示本地一次性快照兜底"
              : "访问页面时实时读取（后端 60 秒短缓存）",
          },
          {
            key: "submit",
            label: "实验提交",
            children:
              "试验任务经研究任务内的 flux-experiment 技能下发，本页不提交任务",
          },
          {
            key: "scope",
            label: "账号范围",
            children: "仅展示当前接入账号可见的模板与字典，不代表平台全部能力",
          },
        ]}
      />
      <p className="ec-section-note">
        实验模板的预计周期与优先级为平台配置原值；实际排期、样品与费用需与平台确认。
      </p>
    </>
  );
}

export default function FluxPlatform() {
  const [params, setParams] = useSearchParams();
  const { data, loading, usingFallback, error, reload } = usePlatformInventory(
    fetchFluxInventory,
    fallbackFluxInventory,
  );
  if (loading && !data) {
    return (
      <div className="ec-page">
        <Link className="ec-back" to="/resources/equipment">
          <ArrowLeft size={15} />
          返回设备资源
        </Link>
        <p className="ec-muted">正在读取云实验平台数据…</p>
      </div>
    );
  }
  const snapshot = data ?? fallbackFluxInventory;
  return (
    <div className="ec-page" data-testid="flux-platform-detail">
      <Link className="ec-back" to="/resources/equipment">
        <ArrowLeft size={15} />
        返回设备资源
      </Link>
      <header className="ec-detail-header">
        <div className="ec-detail-identity">
          <PlatformMark kind="flux" />
          <div>
            <h1>云实验平台</h1>
            <Tag color={usingFallback ? "orange" : "green"}>
              {usingFallback ? "快照兜底" : "实时接入"}
            </Tag>
            <p className="ec-detail-meta">
              开放 API 实时读取 · 获取于{" "}
              {formatBeijingTime(snapshot.observedAt)}（北京时间）
            </p>
          </div>
        </div>
        <Button onClick={reload}>刷新数据</Button>
      </header>
      {usingFallback && (
        <Alert
          type="warning"
          showIcon
          title="实时访问失败，当前展示本地快照兜底"
          description={`请确认后端服务与云实验平台网络可达后重试。${error ? `（${error}）` : ""}`}
          action={
            <Button size="small" onClick={reload}>
              重试
            </Button>
          }
        />
      )}
      {snapshot.partialErrors.length > 0 && (
        <Alert
          type="info"
          showIcon
          title="部分数据未取得"
          description={snapshot.partialErrors.join("；")}
          style={{ marginTop: 12 }}
        />
      )}
      <Tabs
        className="ec-detail-tabs"
        activeKey={
          ["directory", "recipes", "info"].includes(params.get("tab") ?? "")
            ? (params.get("tab") as string)
            : "directory"
        }
        onChange={(value) => setParams({ tab: value })}
        items={[
          {
            key: "directory",
            label: "实验能力目录",
            children: <FluxDirectory snapshot={snapshot} usingFallback={usingFallback} />,
          },
          {
            key: "recipes",
            label: "PVD 工艺字典",
            children: <FluxRecipes snapshot={snapshot} />,
          },
          { key: "info", label: "平台信息", children: <FluxInfo snapshot={snapshot} usingFallback={usingFallback} /> },
        ]}
      />
    </div>
  );
}
