export type AgentPresentationKind = "orchestrator" | "research" | "system" | "workflow";

export interface AgentPresentation {
  id: string;
  displayName: string;
  mentionName: string;
  shortRole: string;
  boundary: string;
  kind: AgentPresentationKind;
  color: string;
}

const AGENTS: Record<string, AgentPresentation> = {
  openair_orchestrator: {
    id: "openair_orchestrator",
    displayName: "总控",
    mentionName: "总控",
    shortRole: "对齐目标、分派角色并收口结论",
    boundary: "只做协同收口，不替代专业智能体的证据判断。",
    kind: "orchestrator",
    color: "var(--wb-accent-strong)",
  },
  research_start_draft: {
    id: "research_start_draft",
    displayName: "研究起点规划 智能体",
    mentionName: "研究起点规划 智能体",
    shortRole: "把原始目标整理成可确认的研究起点",
    boundary: "确认前不启动检索、实验、训练或高成本执行。",
    kind: "research",
    color: "var(--wb-accent-strong)",
  },
  research_question_refinement: {
    id: "research_question_refinement",
    displayName: "研究问题凝练 智能体",
    mentionName: "研究问题凝练 智能体",
    shortRole: "收敛研究对象、变量边界和可验证问题",
    boundary: "只生成问题候选和边界，不生成已验证结论。",
    kind: "research",
    color: "var(--wb-accent-strong)",
  },
  key_literature_positioning: {
    id: "key_literature_positioning",
    displayName: "关键文献定位 智能体",
    mentionName: "关键文献定位 智能体",
    shortRole: "界定检索边界、关键词和阅读优先级",
    boundary: "确认前不声明已完成真实检索或文献综述。",
    kind: "research",
    color: "var(--wb-accent-strong)",
  },
  research_progress_planning: {
    id: "research_progress_planning",
    displayName: "研究推进计划 智能体",
    mentionName: "研究推进计划 智能体",
    shortRole: "把目标拆成阶段、产物、确认门和停止条件",
    boundary: "只生成可审阅计划，不自动执行下游步骤。",
    kind: "research",
    color: "var(--wb-accent-strong)",
  },
  paper_evidence_extraction: {
    id: "paper_evidence_extraction",
    displayName: "论文证据抽取 智能体",
    mentionName: "论文证据抽取 智能体",
    shortRole: "从可读材料中抽取 claim-evidence rows",
    boundary: "没有可读材料和 locator 时不生成证据行。",
    kind: "research",
    color: "var(--wb-accent-strong)",
  },
  research_gap_identification: {
    id: "research_gap_identification",
    displayName: "研究空缺识别 智能体",
    mentionName: "研究空缺识别 智能体",
    shortRole: "从来源覆盖和证据表中识别候选研究空缺",
    boundary: "topic-only 输入不能生成 publishable gap 或 novelty claim。",
    kind: "research",
    color: "var(--wb-accent-strong)",
  },
  mechanism_dispute_mapping: {
    id: "mechanism_dispute_mapping",
    displayName: "机制争议图谱 智能体",
    mentionName: "机制争议图谱 智能体",
    shortRole: "把证据、推断、冲突和缺口组织成机制链",
    boundary: "每条机制边必须可追溯，或明确标为推断/缺口。",
    kind: "research",
    color: "var(--wb-accent-strong)",
  },
  validation_design: {
    id: "validation_design",
    displayName: "验证方案设计 智能体",
    mentionName: "验证方案设计 智能体",
    shortRole: "设计变量、对照、基线、消融和分析计划",
    boundary: "只设计方案，不声明实验已执行或结果已成立。",
    kind: "research",
    color: "var(--wb-accent-strong)",
  },
  result_reliability_audit: {
    id: "result_reliability_audit",
    displayName: "结果可靠性审计 智能体",
    mentionName: "结果可靠性审计 智能体",
    shortRole: "检查结果 claim、记录、日志和指标是否可追溯",
    boundary: "没有检查记录时不给通过/不通过结论。",
    kind: "research",
    color: "var(--wb-accent-strong)",
  },
  openair_summary_agent: {
    id: "openair_summary_agent",
    displayName: "总结 智能体",
    mentionName: "总结 智能体",
    shortRole: "把长输出压缩成用户可见摘要",
    boundary: "只做表达整理，不替代科研质量判断。",
    kind: "system",
    color: "var(--wb-accent-strong)",
  },
  openair_review_agent: {
    id: "openair_review_agent",
    displayName: "复核 智能体",
    mentionName: "复核 智能体",
    shortRole: "复核产物状态、风险和可用性",
    boundary: "只基于已有产物和规则复核，不补造证据。",
    kind: "system",
    color: "var(--wb-accent-strong)",
  },
  openair_presentation_guidance_agent: {
    id: "openair_presentation_guidance_agent",
    displayName: "汇报指导 智能体",
    mentionName: "汇报指导 智能体",
    shortRole: "把已有材料整理成展示或报告结构",
    boundary: "不凭空生成研究结果，不绕过证据边界。",
    kind: "system",
    color: "var(--wb-accent-strong)",
  },
  lightweight_research_assistant: {
    id: "lightweight_research_assistant",
    displayName: "轻量研究助理 智能体",
    mentionName: "轻量研究助理 智能体",
    shortRole: "做轻量解释、整理和边界检查",
    boundary: "不伪装成完整科研专业智能体。",
    kind: "research",
    color: "var(--wb-accent-strong)",
  },
  evidence_review_workflow: {
    id: "evidence_review_workflow",
    displayName: "证据整理工作流模板",
    mentionName: "证据整理工作流模板",
    shortRole: "组织证据、来源、分歧和反例的工作流模板",
    boundary: "这是工作流模板，不是单个可对话的智能体。",
    kind: "workflow",
    color: "var(--wb-text-muted)",
  },
};

const AGENT_ALIASES: Record<string, string> = {
  agent_evidence_extraction_v0_3: "paper_evidence_extraction",
  paper_evidence_extraction_agent: "paper_evidence_extraction",
  agent_result_audit_v0_3: "result_reliability_audit",
  result_audit: "result_reliability_audit",
  agent_validation_design_v0_3: "validation_design",
  validation_design_planning: "validation_design",
  agent_gap_identification_v0_3: "research_gap_identification",
  gap_identification: "research_gap_identification",
  agent_question_refinement_v0_3: "research_question_refinement",
  agent_literature_research_v0_3: "key_literature_positioning",
  agent_work_plan_v0_3: "research_progress_planning",
  exp_research_task_workflow_planning_v0_2: "research_progress_planning",
  agent_summary_v0_3: "openair_summary_agent",
  agent_research_summary_v0_3: "openair_summary_agent",
  research_summary_agent: "openair_summary_agent",
  agent_artifact_review_v0_3: "openair_review_agent",
  agent_review_v0_3: "openair_review_agent",
  artifact_review_agent: "openair_review_agent",
  agent_presentation_guidance_v0_3: "openair_presentation_guidance_agent",
  agent_presentation_v0_3: "openair_presentation_guidance_agent",
  presentation_guidance_agent: "openair_presentation_guidance_agent",
  new_task_planning: "research_start_draft",
  workbench_chat: "openair_orchestrator",
  "workbench-chat": "openair_orchestrator",
};

const SKILL_HINTS: Array<[RegExp, string]> = [
  [/evidence[_-]?extraction/i, "paper_evidence_extraction"],
  [/gap[_-]?identification|gap[_-]?analysis/i, "research_gap_identification"],
  [/validation[_-]?design|variable[_-]?definition|baseline[_-]?selection|ablation[_-]?design|analysis[_-]?planning/i, "validation_design"],
  [/result[_-]?audit|result[_-]?acceptance|quality[_-]?audit|reliability/i, "result_reliability_audit"],
  [/question[_-]?refinement|intent[_-]?understanding/i, "research_question_refinement"],
  [/literature[_-]?search|key[_-]?literature|source[_-]?screening/i, "key_literature_positioning"],
  [/mechanism[_-]?explanation|mechanism[_-]?dispute/i, "mechanism_dispute_mapping"],
  [/task[_-]?breakdown|work[_-]?plan|progress[_-]?planning/i, "research_progress_planning"],
];

export function normalizeAgentId(agentId?: string | null): string | null {
  if (!agentId) return null;
  const normalized = String(agentId).trim();
  if (!normalized) return null;
  if (AGENTS[normalized]) return normalized;
  return AGENT_ALIASES[normalized] ?? null;
}

export function inferAgentIdFromCapability(name?: string | null, targetAgent?: string | null): string | null {
  const explicit = normalizeAgentId(targetAgent);
  if (explicit) return explicit;
  const raw = String(name ?? "").trim();
  if (!raw) return null;
  const alias = normalizeAgentId(raw);
  if (alias) return alias;
  for (const [pattern, agentId] of SKILL_HINTS) {
    if (pattern.test(raw)) return agentId;
  }
  return null;
}

export function getAgentPresentation(agentId?: string | null): AgentPresentation | null {
  const normalized = normalizeAgentId(agentId);
  return normalized ? AGENTS[normalized] ?? null : null;
}

export function getAgentDisplayName(agentId?: string | null, fallback = "未标注执行主体"): string {
  return getAgentPresentation(agentId)?.displayName ?? fallback;
}

type CapabilityExecutionSubject = {
  name?: string | null;
  targetAgent?: string | null;
  runtime?: string | null;
};

/** User-visible responsibility is an Agent role, never a generic model label. */
export function getCapabilityExecutionSubject(call: CapabilityExecutionSubject): string {
  const agentId = inferAgentIdFromCapability(call.name, call.targetAgent);
  if (agentId) return getAgentDisplayName(agentId);

  switch (String(call.runtime ?? "").trim().toLowerCase()) {
    case "huiyan_chat_completions":
      return "HUIYAN 总控";
    case "specialist_agent_runtime_gateway":
      return "OpenAIR 总控";
    case "replay":
    case "replay_fallback":
      return "历史回放记录";
    case "fixture":
      return "样例记录";
    case "python_adapter":
      return "OpenAIR 运行器";
    default:
      return "未标注执行主体";
  }
}

export function agentMention(agentId?: string | null): string | null {
  const presentation = getAgentPresentation(agentId);
  return presentation ? `@${presentation.mentionName}` : null;
}

export function agentImpactLabel(agentId?: string | null): string {
  const normalized = normalizeAgentId(agentId);
  if (normalized === "paper_evidence_extraction") return "影响产物：证据表";
  if (normalized === "research_gap_identification") return "影响产物：候选 Gap 列表";
  if (normalized === "mechanism_dispute_mapping") return "影响产物：机制链与争议图谱";
  if (normalized === "validation_design") return "影响产物：验证方案草案";
  if (normalized === "result_reliability_audit") return "影响状态：结果可信度与修复动作";
  if (normalized === "research_question_refinement") return "影响产物：研究问题候选";
  if (normalized === "key_literature_positioning") return "影响产物：检索边界与阅读优先级";
  if (normalized === "research_progress_planning") return "影响产物：阶段计划与确认门";
  if (normalized === "openair_summary_agent") return "影响表达：用户可见摘要";
  if (normalized === "openair_review_agent") return "影响状态：产物复核结论";
  if (normalized === "openair_presentation_guidance_agent") return "影响产物：汇报或展示结构";
  return "影响任务：当前协作记录";
}
