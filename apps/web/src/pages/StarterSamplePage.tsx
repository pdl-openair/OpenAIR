import { useMemo, useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { Alert, Button, Card, Space, Tag, Typography } from "antd";
import { ArrowLeftOutlined, FileTextOutlined } from "@ant-design/icons";
import { motion } from "motion/react";
import { usePageHeader } from "../context/PageHeaderContext";
import { cn } from "../lib/cn";

interface StarterSampleStage {
  id: string;
  label: string;
  summary: string;
  artifactTitle: string;
  artifactSummary: string;
  boundary: string;
  nextAction: string;
}

interface StarterSampleView {
  starterName: string;
  sampleId: string;
  taskTitle: string;
  realInput: string;
  taskGoal: string;
  boundary: string;
  stages: StarterSampleStage[];
}

const VERIFIED_SAMPLES: Record<string, StarterSampleView> = {
  project_research_direction_review_v0_3: {
    starterName: "快速文献综述",
    sampleId: "b7-r2s-1-graphrag-literature-review",
    taskTitle: "研究 GraphRAG 在科研假说生成中如何减少幻觉",
    realInput:
      "研究 GraphRAG 在科研假说生成中如何减少幻觉，需要比较基线、证据链、负结果记录，材料包括 15 篇论文和初步实验日志。",
    taskGoal:
      "形成可复核的文献调研路径、来源表、推荐阅读清单、短综述草案和缺口与下一步。",
    boundary:
      "这是一次真实任务路径样例，不是最终科研结论；metadata/abstract-bounded，不可直接作为引用结论。",
    stages: [
      {
        id: "problem",
        label: "阶段 1 问题建模",
        summary: "把宽泛问题收束为可检索、可比较、可复核的研究目标。",
        artifactTitle: "检索策略草案",
        artifactSummary:
          "围绕 GraphRAG、科研假说生成、幻觉抑制、证据链和负结果记录形成第一版检索入口。",
        boundary: "当前只说明检索方向，不代表已经覆盖全部相关论文。",
        nextAction: "人工确认关键词、排除词和目标领域后，再进入来源整理。"
      },
      {
        id: "method",
        label: "阶段 2 来源整理",
        summary: "按候选来源、可读性和主题相关性组织初步来源表。",
        artifactTitle: "来源表与推荐阅读",
        artifactSummary:
          "整理候选论文的题名、摘要线索、推荐理由和待核查字段，帮助决定优先阅读顺序。",
        boundary: "题录和摘要只能作为线索，不能替代全文证据。",
        nextAction: "补齐全文可读性和关键段落后，再做证据抽取。"
      },
      {
        id: "experiment",
        label: "阶段 3 证据检查",
        summary: "把来源线索转成可复核的问题、证据缺口和风险提示。",
        artifactTitle: "缺口与证据边界",
        artifactSummary:
          "记录哪些结论仍缺全文、基线或实验日志支持，避免把摘要归纳当作结论。",
        boundary: "当前阶段暂无更多细节时，会明确展示空态，不会静默无响应。",
        nextAction: "确认可引用材料后，再进入机制或写作阶段。"
      },
      {
        id: "report",
        label: "阶段 4 短综述草案",
        summary: "形成可继续写作的短综述结构，但保留来源边界和人工复核要求。",
        artifactTitle: "短综述与下一步",
        artifactSummary:
          "输出研究问题、候选机制、证据缺口、推荐阅读和下一步复核动作。",
        boundary:
          "样例只用于浏览工作台路径，不可继续生成，也不会写入你的任务。",
        nextAction: "回到科研起点创建真实任务，或用自己的材料开始新的工作台。"
      }
    ]
  },
  agent_work_plan_v0_3: {
    starterName: "研究线索 智能体",
    sampleId: "b7-r2s-1-egfr-research-agent",
    taskTitle: "结直肠癌类器官 EGFR 抑制剂响应证据整理",
    realInput: "结直肠癌类器官 EGFR 抑制剂响应证据整理",
    taskGoal: "把目标整理成可确认的阶段、输入、缺口和下一步动作。",
    boundary:
      "这是一次真实任务路径样例，不是最终科研结论；尚未补充外部论文、实验数据或临床样本来源。",
    stages: [
      {
        id: "problem",
        label: "阶段 1 目标澄清",
        summary: "把 EGFR 抑制剂响应整理为目标、对象、指标和材料缺口。",
        artifactTitle: "研究起点草案",
        artifactSummary:
          "列出类器官队列、药物、响应指标、样本来源和需要人工确认的问题。",
        boundary: "该草案不是证据结论，只用于确认任务范围。",
        nextAction: "补充队列、药物和响应指标后确认计划。"
      },
      {
        id: "method",
        label: "阶段 2 证据准备",
        summary: "明确需要哪些论文、实验记录或临床样本信息。",
        artifactTitle: "材料补充清单",
        artifactSummary: "列出缺失来源、可读材料、实验记录和人工复核点。",
        boundary: "缺材料时不会直接生成证据表。",
        nextAction: "上传或补充可引用材料后继续。"
      },
      {
        id: "experiment",
        label: "阶段 3 分析计划",
        summary: "规划如何比较响应指标和候选机制。",
        artifactTitle: "分析路径",
        artifactSummary: "拆分响应定义、候选机制、对照基线和风险点。",
        boundary: "当前阶段暂无更多详情时，会明确展示空态，不会静默无响应。",
        nextAction: "确认分析范围后进入证据抽取。"
      },
      {
        id: "report",
        label: "阶段 4 继续推进",
        summary: "整理可恢复的任务状态和下一步动作。",
        artifactTitle: "任务状态摘要",
        artifactSummary:
          "保留目标、缺口、下一步和不可引用边界，方便从真实任务继续。",
        boundary: "样例只读，不可继续生成，也不会写入你的任务。",
        nextAction: "创建真实任务后在工作台继续推进。"
      }
    ]
  }
};

const sampleCardClass = cn(
  "mt-4 rounded-lg border border-border! bg-surface! shadow-sm"
);

export default function StarterSamplePage() {
  const navigate = useNavigate();
  const { starterId } = useParams<{ starterId: string }>();
  const sample = starterId ? VERIFIED_SAMPLES[starterId] : undefined;
  const [selectedStageId, setSelectedStageId] = useState("problem");
  const [detailOpen, setDetailOpen] = useState(false);

  const headerAction = useMemo(
    () => (
      <Button
        type="text"
        icon={<ArrowLeftOutlined />}
        onClick={() => navigate("/starters")}
      >
        返回科研起点
      </Button>
    ),
    [navigate]
  );

  usePageHeader({
    title: sample?.starterName,
    action: headerAction
  });

  const selectedStage = useMemo(() => {
    if (!sample) return null;
    return (
      sample.stages.find(stage => stage.id === selectedStageId) ??
      sample.stages[0] ??
      null
    );
  }, [sample, selectedStageId]);

  if (!sample) return <Navigate to="/starters" replace />;

  return (
    <div className="min-h-full bg-bg" data-testid="starter-sample-page">
      <section className="mx-auto max-w-[1120px] px-9 pb-10 pt-7 max-[720px]:px-4.5 max-[720px]:pb-7 max-[720px]:pt-5">
        <Space wrap className="mb-4">
          <Tag color="green">真实任务路径样例</Tag>
          <Tag>{sample.starterName}</Tag>
        </Space>
        <Typography.Title level={2}>{sample.taskTitle}</Typography.Title>
        <Alert
          type="info"
          showIcon
          message="这是一次真实任务路径样例，不是你的任务。"
          description="它用于了解这个起点如何使用，不会写入你的项目列表，也不能作为科研结论引用。"
        />

        <div className="mt-4.5 grid grid-cols-[240px_minmax(0,1fr)] gap-4 max-[720px]:grid-cols-1">
          <aside className="grid content-start gap-2" aria-label="样例阶段">
            {sample.stages.map((stage, index) => {
              const isActive = selectedStage?.id === stage.id;
              return (
                <motion.button
                  key={stage.id}
                  type="button"
                  whileHover={{ borderColor: "var(--wb-accent-strong)" }}
                  className={cn(
                    "grid w-full cursor-pointer grid-cols-[26px_minmax(0,1fr)] items-center gap-2 rounded-lg border border-border bg-surface p-2.5 text-left text-text",
                    "focus-visible:border-accent-strong focus-visible:outline-none",
                    isActive && "border-accent-strong bg-info-soft"
                  )}
                  onClick={() => {
                    setSelectedStageId(stage.id);
                    setDetailOpen(false);
                  }}
                  data-testid={`sample-stage-${index + 1}`}
                >
                  <span
                    className={cn(
                      "inline-flex h-6 w-6 items-center justify-center rounded-full bg-surface-muted font-semibold text-text-secondary",
                      isActive && "bg-accent-strong text-white"
                    )}
                  >
                    {index + 1}
                  </span>
                  <strong>{stage.label.replace(/^阶段 \d\s*/, "")}</strong>
                </motion.button>
              );
            })}
          </aside>

          <section
            className="min-w-0"
            data-testid="sample-workspace-stage-detail"
          >
            <Card
              variant="borderless"
              className={cn(sampleCardClass, "[&_h4.ant-typography]:mt-3")}
            >
              <Space wrap size={8}>
                <Tag color="processing">{selectedStage?.label}</Tag>
                <Tag>只读浏览</Tag>
              </Space>
              <Typography.Title level={4}>
                {selectedStage?.summary ?? "当前阶段暂无更多详情"}
              </Typography.Title>
              <div className="grid grid-cols-2 gap-3 max-[720px]:grid-cols-1">
                {[
                  { label: "真实输入", value: sample.realInput },
                  { label: "任务目标", value: sample.taskGoal },
                  {
                    label: "当前边界",
                    value: selectedStage?.boundary ?? sample.boundary
                  },
                  {
                    label: "下一步动作",
                    value: selectedStage?.nextAction ?? "当前暂无下一步动作"
                  }
                ].map(item => (
                  <div
                    key={item.label}
                    className="min-w-0 rounded-lg border border-border bg-surface-soft p-3 [&_p.ant-typography]:mt-1"
                  >
                    <Typography.Text type="secondary">
                      {item.label}
                    </Typography.Text>
                    <Typography.Paragraph>{item.value}</Typography.Paragraph>
                  </div>
                ))}
              </div>
            </Card>

            <motion.div whileHover={{ borderColor: "var(--wb-accent-strong)" }}>
              <Card
                variant="borderless"
                className={cn(
                  sampleCardClass,
                  "cursor-pointer hover:border-accent-strong!"
                )}
                data-testid="sample-artifact-card"
                onClick={() => setDetailOpen(true)}
              >
                <div className="grid grid-cols-[28px_minmax(0,1fr)_auto] items-center gap-2.5 max-[720px]:grid-cols-[24px_minmax(0,1fr)] [&_p.ant-typography]:mt-1">
                  <FileTextOutlined />
                  <div>
                    <Typography.Text strong>
                      {selectedStage?.artifactTitle ?? "当前阶段暂无更多详情"}
                    </Typography.Text>
                    <Typography.Paragraph type="secondary">
                      {selectedStage?.artifactSummary ?? "当前阶段暂无更多详情"}
                    </Typography.Paragraph>
                  </div>
                  <Button
                    type="link"
                    className="max-[720px]:col-start-2 max-[720px]:justify-self-start max-[720px]:px-0!"
                    onClick={event => {
                      event.stopPropagation();
                      setDetailOpen(true);
                    }}
                  >
                    查看详情
                  </Button>
                </div>
              </Card>
            </motion.div>

            {detailOpen ? (
              <Card
                variant="borderless"
                className={sampleCardClass}
                data-testid="sample-artifact-detail"
              >
                <Typography.Title level={4}>
                  {selectedStage?.artifactTitle}
                </Typography.Title>
                <Typography.Paragraph>
                  {selectedStage?.artifactSummary}
                </Typography.Paragraph>
                <Alert
                  type="warning"
                  showIcon
                  message="只读限制"
                  description="样例只能浏览阶段和产物详情，不能继续生成、不能提交输入，也不会写入你的任务。"
                />
                <div className="mt-3.5 flex flex-wrap gap-2">
                  <Button
                    onClick={() => setDetailOpen(false)}
                    data-testid="sample-back-workspace"
                  >
                    返回样例工作台
                  </Button>
                  <Button disabled data-testid="sample-readonly-continue">
                    继续生成
                  </Button>
                </div>
              </Card>
            ) : null}
          </section>
        </div>
      </section>
    </div>
  );
}
