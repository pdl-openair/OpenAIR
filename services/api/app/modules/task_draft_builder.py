from __future__ import annotations

import re
from dataclasses import dataclass

from app.modules import pack_config
from app.modules.research_start_planner import (
    ResearchStartPlan,
    build_creation_plan_stages,
    plan_research_start_draft,
)
from app.modules.task_execution_graph import (
    build_creation_plan_stages_from_substages,
)
from app.modules.task_constants import STAGE_ORDER


POLLUTION_PATTERNS = [
    re.compile(r"把\s*(?:任务)?\s*改成(?:我的)?真实任务[:：\s]*", re.IGNORECASE),
    re.compile(r"示例中的科研\s*agent", re.IGNORECASE),
    re.compile(r"\bdemo\b", re.IGNORECASE),
    re.compile(r"\bfixture\b", re.IGNORECASE),
    re.compile(r"\bpreview\b", re.IGNORECASE),
    re.compile(r"\bproof\b", re.IGNORECASE),
    re.compile(r"\badapter\b", re.IGNORECASE),
    re.compile(r"\bmodel\s*=\s*none\b", re.IGNORECASE),
    re.compile(r"\bworkflow_lite\b", re.IGNORECASE),
    re.compile(r"\btask_[a-z0-9_:-]*", re.IGNORECASE),
    re.compile(r"\bartifact_[a-z0-9_:-]*", re.IGNORECASE),
]


DOMAIN_KEYWORDS = {
    "battery": [
        "锂金属电池",
        "固态电解质",
        "界面稳定性",
        "证据链",
        "实验设计",
        "界面反应",
        "循环稳定性",
    ],
    "organoid": [
        "结直肠癌",
        "类器官",
        "EGFR",
        "抑制剂",
        "响应证据",
        "药物响应",
        "生物标志物",
    ],
    "ai_repo": [
        "长上下文",
        "AI 助手",
        "代码仓库",
        "评测基线",
        "复核",
        "任务评测",
        "回归用例",
    ],
}


GENERIC_KEYWORDS = [
    "证据",
    "实验",
    "文献",
    "数据",
    "评测",
    "基线",
    "机制",
    "模型",
    "任务",
    "复核",
    "设计",
    "稳定性",
    "响应",
]


KEY_LITERATURE_TERMS = [
    "关键文献",
    "找文献",
    "检索词",
    "代表论文",
    "绕不开",
    "必读",
    "综述",
    "seed paper",
    "key literature",
    "must-read",
    "representative papers",
    "review",
]


EVIDENCE_ORGANIZING_TERMS = [
    "evidence organizing",
    "evidence table",
    "evidence extraction",
    "direct evidence",
    "supporting evidence",
    "conflict points",
    "证据整理",
    "证据表",
    "直接证据",
    "支持证据",
    "作者推断",
    "冲突点",
    "摘录",
    "材料",
    "来源包",
]


MECHANISM_MAPPING_TERMS = [
    "mechanism chain",
    "mechanism map",
    "causal edge",
    "mechanism dispute",
    "机制链",
    "机制图谱",
    "机制争议",
    "机制解释",
    "因果边",
    "推断边",
    "证据边",
    "冲突解释",
    "分歧",
    "串成",
]


EVIDENCE_EXTRACTION_TERMS = [
    "claim-evidence",
    "evidence extraction",
    "extract evidence",
    "figure caption",
    "result section",
    "证据抽取",
    "抽取证据",
    "结果段",
    "图注",
    "每条结论",
    "对应的证据",
    "支持类型",
]

QUESTION_REFINEMENT_TERMS = [
    "research question",
    "question refinement",
    "researchable question",
    "scope the question",
    "narrow the topic",
    "问题凝练",
    "问题形成",
    "可研究问题",
    "收敛问题",
]


GAP_IDENTIFICATION_TERMS = [
    "research gap",
    "gap analysis",
    "knowledge gap",
    "missing evidence",
    "研究空缺",
    "空缺识别",
    "缺口识别",
    "知识缺口",
]


VALIDATION_DESIGN_TERMS = [
    "validation design",
    "experiment design",
    "variable definition",
    "baseline selection",
    "ablation design",
    "analysis plan",
    "验证方案",
    "实验设计",
    "变量定义",
    "对照条件",
    "基线",
    "消融",
]


RESULT_AUDIT_TERMS = [
    "quality audit",
    "result audit",
    "reliability audit",
    "reproducibility",
    "traceability",
    "质量审计",
    "结果审计",
    "可复现",
    "可追溯",
]


def _is_evidence_organizing_goal(goal: str) -> bool:
    text = goal.lower()
    return any(term.lower() in text for term in EVIDENCE_ORGANIZING_TERMS)


def _is_evidence_extraction_goal(goal: str) -> bool:
    text = goal.lower()
    return any(term.lower() in text for term in EVIDENCE_EXTRACTION_TERMS)


def _is_mechanism_mapping_goal(goal: str) -> bool:
    text = goal.lower()
    return any(term.lower() in text for term in MECHANISM_MAPPING_TERMS)


def _is_question_refinement_goal(goal: str) -> bool:
    text = goal.lower()
    return any(term.lower() in text for term in QUESTION_REFINEMENT_TERMS)


def _is_gap_identification_goal(goal: str) -> bool:
    text = goal.lower()
    return any(term.lower() in text for term in GAP_IDENTIFICATION_TERMS)


def _is_validation_design_goal(goal: str) -> bool:
    text = goal.lower()
    return any(term.lower() in text for term in VALIDATION_DESIGN_TERMS)


def _is_result_audit_goal(goal: str) -> bool:
    text = goal.lower()
    return any(term.lower() in text for term in RESULT_AUDIT_TERMS)


def _is_key_literature_goal(goal: str) -> bool:
    text = goal.lower()
    if _is_evidence_organizing_goal(goal):
        return False
    return any(term.lower() in text for term in KEY_LITERATURE_TERMS) and any(
        signal in text
        for signal in ["文献", "论文", "paper", "literature", "review", "sei", "检索"]
    )


@dataclass(frozen=True)
class ResearchStartDraft:
    title: str
    goal: str
    keywords: list[str]
    stage_labels: list[str]
    missing_info: list[str]
    next_action: str
    boundary: str
    structured: dict
    plan: dict
    markdown: str


def clean_research_input(value: str | None) -> str:
    cleaned = (value or "").strip()
    for pattern in POLLUTION_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"^[：:，,；;。.\s]+", "", cleaned)
    cleaned = re.sub(r"[：:，,；;。.\s]+$", "", cleaned)
    return cleaned.strip()


_TITLE_TOPIC_TERMS = (
    "GraphRAG",
    "RAG",
    "科研文献问答",
    "文献问答",
    "混合检索",
    "类器官",
    "固态电解质",
    "锂金属电池",
    "AI 助手",
    "代码仓库",
)


def _title_purpose(planner_plan: ResearchStartPlan | None) -> str:
    research_type = str(getattr(planner_plan, "researchType", "") or "")
    by_type = {
        "comparative_literature_analysis_start_draft": "文献对比",
        "key_literature_start_draft": "文献研究",
        "validation_design_start_draft": "验证方案",
        "result_audit_start_draft": "结果审计",
        "evidence_extraction_start_draft": "证据整理",
        "mechanism_mapping_start_draft": "机制梳理",
        "gap_identification_start_draft": "空缺识别",
        "question_refinement_start_draft": "问题凝练",
        "research_planning_start_draft": "研究规划",
    }
    return by_type.get(research_type, "研究")

def derive_research_title(
    goal: str,
    fallback: str = "待确认研究任务",
    *,
    planner_plan: ResearchStartPlan | None = None,
) -> str:
    """Build compact presentation metadata without scientific reclassification."""
    cleaned = clean_research_input(goal)
    if not cleaned:
        return fallback

    display = re.sub(
        r"^(请|帮我|帮忙|需要|想要|我想|调研|研究|分析|比较|对比|评估|设计|探索|梳理|调查)\s*",
        "",
        cleaned,
    ).strip() or cleaned
    if len(display) <= 20:
        return display

    subject = re.split(
        r"[，,。；;：:!?！？\s]+",
        display,
        maxsplit=1,
    )[0]
    subject = re.sub(r"^(关于|针对|围绕)", "", subject).strip()
    subject = subject[:15].strip() or "研究问题"
    title = f"{subject}{_title_purpose(planner_plan)}"
    title = re.sub(r"\s+", " ", title).strip(" ，,。；;：:")
    return title[:20] if title else fallback

def _keywords_for(planner_plan: ResearchStartPlan) -> list[str]:
    """Return only formally supplied keywords; current plan contract has none."""
    _ = planner_plan
    return []

def _stage_labels(
    research_type: str,
    *,
    planner_plan: ResearchStartPlan,
) -> list[str]:
    if planner_plan.stageDescriptions:
        return list(planner_plan.stageDescriptions.values())

    by_type = {
        "question_refinement_start_draft": [
            "确认研究问题边界",
            "形成问题候选",
            "确认验证与证据需求",
            "确定下一步入口",
        ],
        "gap_identification_start_draft": [
            "确认材料与判定标准",
            "整理证据与未覆盖问题",
            "形成空缺清单",
            "确认补证据优先级",
        ],
        "key_literature_start_draft": [
            "确认检索边界",
            "准备种子来源",
            "确认筛选标准",
            "形成阅读候选",
        ],
        "validation_design_start_draft": [
            "确认验证目标与约束",
            "定义变量与对照",
            "设计基线与分析计划",
            "确认资源和停止条件",
        ],
        "result_audit_start_draft": [
            "确认审计对象和材料",
            "检查追溯与一致性",
            "检查分析与复现边界",
            "形成修复建议",
        ],
        "evidence_extraction_start_draft": [
            "确认材料和 claim 目标",
            "定义证据字段",
            "抽取证据并区分推断",
            "标注冲突和缺口",
        ],
        "mechanism_mapping_start_draft": [
            "确认机制问题与证据范围",
            "整理证据/推断/冲突边",
            "标注证据不足",
            "形成验证建议",
        ],
        "evidence_organizing_start_draft": [
            "确认材料与证据目标",
            "设计证据表结构",
            "整理冲突和缺口",
            "确认后续处理顺序",
        ],
        "research_planning_start_draft": [
            "确认研究目标和已有材料",
            "拆解阶段和产物",
            "确认输入、风险和人工门",
            "确认后续执行入口",
        ],
    }
    return list(
        by_type.get(
            research_type,
            [
                "确认研究目标和边界",
                "拆解阶段和产物",
                "确认输入与风险",
                "确认后续执行入口",
            ],
        )
    )

def _has_inline_materials(goal: str) -> bool:
    text = goal.lower()
    section_pattern = re.compile(
        r"(?:材料|摘录|论文摘录|实验记录)\s*[a-z0-9一二三四五六七八九十]*\s*[：:]",
        re.IGNORECASE,
    )
    if section_pattern.search(goal):
        return True
    return any(marker in text for marker in ["r=", "p<", "cv>", "auc", "recist"])


def _missing_info(planner_plan: ResearchStartPlan) -> list[str]:
    return list(planner_plan.understanding.missingInputs)

def _next_action(research_type: str) -> str:
    by_type = {
        "comparative_literature_analysis_start_draft": "先确认正式指定来源、对比维度和证据边界，再进入证据抽取与对比综合。",
        "validation_design_start_draft": "先确认变量、对照/基线、资源约束和停止条件，再进入验证执行。",
        "question_refinement_start_draft": "先确认研究对象、变量边界、排除范围和可验证性，再选择下一步科研能力。",
        "gap_identification_start_draft": "先确认上游材料和空缺判定标准，再进入有证据约束的空缺识别。",
        "key_literature_start_draft": "先确认检索边界、种子来源和筛选标准，再执行真实检索。",
        "result_audit_start_draft": "先补齐并确认审计记录和判定标准，再进入可靠性审计。",
        "evidence_extraction_start_draft": "先确认可读材料、claim 目标和引用位置，再进入证据抽取。",
        "mechanism_mapping_start_draft": "先确认证据范围和候选机制，再整理机制、推断、冲突和缺口。",
        "evidence_organizing_start_draft": "先确认待整理材料、证据字段和引用边界，再进入证据整理。",
        "research_planning_start_draft": "先补充或确认研究目标、已有材料、期望产物和约束，再决定下一步科研能力。",
    }
    return by_type.get(
        research_type,
        "先确认研究目标、已有材料和期望产物，再决定下一步科研能力。",
    )

def _stage_description_map(stage_labels: list[str]) -> dict[str, str]:
    return {
        stage_key: stage_labels[idx]
        for idx, stage_key in enumerate(STAGE_ORDER)
        if idx < len(stage_labels) and stage_labels[idx]
    }


def _has_any(goal: str, terms: list[str]) -> bool:
    text = goal.lower()
    return any(term.lower() in text for term in terms)


def _planned_sdr_skill_ids(goal: str, research_type: str) -> list[str]:
    if research_type == "key_literature_start_draft" or _is_key_literature_goal(goal):
        return ["intent_understanding", "literature_search"]

    if research_type == "evidence_extraction_start_draft" or _is_evidence_extraction_goal(goal):
        return ["intent_understanding", "evidence_extraction"]

    if research_type == "mechanism_mapping_start_draft" or _is_mechanism_mapping_goal(goal):
        return ["intent_understanding", "evidence_extraction", "mechanism_explanation", "gap_identification"]

    if research_type == "research_planning_start_draft":
        return ["intent_understanding", "question_refinement", "task_breakdown"]

    if research_type == "question_refinement_start_draft" or _is_question_refinement_goal(goal):
        return ["intent_understanding", "question_refinement"]

    if research_type == "gap_identification_start_draft" or _is_gap_identification_goal(goal):
        return ["intent_understanding", "evidence_extraction", "gap_identification"]

    if research_type == "validation_design_start_draft" or _is_validation_design_goal(goal):
        return [
            "intent_understanding",
            "question_refinement",
            "hypothesis_formation",
            "variable_definition",
            "baseline_selection",
            "ablation_design",
            "analysis_planning",
        ]

    if research_type == "result_audit_start_draft" or _is_result_audit_goal(goal):
        return ["intent_understanding", "evidence_extraction", "result_acceptance"]

    if _is_evidence_organizing_goal(goal):
        skills = ["intent_understanding", "evidence_extraction"]
        if _has_any(goal, ["gap", "missing", "缺口", "边界", "冲突"]):
            skills.append("gap_identification")
        if _has_any(goal, ["experiment", "validation", "plan", "实验", "验证", "计划", "下一步"]):
            skills.append("task_breakdown")
        return list(dict.fromkeys(skills))

    skills: list[str] = ["intent_understanding"]
    if _has_any(goal, ["literature", "paper", "review", "文献", "论文"]):
        skills.append("literature_search")
    if _has_any(goal, ["gap", "missing", "缺口", "边界"]):
        skills.append("gap_identification")
    if _has_any(goal, ["evidence", "support", "source packet", "证据", "支持", "来源包", "材料", "摘录"]):
        skills.append("evidence_extraction")
    if _has_any(goal, ["experiment", "validation", "plan", "实验", "验证", "计划"]):
        skills.append("task_breakdown")

    if len(skills) == 1:
        skills.extend(["question_refinement", "task_breakdown"])
    return list(dict.fromkeys(skills))


def _default_pack_start_substages(pack: str) -> list[dict]:
    selected: list[dict] = []
    for stage_key in STAGE_ORDER:
        entries = [
            entry
            for entry in pack_config.load_stage_substage_map(pack=pack).get(stage_key, [])
            if entry.get("enabled", True)
        ]
        if entries:
            selected.append({"substage": entries[0]["key"]})
        if len(selected) >= 3:
            break
    return selected


def _plan(
    goal: str,
    stage_labels: list[str],
    missing_info: list[str],
    *,
    pack: str = "sdr",
    research_type: str = "research_start_draft",
    planner_plan: ResearchStartPlan | None = None,
) -> dict:
    # Resolve pack; unknown / missing packs fall back to sdr so the draft is
    # always well-formed. The chosen pack drives both stageCatalog shape and
    # the recorded skillPack — letting the user pick "materials" at task
    # creation makes the initial workflow use materials substages instead of
    # the sdr default (which is what surfaced constraints_criteria in a
    # materials task).
    available_packs = {p.get("key") for p in pack_config.list_available_packs()}
    resolved_pack = pack if pack in available_packs else "sdr"
    stage_descriptions = _stage_description_map(stage_labels)
    if resolved_pack == "sdr" and planner_plan is not None:
        stages = build_creation_plan_stages(planner_plan, pack=resolved_pack)
        research_type = planner_plan.researchType
    else:
        stages = build_creation_plan_stages_from_substages(
            _default_pack_start_substages(resolved_pack),
            pack=resolved_pack,
            stage_descriptions=stage_descriptions,
        )

    return {
        "goalSummary": goal,
        "domain": "research_task",
        "skillPack": resolved_pack,
        "researchType": research_type,
        "planningSource": planner_plan.planningSource if planner_plan else "deterministic_fallback",
        "planningAudit": dict(planner_plan.planningAudit) if planner_plan else {},
        "planningReason": planner_plan.understanding.planningRationale if planner_plan else None,
        "understanding": planner_plan.understanding.to_dict() if planner_plan else None,
        "selectedCapabilities": [cap.to_dict() for cap in planner_plan.selectedCapabilities] if planner_plan else [],
        "clarifications": missing_info,
        "stages": stages,
        "readyToConfirm": True,
    }


def _markdown(
    goal: str,
    keywords: list[str],
    stage_labels: list[str],
    missing_info: list[str],
    next_action: str,
    boundary: str,
    planner_plan: ResearchStartPlan,
) -> str:
    planned_outputs = planner_plan.understanding.targetOutputs
    evidence_boundaries = list(
        dict.fromkeys(
            [boundary, *planner_plan.understanding.evidenceBoundaries]
        )
    )
    missing_items = (
        "\n".join(f"- {item}" for item in missing_info)
        if missing_info
        else "- 暂无正式缺失项；仍需确认计划边界后再执行。"
    )
    keyword_items = (
        "\n".join(f"- {keyword}" for keyword in keywords)
        if keywords
        else "- 暂无正式关键词；当前代码不从自由文本自行推断。"
    )
    return "\n".join(
        [
            "# 研究起点草案",
            "",
            "这是一份创建任务后的起点草案，用来确认系统对输入的理解；它不是最终科研结论。",
            "",
            "## 我理解的研究问题",
            goal,
            "",
            "## 已识别的关键词",
            keyword_items,
            "",
            "## 计划产物",
            "\n".join(f"- {item}" for item in planned_outputs),
            "",
            "## 建议拆解的阶段",
            "\n".join(
                f"{i}. {label}"
                for i, label in enumerate(stage_labels, start=1)
            ),
            "",
            "## 已有输入",
            f"- 用户一句话输入：{goal}",
            "",
            "## 仍缺的信息",
            missing_items,
            "",
            "## 当前不能引用的边界",
            "\n".join(f"- {item}" for item in evidence_boundaries),
            "",
            "## 下一步推荐动作",
            f"- {next_action}",
        ]
    )

def _structured_research_start(
    *,
    goal: str,
    keywords: list[str],
    missing_info: list[str],
    next_action: str,
    boundary: str,
    source_page: str,
    planner_plan: ResearchStartPlan,
) -> dict:
    existing_materials = [f"用户一句话输入：{goal}"]
    risks = [
        "当前尚未读取外部论文、数据或代码。",
        "未补齐来源前，草案不能作为可引用科研结论。",
    ]
    return {
        "research_goal": goal,
        "comparison_targets": keywords[:4],
        "existing_materials": existing_materials,
        "missing_materials": missing_info,
        "non_citable_boundaries": [boundary],
        "recommended_next_actions": [next_action],
        "risks": risks,
        "user_confirmation_status": {
            "status": "unconfirmed",
            "confirmed_items": [],
            "missing_materials_decision": None,
            "limitations_accepted": False,
            "confirmed_at": None,
        },
        "accepted_limitations": [],
        "source_or_run_refs": [
            {
                "type": "planner",
                "source": planner_plan.planningSource,
                "page": source_page,
                "run_ref": None,
            }
        ],
    }

def build_research_start_draft(
    goal_input: str | None,
    *,
    title_input: str | None = None,
    skill_pack: str | None = None,
    planning_selection: object | None = None,
    source_page: str = "home",
    selected_hint: str | None = None,
    planner_plan_override: ResearchStartPlan | None = None,
    missing_info_override: list[str] | None = None,
) -> ResearchStartDraft:
    goal = (
        clean_research_input(goal_input)
        or clean_research_input(title_input)
        or "待补充研究问题"
    )

    planner_plan = planner_plan_override or plan_research_start_draft(
        raw_input=goal,
        planning_selection=planning_selection,
        source_page=source_page,
        selected_hint=selected_hint,
    )

    keywords = _keywords_for(planner_plan)
    title = (
        clean_research_input(title_input)
        if title_input
        else derive_research_title(goal, planner_plan=planner_plan)
    )
    stage_labels = _stage_labels(
        planner_plan.researchType,
        planner_plan=planner_plan,
    )

    override_items = [
        str(item).strip()
        for item in (missing_info_override or [])
        if str(item).strip()
    ]
    missing_info = override_items if override_items else _missing_info(planner_plan)
    next_action = _next_action(planner_plan.researchType)
    boundary = (
        "当前尚未读取外部论文、数据或代码，"
        "不能把本草案作为可引用科研结论。"
    )

    plan = _plan(
        goal,
        stage_labels,
        missing_info,
        pack=skill_pack or "sdr",
        research_type=planner_plan.researchType,
        planner_plan=planner_plan,
    )
    markdown = _markdown(
        goal,
        keywords,
        stage_labels,
        missing_info,
        next_action,
        boundary,
        planner_plan,
    )
    structured = _structured_research_start(
        goal=goal,
        keywords=keywords,
        missing_info=missing_info,
        next_action=next_action,
        boundary=boundary,
        source_page=source_page,
        planner_plan=planner_plan,
    )
    return ResearchStartDraft(
        title=title,
        goal=goal,
        keywords=keywords,
        stage_labels=stage_labels,
        missing_info=missing_info,
        next_action=next_action,
        boundary=boundary,
        structured=structured,
        plan=plan,
        markdown=markdown,
    )
