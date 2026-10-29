import type { ResourceAvailability, ResourceAvailabilityPresentation, ResourceKind, ResourcePlazaResource, ResourcePrimaryAction } from "./types";

export const RESOURCE_KIND_LABELS: Record<ResourceKind, "技能" | "智能体" | "工作流模板"> = {
  skill: "技能",
  agent: "智能体",
  workflow: "工作流模板",
};

export const RESOURCE_DOMAIN_LABELS: Record<
  "cs_research" | "general" | "materials" | "ope" | "opentrajectory" | "sdr",
  string
> = {
  cs_research: "计算机科研",
  general: "通用",
  materials: "材料",
  ope: "实验规划",
  opentrajectory: "材料轨迹分析",
  sdr: "研究设计",
};

export const RESOURCE_SCENARIO_LABELS: Record<
  "problem_modeling" | "solution_design" | "execution_validation" | "insight_generation",
  string
> = {
  problem_modeling: "问题建模",
  solution_design: "方案设计",
  execution_validation: "执行验证",
  insight_generation: "结果总结",
};

export const RESOURCE_AVAILABILITY_PRESENTATIONS: Record<ResourceAvailability, ResourceAvailabilityPresentation> = {
  tryable: { label: "可试用", primaryActionLabel: "查看详情", color: "success", executionDisabled: false },
  configuration_required: { label: "需要配置", primaryActionLabel: "查看详情", color: "warning", executionDisabled: false },
  view_only: { label: "仅可查看", primaryActionLabel: "查看详情", color: "processing", executionDisabled: false },
  unavailable: { label: "暂不可用", primaryActionLabel: "查看详情", color: "default", executionDisabled: false },
};

const AVAILABILITY_ALIASES: Record<string, ResourceAvailability> = {
  tryable: "tryable", try: "tryable",
  configuration_required: "configuration_required", needs_configuration: "configuration_required", configure: "configuration_required",
  view_only: "view_only", read_only: "view_only", inspect_only: "view_only",
  unavailable: "unavailable", blocked: "unavailable",
};

const PRIMARY_ACTIONS: ResourcePrimaryAction[] = ["try", "configure", "view", "none"];
const EXPECTED_PRIMARY_ACTION: Record<ResourceAvailability, ResourcePrimaryAction> = {
  tryable: "try",
  configuration_required: "configure",
  view_only: "view",
  unavailable: "none",
};

export function resolveResourceAvailability(value: unknown): ResourceAvailability {
  if (typeof value !== "string") return "unavailable";
  return AVAILABILITY_ALIASES[value.trim().toLowerCase()] ?? "unavailable";
}

export function resourceAvailabilityPresentation(
  availability: ResourceAvailability,
  executionBlocked = false,
): ResourceAvailabilityPresentation {
  return executionBlocked ? RESOURCE_AVAILABILITY_PRESENTATIONS.unavailable : RESOURCE_AVAILABILITY_PRESENTATIONS[availability];
}

export function resourceAvailabilityGuidance({
  availability,
}: Pick<ResourcePlazaResource, "availability">): string {
  if (availability === "unavailable") {
    return "当前暂不能使用，可查看研究说明。";
  }
  if (availability === "configuration_required") {
    return "请在详情中查看开始前需要准备的内容。";
  }
  if (availability === "view_only") return "当前可查看研究说明。";
  return "可在详情中查看所需材料和使用方式。";
}

export function resolveResourceKind(value: unknown): ResourceKind {
  if (typeof value !== "string") return "skill";
  const normalized = value.trim().toLowerCase();
  if (normalized === "agent") return "agent";
  if (normalized === "workflow") return "workflow";
  return "skill";
}

export function isKnownResourceKind(value: unknown): boolean {
  return typeof value === "string" && ["skill", "agent", "workflow"].includes(value.trim().toLowerCase());
}

export function isKnownResourceAvailability(value: unknown): boolean {
  return typeof value === "string" && Object.hasOwn(AVAILABILITY_ALIASES, value.trim().toLowerCase());
}

export function resolveResourcePrimaryAction(value: unknown): ResourcePrimaryAction | undefined {
  if (typeof value !== "string") return undefined;
  const normalized = value.trim().toLowerCase() as ResourcePrimaryAction;
  return PRIMARY_ACTIONS.includes(normalized) ? normalized : undefined;
}

export function isConsistentResourceAction(
  availability: ResourceAvailability,
  primaryAction: ResourcePrimaryAction | undefined,
  executionBlocked: boolean,
): boolean {
  if (primaryAction !== EXPECTED_PRIMARY_ACTION[availability]) return false;
  return availability !== "tryable" || !executionBlocked;
}

export function isExecutionBlocked(value: unknown): boolean {
  if (typeof value !== "string") return false;
  return ["execution_blocked", "security_risk", "evidence_risk", "blocked"].includes(value.trim().toLowerCase());
}

const CONTRACT_FIELD_LABELS: Record<string, string> = {
  task_id: "",
  skill_id: "",
  user_goal: "研究目标",
  user_query: "研究需求",
  original_user_query: "原始研究需求",
  role_brief: "任务背景与角色要求",
  input_memory_refs: "已有研究记录",
  input_artifact_refs: "已有材料或产物",
  reasoning_profile: "分析要求",
  scope: "分析范围",
  reasoning_summary: "分析摘要",
  summary: "摘要",
  assumptions: "主要假设",
  uncertainty: "不确定性说明",
  next_action: "下一步建议",
  status: "处理状态",
  audit: "核查记录",
  source_records: "来源记录",
  source_materials: "来源材料",
  source_or_evidence_summary: "来源与证据概览",
  material_manifest: "材料清单",
  material_coverage: "材料覆盖情况",
  material_readiness: "材料准备情况",
  readable_paper_or_result_materials: "可读取的论文或结果材料",
  paper_excerpts: "论文片段",
  paper_packets: "论文材料包",
  figures: "图表",
  tables: "数据表",
  captions: "图表说明",
  experiment_notes: "实验记录",
  research_context: "研究背景",
  research_question: "研究问题",
  research_object: "研究对象",
  hypothesis: "研究假设",
  hypotheses: "研究假设",
  testable_hypothesis: "可检验假设",
  method_idea: "方法思路",
  target_metrics: "目标指标",
  boundary_conditions: "边界条件",
  comparison_baseline: "对比基线",
  output_intent: "期望结果",
  requested_source_scope: "资料来源范围",
  interpreted_constraints: "已解释的约束",
  ambiguities: "待确认信息",
  requirement_confidence: "需求明确程度",
  candidates: "候选项",
  metadata_candidates: "元数据候选项",
  deduped_candidates: "去重后的候选项",
  screened_candidates: "初筛后的候选项",
  skipped_candidates: "未处理的候选项",
  candidate_scores: "候选评分",
  source_tiers: "来源分层",
  topk_selection: "优先候选清单",
  facets: "检索维度",
  search_target: "检索目标",
  search_matrix: "检索矩阵",
  expanded_query_variants: "扩展检索式",
  assembled_facets: "组合检索维度",
  retrieval_report: "检索报告",
  dedup_report: "去重报告",
  scoring_report: "评分报告",
  tiering_report: "分层报告",
  evidence_rows: "证据条目",
  evidence_items: "证据条目",
  evidence_packet: "证据包",
  evidence_schema: "证据结构",
  evidence_needed: "所需证据",
  evidence_supported_conclusions: "有证据支持的结论",
  evidence_upgrade_queue: "证据完善清单",
  claim_inventory: "论点清单",
  claim_evidence_table: "论点与证据对应表",
  claim_or_result: "待核查的论点或结果",
  unsupported_or_missing: "证据不足或缺失项",
  author_inference_or_model_inference: "作者推断或模型推断",
  mechanism_nodes: "机制节点",
  mechanism_edges: "机制关系",
  mechanism_chains: "机制链",
  mechanism_packet: "机制材料包",
  evidence_edges: "证据关系",
  inference_edges: "推断关系",
  conflict_edges: "冲突关系",
  conflicts: "证据冲突",
  gaps: "待补证据",
  gap_list: "研究空缺清单",
  gap_focus_types: "关注的研究空缺类型",
  not_a_gap: "非空缺判断",
  unresolved_gaps: "未解决问题",
  needs_source: "需要补充的来源",
  validation_needs: "验证需求",
  validation_target: "验证目标",
  research_leads: "研究线索",
  review_questions: "复核问题",
  advisor_review_questions: "需要专家复核的问题",
  audit_scope: "核查范围",
  traceability_matrix: "可追溯关系表",
  missing_records: "缺失记录",
  reproducibility_checklist: "可复现性检查清单",
  quality_report: "质量检查报告",
  verdict: "核查结论",
  next_record_to_inspect: "下一项待核查记录",
  variable_table: "变量表",
  controls_and_baselines: "控制条件与对比基线",
  ablation_or_comparison_matrix: "消融或对比方案",
  analysis_plan: "分析方案",
  stop_conditions: "停止条件",
  confirmation_gates: "确认节点",
  execution_status: "执行状态",
  handoff: "后续交接",
  warnings: "注意事项",
  errors: "问题说明",
  extraction_warnings: "提取提醒",
  design_warnings: "设计提醒",
  synthesis_warnings: "综合提醒",
  generation_warnings: "生成提醒",
  priority: "优先级",
  "OpenAIR 受控试用范围内试用": "仅限在 OpenAIR 受控试用范围内使用",
  "仅限 OpenAIR 受控试用范围内试用；不得导出、再分发或用于未授权外部复用。": "仅限在 OpenAIR 受控试用范围内使用；不得导出、再分发或用于未授权的外部复用。",
  "External retrieval, code execution, filesystem access, or evidence collection without an authorized Skill.": "不包含未经授权的外部检索、代码执行、文件访问或证据采集。",
  "Impersonating a missing specialist or Skill.": "不能替代缺失的专业能力或专家判断。",
  "Claiming external facts or completed actions that are absent from durable inputs.": "不能把现有材料中没有记录的外部事实或已完成操作当作真实结果。",
  "Literature retrieval, evidence extraction, experiments, or final scientific conclusions.": "不包含文献检索、证据提取、实验执行或最终科研结论。",
  "Claiming novelty, field-wide absence, or validated findings from topic-only input.": "不能只根据研究主题声称创新性、领域空缺或已验证发现。",
  "Building the four-stage execution plan, which remains the controller's responsibility.": "不负责生成完整研究执行计划。",
  "Treating candidate papers as a completed literature review or verified research conclusion.": "不能把候选文献直接当作完整综述或已验证结论。",
  "Extracting detailed claim-evidence rows from supplied papers.": "不负责从论文中逐条抽取详细论点与证据。",
  "Claiming comprehensive coverage or novelty without a documented search boundary.": "没有明确检索范围时，不能声称覆盖全面或具有创新性。",
  "Topic-only mechanism invention without source-bounded evidence.": "不能在缺少来源证据时仅凭主题推演机制。",
  "Literature discovery or broad novelty claims.": "不包含文献发现或宽泛创新性判断。",
  "Presenting inferred causal edges as experimentally verified facts.": "不能把推断出的因果关系当作实验已验证事实。",
  "Literature search, retrieval, or source discovery.": "不包含文献搜索、检索或来源发现。",
  "Creating evidence from topic-only input or unreadable material references.": "不能仅凭研究主题或不可读取的材料引用生成证据。",
  "Writing final scientific conclusions, novelty claims, or validation plans.": "不负责撰写最终科研结论、创新性声明或验证方案。",
  "Running experiments, rerunning code, or claiming reproduction from secondary summaries.": "不执行实验或代码复跑，也不能仅凭二手摘要声称完成复现。",
  "Issuing pass or fail verdicts without inspected result records.": "未核查原始结果记录时，不能给出通过或不通过结论。",
  "Inventing raw predictions, logs, seeds, datasets, scripts, or missing metric definitions.": "不能补造预测、日志、随机种子、数据集、脚本或缺失的指标定义。",
  "Executing experiments, training models, collecting data, or consuming compute resources.": "不执行实验、模型训练、数据采集或计算资源消耗。",
  "Claiming that validation succeeded or results exist.": "不能声称验证已经成功或结果已经产生。",
  "Hiding ethics, privacy, feasibility, or resource gates behind a protocol draft.": "不能在方案草案中忽略伦理、隐私、可行性或资源约束。",
  "Topic-only ideation without supplied source coverage.": "没有来源覆盖信息时，不能只凭主题生成研究空缺。",
  "Literature retrieval, broad novelty search, or leaderboard ranking.": "不包含文献检索、宽泛创新性搜索或排行榜比较。",
  "Turning weak notes into publishable gap claims or proposal-ready claims.": "不能把证据薄弱的笔记包装成可发表的空缺结论或可直接用于申报的主张。",
};

export function humanizeResourceContractField(value: string): string {
  const normalized = value.trim();
  if (!normalized) return "";
  return CONTRACT_FIELD_LABELS[normalized] ?? normalized;
}
