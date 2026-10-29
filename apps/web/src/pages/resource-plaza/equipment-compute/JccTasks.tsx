import { useState } from "react";
import {
  Alert,
  Descriptions,
  Drawer,
  Empty,
  Segmented,
  Table,
  Tag,
} from "antd";
import { dateTime } from "./data";
import type { DemoRecord, JccSnapshot } from "./data";

export function DemoRecords({
  records,
  device = false,
}: {
  records: DemoRecord[];
  device?: boolean;
}) {
  if (!records.length)
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          device
            ? "暂无演示预约，选择设备可体验申请流程"
            : "暂无演示草稿，可从资源目录选择资源生成"
        }
      />
    );
  return (
    <div className="ec-records">
      {records.map((record) => (
        <article className="ec-record" key={record.id}>
          <div className="ec-record-header">
            <strong>{record.resource}</strong>
            <span>
              <Tag color="purple">演示</Tag>
              <Tag color="orange">{device ? "待管理员确认" : "待提交"}</Tag>
            </span>
          </div>
          <p>研究项目：{record.project}</p>
          <p>{record.requirement}</p>
          <p>
            {device ? "期望时段" : "预计时长"}：{record.time}
          </p>
          <p>
            生成时间：{dateTime(record.createdAt)} · {record.id}
          </p>
          {!device && (
            <p>提交前待确认：执行权限、资源余量、兼容镜像、输入文件与费用。</p>
          )}
        </article>
      ))}
    </div>
  );
}

export default function JccTasks({ payload }: { payload: JccSnapshot }) {
  const [kind, setKind] = useState("train");
  const [selected, setSelected] = useState<JccSnapshot["tasks"][number] | null>(
    null,
  );
  return (
    <>
      <div className="ec-directory-toolbar">
        <Segmented
          value={kind}
          onChange={setKind}
          options={[
            { label: "训练", value: "train" },
            { label: "推理", value: "infer" },
            { label: "超算", value: "hpc" },
            { label: "容器", value: "container" },
            { label: "云主机", value: "vm" },
          ]}
        />
      </div>
      {kind === "train" ? (
        <Table
          rowKey="id"
          dataSource={payload.tasks}
          pagination={false}
          scroll={{ x: 720 }}
          columns={[
            {
              title: "任务名称",
              dataIndex: "name",
              render: (name, row) => (
                <button
                  className="text-brand cursor-pointer border-0 bg-transparent p-0"
                  onClick={() => setSelected(row)}
                >
                  {name}
                </button>
              ),
            },
            {
              title: "状态",
              dataIndex: "status",
              render: (status) => (
                <>
                  <Tag color={status === "Succeeded" ? "success" : "default"}>
                    {status === "Succeeded" ? "已完成" : status}
                  </Tag>
                  <span className="ec-table-meta">{status}</span>
                </>
              ),
            },
            { title: "开始时间", dataIndex: "startTime", render: dateTime },
            { title: "结束时间", dataIndex: "endTime", render: dateTime },
          ]}
        />
      ) : kind === "hpc" ? (
        <Alert
          type="info"
          showIcon
          title="超算全量任务列表暂不支持查询"
          description="JCC CLI 支持按已知任务名查询超算任务。本次快照未采集具体超算任务，不能据此判断任务总数；请前往平台查看。"
        />
      ) : (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="本次查询未返回记录（第 1 页，每页 100 条）"
        />
      )}
      <p className="ec-section-note">
        任务与实例来自采集时点的只读查询，未进行实时轮询；所有时间为北京时间。
      </p>
      <Drawer
        title="训练任务详情 · JCC 实采"
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        size={560}
      >
        {selected && (
          <>
            <Descriptions
              column={1}
              bordered
              items={[
                { key: "name", label: "名称", children: selected.name },
                { key: "id", label: "任务 ID", children: selected.id },
                {
                  key: "status",
                  label: "平台原始状态",
                  children: selected.status,
                },
                {
                  key: "created",
                  label: "创建时间",
                  children: dateTime(selected.createdTime),
                },
                {
                  key: "start",
                  label: "开始时间",
                  children: dateTime(selected.startTime),
                },
                {
                  key: "end",
                  label: "结束时间",
                  children: dateTime(selected.endTime),
                },
              ]}
            />
            <p className="ec-section-note">
              本次仅展示已采集的任务状态与时间，日志与结果文件未采集。可前往云际计算平台进一步查看。
            </p>
          </>
        )}
      </Drawer>
    </>
  );
}
