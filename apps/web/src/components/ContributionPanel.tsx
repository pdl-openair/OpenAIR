import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Empty,
  Form,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  App,
} from "antd";
import { CheckCircleOutlined, CloudUploadOutlined } from "@ant-design/icons";
import type { Contribution } from "@openair/shared";
import { createContribution, listMyContributions, submitContributionPublic } from "../api/governance";
import { fetchUserSkills, type UserSkill } from "../api/skills";
import { ApiError } from "../api/http";
import { cn } from "../lib/cn";

const STATUS_LABEL: Record<string, { color: string; label: string }> = {
  private_ready: { color: "blue", label: "待提交" },
  pending_public_review: { color: "orange", label: "审核中" },
  approved_public: { color: "green", label: "已公开" },
  rejected: { color: "red", label: "未通过" },
  withdrawn: { color: "default", label: "已撤回" },
};

const DOMAIN_OPTIONS = [
  { value: "cs_research", label: "计算机科学与人工智能" },
  { value: "materials", label: "材料科学" },
  { value: "general", label: "跨领域通用" },
];

const SCENE_OPTIONS = [
  { value: "research_materials", label: "查找与整理研究材料" },
  { value: "data_results", label: "处理数据与研究结果" },
  { value: "experiment_computation", label: "开展实验与计算" },
  { value: "research_quality", label: "检查研究质量" },
];

const cardClass = cn(
  "mt-4 rounded-[12px] border border-border bg-surface shadow-sm",
  "[&_.ant-card-head]:border-border",
);

function issueText(issues: string[]): string {
  return issues.length > 0 ? issues.join("；") : "请完善资源后重新检查";
}

function errorText(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    const payload = error.payload as { detail?: unknown } | undefined;
    const detail = payload?.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object") {
      const value = detail as { message?: unknown; issues?: unknown };
      if (typeof value.message === "string") {
        const issues = Array.isArray(value.issues)
          ? value.issues.filter((item): item is string => typeof item === "string")
          : [];
        return issues.length > 0 ? `${value.message}：${issueText(issues)}` : value.message;
      }
    }
  }
  return error instanceof Error ? error.message : fallback;
}

/** Upload → system check → public review → catalog admission. */
export default function ContributionPanel() {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const [items, setItems] = useState<Contribution[]>([]);
  const [skills, setSkills] = useState<UserSkill[]>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [submittingId, setSubmittingId] = useState<string | null>(null);
  const [form] = Form.useForm();

  const reload = () => {
    setLoading(true);
    Promise.all([listMyContributions(), fetchUserSkills()])
      .then(([contributions, uploaded]) => {
        setItems(contributions);
        setSkills(uploaded);
      })
      .catch((error) => message.error(errorText(error, "加载失败")))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const contributedRefs = useMemo(
    () => new Set(items.map((item) => item.resourceRef)),
    [items],
  );
  const availableSkills = skills.filter((skill) => !contributedRefs.has(skill.skill_name));

  const onCreate = async () => {
    const values = await form.validateFields();
    setCreating(true);
    try {
      const contribution = await createContribution({
        resourceType: "skill",
        resourceRef: values.resourceRef,
        title: values.title.trim(),
        description: values.description?.trim() || "",
        domain: values.domain,
        discoveryScene: values.discoveryScene,
        requestPublic: false,
      });
      if (contribution.systemCheckStatus === "passed") {
        message.success("系统检查已通过，可以提交公开审核");
      } else {
        message.warning(`已保存为待完善：${issueText(contribution.systemCheckIssues)}`);
      }
      form.resetFields();
      reload();
    } catch (error) {
      message.error(errorText(error, "保存失败"));
    } finally {
      setCreating(false);
    }
  };

  const onSubmitPublic = async (row: Contribution) => {
    setSubmittingId(row.contributionId);
    try {
      const updated = await submitContributionPublic(row.contributionId);
      if (updated.status === "pending_public_review") {
        message.success("系统复检通过，已提交人工审核");
      } else {
        message.warning(`系统检查未通过：${issueText(updated.systemCheckIssues)}`);
      }
      reload();
    } catch (error) {
      message.error(errorText(error, "提交失败"));
    } finally {
      setSubmittingId(null);
    }
  };

  return (
    <Card
      className={cardClass}
      title={
        <Space>
          <CloudUploadOutlined />
          公开贡献
        </Space>
      }
    >
      <Alert
        type="info"
        showIcon
        className="mb-4"
        message="上传后默认私有可用；公开贡献需依次通过系统结构检查和人工审核。"
        description="审核通过后会冻结发布版本并进入资源目录。公共执行入口未配置前，目录会如实显示“需要配置”。"
      />

      <Form
        form={form}
        layout="vertical"
        className="mb-6 w-full rounded-lg border border-border bg-surface-soft p-4"
        initialValues={{ domain: "general", discoveryScene: "research_materials" }}
      >
        <Typography.Title level={5} className="mt-0!">选择一个已上传资源</Typography.Title>
        <Form.Item
          name="resourceRef"
          label="资源"
          rules={[{ required: true, message: "请选择已上传资源" }]}
        >
          <Select
            placeholder={availableSkills.length > 0 ? "请选择资源" : "暂无可提交资源"}
            options={availableSkills.map((skill) => ({
              value: skill.skill_name,
              label: skill.skill_name,
            }))}
            onChange={(value) => {
              if (!form.getFieldValue("title")) form.setFieldValue("title", value);
            }}
          />
        </Form.Item>
        <Form.Item name="title" label="公开名称" rules={[{ required: true, message: "请填写公开名称" }]}>
          <Input maxLength={200} />
        </Form.Item>
        <Form.Item name="description" label="一句话说明" rules={[{ required: true, message: "请说明它能帮助用户做什么" }]}>
          <Input.TextArea rows={2} maxLength={2000} showCount />
        </Form.Item>
        <div className="grid grid-cols-2 gap-3 max-[720px]:grid-cols-1">
          <Form.Item name="domain" label="研究领域" rules={[{ required: true }]}>
            <Select options={DOMAIN_OPTIONS} />
          </Form.Item>
          <Form.Item name="discoveryScene" label="科研场景" rules={[{ required: true }]}>
            <Select options={SCENE_OPTIONS} />
          </Form.Item>
        </div>
        <Space>
          <Button
            type="primary"
            icon={<CheckCircleOutlined />}
            loading={creating}
            disabled={availableSkills.length === 0}
            onClick={() => void onCreate()}
          >
            保存并进行系统检查
          </Button>
          <Button onClick={() => navigate("/resources/mine?tab=uploaded")}>上传或完善资源</Button>
        </Space>
      </Form>

      {items.length === 0 && !loading ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有公开贡献记录" />
      ) : (
        <Table
          rowKey="contributionId"
          loading={loading}
          dataSource={items}
          pagination={{ pageSize: 8 }}
          columns={[
            { title: "资源", dataIndex: "title", ellipsis: true },
            {
              title: "系统检查",
              dataIndex: "systemCheckStatus",
              width: 140,
              render: (value: Contribution["systemCheckStatus"], row: Contribution) => (
                <Space direction="vertical" size={2}>
                  <Tag color={value === "passed" ? "green" : value === "needs_attention" ? "red" : "default"}>
                    {value === "passed" ? "已通过" : value === "needs_attention" ? "待完善" : "未检查"}
                  </Tag>
                  {row.systemCheckIssues.length > 0 ? (
                    <Typography.Text type="secondary" className="text-xs">
                      {issueText(row.systemCheckIssues)}
                    </Typography.Text>
                  ) : null}
                </Space>
              ),
            },
            {
              title: "公开状态",
              dataIndex: "status",
              width: 120,
              render: (value: string) => {
                const meta = STATUS_LABEL[value] ?? { color: "default", label: value };
                return <Tag color={meta.color}>{meta.label}</Tag>;
              },
            },
            {
              title: "审核意见",
              dataIndex: "reviewNotes",
              ellipsis: true,
              render: (value: string | null) => value || "—",
            },
            {
              title: "下一步",
              width: 210,
              render: (_, row: Contribution) => {
                const catalogResourceId = row.catalogResourceId;
                if (row.status === "approved_public" && catalogResourceId) {
                  return (
                    <Button
                      size="small"
                      onClick={() => navigate(`/resources/catalog/${encodeURIComponent(catalogResourceId)}`)}
                    >
                      查看目录页面
                    </Button>
                  );
                }
                if (row.status === "pending_public_review") return <Typography.Text type="secondary">等待人工审核</Typography.Text>;
                if (["private_ready", "rejected", "withdrawn"].includes(row.status)) {
                  return (
                    <Space wrap>
                      <Button
                        size="small"
                        type={row.systemCheckStatus === "passed" ? "primary" : "default"}
                        loading={submittingId === row.contributionId}
                        onClick={() => void onSubmitPublic(row)}
                      >
                        {row.systemCheckStatus === "passed" ? "提交公开审核" : "重新检查"}
                      </Button>
                      <Button size="small" onClick={() => navigate("/resources/mine?tab=uploaded")}>继续完善</Button>
                    </Space>
                  );
                }
                return null;
              },
            },
          ]}
        />
      )}
    </Card>
  );
}
