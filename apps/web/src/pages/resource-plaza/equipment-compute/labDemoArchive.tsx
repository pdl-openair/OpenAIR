/**
 * 【留档】材料表征平台演示页（2026-10-15 设备算力演示产物）。
 *
 * 2026-10-18 设备资源 Tab 改为只保留真实平台（云实验平台）后，此演示页不再挂路由。
 * 代码保留供后续真实设备平台接入时参考预约/送样交互；未被任何路由 import，
 * 不会进入生产 bundle。如需恢复演示：在 PlatformDetailPage 增加 "lab" 分支并
 * 在路由中注册 /resources/equipment/platforms/lab。
 */

import { useState } from "react";
import { Alert, Button, Descriptions, Drawer, Tabs, Tag } from "antd";
import { ArrowLeft } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { DemoRequestModal, PlatformMark } from "./common";
import type { DemoRecord } from "./data";
import { DemoRecords } from "./JccTasks";
import "./equipment-compute.css";

interface Device {
  id: string;
  name: string;
  subtitle: string;
  capabilities: string[];
  requirements: string;
  service: string;
  availability: string;
  maintenance: boolean;
}

const devices: Device[] = [
  {
    id: "XRD-02",
    name: "X 射线衍射仪",
    subtitle: "粉末衍射与物相分析",
    capabilities: ["物相鉴定", "晶体结构分析", "粉末衍射"],
    requirements:
      "示例要求：干燥粉末约 0.5 g，注明成分与潜在危害；具体要求需由管理员确认。",
    service: "送样检测，由管理员操作",
    availability: "工作日 09:00–17:00（示例）",
    maintenance: false,
  },
  {
    id: "SEM-01",
    name: "扫描电子显微镜",
    subtitle: "微观形貌与元素分析",
    capabilities: ["微观形貌", "EDS 能谱", "断口分析"],
    requirements: "示例要求：样品干燥、真空兼容；非导电样品需确认喷镀方案。",
    service: "管理员协助，制样后检测",
    availability: "维护中，开放时间待确认（示例）",
    maintenance: true,
  },
];

export default function LabPlatform() {
  const [params, setParams] = useSearchParams();
  const [selected, setSelected] = useState<Device | null>(null);
  const [request, setRequest] = useState<string | null>(null);
  const [records, setRecords] = useState<DemoRecord[]>([]);
  return (
    <div className="ec-page">
      <Link className="ec-back" to="/resources/equipment">
        <ArrowLeft size={15} />
        返回设备资源
      </Link>
      <header className="ec-detail-header">
        <div className="ec-detail-identity">
          <PlatformMark kind="lab" />
          <div>
            <h1>材料表征平台</h1>
            <Tag color="orange">演示 · 待真实接入</Tag>
            <p className="ec-detail-meta">
              示例设备与预约流程，真实平台接入后同步设备状态
            </p>
          </div>
        </div>
      </header>
      <Alert
        type="warning"
        showIcon
        title="设备与预约信息均为演示示例"
        description="展示后续接入实验设备的使用方式。当前未连接真实实验室，申请不会占用设备或通知管理员。"
      />
      <Tabs
        className="ec-detail-tabs"
        activeKey={params.get("tab") === "records" ? "records" : "directory"}
        onChange={(tab) => setParams({ tab })}
        items={[
          {
            key: "directory",
            label: "设备目录",
            children: (
              <div className="ec-device-list">
                {devices.map((device) => (
                  <article className="ec-device-card" key={device.id}>
                    <div className="ec-record-header">
                      <Tag>{device.id}</Tag>
                      <Tag color={device.maintenance ? "default" : "success"}>
                        {device.maintenance
                          ? "维护中（示例）"
                          : "可申请（示例）"}
                      </Tag>
                    </div>
                    <h3>{device.name}</h3>
                    <p>{device.subtitle}</p>
                    <div className="ec-capabilities">
                      {device.capabilities.map((capability) => (
                        <Tag key={capability}>{capability}</Tag>
                      ))}
                    </div>
                    <p>{device.service}</p>
                    <p>{device.availability}</p>
                    <div className="ec-card-actions">
                      <Button onClick={() => setSelected(device)}>
                        查看设备
                      </Button>
                      <Button
                        type="primary"
                        disabled={device.maintenance}
                        onClick={() =>
                          setRequest(`${device.name} ${device.id}`)
                        }
                      >
                        {device.maintenance ? "维护中暂不可申请" : "演示预约"}
                      </Button>
                    </div>
                  </article>
                ))}
              </div>
            ),
          },
          {
            key: "records",
            label: `预约记录（演示${records.length ? ` · ${records.length}` : ""}）`,
            children: (
              <>
                <section className="ec-info-panel">
                  <h3>预约流程</h3>
                  <p>
                    选择设备 → 填写样品与检测目标 → 生成演示申请 →
                    待管理员确认。
                  </p>
                  <p>
                    生成申请不等于预约成功；时段、样品与费用确认后才能安排检测。演示记录仅保存在本页，刷新或离开后清空。
                  </p>
                </section>
                <DemoRecords records={records} device />
              </>
            ),
          },
        ]}
      />
      <Drawer
        title="设备详情 · 演示"
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        size={560}
        footer={
          selected && !selected.maintenance ? (
            <Button
              type="primary"
              onClick={() => {
                setRequest(`${selected.name} ${selected.id}`);
                setSelected(null);
              }}
            >
              演示预约
            </Button>
          ) : null
        }
      >
        {selected && (
          <>
            <h2>
              {selected.name} {selected.id}
            </h2>
            <p className="ec-section-note">{selected.subtitle}</p>
            <Descriptions
              column={1}
              bordered
              items={[
                {
                  key: "capability",
                  label: "能力",
                  children: selected.capabilities.join("、"),
                },
                {
                  key: "requirements",
                  label: "样品要求",
                  children: selected.requirements,
                },
                {
                  key: "service",
                  label: "服务方式",
                  children: selected.service,
                },
                {
                  key: "time",
                  label: "开放情况",
                  children: selected.availability,
                },
                {
                  key: "price",
                  label: "服务费用",
                  children: "待管理员确认（演示）",
                },
              ]}
            />
          </>
        )}
      </Drawer>
      {request && (
        <DemoRequestModal
          resource={request}
          device
          onClose={() => setRequest(null)}
          onCreate={(record) => {
            setRecords((current) => [record, ...current]);
            setParams({ tab: "records" });
          }}
        />
      )}
    </div>
  );
}
