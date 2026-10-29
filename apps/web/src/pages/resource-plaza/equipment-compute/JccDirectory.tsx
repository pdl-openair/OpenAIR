import { useState } from "react";
import { Button, Descriptions, Drawer, Input, Select, Table, Tag } from "antd";
import { Search } from "lucide-react";
import { groupLabels, quotaByType, quotaRangeText } from "./data";
import type { ComputeResource, JccSnapshot, ResourceGroup } from "./data";

export default function JccDirectory({
  payload,
  onUse,
}: {
  payload: JccSnapshot;
  onUse: (resource: string) => void;
}) {
  const [group, setGroup] = useState<ResourceGroup>("train");
  const [type, setType] = useState("all");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<ComputeResource | null>(null);
  const [showImages, setShowImages] = useState(false);
  const [imageQuery, setImageQuery] = useState("");
  const groupResources = payload.resources.filter((r) => r.group === group);
  const types = [...new Set(groupResources.map((r) => r.type))];
  const filtered = groupResources.filter(
    (r) =>
      (type === "all" || r.type === type) &&
      `${r.name} ${r.clusterId} ${r.region}`
        .toLowerCase()
        .includes(query.trim().toLowerCase()),
  );
  const quota = quotaByType(payload.trainQuota, selected?.type);
  const images = payload.images.filter((i) =>
    `${i.name} ${i.arch}`
      .toLowerCase()
      .includes(imageQuery.trim().toLowerCase()),
  );
  return (
    <>
      <div className="ec-directory-toolbar">
        <Select
          aria-label="资源类别"
          value={group}
          onChange={(value) => {
            setGroup(value);
            setType("all");
          }}
          options={Object.entries(groupLabels).map(([value, label]) => ({
            value,
            label,
          }))}
        />
        {group === "train" && (
          <Select
            aria-label="加速卡类别"
            value={type}
            onChange={setType}
            options={[
              { value: "all", label: "全部加速卡" },
              ...types.map((value) => ({ value, label: value })),
            ]}
          />
        )}
        <Input
          aria-label="搜索资源规格"
          prefix={<Search size={15} />}
          allowClear
          placeholder="搜索规格、集群 ID 或地域"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <span className="ec-directory-count">
          {group === "hpc" ? "集群" : "资源规格"} {filtered.length} 项
        </span>
        <Button onClick={() => setShowImages(true)}>查看镜像目录</Button>
      </div>
      <Table<ComputeResource>
        rowKey="id"
        size="middle"
        dataSource={filtered}
        scroll={{ x: 800 }}
        pagination={{
          pageSize: 6,
          showSizeChanger: false,
          hideOnSinglePage: true,
        }}
        locale={{ emptyText: "没有匹配的资源，请调整筛选条件" }}
        columns={[
          {
            title: group === "hpc" ? "超算集群" : "资源规格",
            key: "name",
            width: 330,
            render: (_, row) => (
              <>
                <Button
                  type="link"
                  className="ec-table-title"
                  style={{ padding: 0, height: "auto" }}
                  onClick={() => setSelected(row)}
                >
                  {row.name}
                </Button>
                <div className="ec-table-meta">
                  {row.type} · 地域：
                  {row.region === "default"
                    ? "default（平台返回值）"
                    : row.region}
                </div>
              </>
            ),
          },
          {
            title: "集群 ID",
            dataIndex: "clusterId",
            width: 195,
            render: (value) => <span className="ec-table-meta">{value}</span>,
          },
          {
            title: "平台报告数量",
            key: "count",
            width: 150,
            render: (_, row) =>
              row.totalCount === null ? (
                "未取得"
              ) : (
                <>
                  <span>
                    {row.availableCount ?? "未取得"} / {row.totalCount}
                  </span>
                  <div className="ec-table-meta">可用 / 总数量</div>
                </>
              ),
          },
          {
            title: "操作",
            key: "action",
            width: 150,
            render: (_, row) => (
              <div className="flex flex-wrap gap-2">
                <Button size="small" onClick={() => setSelected(row)}>
                  详情
                </Button>
                <Button
                  size="small"
                  type="primary"
                  onClick={() => onUse(`${row.name} · 集群 ${row.clusterId}`)}
                >
                  用于研究
                </Button>
              </div>
            ),
          },
        ]}
      />
      <p className="ec-section-note">
        {group === "hpc"
          ? "仅查询了支持 VASP 的集群；本次未取得节点数、空闲核数或队列状态。"
          : "数量来自平台规格字段，统计口径待核实，不表示实时空闲 GPU 数。集群名称未取得时保留原始集群 ID。"}{" "}
        计费与排队信息需前往平台确认。
      </p>
      <Drawer
        title="资源详情 · JCC 实采"
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        size={620}
        className="ec-resource-drawer"
        footer={
          <Button
            type="primary"
            onClick={() => {
              if (selected)
                onUse(`${selected.name} · 集群 ${selected.clusterId}`);
              setSelected(null);
            }}
          >
            生成演示使用草稿
          </Button>
        }
      >
        {selected && (
          <>
            <h3 style={{ marginTop: 0 }}>{selected.name}</h3>
            <Descriptions
              column={1}
              bordered
              size="small"
              items={[
                {
                  key: "group",
                  label: "资源类别",
                  children: groupLabels[selected.group as ResourceGroup],
                },
                { key: "id", label: "集群 ID", children: selected.clusterId },
                {
                  key: "spec",
                  label: "规格 ID",
                  children: selected.specId || "未取得",
                },
                {
                  key: "region",
                  label: "地域（原值）",
                  children: selected.region,
                },
                {
                  key: "count",
                  label: "平台报告可用 / 总数量",
                  children: `${selected.availableCount ?? "未取得"} / ${selected.totalCount ?? "未取得"}`,
                },
                ...selected.details.map((d, index) => ({
                  key: `spec-${index}`,
                  label: d.name,
                  children: `${d.totalValue} ${d.totalUnit}（平台原值）`,
                })),
              ]}
            />
            {selected.group === "train" && quota && (
              <>
                <h3>账号资源范围 · {quota.type}</h3>
                <Descriptions
                  column={1}
                  bordered
                  size="small"
                  items={[
                    {
                      key: "cpu",
                      label: "CPU 范围",
                      children: quotaRangeText(quota.cpu),
                    },
                    {
                      key: "gpu",
                      label: "加速卡数量上限字段",
                      children: `${quotaRangeText(quota.gpuNumber)}（gpuNumber）`,
                    },
                    {
                      key: "memory",
                      label: "内存范围",
                      children: quotaRangeText(quota.memory),
                    },
                    {
                      key: "storage",
                      label: "存储范围",
                      children: quotaRangeText(quota.storage),
                    },
                  ]}
                />
                <p className="ec-section-note">
                  以上为账号资源范围原值，不能据此推算实时库存；字段单位与约束含义需提交前核实。
                </p>
              </>
            )}
            <h3>使用前确认</h3>
            <p className="ec-muted">
              {selected.group === "hpc"
                ? "确认 VASP 授权、编译架构、输入文件、节点与队列配置。"
                : "确认账号授权、资源可用性、镜像与加速卡兼容性，以及代码和数据位置。"}{" "}
              此处只生成演示草稿。
            </p>
          </>
        )}
      </Drawer>
      <Drawer
        title="镜像目录 · JCC 实采"
        open={showImages}
        onClose={() => setShowImages(false)}
        size={760}
      >
        <p className="ec-section-note">
          平台报告 {payload.imageTotal ?? "未取得"} 个镜像，本次采集前{" "}
          {payload.images.length} 条。此目录未建立与所选资源的兼容性匹配。
        </p>
        <Input
          aria-label="搜索已采集镜像"
          allowClear
          placeholder="搜索已采集镜像名称或架构"
          value={imageQuery}
          onChange={(e) => setImageQuery(e.target.value)}
          style={{ marginBottom: 16 }}
        />
        <Table
          rowKey="id"
          size="small"
          dataSource={images}
          scroll={{ x: 540 }}
          pagination={{ pageSize: 8, showSizeChanger: false }}
          columns={[
            {
              title: "名称",
              dataIndex: "name",
              render: (name) => (
                <span style={{ overflowWrap: "anywhere" }}>{name}</span>
              ),
            },
            {
              title: "架构",
              dataIndex: "arch",
              width: 100,
              render: (value) => value || "未取得",
            },
            {
              title: "平台状态（原值）",
              dataIndex: "availabilityStatus",
              width: 140,
              render: (value) => <Tag>{value || "未取得"}</Tag>,
            },
          ]}
        />
      </Drawer>
    </>
  );
}
