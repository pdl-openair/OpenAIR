import type { UiLanguage } from "@openair/shared";
import rawArtifacts from "@fixtures/recorded_runs/cs_research_ope_001/artifacts.json";
import rawCalls from "@fixtures/recorded_runs/cs_research_ope_001/capability_calls.json";
import rawStages from "@fixtures/recorded_runs/cs_research_ope_001/stages.json";
import rawTask from "@fixtures/recorded_runs/cs_research_ope_001/task.json";
import { workbenchModelFromCsResearchFixture, type WorkbenchArtifact, type WorkbenchTaskModel } from "../model/workbenchTask";
import type {
  FixtureArtifactJson,
  FixtureCapabilityCallJson,
  FixtureStageJson,
  FixtureTaskJson,
} from "./fixtureTypes";

export const CS_RESEARCH_OPE_FIXTURE_TASK_ID = "task_cs_ope_001";

const B2_DEMO_PROFILES: Record<string, { title: string; goal: string; domain: string; failed?: boolean }> = {
  task_demo_materials_b2: {
    title: "高熵合金低温韧性研究线索整理",
    goal: "梳理高熵合金成分、工艺、结构表征与低温力学性能之间的证据边界，形成下一步实验补证计划。",
    domain: "materials_research",
  },
  task_demo_biomed_b2: {
    title: "肿瘤类器官药物响应证据整理",
    goal: "整理类器官模型、样本来源、给药条件、对照设计和统计边界，判断当前证据是否足以支持后续验证。",
    domain: "biomed_research",
  },
  task_demo_ai_b2: {
    title: "长任务 AI 智能体评测基线复核",
    goal: "核对数据集、baseline、metric、代码环境和复现实验缺口，形成可恢复的评测继续入口。",
    domain: "ai_research",
  },
  task_demo_failed_b2: {
    title: "低置信来源拦截：短综述草稿待补证据",
    goal: "当前内容来自低置信记录，只能作为线索草稿，需补 DOI、arXiv、PMID 或上传来源文件。",
    domain: "ai_research",
    failed: true,
  },
};

export function loadCsResearchOpeWorkbenchFixture(language: UiLanguage = "zh-CN"): WorkbenchTaskModel {
  return workbenchModelFromCsResearchFixture(
    rawTask as FixtureTaskJson,
    rawStages as FixtureStageJson[],
    rawArtifacts as FixtureArtifactJson[],
    rawCalls as FixtureCapabilityCallJson[],
    language,
  );
}

export function isB2DemoFixtureTask(taskId: string): boolean {
  return taskId in B2_DEMO_PROFILES;
}

function b2DemoArtifacts(taskId: string, updatedAt: string): WorkbenchArtifact[] {
  const shared = {
    relatedStage: "problem_modeling" as const,
    createdAt: updatedAt,
    sourceMode: "fixture" as const,
    sourceLabel: "低置信材料，需补来源",
    handoffActions: ["加入状态摘要", "标记需复核", "保留当前上下文"],
  };
  return [
    {
      ...shared,
      id: `${taskId}_viewer_markdown_report`,
      type: "research_report",
      title: "研究方向调研摘要",
      summary: "用于整理当前研究问题、候选路线和待补证据。",
      format: "markdown",
      trustStatus: "low_confidence",
      processingStatus: "示例内容已加载，待补真实来源",
      content: [
        "# 研究方向调研摘要：OPE 算法库生态调研",
        "",
        "## 摘要",
        "该摘要用于整理研究报告类产物的阅读形态。当前材料仍需补充真实来源，不代表真实数据库、全文论文或外部检索已经接入。",
        "",
        "## 证据边界",
        "- 当前只覆盖问题描述、候选路线和人工整理要点。",
        "- 不包含真实 DOI、arXiv、PMID 或全文精读结果。",
        "- 不能直接作为正式科研结论引用。",
        "",
        "## 下一步摘要",
        "补充真实来源、明确筛选条件，并将候选路线放入表格产物中进行复核。",
      ].join("\n"),
    },
    {
      ...shared,
      id: `${taskId}_viewer_table_summary`,
      type: "comparison_table",
      title: "候选路线对比表",
      summary: "用于整理表格 / 数据摘要类产物。",
      format: "csv",
      trustStatus: "review_needed",
      processingStatus: "表格已加载，待人工复核",
      content: [
        "候选路线,输入材料,证据边界,主要风险,下一步",
        "路线 A：文献调研,关键词与种子论文,字段待补充，未连接真实数据库,来源不足,补 DOI / arXiv / PMID",
        "路线 B：参数整理,人工给定条件,未运行实验设备或仿真,参数可能缺失,确认范围后再建表",
        "路线 C：复现准备,命令与配置片段,未执行真实复跑,环境可能不完整,进入配置片段复核",
      ].join("\n"),
    },
    {
      ...shared,
      id: `${taskId}_viewer_code_config`,
      type: "code_config",
      title: "复现配置片段（待复跑）",
      summary: "用于整理代码 / 配置 / 运行片段类产物。",
      format: "yaml",
      trustStatus: "sample_placeholder",
      processingStatus: "配置片段 / 待复跑 / 需复核",
      content: [
        "# Configuration snippet pending rerun; not a successful run",
        "task:",
        "  name: ope_reliability_review",
        "  mode: dry_run",
        "inputs:",
        "  literature_seed: pending_real_sources",
        "  max_sources: 12",
        "checks:",
        "  require_human_review: true",
        "  allow_external_database_claim: false",
        "next_step: rerun_after_real_sources_are_added",
      ].join("\n"),
    },
    {
      ...shared,
      id: `${taskId}_viewer_pdf_placeholder`,
      type: "paper_pdf",
      title: "论文附件待接入",
      summary: "用于整理 PDF / 论文 / 附件待接入状态。",
      format: "pdf",
      trustStatus: "sample_placeholder",
      processingStatus: "等待补充，未进行真实 PDF 解析",
      pageCount: null,
      handoffActions: ["加入阅读清单", "等待上传 PDF", "保留占位说明"],
      content: null,
    },
    {
      ...shared,
      id: `${taskId}_viewer_failed_snippet`,
      type: "run_fragment",
      title: "运行片段失败记录",
      summary: "用于验证处理失败态不会打散主布局。",
      format: "code",
      trustStatus: "failed",
      processingStatus: "处理失败，未完成真实复跑",
      handoffActions: ["保留失败原因", "补充输入后重试", "不要引用为结果"],
      content: [
        "# Failed run fragment",
        "# This is not a successful run.",
        "python reproduce.py --config configs/ope_review.yaml",
        "",
        "status: failed",
        "reason: missing real source packet",
        "impact: no scientific result was produced",
        "next_step: add verified sources and rerun",
      ].join("\n"),
    },
  ];
}

export function loadB2DemoWorkbenchFixture(taskId: string, language: UiLanguage = "zh-CN"): WorkbenchTaskModel {
  const profile = B2_DEMO_PROFILES[taskId];
  const base = loadCsResearchOpeWorkbenchFixture(language);
  const prefixArtifactRef = (id: string) => `${taskId}_${id}`;
  const demoArtifacts = b2DemoArtifacts(taskId, base.updatedAt);
  const demoArtifactIds = demoArtifacts.map((artifact) => artifact.id);
  const baseArtifacts = base.artifacts.map((artifact, index) => ({
    ...artifact,
    id: prefixArtifactRef(artifact.id),
    title:
      index === 0
        ? profile.failed
          ? "短综述草稿，待补真实来源"
          : "研究线索整理草稿，暂不可直接引用"
        : artifact.title,
    summary:
      index === 0
        ? `${profile.goal} 当前产物用于恢复研究上下文，正式引用前必须补充真实来源并人工复核。`
        : artifact.summary,
    sourceMode: profile.failed ? "replay" as const : "fixture" as const,
    sourceLabel: "低置信材料，需补来源",
    trustStatus: profile.failed ? "failed" as const : "low_confidence" as const,
    processingStatus: profile.failed ? "处理失败，待补真实来源" : "内容已加载，待复核",
  }));
  return {
    ...base,
    taskId,
    title: profile.title,
    goalSummary: profile.goal,
    domain: profile.domain,
    taskStatus: profile.failed ? "failed" : "running",
    resultSource: profile.failed ? "replay" : "demo",
    stages: base.stages.map((stage, index) => ({
      ...stage,
      summary:
        index === 0
          ? `${profile.title} 已形成阶段草稿；当前结论仍需回到真实来源、全文精读状态和领域关键字段中复核。`
          : stage.summary,
      risks:
        index === 0
          ? "当前证据边界尚未完全闭合，不能直接用于正式科研结论。"
          : stage.risks,
      nextSteps:
        index === 0
          ? "补充真实来源文件或 DOI/arXiv/PMID，重新检索并生成下一阶段计划。"
          : stage.nextSteps,
      artifactRefs: [
        ...stage.artifactRefs.map(prefixArtifactRef),
        ...(index === 0 ? demoArtifactIds : []),
      ],
      subStages: stage.subStages.map((sub, subIndex) => ({
        ...sub,
        artifactRefs: [
          ...sub.artifactRefs.map(prefixArtifactRef),
          ...(index === 0 && subIndex === 0 ? demoArtifactIds : []),
        ],
      })),
    })),
    artifacts: [...baseArtifacts, ...demoArtifacts],
    capabilityCalls: base.capabilityCalls.map((call) => ({
      ...call,
      id: `${taskId}_${call.id}`,
      artifactRefs: call.artifactRefs?.map(prefixArtifactRef),
      outputArtifactRefs: call.outputArtifactRefs?.map(prefixArtifactRef),
      runId: profile.failed ? null : call.runId,
      status: profile.failed ? "failed" : call.status,
      partialFailureImpact: profile.failed ? "当前内容来自低置信记录，未完成真实来源核验。" : call.partialFailureImpact,
      sourceMode: profile.failed ? "replay" : call.sourceMode,
    })),
  };
}
