import { useState } from "react";
import { Alert, Button, Form, Input, Modal, Radio, Select, Tag } from "antd";
import { Cpu, FlaskConical, Microscope, Plug, ShieldCheck } from "lucide-react";
import type { DemoRecord } from "./data";

export function PlatformMark({
  kind,
}: {
  kind: "jcc" | "lab" | "flux";
}) {
  return (
    <div className={`ec-platform-mark ec-platform-mark-${kind}`}>
      {kind === "jcc" ? (
        <img src="/platforms/jcc-logo.png" alt="云际计算平台 Logo" />
      ) : kind === "flux" ? (
        <FlaskConical size={30} strokeWidth={1.6} />
      ) : (
        <Microscope size={30} strokeWidth={1.6} />
      )}
    </div>
  );
}
export function SnapshotNote() {
  return (
    <Alert
      type="info"
      showIcon
      title="当前账号可见的只读采集数据"
      description="规格与集群数量不代表全平台总算力；平台报告数量不等于实时空闲卡数。使用前需核对权限、镜像、数据和资源状态。"
    />
  );
}
export function ConnectionModal({
  open,
  kind,
  onClose,
}: {
  open: boolean;
  kind: "device" | "compute";
  onClose: () => void;
}) {
  const [selected, setSelected] = useState(kind);
  return (
    <Modal
      title="接入外部平台"
      open={open}
      onCancel={onClose}
      footer={<Button onClick={onClose}>了解了</Button>}
    >
      <div className="ec-modal-content">
        <Tag color="purple">接入流程预览</Tag>
        <Radio.Group
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          optionType="button"
          options={[
            { label: "设备平台", value: "device" },
            { label: "算力平台", value: "compute" },
          ]}
        />
        <div className="ec-connection-step">
          <Plug size={20} />
          <div>
            <strong>选择平台与访问方式</strong>
            <p>
              {selected === "device"
                ? "设备管理系统、实验室预约平台或独立仪器服务。"
                : "云计算、智算训练或超算平台，通过 API / CLI 接入。"}
            </p>
          </div>
        </div>
        <div className="ec-connection-step">
          <ShieldCheck size={20} />
          <div>
            <strong>由管理员配置授权</strong>
            <p>确认账号可见范围、查询与使用权限，并配置凭据。</p>
          </div>
        </div>
        <div className="ec-connection-step">
          <Cpu size={20} />
          <div>
            <strong>同步资源并关联研究</strong>
            <p>展示资源目录、任务与使用记录，按平台能力开放操作。</p>
          </div>
        </div>
        <Alert
          type="info"
          showIcon
          title="本次为产品演示"
          description="云际展示已采集的数据；新增平台接入功能待实现，此处不采集账号或密钥。"
        />
      </div>
    </Modal>
  );
}

interface RequestValues {
  project: string;
  requirement: string;
  time: string;
  sample?: string;
}
export function DemoRequestModal({
  resource,
  device = false,
  onClose,
  onCreate,
}: {
  resource: string | null;
  device?: boolean;
  onClose: () => void;
  onCreate: (record: DemoRecord) => void;
}) {
  const [form] = Form.useForm<RequestValues>();
  function submit(values: RequestValues) {
    onCreate({
      id: `demo-${crypto.randomUUID().slice(0, 8)}`,
      resource: resource ?? "",
      project: values.project,
      requirement: [
        values.sample && `样品：${values.sample}`,
        values.requirement,
      ]
        .filter(Boolean)
        .join("；"),
      time: values.time,
      createdAt: new Date().toISOString(),
    });
    onClose();
  }
  return (
    <Modal
      open={Boolean(resource)}
      title={device ? "演示预约申请" : "演示使用草稿"}
      onCancel={onClose}
      destroyOnHidden
      footer={
        <>
          <Button onClick={onClose}>取消</Button>
          <Button type="primary" onClick={() => form.submit()}>
            {device ? "生成演示申请" : "生成演示草稿"}
          </Button>
        </>
      }
    >
      <div className="ec-modal-content">
        <Alert
          type="info"
          showIcon
          title={device ? "申请生成后为“待管理员确认”" : "草稿生成后为“待提交”"}
          description="仅在本页演示，刷新或离开页面后清空；不会更改研究项目或向平台提交任务。"
        />
        <p className="ec-selected-resource">{resource}</p>
        <Form form={form} layout="vertical" onFinish={submit} preserve={false}>
          <Form.Item
            name="project"
            label="研究项目（演示）"
            rules={[{ required: true, message: "请选择演示研究项目" }]}
          >
            <Select
              placeholder="选择演示研究项目"
              options={[
                {
                  value: "材料结构与性能研究（演示）",
                  label: "材料结构与性能研究（演示）",
                },
                {
                  value: "科研模型训练与评估（演示）",
                  label: "科研模型训练与评估（演示）",
                },
              ]}
            />
          </Form.Item>
          {device && (
            <Form.Item
              name="sample"
              label="样品信息"
              rules={[
                { required: true, whitespace: true, message: "请填写样品信息" },
              ]}
            >
              <Input
                placeholder="如：干燥粉末，0.5 g，注明成分"
                maxLength={300}
              />
            </Form.Item>
          )}
          <Form.Item
            name="requirement"
            label={device ? "检测目标与备注" : "计算目标与输入要求"}
            rules={[
              { required: true, whitespace: true, message: "请填写使用需求" },
            ]}
          >
            <Input.TextArea
              rows={3}
              maxLength={1000}
              placeholder={
                device
                  ? "说明检测目标、制样情况及注意事项"
                  : "说明计算目标、执行技能、数据与镜像要求"
              }
            />
          </Form.Item>
          <Form.Item
            name="time"
            label={device ? "期望时段" : "预计使用时长"}
            rules={[{ required: true, message: "请选择时间需求" }]}
          >
            <Select
              placeholder="选择时间需求"
              options={(device
                ? [
                    "下一个工作日 上午（示例）",
                    "下一个工作日 下午（示例）",
                    "与管理员协商",
                  ]
                : ["1 小时（预计）", "4 小时（预计）", "由执行方案确定"]
              ).map((value) => ({ label: value, value }))}
            />
          </Form.Item>
        </Form>
        <p className="ec-muted">
          {device
            ? "具体时段、样品安全与服务费用需经管理员确认。"
            : "提交前需核对资源余量、镜像兼容性、执行权限及费用。"}
        </p>
      </div>
    </Modal>
  );
}
