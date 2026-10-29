"""Draft planning contract for research-start task creation.

The planning selection decides where the user should go. This module turns that
decision plus the raw request into a validated draft understanding and a
candidate execution graph.
"""

from __future__ import annotations

from app.execution.research_start_processing import (
    PlannerSemanticInputError,
    read_provider_content,
)

from app.modules.research_planning_stage_framework import research_stage_framework

import json
import os
import re
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal, Mapping, Sequence

import httpx
from pydantic import ValidationError

from app.config import settings
from app.modules.agent_registry import load_agent_registry
from app.modules.provider_registry import ProviderRegistryError, resolve_model_binding
from app.modules.provider_transport_evidence import execute_observed_openai_compatible_json
from app.modules.research_start_planner_budget import research_start_planner_task_budget_seconds
from app.modules.research_design_freeform_semantic_provider import (
    FreeformSemanticProviderConfig,
    prepare_freeform_research_design_attempt,
)
from app.modules.research_design_workflow_semantic_provider import (
    PreparedHybridWorkflowPlannerAttempt,
    WorkflowSemanticProviderConfig,
    prepare_hybrid_workflow_planner_attempt,
)

from app.execution.llm_structured_runner import (
    _build_responses_payload,
    _extract_responses_output_text,
    _openai_model_fallback_chain,
    _parse_llm_json_content,
    _prepare_payload_for_http,
    apply_openai_compatible_thinking,
    extract_final_chat_content,
)
from app.execution.structured_output_provider_adapter import (
    apply_provider_schema_transport,
    structured_response_format,
)
from app.execution.research_start_provider_adapter import (
    PreparedPlannerAttempt,
    ResearchStartHttpTransport,
    execute_prepared_attempt,
)
from app.modules.pvd_deterministic_design import (
    PVD_WORKFLOW_ID,
    PreparedDeterministicPvdPlannerAttempt,
    prepare_deterministic_pvd_planner_attempt,
)
from app.modules.materials_deterministic_design import (
    MATERIALS_FULL_DISCOVERY_WORKFLOW_ID,
    PreparedDeterministicMaterialsPlannerAttempt,
    prepare_deterministic_materials_planner_attempt,
)
from app.execution.durable_skill_runtime import list_registered_durable_skills
from app.modules.task_execution_graph import (
    ExecutionGraphError,
    build_creation_plan_stages_from_substages,
    resolve_skill_substage_items,
)


GOAL_STAGE_LABEL = "\u95ee\u9898\u754c\u5b9a"


def _with_goal_framing(stage_descriptions: dict[str, str], raw_input: str) -> dict[str, str]:
    focus = _clean(raw_input)
    focus = re.sub(r"^(?:我想研究|我想|希望研究|请帮我|帮我|请)", "", focus).strip()
    focus = re.split(r"[。！？；;，,]", focus, maxsplit=1)[0].strip()
    if not focus:
        return stage_descriptions
    descriptions = dict(stage_descriptions)
    current = descriptions.get("problem_modeling", "")
    descriptions["problem_modeling"] = f"{focus[:36]}{GOAL_STAGE_LABEL}: {current}" if current else f"{focus[:36]}{GOAL_STAGE_LABEL}"
    return descriptions


PlanningSource = Literal["llm", "deterministic_fallback"]


class ResearchStartPlannerError(ValueError):
    """Raised when a draft plan cannot be made into a real execution graph."""


class ResearchStartPlannerUnavailable(RuntimeError):
    """Raised when the live draft planner cannot produce a valid plan."""


@dataclass(frozen=True)
class ResearchStartUnderstanding:
    taskType: str
    userIntent: str
    materialSummary: str
    targetOutputs: list[str] = field(default_factory=list)
    evidenceBoundaries: list[str] = field(default_factory=list)
    missingInputs: list[str] = field(default_factory=list)
    riskNotes: list[str] = field(default_factory=list)
    planningRationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlannedCapability:
    kind: Literal["skill", "agent", "workflow"]
    id: str
    skillIds: list[str] = field(default_factory=list)
    substages: list[str] = field(default_factory=list)
    rationale: str = ""
    expectedArtifactTypes: list[str] = field(default_factory=list)
    confirmationRequired: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResearchStartPlan:
    planningSource: PlanningSource
    researchType: str
    understanding: ResearchStartUnderstanding
    selectedCapabilities: list[PlannedCapability]
    stageDescriptions: dict[str, str] = field(default_factory=dict)
    planningAudit: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "planningSource": self.planningSource,
            "researchType": self.researchType,
            "understanding": self.understanding.to_dict(),
            "selectedCapabilities": [cap.to_dict() for cap in self.selectedCapabilities],
            "stageDescriptions": dict(self.stageDescriptions),
            "planningAudit": dict(self.planningAudit),
        }


def hydrate_research_start_plan(payload: Mapping[str, Any]) -> ResearchStartPlan:
    """Recreate a persisted :class:`ResearchStartPlan` from ``to_dict()`` output."""

    def require_mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ResearchStartPlannerError(f"{field_name} must be an object")
        return value

    def require_exact_keys(value: Mapping[str, Any], *, field_name: str, keys: set[str]) -> None:
        if set(value) != keys:
            raise ResearchStartPlannerError(f"{field_name} has an invalid shape")

    def require_string(value: Any, *, field_name: str) -> str:
        if not isinstance(value, str):
            raise ResearchStartPlannerError(f"{field_name} must be a string")
        return value

    def require_string_list(value: Any, *, field_name: str) -> list[str]:
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ResearchStartPlannerError(f"{field_name} must be a list of strings")
        return list(value)

    def require_string_mapping(value: Any, *, field_name: str) -> dict[str, str]:
        mapping = require_mapping(value, field_name=field_name)
        if not all(isinstance(key, str) and isinstance(item, str) for key, item in mapping.items()):
            raise ResearchStartPlannerError(f"{field_name} must map strings to strings")
        return dict(mapping)

    plan = require_mapping(payload, field_name="plan")
    require_exact_keys(
        plan,
        field_name="plan",
        keys={"planningSource", "researchType", "understanding", "selectedCapabilities", "stageDescriptions", "planningAudit"},
    )
    planning_source = require_string(plan["planningSource"], field_name="planningSource")
    if planning_source not in {"llm", "deterministic_fallback"}:
        raise ResearchStartPlannerError(f"unsupported planningSource: {planning_source}")

    understanding_raw = require_mapping(plan["understanding"], field_name="understanding")
    require_exact_keys(
        understanding_raw,
        field_name="understanding",
        keys={
            "taskType",
            "userIntent",
            "materialSummary",
            "targetOutputs",
            "evidenceBoundaries",
            "missingInputs",
            "riskNotes",
            "planningRationale",
        },
    )
    understanding = ResearchStartUnderstanding(
        taskType=require_string(understanding_raw["taskType"], field_name="understanding.taskType"),
        userIntent=require_string(understanding_raw["userIntent"], field_name="understanding.userIntent"),
        materialSummary=require_string(understanding_raw["materialSummary"], field_name="understanding.materialSummary"),
        targetOutputs=require_string_list(understanding_raw["targetOutputs"], field_name="understanding.targetOutputs"),
        evidenceBoundaries=require_string_list(
            understanding_raw["evidenceBoundaries"], field_name="understanding.evidenceBoundaries"
        ),
        missingInputs=require_string_list(understanding_raw["missingInputs"], field_name="understanding.missingInputs"),
        riskNotes=require_string_list(understanding_raw["riskNotes"], field_name="understanding.riskNotes"),
        planningRationale=require_string(understanding_raw["planningRationale"], field_name="understanding.planningRationale"),
    )

    raw_capabilities = plan["selectedCapabilities"]
    if not isinstance(raw_capabilities, list):
        raise ResearchStartPlannerError("selectedCapabilities must be a list")
    capabilities: list[PlannedCapability] = []
    for index, raw_capability in enumerate(raw_capabilities):
        capability = require_mapping(raw_capability, field_name=f"selectedCapabilities[{index}]")
        require_exact_keys(
            capability,
            field_name=f"selectedCapabilities[{index}]",
            keys={
                "kind",
                "id",
                "skillIds",
                "substages",
                "rationale",
                "expectedArtifactTypes",
                "confirmationRequired",
            },
        )
        kind = require_string(capability["kind"], field_name=f"selectedCapabilities[{index}].kind")
        if kind not in {"skill", "agent", "workflow"}:
            raise ResearchStartPlannerError(f"unsupported capability kind: {kind}")
        confirmation_required = capability["confirmationRequired"]
        if not isinstance(confirmation_required, bool):
            raise ResearchStartPlannerError(f"selectedCapabilities[{index}].confirmationRequired must be boolean")
        capabilities.append(
            PlannedCapability(
                kind=kind,  # type: ignore[arg-type]
                id=require_string(capability["id"], field_name=f"selectedCapabilities[{index}].id"),
                skillIds=require_string_list(capability["skillIds"], field_name=f"selectedCapabilities[{index}].skillIds"),
                substages=require_string_list(capability["substages"], field_name=f"selectedCapabilities[{index}].substages"),
                rationale=require_string(capability["rationale"], field_name=f"selectedCapabilities[{index}].rationale"),
                expectedArtifactTypes=require_string_list(
                    capability["expectedArtifactTypes"],
                    field_name=f"selectedCapabilities[{index}].expectedArtifactTypes",
                ),
                confirmationRequired=confirmation_required,
            )
        )

    return ResearchStartPlan(
        planningSource=planning_source,  # type: ignore[arg-type]
        researchType=require_string(plan["researchType"], field_name="researchType"),
        understanding=understanding,
        selectedCapabilities=capabilities,
        stageDescriptions=require_string_mapping(plan["stageDescriptions"], field_name="stageDescriptions"),
        planningAudit=require_string_mapping(plan["planningAudit"], field_name="planningAudit"),
    )


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _lower(value: str | None) -> str:
    return _clean(value).lower()


def _has_any(value: str, terms: list[str]) -> bool:
    text = _lower(value)
    return any(term.lower() in text for term in terms)


def _planning_selection_reason(selection: Any | None) -> str:
    if selection is None:
        return ""
    if isinstance(selection, Mapping):
        return str(selection.get("reason") or "")
    return str(getattr(selection, "reason", "") or "")


def _planning_selection_payload(selection: Any | None) -> dict[str, str]:
    if selection is None:
        return {}
    if isinstance(selection, Mapping):
        workflow_id = str(selection.get("workflow_id") or selection.get("workflowId") or "")
    else:
        workflow_id = str(getattr(selection, "workflow_id", "") or "")
    payload = {"selectionReason": _planning_selection_reason(selection)}
    if workflow_id:
        payload["workflowId"] = workflow_id
    return payload


KEY_LITERATURE_HINTS = {"key_literature_positioning", "agent_literature_research_v0_3", "skill_category_find_sources_v0_3", "skill_category_screen_priorities_v0_3"}
EVIDENCE_EXTRACTION_HINTS = {"agent_evidence_extraction_v0_3", "skill_category_organize_evidence_v0_3"}
MECHANISM_MAPPING_HINTS = {"exp_evidence_mechanism_chain_v0_2", "skill_category_mechanism_map_v0_3"}
RESEARCH_PLANNING_HINTS = {"agent_work_plan_v0_3", "exp_research_task_workflow_planning_v0_2", "skill_category_manage_progress_v0_3"}
QUESTION_REFINEMENT_HINTS = {"agent_question_refinement_v0_3"}
GAP_IDENTIFICATION_HINTS = {"agent_gap_identification_v0_3"}
VALIDATION_DESIGN_HINTS = {"agent_validation_design_v0_3"}
RESULT_AUDIT_HINTS = {"agent_result_audit_v0_3"}


def _is_structured_literature_comparison(raw_input: str) -> bool:
    text = _clean(raw_input)
    if len(text) < 50:
        return False
    lower = text.lower()
    has_literature = _has_any(text, ["paper", "papers", "literature", "论文", "文献"])
    has_compare = _has_any(
        text,
        ["compare", "comparison", "versus", " vs ", "differences", "比较", "对比", "差异", "异同"],
    )
    has_structured_output = _has_any(
        text,
        [
            "table",
            "matrix",
            "structured",
            "scenario",
            "workload",
            "fault tolerance",
            "scheduling",
            "结构化",
            "对比表",
            "表格",
            "矩阵",
            "场景",
            "工作负载",
            "容错",
            "调度",
            "数据复用",
        ],
    )
    has_named_source = (
        ":" in text
        or "（" in text
        or "(" in text
        or any(token in lower for token in ["osdi", "hotcloud", "sigmod", "sosp", "vldb"])
    )
    return has_literature and has_compare and (has_structured_output or has_named_source)


def _is_evidence_extraction(raw_input: str) -> bool:
    return _has_any(
        raw_input,
        [
            "claim-evidence",
            "claim evidence",
            "evidence extraction",
            "extract evidence",
            "figure caption",
            "result section",
            "support type",
            "evidence item",
            "证据抽取",
            "抽取证据",
            "结果段",
            "图注",
            "每条结论",
            "对应的证据",
            "支持类型",
            "直接证据",
            "间接证据",
        ],
    )


def _is_evidence_organizing(raw_input: str) -> bool:
    return _has_any(
        raw_input,
        [
            "evidence organizing",
            "evidence table",
            "direct evidence",
            "author inference",
            "conflict points",
            "paper excerpts",
            "experiment notes",
            "evidence chain",
            "证据整理",
            "证据链",
            "证据表",
            "直接证据",
            "作者推断",
            "冲突点",
            "摘录",
            "实验记录",
            "来源包",
        ],
    )


def _is_mechanism_mapping(raw_input: str) -> bool:
    return _has_any(
        raw_input,
        [
            "mechanism chain",
            "mechanism map",
            "causal edge",
            "causal chain",
            "alternative explanation",
            "mechanistic dispute",
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
            "界面稳定机制",
        ],
    )


def _is_question_refinement(raw_input: str) -> bool:
    return _has_any(
        raw_input,
        [
            "research question",
            "question refinement",
            "researchable question",
            "scope the question",
            "narrow the topic",
            "问题凝练",
            "问题形成",
            "可研究问题",
            "收敛问题",
            "研究问题",
        ],
    )


def _is_gap_identification(raw_input: str) -> bool:
    return _has_any(
        raw_input,
        [
            "research gap",
            "gap analysis",
            "knowledge gap",
            "missing evidence",
            "unresolved question",
            "研究空缺",
            "空缺识别",
            "缺口识别",
            "知识缺口",
            "未解决问题",
        ],
    )


def _is_validation_design(raw_input: str) -> bool:
    if _has_any(
        raw_input,
        [
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
            "分析计划",
        ],
    ):
        return True

    # A research plan can be a computational or wet-lab design even when the
    # user does not use the product's exact "validation design" vocabulary.
    # Require an execution context plus concrete design dimensions so a broad
    # research question is not promoted merely because it mentions planning.
    text = _clean(raw_input)
    if len(text) < 80:
        return False
    has_plan = _has_any(text, ["plan", "planning", "design", "matrix", "计划", "规划", "设计", "矩阵"])
    has_execution_context = _has_any(
        text,
        [
            "experiment",
            "experimental",
            "dft",
            "simulation",
            "computation",
            "实验",
            "计算",
            "表征",
            "电化学",
        ],
    )
    has_design_dimension = _has_any(
        text,
        [
            "variable",
            "parameter",
            "setting",
            "condition",
            "convergence",
            "control",
            "replicate",
            "变量",
            "参数",
            "设置",
            "条件",
            "收敛",
            "对照",
            "重复",
            "配方",
            "工艺",
            "载量",
        ],
    )
    return has_plan and has_execution_context and has_design_dimension


def _is_result_audit(raw_input: str) -> bool:
    return _has_any(
        raw_input,
        [
            "quality audit",
            "result audit",
            "reliability audit",
            "reproducibility",
            "traceability",
            "结果可靠性",
            "质量审计",
            "结果审计",
            "可复现",
            "可追溯",
            "可信度",
        ],
    )


def _material_summary(task_type: str) -> str:
    summaries = {
        "comparative_literature_analysis": "正式路由已确定为结构化文献对比任务；具体来源和对比维度由正式语义规划结果提供。",
        "question_refinement": "正式路由已确定为研究问题凝练任务；后续由专业智能体收敛对象、变量边界和可验证性。",
        "gap_identification": "正式路由已确定为研究空缺识别任务；上游材料和空缺判定依据仍需按证据合同确认。",
        "validation_design": "正式路由已确定为验证方案设计任务；变量、对照、基线、分析计划和资源约束需在确认后完善。",
        "result_audit": "正式路由已确定为结果可靠性审计任务；结果、数据、脚本、日志和证据链需按审计合同提供。",
        "evidence_extraction": "正式路由已确定为证据抽取任务；可读材料、claim 目标和引用位置需按证据合同确认。",
        "mechanism_mapping": "正式路由已确定为机制争议映射任务；机制边、推断边、冲突边和证据边界需由专业智能体处理。",
        "evidence_organizing": "正式路由已确定为证据整理任务；材料、证据字段、冲突和缺口将在确认后结构化。",
        "key_literature_positioning": "正式路由已确定为关键文献定位任务；检索边界、种子来源和筛选标准由专业智能体处理。",
        "research_planning": "当前没有更具体的正式语义路由；仅生成通用研究启动草案，不从自由文本猜测科研类型。",
    }
    return summaries.get(
        task_type,
        "当前只形成结构安全的研究启动草案；具体科研语义等待正式 workflow selection、Planner 或专业智能体确认。",
    )

def _evidence_boundaries(task_type: str) -> list[str]:
    boundaries = [
        "确认前不执行真实检索、读取外部论文、运行代码或生成最终科研结论"
    ]
    by_type = {
        "comparative_literature_analysis": [
            "每个对比单元必须可追溯到正式指定来源，或明确标记为外部/公共知识。",
            "综合结论与场景建议必须和来源证据边界分开表达。",
        ],
        "evidence_extraction": [
            "每条证据必须指向用户提供的可读材料、摘录、图注或实验记录",
            "作者讨论、模型解释和人工推断必须与直接证据分开标注",
        ],
        "mechanism_mapping": [
            "每条机制边必须能追溯到 evidence ID，证据不足时标为 inference/gap",
            "不能把相关性、表征共现或作者猜测写成确定因果",
        ],
        "evidence_organizing": [
            "直接证据、作者推断、冲突点和缺失材料必须分开标注",
            "没有可引用材料支撑时不能把归纳写成最终结论",
        ],
        "key_literature_positioning": [
            "候选文献排序和检索词只是启动草案，确认前不代表已完成文献检索"
        ],
        "research_planning": [
            "计划草案不代表任务已经执行；每个阶段需要人工确认后才进入后续步骤"
        ],
        "question_refinement": [
            "问题草案必须标出研究对象、变量/对象边界、可验证性和排除范围",
            "不能把尚未检索或尚未验证的背景判断写成已成立的研究结论",
        ],
        "gap_identification": [
            "每条空缺必须绑定上游材料、证据 ID 或明确标为待核验假设",
            "不能从 topic-only 直接生成研究空缺、创新点或论文卖点",
        ],
        "validation_design": [
            "验证方案只定义变量、对照、基线、分析计划和停止条件，不自动执行实验或训练",
            "资源、伦理、安全或数据许可未确认时必须保留为人工确认门",
        ],
        "result_audit": [
            "通过/不通过判定必须基于可追溯结果、数据、脚本、日志或证据链",
            "缺少原始记录时只能输出审计缺口，不能替用户背书结论可信",
        ],
    }
    boundaries.extend(by_type.get(task_type, []))
    return list(dict.fromkeys(boundaries))

def _risk_notes(task_type: str) -> list[str]:
    by_type = {
        "comparative_literature_analysis": [
            "对比结论若脱离正式指定来源和执行条件，容易形成过度综合。",
        ],
        "question_refinement": [
            "问题边界、变量和排除范围未确认时，后续检索和验证可能不可执行。",
        ],
        "gap_identification": [
            "空缺识别必须绑定上游材料，避免把未覆盖内容误写成领域空白。",
        ],
        "validation_design": [
            "缺少对照、基线或预先分析计划时，验证结果可能难以解释。",
        ],
        "result_audit": [
            "缺少原始记录会让审计退化为主观判断。",
        ],
        "evidence_extraction": [
            "题录、摘要或作者讨论不能替代可定位的直接证据。",
        ],
        "mechanism_mapping": [
            "机制解释必须区分证据、推断和冲突，避免把共现写成因果。",
        ],
        "evidence_organizing": [
            "作者推断不能和直接证据混在同一证据等级中。",
        ],
        "key_literature_positioning": [
            "检索边界未由专业智能体确认前，候选范围可能不完整。",
        ],
        "research_planning": [
            "当前只有通用结构草案；没有正式语义 Authority 时不能据此推断具体科研任务类型。",
        ],
    }
    return list(by_type.get(task_type, []))

def _target_outputs(task_type: str) -> list[str]:
    if task_type == "comparative_literature_analysis":
        return [
            "structured comparison table",
            "dimension-by-dimension evidence matrix",
            "source-grounded synthesis notes",
            "Map/choose scenario recommendations",
            "citation and evidence boundary notes",
        ]
    if task_type == "question_refinement":
        return ["研究问题候选", "对象/变量边界", "可验证性检查", "排除范围", "候选研究空缺（待核验）", "下一步证据需求", "关键文献定位入口"]
    if task_type == "gap_identification":
        return ["研究空缺清单", "G-ID", "证据依据", "空缺类型", "置信度", "下一步补证据建议"]
    if task_type == "validation_design":
        return ["验证方案草案", "变量表", "对照/基线", "消融矩阵", "分析计划", "停止条件"]
    if task_type == "result_audit":
        return ["质量审计报告", "追溯完整性", "数据一致性", "分析合规性", "可复现性", "修复建议"]
    if task_type == "evidence_extraction":
        return ["claim-evidence 表", "直接/间接/推断标签", "引用定位", "冲突点", "缺失材料"]
    if task_type == "mechanism_mapping":
        return ["机制链草图", "证据边", "推断边", "冲突边", "关键缺口", "下一轮验证建议"]
    if task_type == "evidence_organizing":
        return ["证据表", "冲突点清单", "缺失材料清单", "证据边界说明"]
    if task_type == "key_literature_positioning":
        return ["检索边界草案", "检索词方向", "候选来源筛选标准", "阅读优先级"]
    if task_type == "research_planning":
        return ["阶段计划", "输入清单", "产物定义", "确认点", "风险/缺口", "停止条件"]
    return ["启动草案", "阶段路径", "待确认信息", "下一步计划"]


def _missing_inputs(task_type: str) -> list[str]:
    by_type = {
        "comparative_literature_analysis": [
            "正式指定的来源身份与可引用边界",
            "正式确认的对比维度与交付格式",
        ],
        "question_refinement": [
            "研究对象或领域",
            "已有关键词/导师反馈/种子材料",
            "想排除的范围",
            "可接受的验证方式",
        ],
        "gap_identification": [
            "已有综述或文献笔记",
            "候选来源包或证据表",
            "空缺判定标准",
            "需要优先关注的空缺类型",
        ],
        "validation_design": [
            "待验证假设或问题",
            "可操纵变量和测量指标",
            "候选对照/基线",
            "资源、样本、时间或伦理约束",
        ],
        "result_audit": [
            "结果表、图表或结论草稿",
            "原始数据/实验记录/运行日志",
            "分析脚本或统计方法",
            "证据链或引用定位",
        ],
        "evidence_extraction": [
            "可读论文全文、摘录、图注或实验记录",
            "待核验 claim 或证据目标",
            "引用位置或来源包 ID",
        ],
        "mechanism_mapping": [
            "证据表或证据节点",
            "来源包",
            "候选机制假说或需要比较的解释路线",
            "关键对照或表征条件",
        ],
        "evidence_organizing": [
            "待整理材料或来源包",
            "期望的证据表字段",
            "可引用边界",
        ],
        "key_literature_positioning": [
            "研究对象边界",
            "年份或领域范围",
            "种子论文、DOI 或关键词",
        ],
        "research_planning": [
            "已有材料清单",
            "期望产物和成功标准",
            "时间/资源约束",
            "必须人工确认的节点",
        ],
    }
    return list(
        by_type.get(
            task_type,
            ["研究对象", "已有材料", "期望产物和成功标准"],
        )
    )

def _stage_descriptions(task_type: str) -> dict[str, str]:
    if task_type == "comparative_literature_analysis":
        return {
            "problem_modeling": "Confirm named sources, comparison dimensions, source boundaries, and requested deliverables.",
            "solution_design": "Turn the comparison request into a reviewable evidence matrix and output plan.",
            "execution_validation": "Extract and normalize source-grounded claims before synthesizing differences and scenario fit.",
            "insight_generation": "Produce structured comparison outputs and scenario recommendations with evidence boundaries.",
        }
    if task_type == "question_refinement":
        return {
            "problem_modeling": "收敛研究对象/变量边界与排除范围，并界定关键文献检索入口",
            "solution_design": "把候选研究空缺和下一步证据需求排成可确认的推进路径",
        }
    if task_type == "gap_identification":
        return {
            "problem_modeling": "确认上游材料、空缺判定标准和证据边界",
            "execution_validation": "从材料中标注研究空缺、证据依据、置信度和补证据建议",
        }
    if task_type == "validation_design":
        return {
            "problem_modeling": "确认待验证假设、成功标准和关键约束",
            "solution_design": "设计变量、对照、基线、消融矩阵和预注册分析计划",
            "execution_validation": "保留实验启动前的人工确认门、资源约束和停止条件",
        }
    if task_type == "result_audit":
        return {
            "problem_modeling": "确认审计对象、结果材料和可信度判定标准",
            "execution_validation": "审计追溯完整性、数据一致性、分析合规性和可复现性",
            "insight_generation": "输出修复建议、风险等级和是否可引用的边界",
        }
    if task_type == "evidence_extraction":
        return {
            "problem_modeling": "确认材料范围、claim 目标和可引用边界",
            "execution_validation": "抽取 claim-evidence 条目并标注证据等级、冲突和缺口",
        }
    if task_type == "mechanism_mapping":
        return {
            "problem_modeling": "确认机制问题、证据范围和冲突解释",
            "execution_validation": "把证据整理成机制边、推断边、冲突边和缺口",
            "insight_generation": "提出下一轮验证建议和人工复核点",
        }
    if task_type == "evidence_organizing":
        return {
            "problem_modeling": "确认材料、证据目标和冲突风险",
            "solution_design": "设计证据表字段和整理顺序",
            "execution_validation": "抽取证据并标注推断、冲突和缺口",
        }
    if task_type == "key_literature_positioning":
        return {
            "problem_modeling": "界定研究对象与检索边界",
            "solution_design": "整理检索词、种子论文和来源范围",
        }
    if task_type == "research_planning":
        return {
            "problem_modeling": "界定目标、已有材料和成功标准",
            "solution_design": "拆解阶段、产物、确认点和停止条件",
        }
    return {
        "problem_modeling": "界定研究目标和已有材料",
        "solution_design": "拆解任务路径和确认点",
        "execution_validation": "规划后续验证和产物边界",
    }


def _planned_capabilities(
    task_type: str,
    selected_hint: str | None = None,
) -> list[PlannedCapability]:
    if task_type == "comparative_literature_analysis":
        return [
            PlannedCapability(
                kind="workflow",
                id="comparative_literature_analysis",
                skillIds=[
                    "intent_understanding",
                    "literature_search",
                    "task_breakdown",
                    "evidence_extraction",
                    "result_acceptance",
                ],
                rationale="正式 workflow selection 已确定结构化文献对比任务；代码只组装已声明的执行图。",
                expectedArtifactTypes=[
                    "comparison_table",
                    "evidence_matrix",
                    "source_boundary_notes",
                    "scenario_recommendations",
                ],
            )
        ]

    if task_type == "key_literature_positioning":
        return [
            PlannedCapability(
                kind="agent",
                id="key_literature_positioning",
                skillIds=["intent_understanding", "literature_search"],
                rationale="正式 workflow selection or Hint 已确定关键文献定位任务；确认前不执行真实检索。",
                expectedArtifactTypes=[
                    "search_boundary_draft",
                    "literature_search_strategy",
                ],
            )
        ]

    if task_type == "evidence_extraction":
        return [
            PlannedCapability(
                kind="agent",
                id="paper_evidence_extraction",
                skillIds=["intent_understanding", "evidence_extraction"],
                rationale="正式 workflow selection or Hint 已确定证据抽取任务。",
                expectedArtifactTypes=["evidence_table", "readiness_gap_state"],
            )
        ]

    if task_type == "mechanism_mapping":
        return [
            PlannedCapability(
                kind="agent",
                id="mechanism_dispute_mapping",
                skillIds=[
                    "intent_understanding",
                    "evidence_extraction",
                    "mechanism_explanation",
                    "gap_identification",
                ],
                rationale="正式 workflow selection or Hint 已确定机制争议映射任务。",
                expectedArtifactTypes=[
                    "mechanism_chain",
                    "evidence_table",
                    "gap_list",
                ],
            )
        ]

    if task_type == "question_refinement":
        return [
            PlannedCapability(
                kind="agent",
                id="research_question_refinement",
                skillIds=["intent_understanding", "question_refinement"],
                rationale="正式 workflow selection or Hint 已确定研究问题凝练任务。",
                expectedArtifactTypes=[
                    "question_candidates",
                    "scope_boundary",
                    "verification_readiness",
                ],
            )
        ]

    if task_type == "gap_identification":
        return [
            PlannedCapability(
                kind="agent",
                id="research_gap_identification",
                skillIds=[
                    "intent_understanding",
                    "evidence_extraction",
                    "gap_identification",
                ],
                rationale="正式 workflow selection or Hint 已确定研究空缺识别任务。",
                expectedArtifactTypes=[
                    "gap_list",
                    "evidence_links",
                    "missing_evidence",
                ],
            )
        ]

    if task_type == "validation_design":
        return [
            PlannedCapability(
                kind="agent",
                id="validation_design",
                skillIds=[
                    "intent_understanding",
                    "question_refinement",
                    "hypothesis_formation",
                    "variable_definition",
                    "baseline_selection",
                    "ablation_design",
                    "analysis_planning",
                ],
                rationale="正式 workflow selection or Hint 已确定验证方案设计任务。",
                expectedArtifactTypes=[
                    "validation_plan",
                    "variable_table",
                    "baseline_plan",
                    "analysis_plan",
                ],
            )
        ]

    if task_type == "result_audit":
        return [
            PlannedCapability(
                kind="agent",
                id="result_reliability_audit",
                skillIds=[
                    "intent_understanding",
                    "evidence_extraction",
                    "result_acceptance",
                ],
                rationale="正式 workflow selection or Hint 已确定结果可靠性审计任务。",
                expectedArtifactTypes=[
                    "quality_audit_report",
                    "traceability_gaps",
                    "repair_actions",
                ],
            )
        ]

    if task_type == "evidence_organizing":
        return [
            PlannedCapability(
                kind="workflow",
                id="evidence_review_workflow",
                skillIds=[
                    "intent_understanding",
                    "gap_identification",
                    "task_breakdown",
                    "evidence_extraction",
                ],
                rationale="正式 workflow selection 已确定证据整理工作流；代码只组装固定执行图。",
                expectedArtifactTypes=[
                    "evidence_table",
                    "conflict_list",
                    "missing_materials",
                ],
            )
        ]

    if route_type == "single_agent" and candidate_id:
        raise ResearchStartPlannerError(
            f"agent has no execution metadata mapping: {candidate_id}"
        )

    if task_type == "research_planning":
        return [
            PlannedCapability(
                kind="agent",
                id="research_question_refinement",
                skillIds=["intent_understanding", "question_refinement"],
                rationale="没有更具体的正式语义 Authority；仅生成通用研究规划入口。",
                expectedArtifactTypes=[
                    "question_candidates",
                    "scope_boundary",
                    "verification_readiness",
                ],
            )
        ]

    return [
        PlannedCapability(
            kind="workflow",
            id="research_start_draft",
            skillIds=[
                "intent_understanding",
                "question_refinement",
                "task_breakdown",
            ],
            rationale="只生成通用研究启动草案，不从自由文本推断具体科研类型。",
            expectedArtifactTypes=["workflow_draft"],
        )
    ]

def _task_type(raw_input: str, selected_hint: str | None = None) -> tuple[str, str]:
    expected_from_hint = _expected_task_type_from_hint(selected_hint)
    if expected_from_hint:
        return expected_from_hint, TASK_TYPE_TO_RESEARCH_TYPE[expected_from_hint]

    return "research_planning", "research_planning_start_draft"

TASK_TYPE_TO_RESEARCH_TYPE = {
    "evidence_organizing": "evidence_organizing_start_draft",
    "evidence_extraction": "evidence_extraction_start_draft",
    "mechanism_mapping": "mechanism_mapping_start_draft",
    "key_literature_positioning": "key_literature_start_draft",
    "research_planning": "research_planning_start_draft",
    "question_refinement": "question_refinement_start_draft",
    "gap_identification": "gap_identification_start_draft",
    "validation_design": "validation_design_start_draft",
    "result_audit": "result_audit_start_draft",
    "comparative_literature_analysis": "comparative_literature_analysis_start_draft",
}

PLANNER_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["researchType", "understanding", "selectedCapabilities", "stageDescriptions"],
    "properties": {
        "researchType": {
            "type": "string",
            "enum": list(TASK_TYPE_TO_RESEARCH_TYPE.values()),
        },
        "understanding": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "taskType",
                "userIntent",
                "materialSummary",
                "targetOutputs",
                "evidenceBoundaries",
                "missingInputs",
                "riskNotes",
                "planningRationale",
            ],
            "properties": {
                "taskType": {"type": "string", "enum": list(TASK_TYPE_TO_RESEARCH_TYPE.keys())},
                "userIntent": {"type": "string"},
                "materialSummary": {"type": "string"},
                "targetOutputs": {"type": "array", "items": {"type": "string"}},
                "evidenceBoundaries": {"type": "array", "items": {"type": "string"}},
                "missingInputs": {"type": "array", "items": {"type": "string"}},
                "riskNotes": {"type": "array", "items": {"type": "string"}},
                "planningRationale": {"type": "string"},
            },
        },
        "selectedCapabilities": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "kind",
                    "id",
                    "skillIds",
                    "rationale",
                    "expectedArtifactTypes",
                    "confirmationRequired",
                ],
                "properties": {
                    "kind": {"type": "string", "enum": ["skill", "agent", "workflow"]},
                    "id": {"type": "string"},
                    "skillIds": {"type": "array", "items": {"type": "string"}},
                    "rationale": {"type": "string"},
                    "expectedArtifactTypes": {"type": "array", "items": {"type": "string"}},
                    "confirmationRequired": {"type": "boolean"},
                },
            },
        },
        "stageDescriptions": {
            "type": "object",
            "additionalProperties": {"type": "string"},
        },
    },
}

# The planner composes a starting draft from the system's real specialist agents.
# Each agent maps to concrete sdr skillIds; the left workbench nav and the
# orchestrator's primary/supporting agents are derived from these selections, so a
# richer selection surfaces more of the underlying agent system instead of one thin
# capability. Every skillId here must resolve to an enabled sdr substage.
def _planner_agent_catalog() -> list[dict[str, Any]]:
    """Build planning choices directly from production Agent contracts."""
    catalog: list[dict[str, Any]] = []
    for agent in load_agent_registry().list_agents():
        if agent.agentId == "general_research_agent":
            continue
        skill_ids = list(dict.fromkeys(
            str(mapping.get("substage") or "").strip()
            for mapping in agent.sdr_mappings
            if str(mapping.get("substage") or "").strip()
        ))
        catalog.append({
            "id": agent.agentId,
            "displayName": agent.displayName,
            "kind": "agent",
            "skillIds": skill_ids,
            "useWhen": " ".join(agent.capability),
            "produces": list(agent.outputContract.get("requiredFields") or []),
            "boundary": " ".join(agent.notFor),
        })
    return catalog



# Allowed skillIds the planner may reference; each maps to an enabled sdr substage.

PROHIBITED_PLANNER_CLAIM_PATTERNS = (
    re.compile(r"(?:验证|检索|阅读|实验|计算|测试|分析|测量|表征|运行|研究)\s*(?:已经|已)?完成"),
    re.compile(r"(?:已经|已)(?:检索|阅读|验证|实验|计算|测试|分析|测量|表征|运行)"),
    re.compile(r"\b(?:research|retrieval|experiment|calculation|analysis|validation)\s+(?:is\s+)?completed\b", re.IGNORECASE),
    re.compile(r"\b(?:retrieved|verified|final conclusion)\b", re.IGNORECASE),
)


def research_start_planner_mode() -> str:
    return (os.environ.get("OPENAIR_RESEARCH_START_PLANNER_MODE") or "auto").strip().lower()


def should_use_live_research_start_planner() -> bool:
    mode = research_start_planner_mode()
    if mode in {"off", "fallback", "deterministic", "disabled"}:
        return False
    if (os.environ.get("OPENAIR_ENV") or "").strip().lower() == "test" and mode not in {"live", "required"}:
        return False
    if os.environ.get("PYTEST_CURRENT_TEST") and mode not in {"live", "required"}:
        return False
    return mode in {"", "auto", "live", "required"}


def _live_planner_required() -> bool:
    return research_start_planner_mode() in {"required", "require", "strict"}


def live_research_start_planner_required() -> bool:
    return _live_planner_required()


def _bounded_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw:
        try:
            return max(minimum, min(int(raw), maximum))
        except ValueError:
            pass
    return default


def _bounded_float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name)
    if raw:
        try:
            return max(minimum, min(float(raw), maximum))
        except ValueError:
            pass
    return default


def _planner_provider() -> str:
    return resolve_model_binding("planner", validate=False).provider


def _planner_chat_model() -> str:
    # ResearchDesign is the controller's highest-leverage reasoning boundary.
    # Prefer the dedicated planner model, then the deployment's SOL/controller
    # model. A workflow selection or generic low-cost model may remain as a compatibility
    # fallback, but must not silently outrank the controller model.
    return resolve_model_binding("planner", validate=False).model


def _planner_openai_model() -> str:
    return resolve_model_binding("planner", validate=False).model


def _planner_chat_model_fallback_chain() -> list[str]:
    return [_planner_chat_model()]


def _planner_max_tokens() -> int:
    return _bounded_int_env("OPENAIR_RESEARCH_START_PLANNER_LLM_MAX_TOKENS", 1800, 600, 5000)


def _planner_attempts() -> int:
    return _bounded_int_env("OPENAIR_RESEARCH_START_PLANNER_LLM_ATTEMPTS", 1, 1, 3)


def _planner_temperature() -> float:
    return _bounded_float_env("OPENAIR_RESEARCH_START_PLANNER_LLM_TEMPERATURE", 0.2, 0.0, 1.0)


def research_start_planner_wait_seconds() -> float:
    """Use the Planner-owned budget when joining an optional prewarmed plan.

    The small grace period is only for local hand-off overhead.  This layer must
    not reintroduce a shorter fixed ceiling than the formal Planner budget.
    """
    return research_start_planner_task_budget_seconds() + 5.0


def _freeform_semantic_provider_config() -> FreeformSemanticProviderConfig | None:
    """Resolve the single Provider boundary for freeform scientific planning."""
    try:
        provider = _planner_provider()
    except ProviderRegistryError:
        return None
    if provider == "huiyan_chat_completions":
        api_base = os.environ.get("LLM_PROVIDER_BASE_URL")
        if not api_base or not os.environ.get("LLM_PROVIDER_API_KEY"):
            return None
        model = _planner_chat_model()
        return FreeformSemanticProviderConfig(
            provider=provider,
            model=model,
            endpoint=f"{api_base.rstrip('/')}/v1/chat/completions",
            timeout_seconds=research_start_planner_task_budget_seconds(),
            max_tokens=_planner_max_tokens(),
            temperature=_planner_temperature(),
        )
    if provider == "openai_responses":
        if not os.environ.get("OPENAI_API_KEY"):
            return None
        model = _planner_openai_model()
        return FreeformSemanticProviderConfig(
            provider=provider,
            model=model,
            endpoint=f"{os.environ.get('OPENAI_API_BASE_URL', 'https://api.openai.com').rstrip('/')}/v1/responses",
            timeout_seconds=research_start_planner_task_budget_seconds(),
            max_tokens=_planner_max_tokens(),
            temperature=_planner_temperature(),
        )
    return None


def _workflow_semantic_provider_config() -> WorkflowSemanticProviderConfig | None:
    """Resolve one bounded Provider configuration for Workflow scientific semantics."""
    try:
        provider = _planner_provider()
    except ProviderRegistryError:
        return None
    if provider == "huiyan_chat_completions":
        api_base = os.environ.get("LLM_PROVIDER_BASE_URL")
        if not api_base or not os.environ.get("LLM_PROVIDER_API_KEY"):
            return None
        model = _planner_chat_model()
        return WorkflowSemanticProviderConfig(
            provider=provider,
            model=model,
            endpoint=f"{api_base.rstrip('/')}/v1/chat/completions",
            timeout_seconds=research_start_planner_task_budget_seconds(),
        )
    if provider == "openai_responses":
        if not os.environ.get("OPENAI_API_KEY"):
            return None
        model = _planner_openai_model()
        return WorkflowSemanticProviderConfig(
            provider=provider,
            model=model,
            endpoint=f"{os.environ.get('OPENAI_API_BASE_URL', 'https://api.openai.com').rstrip('/')}/v1/responses",
            timeout_seconds=research_start_planner_task_budget_seconds(),
        )
    return None


def _planner_response_format_enabled() -> bool:
    raw = (os.environ.get("OPENAIR_RESEARCH_START_PLANNER_LLM_RESPONSE_FORMAT") or "auto").strip().lower()
    return raw not in {"0", "false", "off", "disabled", "none"}


def _is_unsupported_response_format_error(exc: httpx.HTTPStatusError) -> bool:
    if exc.response.status_code not in {400, 422}:
        return False
    text = exc.response.text.lower()
    if "response_format" not in text:
        return False
    return any(
        marker in text
        for marker in (
            "unsupported",
            "not support",
            "not_supported",
            "unknown",
            "unrecognized",
            "invalid",
            "extra",
            "forbidden",
            "not allowed",
        )
    )


def _should_retry_without_response_format(exc: Exception) -> bool:
    return not isinstance(exc, httpx.TransportError)


def _planning_selection_for_prompt(
    selection: Any | None,
    *,
    research_design_only: bool = False,
) -> dict[str, Any]:
    del research_design_only
    return _planning_selection_payload(selection)


def _validate_live_planner_capability(*, kind: str, capability_id: str, skill_ids: list[str]) -> None:
    if kind == "agent":
        registered = load_agent_registry().get(capability_id)
        if registered is None:
            raise ResearchStartPlannerError(f"LLM planner selected unknown agent: {capability_id}")
        catalog_entry = next(
            (row for row in _planner_agent_catalog() if row["id"] == registered.agentId), None
        )
        if catalog_entry is None:
            raise ResearchStartPlannerError(f"LLM planner selected unknown agent: {capability_id}")

        allowed_for_agent = {str(skill_id) for skill_id in catalog_entry.get("skillIds") or []}
        outside_agent = [skill_id for skill_id in skill_ids if skill_id not in allowed_for_agent]
        if outside_agent:
            raise ResearchStartPlannerError(
                f"LLM planner agent {capability_id} uses skillIds outside its catalog: {', '.join(outside_agent)}"
            )
    _validate_durable_skill_ids(skill_ids)


def _validate_durable_skill_ids(skill_ids: Sequence[str]) -> None:
    """Keep ResearchDesign complete while rejecting only unknown Skill identities.

    Runtime readiness belongs to ExecutionDecision. A known Skill that is
    currently fail-closed must remain in the design so the existing decision
    resolver can choose a truthful Agent route or persist an unavailable state.
    """
    registered_ids: set[str] = set()
    for skill in list_registered_durable_skills():
        for value in (skill.get("skillId"), skill.get("claudeName")):
            normalized = _clean(str(value or "")).replace("-", "_").lower()
            if normalized:
                registered_ids.add(normalized)
    unknown = [
        skill_id
        for skill_id in skill_ids
        if _clean(skill_id).replace("-", "_").lower() not in registered_ids
    ]
    if unknown:
        raise ResearchStartPlannerError(
            f"Planner selected unknown Skill: {', '.join(unknown)}"
        )


def _planner_system_prompt() -> str:
    if settings.research_orchestration_v1:
        return """You are the OpenAIR ResearchDesign semantic Planner.

Return raw JSON only, matching the supplied PlannerSemanticProposalV2 schema.
You decide research meaning; the server compiles the production contract.

Schema compliance (fail-closed):
- Output one JSON object only. No markdown fences, no commentary, no trailing text.
- Use exactly the schema property names (camelCase). Do not invent sibling keys.
- Every string field must be a non-empty string. Arrays may be empty only when the schema allows it.
- schemaVersion must be exactly "planner-semantic.v2" when required by the schema.
- workItems[].definitionId must be copied exactly from availableDefinitions[].definitionId.
- workItems[].expectedArtifactTypes must be empty or a subset of that Definition's artifactTypes.
- workItems[].targetOutputRefs must copy exact strings from understanding.targetOutputs.
- Do not wrap the proposal in contractId/semanticVersion/body or any other envelope.

Rules:
- Express only the user's goal, understanding, constraints, requested external actions, and necessary semantic work items.
- understanding.primaryResearchQuestions contains the smallest set of independently necessary questions that directly determine the project's research line, target outputs, and completion. If the user asks one core judgment, comparison, causal, or decision question, normally keep exactly one primary question. Multiple primary questions are appropriate only when the user actually asks for multiple independent conclusions or deliverables.
- understanding.auxiliaryResearchDimensions contains mechanisms, variables, evaluation dimensions, evidence standards, applicability conditions, risks, failure modes, or other factors needed for explanation, retrieval, comparison, or evidence judgment. These must not be promoted into new project goals or completion requirements merely to make the plan look comprehensive.
- If an auxiliary factor truly needs to become a new research line, make that explicit in primaryResearchQuestions and targetOutputs so the user can confirm the scope change.
- Each workItems[].objective must contain only the minimum project-specific scientific context needed for that work item. Do not mechanically repeat the full goal, all primary questions, all auxiliary dimensions, or all target outputs in every objective.
- Select definitionId only from availableDefinitions by semantic fit. Never select by catalog order.
- planningApplicability is part of each Definition's formal planning scope. A generic-looking label does not make a specialized Definition general. Select a specialized Definition only when the current research request genuinely falls within that professional scope. Freeform planning does not require a Workflow to use a specialized Definition when it is scientifically applicable.
- Include only work items the research request actually needs. Use the four-stage research responsibilities to plan objectives from the request; never mechanically expand a preset task list.
- localKey is a proposal-local semantic name, not a production instance ID.
- targetOutputRefs must copy exact values from understanding.targetOutputs and assign every target output to at least one work item.
- A work item may claim a targetOutputRef only when the selected Definition label, description, and artifactTypes can directly produce that requested output. Scope/question-refinement Definitions may refine or bound a question, but must not claim literature findings, implementation inventories, evidence-backed method comparisons, or validation/experiment-design deliverables.
- When the user explicitly asks for both literature positioning/comparison and a reproducible validation or experiment design, select separate semantically matching Definitions when they are available; do not collapse those deliverables into one question-refinement work item.
- understanding.missingInputs contains only information that genuinely blocks useful research from starting. Do not list optional preferences, seed papers/repositories, preferred defaults, or facts that the research workflow is expected to discover. If the topic, time boundary, comparison dimensions, and requested outputs are explicit, proceed and represent remaining choices as assumptions or downstream research work instead of user blockers.
- dependsOnLocalKeys expresses semantic dependency direction only.
- expectedArtifactTypes must be empty or a subset of the selected Definition artifactTypes.
- Do not emit targetRequirements or completionMode. Completion and quality evaluation come from frozen Definition facts and are assembled by the server.
- Do not emit planningMode, planningComplexity, boundary objects, acceptance criteria, criterion/dependency keys, evaluator refs, coverage paths, coverage candidates, sequence hints, instance IDs, or execution authority.
- Do not choose Agents, Skills, runtimes, execution strategies, Assignments, WorkUnits, or Dispatch facts.
- Do not search, run code, execute tools, imply completed scientific work, or fabricate conclusions.
- Preserve explicit user constraints, material boundaries, requested outputs, and prohibited actions without widening them.
- When workflowPlanningContext is present, it is a frozen planning constraint, not a ready-made ResearchDesign. Include every requiredNodes[].definitionId exactly once. Select optionalNodes only when the user's project needs them. Preserve each listed Workflow dependency between selected Workflow nodes. requestedExternalActions must stay within boundaryPolicy.allowedExternalActions and must never include boundaryPolicy.prohibitedActions. You may add semantically necessary general availableDefinitions, including a final result producer, but they must not replace required Workflow nodes.
"""
    return """You are the OpenAIR Research Start Draft Planner.

Your job is to create the first confirmable workbench draft from the user's research-start request and the planning selection decision.
You are a planner, not an executor.

Rules:
- Return raw JSON only, matching the supplied schema.
- Do not search, read external papers, run code, execute skills, or claim work is complete.
- Do not generate final scientific conclusions before confirmation.
- In every user-facing field, never claim that research work (retrieval, reading, validation, experiment, calculation, analysis, or results) is completed or verified. A plan/draft may be described as organized, but it must not imply completed scientific work. When a check or result remains open, write `待验证`, `未完成`, `缺少结果`, or the equivalent boundary instead.
- You compose a starting plan from the system's real specialist agents in agentCatalog. Prefer composing MULTIPLE agents into a coherent path (a primary agent plus the natural next agents) instead of a single thin capability, so the plan actually uses the underlying agent system. Typically 2-3 capabilities.
- Every selectedCapabilities entry should map to one agentCatalog agent: set kind="agent", id=<agent id>, and skillIds to that agent's skillIds (you may trim to the subset this draft needs). Only use skillIds listed in allowedSkillIds.
- The left workbench navigation and the orchestrator's primary/supporting agents are derived from selectedCapabilities, so order them primary-first.
- Do not expand the full SDR pack. Select only the agents this specific draft needs.
- Topic-only inputs (a direction, keywords, disease/model scenario, but no supplied papers/evidence/data): the PRIMARY agent must be research_question_refinement. You may add key_literature_positioning as the next specialist step to reach evidence. Research progress planning is the controller's responsibility and must never appear as a specialist Agent capability. Do NOT select research_gap_identification, paper_evidence_extraction, mechanism_dispute_mapping, or result_reliability_audit for topic-only input — those need supplied materials. Represent "possible research gaps" only as candidate hypotheses / next evidence needs in understanding.targetOutputs and evidenceBoundaries, never as evidence-grounded gaps.
- Do not force a topic-only user to supply an existing paper packet, evidence table, dataset, or experiment records before continuing.
- Only select paper_evidence_extraction / research_gap_identification / mechanism_dispute_mapping / result_reliability_audit when the user actually supplies readable materials, sources, evidence, or result records.
- User-facing fields should be concise Simplified Chinese when the input is Chinese; keep domain tokens such as SEI as-is.
- The understanding must be semantic: summarize materials, intended outputs, boundaries, and risks. Do not merely repeat the raw input.
- The user prompt includes requiredTaskPair. It is an immutable pair: set understanding.taskType to requiredTaskPair.taskType and researchType to requiredTaskPair.researchType exactly. Never combine a taskType from one pair with a researchType from another pair."""

def _planner_user_prompt(
    *,
    raw_input: str,
    planning_selection: Any | None,
    source_page: str,
    selected_hint: str | None,
    definition_catalog: Sequence[Mapping[str, Any]] = (),
    workflow_planning_context: Mapping[str, Any] | None = None,
) -> str:
    if settings.research_orchestration_v1:
        payload = {
            "input": raw_input,
            "sourcePage": source_page,
            "selectedHint": selected_hint,
            "planningContext": _planning_selection_for_prompt(
                planning_selection,
                research_design_only=True,
            ),
            "researchStageFramework": research_stage_framework(),
            "availableDefinitions": list(definition_catalog),
            "workflowPlanningContext": (
                dict(workflow_planning_context)
                if workflow_planning_context is not None
                else None
            ),
        }
        return (
            "Create only a PlannerSemanticProposalV2. The server will deterministically compile all "
            "ResearchDesign mechanics. Every workItems[].definitionId must exactly equal one available "
            "Definition ID, except harmless case differences that resolve uniquely. A singleton_collection "
            "Definition may appear in only one semantic work item.\n\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )
    legacy_catalog = _planner_agent_catalog()
    required_task_type, required_research_type = _task_type(raw_input, selected_hint)
    payload = {
        "input": raw_input,
        "sourcePage": source_page,
        "selectedHint": selected_hint,
        "planningContext": _planning_selection_for_prompt(planning_selection),
        "agentCatalog": legacy_catalog,
        "allowedSkillIds": list(dict.fromkeys(
            skill_id for row in legacy_catalog for skill_id in row["skillIds"]
        )),
        "taskTypes": TASK_TYPE_TO_RESEARCH_TYPE,
        "requiredTaskPair": {
            "taskType": required_task_type,
            "researchType": required_research_type,
        },
    }
    return (
        "Plan this research-start draft. Return a JSON object only. "
        "requiredTaskPair is a hard contract: copy both of its values exactly into the corresponding output fields. "
        "The plan must be suitable for building the real task execution graph.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _clean_string_list(value: Any, *, field_name: str, max_items: int = 12) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ResearchStartPlannerError(f"LLM planner field must be a list: {field_name}")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        cleaned = item.strip()
        if cleaned and cleaned not in items:
            items.append(cleaned)
    return items[:max_items]


def _require_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchStartPlannerError(f"LLM planner field must be a non-empty string: {field_name}")
    return value.strip()


def _reject_prohibited_claims(*values: str) -> None:
    text = "\n".join(values).lower()
    for pattern in PROHIBITED_PLANNER_CLAIM_PATTERNS:
        for match in pattern.finditer(text):
            prefix = text[max(0, match.start() - 10) : match.start()]
            if any(marker in prefix for marker in ("未", "不", "不能", "不得", "不要", "不应", "不可", "避免", "尚未", "尚无", "没有", "待", "确认前不")):
                continue
            raise ResearchStartPlannerError(f"LLM planner claimed completed work: {match.group(0)}")


def _expected_task_type_from_hint(selected_hint: str | None) -> str | None:
    if selected_hint in KEY_LITERATURE_HINTS:
        return "key_literature_positioning"
    if selected_hint in EVIDENCE_EXTRACTION_HINTS:
        return "evidence_extraction"
    if selected_hint in MECHANISM_MAPPING_HINTS:
        return "mechanism_mapping"
    if selected_hint in RESEARCH_PLANNING_HINTS:
        return "research_planning"
    if selected_hint in QUESTION_REFINEMENT_HINTS:
        return "question_refinement"
    if selected_hint in GAP_IDENTIFICATION_HINTS:
        return "gap_identification"
    if selected_hint in VALIDATION_DESIGN_HINTS:
        return "validation_design"
    if selected_hint in RESULT_AUDIT_HINTS:
        return "result_audit"
    return None


def _plan_from_llm_payload(
    payload: dict[str, Any],
    *,
    raw_input: str,
    planning_selection: Any | None,
    selected_hint: str | None,
) -> ResearchStartPlan:
    if not isinstance(payload, dict):
        raise ResearchStartPlannerError("LLM planner output must be a JSON object")
    understanding_raw = payload.get("understanding")
    if not isinstance(understanding_raw, dict):
        raise ResearchStartPlannerError("LLM planner output missing understanding object")

    task_type = _require_text(understanding_raw.get("taskType"), field_name="understanding.taskType")
    if task_type not in TASK_TYPE_TO_RESEARCH_TYPE:
        raise ResearchStartPlannerError(f"unsupported LLM planner taskType: {task_type}")
    expected_task_type = _expected_task_type_from_hint(selected_hint)
    if expected_task_type and task_type != expected_task_type:
        raise ResearchStartPlannerError(
            f"LLM planner taskType {task_type} conflicts with selected hint taskType {expected_task_type}"
        )
    research_type = _require_text(payload.get("researchType"), field_name="researchType")
    expected_research_type = TASK_TYPE_TO_RESEARCH_TYPE[task_type]
    if research_type != expected_research_type:
        raise ResearchStartPlannerError(
            f"LLM planner researchType {research_type} conflicts with taskType {task_type}"
        )

    planning_reason = _require_text(understanding_raw.get("planningRationale"), field_name="understanding.planningRationale")
    understanding = ResearchStartUnderstanding(
        taskType=task_type,
        userIntent=_require_text(understanding_raw.get("userIntent"), field_name="understanding.userIntent"),
        materialSummary=_require_text(understanding_raw.get("materialSummary"), field_name="understanding.materialSummary"),
        targetOutputs=_clean_string_list(understanding_raw.get("targetOutputs"), field_name="understanding.targetOutputs"),
        evidenceBoundaries=_clean_string_list(
            understanding_raw.get("evidenceBoundaries"),
            field_name="understanding.evidenceBoundaries",
        ),
        missingInputs=_clean_string_list(understanding_raw.get("missingInputs"), field_name="understanding.missingInputs"),
        riskNotes=_clean_string_list(understanding_raw.get("riskNotes"), field_name="understanding.riskNotes"),
        planningRationale=planning_reason,
    )
    if not understanding.targetOutputs:
        raise ResearchStartPlannerError("LLM planner must include at least one target output")
    _reject_prohibited_claims(
        understanding.userIntent,
        understanding.materialSummary,
        *understanding.targetOutputs,
        *understanding.evidenceBoundaries,
        *understanding.riskNotes,
        understanding.planningRationale,
    )

    raw_capabilities = payload.get("selectedCapabilities")
    if not isinstance(raw_capabilities, list) or not raw_capabilities:
        raise ResearchStartPlannerError("LLM planner must select at least one capability")
    capabilities: list[PlannedCapability] = []
    for index, item in enumerate(raw_capabilities):
        if not isinstance(item, dict):
            raise ResearchStartPlannerError(f"LLM planner capability must be an object at index {index}")
        kind = _require_text(item.get("kind"), field_name=f"selectedCapabilities[{index}].kind")
        if kind not in {"skill", "agent", "workflow"}:
            raise ResearchStartPlannerError(f"unsupported LLM planner capability kind: {kind}")
        capability_id = _require_text(item.get("id"), field_name=f"selectedCapabilities[{index}].id")
        skill_ids = _clean_string_list(item.get("skillIds"), field_name=f"selectedCapabilities[{index}].skillIds")
        if not skill_ids:
            raise ResearchStartPlannerError(f"LLM planner capability has no skillIds: {capability_id}")
        _validate_live_planner_capability(kind=kind, capability_id=capability_id, skill_ids=skill_ids)
        rationale = _require_text(item.get("rationale"), field_name=f"selectedCapabilities[{index}].rationale")
        expected_artifacts = _clean_string_list(
            item.get("expectedArtifactTypes"),
            field_name=f"selectedCapabilities[{index}].expectedArtifactTypes",
        )
        confirmation_required = item.get("confirmationRequired")
        if not isinstance(confirmation_required, bool):
            raise ResearchStartPlannerError(
                f"LLM planner confirmationRequired must be boolean: {capability_id}"
            )
        _reject_prohibited_claims(capability_id, rationale, *expected_artifacts)
        capabilities.append(
            PlannedCapability(
                kind=kind,  # type: ignore[arg-type]
                id=capability_id,
                skillIds=skill_ids,
                rationale=rationale,
                expectedArtifactTypes=expected_artifacts,
                confirmationRequired=confirmation_required,
            )
        )

    stage_descriptions_raw = payload.get("stageDescriptions") or {}
    if not isinstance(stage_descriptions_raw, dict):
        raise ResearchStartPlannerError("LLM planner stageDescriptions must be an object")
    stage_descriptions = {
        str(key).strip(): str(value).strip()
        for key, value in stage_descriptions_raw.items()
        if str(key).strip() and isinstance(value, str) and value.strip()
    }
    stage_descriptions = _with_goal_framing(stage_descriptions, raw_input)
    _reject_prohibited_claims(*stage_descriptions.values())

    plan = ResearchStartPlan(
        planningSource="llm",
        researchType=research_type,
        understanding=understanding,
        selectedCapabilities=capabilities,
        stageDescriptions=stage_descriptions,
    )
    build_creation_plan_stages(plan, pack="sdr")
    return plan


def _live_planning_audit(
    *,
    provider: str,
    requested_model: str,
    response_body: dict[str, Any],
) -> dict[str, str]:
    audit: dict[str, str] = {
        "mode": "live",
        "provider": provider,
        "model": str(response_body.get("model") or requested_model),
    }
    run_id = response_body.get("id")
    if isinstance(run_id, str) and run_id.strip():
        audit["runId"] = run_id.strip()
    # Usage observation is owned by execute_prepared_attempt (worker/prepared path)
    # or by the direct sync runners below — planningAudit stays string-only.
    return audit


def _run_chat_completions_planner(
    *,
    raw_input: str,
    planning_selection: Any | None,
    source_page: str,
    selected_hint: str | None,
    repair_instruction: str | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    api_key = os.environ.get("LLM_PROVIDER_API_KEY")
    api_base = os.environ.get("LLM_PROVIDER_BASE_URL")
    if not api_key:
        raise ResearchStartPlannerUnavailable("LLM_PROVIDER_API_KEY environment variable not set")
    if not api_base:
        raise ResearchStartPlannerUnavailable("LLM_PROVIDER_BASE_URL environment variable not set")

    attempts_per_model = _planner_attempts()
    include_response_format = _planner_response_format_enabled()
    last_error: str | None = None
    logical_submission_ordinal = 0
    for model in _planner_chat_model_fallback_chain():
        attempt = 0
        while attempt < attempts_per_model:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": _planner_system_prompt()},
                    {
                        "role": "user",
                        "content": _planner_user_prompt(
                            raw_input=raw_input,
                            planning_selection=planning_selection,
                            source_page=source_page,
                            selected_hint=selected_hint,
                        ) + (f"\n\n{repair_instruction}" if repair_instruction else ""),
                    },
                ],
                "temperature": _planner_temperature(),
                "max_tokens": _planner_max_tokens(),
            }
            response_format = structured_response_format(
                "huiyan_chat_completions",
                schema_name="research_start_draft_plan",
                enabled=include_response_format,
            )
            if response_format:
                payload["response_format"] = response_format
            payload = apply_openai_compatible_thinking(
                payload, "OPENAIR_RESEARCH_START_PLANNER_LLM_THINKING"
            )
            payload, _ = _prepare_payload_for_http(payload)
            payload = apply_provider_schema_transport(
                "huiyan_chat_completions",
                payload=payload,
                response_format=response_format,
                provider_schema=PLANNER_PLAN_SCHEMA,
            )
            try:
                logical_submission_ordinal += 1
                response_body = execute_observed_openai_compatible_json(
                    protocol="chat_completions",
                    base_url=api_base,
                    api_key=api_key,
                    timeout_seconds=research_start_planner_task_budget_seconds(),
                    payload=payload,
                    adapter="huiyan_chat_completions",
                    provider="huiyan_chat_completions",
                    model=model,
                    operation_identity="research_start:research_planner",
                    logical_attempt_ordinal=logical_submission_ordinal,
                )
                content, _choice = extract_final_chat_content(response_body)
                if not content.strip():
                    raise ValueError("empty LLM planner response content")
                from llm_core.token_usage import record_observed_usage

                record_observed_usage(
                    provider="huiyan_chat_completions",
                    model=str(response_body.get("model") or model),
                    body=response_body,
                )
                return (
                    _parse_llm_json_content(content),
                    _live_planning_audit(
                        provider="huiyan_chat_completions",
                        requested_model=model,
                        response_body=response_body,
                    ),
                )
            except httpx.HTTPStatusError as exc:
                if include_response_format and _is_unsupported_response_format_error(exc):
                    include_response_format = False
                    last_error = f"{model} attempt {attempt + 1}/{attempts_per_model}: response_format unsupported"
                    continue
                last_error = f"{model} attempt {attempt + 1}/{attempts_per_model}: {exc}"
                attempt += 1
            except Exception as exc:
                if include_response_format and _should_retry_without_response_format(exc):
                    include_response_format = False
                    last_error = f"{model} attempt {attempt + 1}/{attempts_per_model}: response_format retry without structured mode after {exc}"
                    continue
                last_error = f"{model} attempt {attempt + 1}/{attempts_per_model}: {exc}"
                attempt += 1
    raise ResearchStartPlannerUnavailable(last_error or "chat completions planner failed")


def _run_openai_responses_planner(
    *,
    raw_input: str,
    planning_selection: Any | None,
    source_page: str,
    selected_hint: str | None,
    repair_instruction: str | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    api_key = os.environ.get("OPENAI_API_KEY")
    api_base = os.environ.get("OPENAI_API_BASE_URL", "https://api.openai.com")
    if not api_key:
        raise ResearchStartPlannerUnavailable("OPENAI_API_KEY environment variable not set")

    endpoint_base = api_base.rstrip("/")
    last_error: str | None = None
    logical_submission_ordinal = 0
    for model in _openai_model_fallback_chain(_planner_openai_model()):
        response_format = structured_response_format(
            "openai_responses",
            schema_name="research_start_draft_plan",
        )
        payload = _build_responses_payload(
            model=model,
            system_prompt=_planner_system_prompt(),
            user_prompt=_planner_user_prompt(
                raw_input=raw_input,
                planning_selection=planning_selection,
                source_page=source_page,
                selected_hint=selected_hint,
            ) + (f"\n\n{repair_instruction}" if repair_instruction else ""),
            output_schema=PLANNER_PLAN_SCHEMA,
            schema_name="research_start_draft_plan",
            max_completion_tokens=_planner_max_tokens(),
            reasoning_effort=os.environ.get("OPENAIR_RESEARCH_START_PLANNER_REASONING_EFFORT") or None,
            temperature=_planner_temperature(),
        )
        payload, _ = _prepare_payload_for_http(payload)
        payload = apply_provider_schema_transport(
            "openai_responses",
            payload=payload,
            response_format=response_format,
            provider_schema=PLANNER_PLAN_SCHEMA,
        )
        try:
            logical_submission_ordinal += 1
            response_body = execute_observed_openai_compatible_json(
                protocol="responses",
                base_url=endpoint_base,
                api_key=api_key,
                timeout_seconds=research_start_planner_task_budget_seconds(),
                payload=payload,
                adapter="openai_responses",
                provider="openai_responses",
                model=model,
                operation_identity="research_start:research_planner",
                logical_attempt_ordinal=logical_submission_ordinal,
            )
            content = _extract_responses_output_text(response_body)
            from llm_core.token_usage import record_observed_usage

            record_observed_usage(
                provider="openai_responses",
                model=str(response_body.get("model") or model),
                body=response_body,
            )
            return (
                _parse_llm_json_content(content),
                _live_planning_audit(
                    provider="openai_responses",
                    requested_model=model,
                    response_body=response_body,
                ),
            )
        except Exception as exc:
            last_error = f"{model}: {exc}"
            continue
    raise ResearchStartPlannerUnavailable(last_error or "OpenAI responses planner failed")


def _planner_repair_instruction(payload: dict[str, Any], error: ResearchStartPlannerError) -> str:
    return (
        "Your previous draft failed OpenAIR's safety validation. Return a corrected JSON object only. "
        "Do not claim completed or verified research; preserve the requiredTaskPair and source boundaries. "
        f"Validation error: {error}. Previous JSON: {json.dumps(payload, ensure_ascii=False)}"
    )


def _planner_intent_value(intent: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in intent:
            return intent[key]
    return default


class _PlannerSemanticProposalError(PlannerSemanticInputError):
    def __init__(
        self,
        message: str,
        *,
        safe_error_code: str,
        safe_field_paths: tuple[str, ...],
        safe_repair_context: Mapping[str, Any] | None = None,
    ) -> None:
        ValueError.__init__(self, message)
        self.safe_error_code = safe_error_code
        self.safe_field_paths = safe_field_paths
        self.safe_repair_context = dict(safe_repair_context or {})


_JSON_PARSE_REPAIR_SCHEMA_VERSION = "planner-json-parse-repair.v1"
_FULL_SEMANTIC_REPAIR_SCHEMA_VERSION = "planner-full-semantic-repair.v1"
_JSON_PARSE_EXCERPT_CHARS = 800


class _PlannerJsonParseError(PlannerSemanticInputError):
    """Raised when the provider answered but the body is not a JSON object."""

    def __init__(
        self,
        message: str,
        *,
        safe_repair_context: Mapping[str, Any] | None = None,
    ) -> None:
        ValueError.__init__(self, message)
        self.safe_error_code = "json_parse_error"
        self.safe_field_paths = ("/",)
        self.safe_repair_context = dict(safe_repair_context or {})


def _json_parse_repair_context_from_content(content: str) -> dict[str, Any]:
    excerpt = str(content or "")[:_JSON_PARSE_EXCERPT_CHARS]
    return {
        "repairKind": "json_parse",
        "schemaVersion": _JSON_PARSE_REPAIR_SCHEMA_VERSION,
        "previousContentExcerpt": excerpt,
    }


def _full_semantic_repair_context_from_validation(
    payload: Mapping[str, Any],
    *,
    field_paths: Sequence[str],
    validation_summary: str,
) -> dict[str, Any]:
    """Allow one complete semantic re-issue when focused repair cannot be classified.

    fields that the focused repair envelope does not cover. A single full
    re-issue keeps the existing two-call planner budget while recovering those
    cases instead of failing closed immediately.
    """
    try:
        bounded = _bounded_semantic_payload(payload)
        excerpt = json.dumps(bounded, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        excerpt = json.dumps(
            {key: payload.get(key) for key in ("schemaVersion", "goal", "understanding", "workItems") if key in payload},
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    return {
        "repairKind": "full_semantic",
        "schemaVersion": _FULL_SEMANTIC_REPAIR_SCHEMA_VERSION,
        "fieldPaths": [path for path in field_paths if isinstance(path, str)][:32],
        "validationSummary": str(validation_summary or "")[:800],
        "previousContentExcerpt": excerpt[:_JSON_PARSE_EXCERPT_CHARS],
    }


def _full_semantic_repair_context(prior_failure: Any) -> dict[str, Any] | None:
    if not isinstance(prior_failure, str):
        return None
    try:
        failure = json.loads(prior_failure)
    except (TypeError, ValueError):
        return None
    if not isinstance(failure, Mapping):
        return None
    context = failure.get("repairContext")
    if not isinstance(context, Mapping):
        context = failure if failure.get("repairKind") == "full_semantic" else None
    if (
        not isinstance(context, Mapping)
        or context.get("repairKind") != "full_semantic"
        or context.get("schemaVersion") != _FULL_SEMANTIC_REPAIR_SCHEMA_VERSION
    ):
        return None
    return {
        "repairKind": "full_semantic",
        "schemaVersion": _FULL_SEMANTIC_REPAIR_SCHEMA_VERSION,
        "fieldPaths": [
            path for path in (context.get("fieldPaths") or ()) if isinstance(path, str)
        ][:32],
        "validationSummary": str(context.get("validationSummary") or "")[:800],
        "previousContentExcerpt": str(context.get("previousContentExcerpt") or "")[
            :_JSON_PARSE_EXCERPT_CHARS
        ],
    }


def _json_parse_repair_context(prior_failure: Any) -> dict[str, Any] | None:
    if not isinstance(prior_failure, str):
        return None
    try:
        failure = json.loads(prior_failure)
    except (TypeError, ValueError):
        return None
    if not isinstance(failure, Mapping):
        return None
    context = failure.get("repairContext")
    if not isinstance(context, Mapping):
        # Accept a bare repairContext envelope (tests / resume helpers).
        context = failure if failure.get("repairKind") == "json_parse" else None
    if (
        not isinstance(context, Mapping)
        or context.get("repairKind") != "json_parse"
        or context.get("schemaVersion") != _JSON_PARSE_REPAIR_SCHEMA_VERSION
    ):
        return None
    excerpt = context.get("previousContentExcerpt")
    if excerpt is not None and not isinstance(excerpt, str):
        return None
    return {
        "repairKind": "json_parse",
        "schemaVersion": _JSON_PARSE_REPAIR_SCHEMA_VERSION,
        "previousContentExcerpt": str(excerpt or "")[:_JSON_PARSE_EXCERPT_CHARS],
    }


def _parse_planner_json_object(content: str) -> dict[str, Any]:
    if not str(content or "").strip():
        raise _PlannerJsonParseError(
            "empty LLM planner response content",
            safe_repair_context=_json_parse_repair_context_from_content(content),
        )
    try:
        return _parse_llm_json_content(content)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise _PlannerJsonParseError(
            "planner response is not a valid JSON object",
            safe_repair_context=_json_parse_repair_context_from_content(content),
        ) from exc


def _semantic_json_pointer(location: Sequence[Any]) -> str:
    aliases = {
        "schema_version": "schemaVersion",
        "user_intent": "userIntent",
        "material_summary": "materialSummary",
        "primary_research_questions": "primaryResearchQuestions",
        "auxiliary_research_dimensions": "auxiliaryResearchDimensions",
        "target_outputs": "targetOutputs",
        "evidence_boundaries": "evidenceBoundaries",
        "missing_inputs": "missingInputs",
        "risk_notes": "riskNotes",
        "requested_external_actions": "requestedExternalActions",
        "work_items": "workItems",
        "local_key": "localKey",
        "definition_id": "definitionId",
        "input_refs": "inputRefs",
        "expected_artifact_types": "expectedArtifactTypes",
        "target_output_refs": "targetOutputRefs",
        "depends_on_local_keys": "dependsOnLocalKeys",
    }
    return "/" + "/".join(aliases.get(str(part), str(part)) for part in location)


def _bounded_semantic_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "schemaVersion",
        "goal",
        "understanding",
        "constraints",
        "requestedExternalActions",
        "workItems",
    }
    bounded = {key: deepcopy(value) for key, value in payload.items() if key in allowed}
    encoded = json.dumps(bounded, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) > 48_000:
        raise _PlannerSemanticProposalError(
            "semantic proposal exceeds the bounded repair envelope",
            safe_error_code="planner_semantic_payload_oversized",
            safe_field_paths=("/",),
        )
    return bounded


def _semantic_repair_context(
    payload: Mapping[str, Any],
    *,
    field_paths: Sequence[str],
    definition_catalog: Sequence[Mapping[str, Any]],
    graph_issues: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any] | None:
    repairable = {
        "localKey",
        "objective",
        "definitionId",
        "expectedArtifactTypes",
        "targetOutputRefs",
        "dependsOnLocalKeys",
        "targetRequirements.completionMode",
    }
    selected_fields: set[str] = set()
    for path in field_paths:
        match = re.fullmatch(
            r"/workItems/(\d+)/(localKey|objective|definitionId|expectedArtifactTypes|targetOutputRefs|dependsOnLocalKeys)",
            path,
        )
        if match:
            selected_fields.add(match.group(2))
            continue
        if re.fullmatch(r"/targetRequirements/(\d+)/completionMode", path):
            selected_fields.add("targetRequirements.completionMode")
            continue
        if path == "/workItems":
            selected_fields.add("targetOutputRefs")
            continue
        if path == "/understanding/targetOutputs":
            selected_fields.add("understanding.targetOutputs")
            continue
        return None
    if not selected_fields or not selected_fields.issubset(
        repairable | {"understanding.targetOutputs"}
    ):
        return None
    context = {
        "repairKind": "focused_semantic",
        "schemaVersion": "planner-semantic-repair.v2",
        "semanticProposal": _bounded_semantic_payload(payload),
        "fieldPaths": list(dict.fromkeys(field_paths))[:16],
        "definitionCandidates": [str(row["definitionId"]) for row in definition_catalog],
        "artifactCandidatesByDefinition": {
            str(row["definitionId"]): list(row.get("artifactTypes") or ())
            for row in definition_catalog
        },
    }
    if graph_issues:
        context["graphIssues"] = [deepcopy(dict(issue)) for issue in graph_issues][:16]
    return context


def _semantic_graph_issues_from_validation(exc: ValidationError) -> tuple[dict[str, Any], ...]:
    issues: list[dict[str, Any]] = []
    for item in exc.errors(include_url=False, include_context=True, include_input=False):
        context = item.get("ctx")
        encoded = context.get("graphIssues") if isinstance(context, Mapping) else None
        if not isinstance(encoded, str):
            continue
        try:
            decoded = json.loads(encoded)
        except (TypeError, ValueError):
            continue
        if not isinstance(decoded, list):
            continue
        for issue in decoded:
            if isinstance(issue, Mapping):
                issues.append(dict(issue))
    return tuple(issues)


def _without_model_completion_authority(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Drop completion-mode choices that belong to frozen Definition truth."""
    sanitized = deepcopy(dict(payload))
    sanitized.pop("targetRequirements", None)
    return sanitized


def _validate_semantic_payload(
    payload: Mapping[str, Any],
    *,
    raw_input: str,
    definition_catalog: Sequence[Mapping[str, Any]],
):
    from app.models.research_planner_semantic import PlannerSemanticProposalV2

    try:
        semantic = PlannerSemanticProposalV2.model_validate(payload)
    except ValidationError as exc:
        graph_issues = _semantic_graph_issues_from_validation(exc)
        graph_paths = [
            str(path)
            for issue in graph_issues
            for path in (issue.get("fieldPaths") or ())
            if isinstance(path, str)
        ]
        paths = tuple(dict.fromkeys(graph_paths or [
            _semantic_json_pointer(item.get("loc") or ())
            for item in exc.errors(include_url=False, include_context=False, include_input=False)
        ])) or ("/",)
        summary = "; ".join(
            f"{_semantic_json_pointer(item.get('loc') or ())}: {item.get('msg')}"
            for item in exc.errors(include_url=False, include_context=False, include_input=False)[:8]
        )
        repair_context = _semantic_repair_context(
            payload,
            field_paths=paths,
            definition_catalog=definition_catalog,
            graph_issues=graph_issues,
        )
        if repair_context is None:
            repair_context = _full_semantic_repair_context_from_validation(
                payload,
                field_paths=paths,
                validation_summary=summary,
            )
        raise _PlannerSemanticProposalError(
            "planner semantic proposal is invalid",
            safe_error_code="planner_semantic_proposal_invalid",
            safe_field_paths=paths,
            safe_repair_context=repair_context,
        ) from exc

    if not semantic.understanding.primary_research_questions:
        paths = ("/understanding/primaryResearchQuestions",)
        raise _PlannerSemanticProposalError(
            "planner semantic proposal is missing a primary research question",
            safe_error_code="planner_semantic_proposal_invalid",
            safe_field_paths=paths,
            safe_repair_context=_full_semantic_repair_context_from_validation(
                payload,
                field_paths=paths,
                validation_summary="/understanding/primaryResearchQuestions: must contain at least one item",
            ),
        )

    return semantic


def _strict_semantic_output_schema() -> dict[str, Any]:
    """Build the provider schema for semantic choices the model actually owns.

    ``targetRequirements`` remains locally parseable for old semantic payloads,
    but it is intentionally excluded from the live provider contract. Completion
    and quality evaluation are deterministic Definition facts, not model choices.
    """
    from app.models.research_planner_semantic import PlannerSemanticProposalV2

    schema = PlannerSemanticProposalV2.model_json_schema(by_alias=True)
    properties = schema.get("properties")
    if isinstance(properties, dict):
        properties.pop("targetRequirements", None)
    required = schema.get("required")
    if isinstance(required, list):
        schema["required"] = [name for name in required if name != "targetRequirements"]
    definitions = schema.get("$defs")
    if isinstance(definitions, dict):
        definitions.pop("PlannerSemanticTargetRequirementV2", None)

    def require_object_properties(node: Any) -> None:
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                required = node.setdefault("required", [])
                for name in properties:
                    if name not in required:
                        required.append(name)
            for value in node.values():
                require_object_properties(value)
        elif isinstance(node, list):
            for value in node:
                require_object_properties(value)

    require_object_properties(schema)
    return schema


def _focused_semantic_repair_context(prior_failure: Any) -> dict[str, Any] | None:
    if not isinstance(prior_failure, str):
        return None
    try:
        failure = json.loads(prior_failure)
    except (TypeError, ValueError):
        return None
    if not isinstance(failure, Mapping):
        return None
    context = failure.get("repairContext")
    if (
        not isinstance(context, Mapping)
        or context.get("repairKind") != "focused_semantic"
        or context.get("schemaVersion") != "planner-semantic-repair.v2"
        or not isinstance(context.get("semanticProposal"), Mapping)
        or not isinstance(context.get("fieldPaths"), list)
        or not context.get("fieldPaths")
    ):
        return None
    return deepcopy(dict(context))


def _focused_target_requirement_indexes(context: Mapping[str, Any]) -> list[int]:
    indexes: list[int] = []
    for path in context.get("fieldPaths") or ():
        match = re.fullmatch(r"/targetRequirements/(\d+)/completionMode", str(path))
        if match:
            indexes.append(int(match.group(1)))
    return list(dict.fromkeys(indexes))


def _focused_semantic_repair_fields(context: Mapping[str, Any]) -> tuple[set[str], list[int]]:
    fields: set[str] = set()
    indexes: list[int] = []
    proposal = context["semanticProposal"]
    work_items = proposal.get("workItems") if isinstance(proposal, Mapping) else None
    repair_paths = list(context["fieldPaths"])
    graph_issues = context.get("graphIssues")
    if isinstance(graph_issues, list):
        for issue in graph_issues:
            if not isinstance(issue, Mapping):
                continue
            repair_paths.extend(
                path
                for path in (issue.get("fieldPaths") or ())
                if isinstance(path, str)
            )
            for work_item in issue.get("workItems") or ():
                if isinstance(work_item, Mapping) and isinstance(work_item.get("index"), int):
                    indexes.append(int(work_item["index"]))
    for path in repair_paths:
        if path == "/workItems":
            fields.add("targetOutputRefs")
            if isinstance(work_items, list):
                indexes.extend(range(len(work_items)))
            continue
        if path == "/understanding/targetOutputs":
            fields.add("understanding.targetOutputs")
            continue
        if re.fullmatch(r"/targetRequirements/(\d+)/completionMode", str(path)):
            fields.add("targetRequirements.completionMode")
            continue
        match = re.fullmatch(
            r"/workItems/(\d+)/(localKey|objective|definitionId|expectedArtifactTypes|targetOutputRefs|dependsOnLocalKeys)",
            str(path),
        )
        if match:
            indexes.append(int(match.group(1)))
            fields.add(match.group(2))
    return fields, list(dict.fromkeys(indexes))


def _focused_semantic_repair_schema(context: Mapping[str, Any]) -> dict[str, Any]:
    if context.get("repairMode") == "capability_coverage":
        semantic_schema = _strict_semantic_output_schema()
        definitions = semantic_schema["$defs"]
        definitions["PlannerSemanticWorkItemV2"]["properties"]["definitionId"]["enum"] = list(
            context.get("definitionCandidates") or ()
        )
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$defs": definitions,
            "type": "object",
            "additionalProperties": False,
            "required": ["understandingRepair", "workItemsReplacement"],
            "properties": {
                "understandingRepair": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["targetOutputs"],
                    "properties": {
                        "targetOutputs": deepcopy(
                            definitions["PlannerSemanticUnderstandingV2"]["properties"]["targetOutputs"]
                        ),
                    },
                },
                "workItemsReplacement": deepcopy(semantic_schema["properties"]["workItems"]),
            },
        }

    proposal = context["semanticProposal"]
    work_items = proposal.get("workItems") if isinstance(proposal, Mapping) else []
    fields, indexes = _focused_semantic_repair_fields(context)
    properties: dict[str, Any] = {}
    required: list[str] = []
    if "understanding.targetOutputs" in fields:
        properties["understandingRepair"] = {
            "type": "object",
            "additionalProperties": False,
            "required": ["targetOutputs"],
            "properties": {
                "targetOutputs": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 12,
                    "items": {"type": "string", "minLength": 1},
                }
            },
        }
        required.append("understandingRepair")
    work_fields = fields - {
        "understanding.targetOutputs",
        "targetRequirements.completionMode",
    }
    if work_fields:
        affected_keys = [
            str(work_items[index].get("localKey") or "")
            for index in indexes
            if isinstance(work_items, list)
            and index < len(work_items)
            and isinstance(work_items[index], Mapping)
            and str(work_items[index].get("localKey") or "")
        ]
        if not affected_keys and isinstance(work_items, list):
            affected_keys = [
                str(row.get("localKey") or "")
                for row in work_items
                if isinstance(row, Mapping) and str(row.get("localKey") or "")
            ]
        graph_repair = bool(context.get("graphIssues"))
        item_properties: dict[str, Any]
        item_required: list[str]
        if graph_repair:
            item_properties = {
                "workItemIndex": {"type": "integer", "enum": indexes},
            }
            item_required = ["workItemIndex"]
        else:
            item_properties = {
                "localKey": {"type": "string", "enum": list(dict.fromkeys(affected_keys))},
            }
            item_required = ["localKey"]
        if "localKey" in work_fields:
            item_properties["localKey"] = {"type": "string", "minLength": 1}
            item_required.append("localKey")
        if "objective" in work_fields:
            item_properties["objective"] = {"type": "string", "minLength": 1}
            item_required.append("objective")
        if "definitionId" in work_fields:
            item_properties["definitionId"] = {
                "type": "string",
                "enum": list(context.get("definitionCandidates") or ()),
            }
            item_required.append("definitionId")
        if "expectedArtifactTypes" in work_fields:
            artifacts = sorted({
                str(value)
                for values in (context.get("artifactCandidatesByDefinition") or {}).values()
                for value in (values or ())
            })
            item_properties["expectedArtifactTypes"] = {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string", "enum": artifacts},
            }
            item_required.append("expectedArtifactTypes")
        if "targetOutputRefs" in work_fields:
            understanding = proposal.get("understanding") if isinstance(proposal, Mapping) else None
            outputs = understanding.get("targetOutputs") if isinstance(understanding, Mapping) else []
            item_properties["targetOutputRefs"] = {
                "type": "array",
                "items": {"type": "string", **({"enum": outputs} if outputs else {"minLength": 1})},
            }
            item_required.append("targetOutputRefs")
        if "dependsOnLocalKeys" in work_fields:
            all_keys = [
                str(row.get("localKey") or "")
                for row in work_items
                if isinstance(row, Mapping) and str(row.get("localKey") or "")
            ]
            item_properties["dependsOnLocalKeys"] = {
                "type": "array",
                "items": {"type": "string", "enum": list(dict.fromkeys(all_keys))},
            }
            item_required.append("dependsOnLocalKeys")
        properties["workItemRepairs"] = {
            "type": "array",
            "minItems": len(indexes) if graph_repair else 1,
            "maxItems": len(indexes) if graph_repair else max(1, len(set(affected_keys))),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": item_required,
                "properties": item_properties,
            },
        }
        required.append("workItemRepairs")
    if "targetRequirements.completionMode" in fields:
        requirement_indexes = _focused_target_requirement_indexes(context)
        if not requirement_indexes:
            requirement_indexes = [0]
        properties["targetRequirementRepairs"] = {
            "type": "array",
            "minItems": len(requirement_indexes),
            "maxItems": len(requirement_indexes),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["targetRequirementIndex", "completionMode"],
                "properties": {
                    "targetRequirementIndex": {
                        "type": "integer",
                        "enum": requirement_indexes,
                    },
                    "completionMode": {
                        "type": "string",
                        "enum": ["artifact_presence", "professional_judgment"],
                    },
                },
            },
        }
        required.append("targetRequirementRepairs")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def _apply_focused_semantic_repair(
    context: Mapping[str, Any],
    repair_payload: Mapping[str, Any],
) -> dict[str, Any]:
    proposal = deepcopy(dict(context["semanticProposal"]))
    if context.get("repairMode") == "capability_coverage":
        understanding_repair = repair_payload.get("understandingRepair")
        replacement = repair_payload.get("workItemsReplacement")
        if (
            not isinstance(understanding_repair, Mapping)
            or not isinstance(understanding_repair.get("targetOutputs"), list)
            or not isinstance(replacement, list)
            or not replacement
        ):
            raise _PlannerSemanticProposalError(
                "capability coverage repair omitted target outputs or work items",
                safe_error_code="planner_semantic_repair_invalid",
                safe_field_paths=("/understandingRepair/targetOutputs", "/workItemsReplacement"),
            )
        proposal.setdefault("understanding", {})["targetOutputs"] = deepcopy(
            understanding_repair["targetOutputs"]
        )
        proposal["workItems"] = deepcopy(replacement)
        return proposal
    fields, indexes = _focused_semantic_repair_fields(context)
    understanding_repair = repair_payload.get("understandingRepair")
    if "understanding.targetOutputs" in fields:
        if not isinstance(understanding_repair, Mapping) or not isinstance(understanding_repair.get("targetOutputs"), list):
            raise _PlannerSemanticProposalError(
                "focused semantic repair omitted targetOutputs",
                safe_error_code="planner_semantic_repair_invalid",
                safe_field_paths=("/understandingRepair/targetOutputs",),
            )
        proposal.setdefault("understanding", {})["targetOutputs"] = list(understanding_repair["targetOutputs"])
    work_fields = fields - {
        "understanding.targetOutputs",
        "targetRequirements.completionMode",
    }
    if work_fields:
        repairs = repair_payload.get("workItemRepairs")
        work_items = proposal.get("workItems")
        if not isinstance(repairs, list) or not isinstance(work_items, list):
            raise _PlannerSemanticProposalError(
                "focused semantic work-item repair is invalid",
                safe_error_code="planner_semantic_repair_invalid",
                safe_field_paths=("/workItemRepairs",),
            )
        graph_repair = bool(context.get("graphIssues"))
        by_key = {
            str(row.get("localKey")): row
            for row in work_items
            if isinstance(row, dict) and isinstance(row.get("localKey"), str)
        }
        repaired_keys: set[str] = set()
        repaired_indexes: set[int] = set()
        for index, repair in enumerate(repairs):
            if not isinstance(repair, Mapping):
                raise _PlannerSemanticProposalError(
                    "focused semantic repair row is invalid",
                    safe_error_code="planner_semantic_repair_invalid",
                    safe_field_paths=(f"/workItemRepairs/{index}",),
                )
            if graph_repair:
                work_item_index = repair.get("workItemIndex")
                if (
                    not isinstance(work_item_index, int)
                    or work_item_index not in indexes
                    or work_item_index in repaired_indexes
                    or work_item_index >= len(work_items)
                    or not isinstance(work_items[work_item_index], dict)
                ):
                    raise _PlannerSemanticProposalError(
                        "focused semantic repair references an invalid workItemIndex",
                        safe_error_code="planner_semantic_repair_invalid",
                        safe_field_paths=(f"/workItemRepairs/{index}/workItemIndex",),
                    )
                repaired_indexes.add(work_item_index)
                target = work_items[work_item_index]
            else:
                local_key = str(repair.get("localKey") or "")
                target = by_key.get(local_key)
                if target is None or local_key in repaired_keys:
                    raise _PlannerSemanticProposalError(
                        "focused semantic repair references an invalid localKey",
                        safe_error_code="planner_semantic_repair_invalid",
                        safe_field_paths=(f"/workItemRepairs/{index}/localKey",),
                    )
                repaired_keys.add(local_key)
            for field_name in work_fields:
                if field_name not in repair:
                    raise _PlannerSemanticProposalError(
                        "focused semantic repair omitted a required field",
                        safe_error_code="planner_semantic_repair_invalid",
                        safe_field_paths=(f"/workItemRepairs/{index}/{field_name}",),
                    )
                target[field_name] = deepcopy(repair[field_name])
        if graph_repair and repaired_indexes != set(indexes):
            raise _PlannerSemanticProposalError(
                "focused semantic repair did not cover every affected work item",
                safe_error_code="planner_semantic_repair_invalid",
                safe_field_paths=("/workItemRepairs",),
            )
    if "targetRequirements.completionMode" in fields:
        repairs = repair_payload.get("targetRequirementRepairs")
        requirements = proposal.get("targetRequirements")
        requirement_indexes = _focused_target_requirement_indexes(context)
        if (
            not isinstance(repairs, list)
            or not isinstance(requirements, list)
            or not requirement_indexes
        ):
            raise _PlannerSemanticProposalError(
                "focused semantic target-requirement repair is invalid",
                safe_error_code="planner_semantic_repair_invalid",
                safe_field_paths=("/targetRequirementRepairs",),
            )
        repaired_requirement_indexes: set[int] = set()
        for index, repair in enumerate(repairs):
            if not isinstance(repair, Mapping):
                raise _PlannerSemanticProposalError(
                    "focused semantic target-requirement repair row is invalid",
                    safe_error_code="planner_semantic_repair_invalid",
                    safe_field_paths=(f"/targetRequirementRepairs/{index}",),
                )
            requirement_index = repair.get("targetRequirementIndex")
            completion_mode = repair.get("completionMode")
            if (
                not isinstance(requirement_index, int)
                or requirement_index not in requirement_indexes
                or requirement_index in repaired_requirement_indexes
                or requirement_index >= len(requirements)
                or not isinstance(requirements[requirement_index], dict)
                or completion_mode not in {"artifact_presence", "professional_judgment"}
            ):
                raise _PlannerSemanticProposalError(
                    "focused semantic target-requirement repair references an invalid row",
                    safe_error_code="planner_semantic_repair_invalid",
                    safe_field_paths=(f"/targetRequirementRepairs/{index}",),
                )
            repaired_requirement_indexes.add(requirement_index)
            requirements[requirement_index]["completionMode"] = completion_mode
        if repaired_requirement_indexes != set(requirement_indexes):
            raise _PlannerSemanticProposalError(
                "focused semantic repair did not cover every failed target requirement",
                safe_error_code="planner_semantic_repair_invalid",
                safe_field_paths=("/targetRequirementRepairs",),
            )
    return proposal

def _normalize_research_design_revision_spec(value: Mapping[str, Any]) -> dict[str, Any]:
    goal = str(value.get("goal") or "").strip()
    raw_steps = value.get("steps")
    if not goal or not isinstance(raw_steps, list) or not raw_steps or len(raw_steps) > 64:
        raise ResearchStartPlannerUnavailable("research design revision requires a goal and 1-64 research steps")
    allowed_stages = {
        "problem_modeling",
        "solution_design",
        "execution_validation",
        "insight_generation",
    }
    steps: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_steps):
        if not isinstance(raw, Mapping):
            raise ResearchStartPlannerUnavailable(f"research design revision step {index} is invalid")
        local_key = str(raw.get("localKey") or "").strip()
        stage_key = str(raw.get("stageKey") or "").strip()
        objective = str(raw.get("objective") or "").strip()
        if not local_key or local_key in seen or stage_key not in allowed_stages or not objective:
            raise ResearchStartPlannerUnavailable(f"research design revision step {index} is invalid")
        seen.add(local_key)
        steps.append({"localKey": local_key, "stageKey": stage_key, "objective": objective})
    preserved_constraints = [str(item).strip() for item in (value.get("constraints") or []) if str(item).strip()]
    preserved_evidence_boundaries = [
        str(item).strip() for item in (value.get("evidenceBoundaries") or []) if str(item).strip()
    ]
    preserved_external_actions = [
        str(item).strip() for item in (value.get("requestedExternalActions") or []) if str(item).strip()
    ]
    material_summary_raw = value.get("materialSummary")
    material_summary = str(material_summary_raw).strip() if material_summary_raw is not None else None
    return {
        "goal": goal,
        "steps": steps,
        "constraints": preserved_constraints,
        "evidenceBoundaries": preserved_evidence_boundaries,
        "requestedExternalActions": preserved_external_actions,
        "materialSummary": material_summary or None,
    }


def _research_design_revision_prompt(
    *,
    revision: Mapping[str, Any],
    definition_catalog: Sequence[Mapping[str, Any]],
) -> str:
    return (
        "Revise an unconfirmed ResearchDesign from authoritative user edits. "
        "The user controls only the research goal and research work. Rebuild semantic identity, dependencies, "
        "target-output ownership, and Definition selection from the frozen catalog; do not copy a prior node contract. "
        "Return a complete PlannerSemanticProposalV2 JSON object only. "
        "You MUST return exactly one workItem for every researchSteps row and no extra workItems. "
        "Each workItem.localKey and objective MUST exactly equal its researchSteps localKey and objective. "
        "Choose a semantically matching definitionId whose catalog stage exactly equals that row's stageKey. "
        "Recompute dependsOnLocalKeys, targetOutputRefs, expectedArtifactTypes, and inputRefs so the whole edited plan "
        "is internally coherent. The semantic goal MUST exactly equal researchGoal. "
        "The user did not edit constraints, evidence boundaries, or requested external actions: preserve those arrays "
        "exactly as supplied. Preserve materialSummary when one is supplied. Do not execute research.\n\n"
        + json.dumps(
            {
                "researchGoal": revision["goal"],
                "researchSteps": revision["steps"],
                "preservedConstraints": revision["constraints"],
                "preservedEvidenceBoundaries": revision["evidenceBoundaries"],
                "preservedRequestedExternalActions": revision["requestedExternalActions"],
                "preservedMaterialSummary": revision["materialSummary"],
                "availableDefinitions": list(definition_catalog),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _validate_research_design_revision_semantic(
    semantic: Any,
    *,
    revision: Mapping[str, Any],
    definition_catalog: Sequence[Mapping[str, Any]],
) -> None:
    expected_steps = {str(row["localKey"]): row for row in revision["steps"]}
    actual_steps = {row.local_key: row for row in semantic.work_items}
    semantic_evidence = list(semantic.understanding.evidence_boundaries)
    semantic_material_summary = semantic.understanding.material_summary
    preserved_material_summary = revision["materialSummary"]
    preserved_context_matches = (
        list(semantic.constraints) == list(revision["constraints"])
        and semantic_evidence == list(revision["evidenceBoundaries"])
        and list(semantic.requested_external_actions) == list(revision["requestedExternalActions"])
        and (preserved_material_summary is None or semantic_material_summary == preserved_material_summary)
    )
    if (
        semantic.goal != revision["goal"]
        or set(actual_steps) != set(expected_steps)
        or not preserved_context_matches
    ):
        raise _PlannerSemanticProposalError(
            "planner did not preserve the authoritative research design revision",
            safe_error_code="research_design_revision_semantic_mismatch",
            safe_field_paths=(
                "/goal",
                "/workItems",
                "/constraints",
                "/understanding/evidenceBoundaries",
                "/requestedExternalActions",
                "/understanding/materialSummary",
            ),
        )
    stage_by_definition = {
        str(row.get("definitionId") or ""): str(row.get("stage") or "")
        for row in definition_catalog
    }
    bad_paths: list[str] = []
    for index, row in enumerate(semantic.work_items):
        expected = expected_steps[row.local_key]
        if row.objective != expected["objective"]:
            bad_paths.append(f"/workItems/{index}/objective")
        if stage_by_definition.get(row.definition_id) != expected["stageKey"]:
            bad_paths.append(f"/workItems/{index}/definitionId")
    if bad_paths:
        raise _PlannerSemanticProposalError(
            "planner mapped the edited research work outside the requested research stage",
            safe_error_code="research_design_revision_semantic_mismatch",
            safe_field_paths=tuple(bad_paths),
        )


def prepare_planner_attempt(
    intent: Mapping[str, Any],
    decision: Any,
    ordinal: int,
    prior_failure: Any = None,
    *,
    definition_catalog: Sequence[Mapping[str, Any]] | None = None,
    semantic_revision: Mapping[str, Any] | None = None,
    workflow_planning_context: Mapping[str, Any] | None = None,
    planning_sources: Sequence[Mapping[str, Any]] | None = None,
) -> PreparedPlannerAttempt | PreparedHybridWorkflowPlannerAttempt:
    """Freeze one Planner attempt without performing network I/O."""
    if ordinal < 1:
        raise ValueError("planner attempt ordinal must be positive")
    if settings.research_orchestration_v1 and ordinal > 2:
        raise ResearchStartPlannerUnavailable(
            "v1.2 semantic planning allows at most one classified repair call"
        )

    raw_input = str(_planner_intent_value(intent, "rawInput", "raw_input", "input", default=""))
    source_page = str(_planner_intent_value(intent, "sourcePage", "source_page", default="home"))
    selected_hint = _planner_intent_value(intent, "selectedHint", "selected_hint")
    decision_snapshot = json.loads(json.dumps(
        _planning_selection_for_prompt(
            decision,
            research_design_only=settings.research_orchestration_v1,
        ),
        ensure_ascii=False,
        sort_keys=True,
    ))

    frozen_definition_catalog: tuple[dict[str, Any], ...] = ()
    prompt_definition_catalog: tuple[dict[str, Any], ...] = ()
    repair_context: dict[str, Any] | None = None
    json_parse_context: dict[str, Any] | None = None
    full_semantic_context: dict[str, Any] | None = None
    revision_spec: dict[str, Any] | None = None
    if settings.research_orchestration_v1:
        if not definition_catalog:
            raise ResearchStartPlannerUnavailable("frozen Definition catalog is required for v1.2 planning")
        frozen_definition_catalog = tuple(
            {
                "definitionId": str(row["definitionId"]),
                "definitionVersion": int(row["definitionVersion"]),
                "pack": str(row.get("pack") or ""),
                "stage": str(row["stage"]),
                "label": str(row["label"]),
                "description": str(row.get("description") or ""),
                "planningApplicability": dict(row.get("planningApplicability") or {}),
                **(
                    {"instancePolicy": dict(row["instancePolicy"])}
                    if isinstance(row.get("instancePolicy"), Mapping)
                    else {"instancePolicy": {"mode": "repeatable"}}
                ),
                "requiredInputRefs": list(row.get("requiredInputRefs") or ()),
                "artifactTypes": list(row.get("artifactTypes") or ()),
                "criterionRefs": list(row.get("criterionRefs") or ()),
                "contractComplete": bool(row.get(
                    "contractComplete",
                    bool(
                        row.get("requiredInputRefs")
                        and row.get("artifactTypes")
                        and row.get("criterionRefs")
                    ),
                )),
            }
            for row in definition_catalog
        )
        definition_ids = [row["definitionId"] for row in frozen_definition_catalog]
        if (
            any(not value for value in definition_ids)
            or len(set(definition_ids)) != len(definition_ids)
            or any(
                row["instancePolicy"].get("mode") not in {"repeatable", "singleton_collection"}
                or not row["pack"]
                or row["planningApplicability"].get("scopeKind") not in {"general", "specialized", "legacy_unspecified"}
                or not str(row["planningApplicability"].get("scopeLabel") or "").strip()
                or (
                    row["contractComplete"]
                    and (
                        not row["requiredInputRefs"]
                        or not row["artifactTypes"]
                        or not row["criterionRefs"]
                    )
                )
                for row in frozen_definition_catalog
            )
        ):
            raise ResearchStartPlannerUnavailable("frozen Definition catalog is invalid")
        if workflow_planning_context is not None and semantic_revision is None:
            # PVD is a deterministic pipeline: produce the full-chain design
            # without an LLM planner call so every PVD task covers S1..S8.
            if str(workflow_planning_context.get("workflowId") or "") == PVD_WORKFLOW_ID:
                return prepare_deterministic_pvd_planner_attempt(
                    raw_input=raw_input,
                    ordinal=ordinal,
                    definition_catalog=frozen_definition_catalog,
                    workflow_planning_context=workflow_planning_context,
                    planning_sources=tuple(planning_sources or ()),
                )
            # Materials full-discovery is likewise deterministic: a fixed
            # five-node chain (literature → seeds → evolution → DFT → report)
            # so repeated runs are stable and comparable.
            if (
                str(workflow_planning_context.get("workflowId") or "")
                == MATERIALS_FULL_DISCOVERY_WORKFLOW_ID
            ):
                return prepare_deterministic_materials_planner_attempt(
                    raw_input=raw_input,
                    ordinal=ordinal,
                    definition_catalog=frozen_definition_catalog,
                    workflow_planning_context=workflow_planning_context,
                    planning_sources=tuple(planning_sources or ()),
                )
            provider_config = _workflow_semantic_provider_config()
            if provider_config is None:
                raise ResearchStartPlannerUnavailable(
                    "Workflow scientific-semantic provider configuration is unavailable"
                )
            return prepare_hybrid_workflow_planner_attempt(
                raw_input=raw_input,
                ordinal=ordinal,
                definition_catalog=frozen_definition_catalog,
                workflow_planning_context=workflow_planning_context,
                provider_config=provider_config,
                planning_sources=tuple(planning_sources or ()),
            )

        if semantic_revision is None:
            provider_config = _freeform_semantic_provider_config()
            if provider_config is None:
                raise ResearchStartPlannerUnavailable(
                    "freeform scientific Planner provider configuration is unavailable"
                )
            try:
                return prepare_freeform_research_design_attempt(
                    raw_input=raw_input,
                    source_page=source_page,
                    selected_hint=str(selected_hint) if selected_hint is not None else None,
                    ordinal=ordinal,
                    definition_catalog=frozen_definition_catalog,
                    provider_config=provider_config,
                    planning_sources=tuple(planning_sources or ()),
                    prior_failure=prior_failure,
                )
            except ValueError as exc:
                raise ResearchStartPlannerUnavailable(str(exc)) from exc

        prompt_definition_catalog = tuple({
            "definitionId": row["definitionId"],
            "stage": row["stage"],
            "label": row["label"],
            "description": row["description"],
            "planningApplicability": row["planningApplicability"],
            "instancePolicy": row["instancePolicy"],
            "requiredInputRefs": row["requiredInputRefs"],
            "artifactTypes": row["artifactTypes"],
        } for row in frozen_definition_catalog)
        if semantic_revision is not None:
            if ordinal != 1 or prior_failure is not None:
                raise ResearchStartPlannerUnavailable("research design revision uses one complete semantic planner attempt")
            revision_spec = _normalize_research_design_revision_spec(semantic_revision)
        repair_context = _focused_semantic_repair_context(prior_failure)
        json_parse_context = (
            None if repair_context is not None else _json_parse_repair_context(prior_failure)
        )
        full_semantic_context = (
            None
            if repair_context is not None or json_parse_context is not None
            else _full_semantic_repair_context(prior_failure)
        )
        if (
            prior_failure is not None
            and repair_context is None
            and json_parse_context is None
            and full_semantic_context is None
        ):
            raise ResearchStartPlannerUnavailable(
                "only a classified focused semantic, json_parse, or full_semantic failure may consume the second planner call"
            )
        if repair_context is None:
            output_schema = _strict_semantic_output_schema()
            output_schema["$defs"]["PlannerSemanticWorkItemV2"]["properties"]["definitionId"]["enum"] = definition_ids
        else:
            output_schema = _focused_semantic_repair_schema(repair_context)
    else:
        output_schema = PLANNER_PLAN_SCHEMA

    system_prompt = _planner_system_prompt()
    if revision_spec is not None:
        user_prompt = _research_design_revision_prompt(
            revision=revision_spec,
            definition_catalog=prompt_definition_catalog,
        )
    elif repair_context is not None:
        coverage_instruction = (
            " The accepted proposal omitted an explicitly requested research capability. Replace only "
            "understanding.targetOutputs and workItems so each requested capability is owned by a semantically "
            "matching Definition and targetOutputRefs. Do not add unrequested capability families and do not "
            "optimize for a work-item count."
            if repair_context.get("repairMode") == "capability_coverage"
            else ""
        )
        target_requirement_instruction = (
            " completionMode=professional_judgment is valid only when the selected workItem Definition already "
            "declares exactly one authorized criterionRefs evaluator. Use definitionContractFacts; do not invent "
            "evaluators, agents, or Definition contracts. Repair only the listed targetRequirements fields."
            if "targetRequirements.completionMode"
            in _focused_semantic_repair_fields(repair_context)[0]
            else ""
        )
        user_prompt = (
            "Repair only the classified semantic fields. Do not return a complete semantic proposal and do not "
            "return any ResearchDesign mechanical fields. Preserve every semantic field not listed in fieldPaths."
            + coverage_instruction
            + target_requirement_instruction
            + "\n\n"
            + json.dumps(
                {
                    "input": raw_input,
                    "acceptedSemanticFields": repair_context["semanticProposal"],
                    "fieldPaths": repair_context["fieldPaths"],
                    "definitionCandidates": repair_context.get("definitionCandidates", []),
                    "artifactCandidatesByDefinition": repair_context.get("artifactCandidatesByDefinition", {}),
                    "failedTargetRequirement": repair_context.get("failedTargetRequirement"),
                    "definitionContractFacts": repair_context.get("definitionContractFacts", {}),
                    "missingTargetOutputs": repair_context.get("missingTargetOutputs", []),
                    "requestedCapabilities": repair_context.get("requestedCapabilities", []),
                    "missingCapabilities": repair_context.get("missingCapabilities", []),
                    "definitionCandidatesByCapability": repair_context.get("definitionCandidatesByCapability", {}),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        user_prompt = _planner_user_prompt(
            raw_input=raw_input,
            planning_selection=decision_snapshot,
            source_page=source_page,
            selected_hint=selected_hint,
            definition_catalog=prompt_definition_catalog,
            workflow_planning_context=workflow_planning_context,
        )
        if json_parse_context is not None:
            excerpt = str(json_parse_context.get("previousContentExcerpt") or "")
            user_prompt += (
                "\n\nYour previous draft was not valid JSON. Return a complete PlannerSemanticProposalV2 "
                "JSON object only (no markdown fences, no commentary). "
                f"Previous content excerpt: {json.dumps(excerpt, ensure_ascii=False)}"
            )
        elif full_semantic_context is not None:
            excerpt = str(full_semantic_context.get("previousContentExcerpt") or "")
            summary = str(full_semantic_context.get("validationSummary") or "")
            field_paths = list(full_semantic_context.get("fieldPaths") or ())
            user_prompt += (
                "\n\nYour previous draft failed the semantic planning contract. Return a complete "
                "corrected PlannerSemanticProposalV2 JSON object only (no markdown fences, no commentary). "
                "Fix every listed field path and keep semantic meaning unchanged.\n"
                f"Invalid field paths: {json.dumps(field_paths, ensure_ascii=False)}\n"
                f"Validation summary: {json.dumps(summary, ensure_ascii=False)}\n"
                f"Previous content excerpt: {json.dumps(excerpt, ensure_ascii=False)}"
            )

    if not settings.research_orchestration_v1 and prior_failure is not None:
        feedback = (
            prior_failure
            if isinstance(prior_failure, str)
            else json.dumps(prior_failure, ensure_ascii=False, sort_keys=True)
        )
        user_prompt += (
            "\n\nYour previous draft failed validation. Return a corrected complete legacy draft JSON only. "
            "Preserve requiredTaskPair and source boundaries. Validation feedback: " + feedback
        )


    provider = _planner_provider()
    if settings.research_orchestration_v1:
        from app.modules.research_design_compiler import compile_research_design_proposal
        from app.modules.research_design_service import PlannerProviderResult

        def decode_v12(payload: Mapping[str, Any], body: Mapping[str, Any]) -> PlannerProviderResult:
            semantic_payload = (
                _apply_focused_semantic_repair(repair_context, payload)
                if repair_context is not None
                else dict(payload)
            )
            semantic_payload = _without_model_completion_authority(semantic_payload)
            semantic = _validate_semantic_payload(
                semantic_payload,
                raw_input=raw_input,
                definition_catalog=frozen_definition_catalog,
            )
            if revision_spec is not None:
                _validate_research_design_revision_semantic(
                    semantic,
                    revision=revision_spec,
                    definition_catalog=frozen_definition_catalog,
                )
            proposal = compile_research_design_proposal(
                semantic,
                definition_catalog=frozen_definition_catalog,
                raw_input=raw_input,
                workflow_planning_context=workflow_planning_context,
            )
            provider_call_ref = body.get("id")
            if not isinstance(provider_call_ref, str) or not provider_call_ref.strip():
                raise ValueError("Planner provider response is missing a call identity")
            return PlannerProviderResult(
                proposal=proposal.model_dump(mode="json", by_alias=True),
                provider_call_ref=provider_call_ref.strip(),
            )

    if provider == "huiyan_chat_completions":
        api_key = os.environ.get("LLM_PROVIDER_API_KEY")
        api_base = os.environ.get("LLM_PROVIDER_BASE_URL")
        if not api_key or not api_base:
            raise ResearchStartPlannerUnavailable("HUIYAN planner provider configuration is incomplete")
        model = _planner_chat_model()
        schema_name = (
            "planner_semantic_proposal_v2"
            if settings.research_orchestration_v1
            else "research_start_draft_plan"
        )
        response_format = structured_response_format(
            provider,
            schema_name=schema_name,
            enabled=_planner_response_format_enabled(),
        )
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": _planner_temperature(),
            "max_tokens": _planner_max_tokens(),
        }
        if response_format:
            payload["response_format"] = response_format
        payload = apply_openai_compatible_thinking(
            payload, "OPENAIR_RESEARCH_START_PLANNER_LLM_THINKING"
        )
        endpoint = f"{api_base.rstrip('/')}/v1/chat/completions"

        def decode(body: Mapping[str, Any]) -> Any:
            content, _choice = read_provider_content(extract_final_chat_content, body)
            parsed = _parse_planner_json_object(content)
            if settings.research_orchestration_v1:
                return decode_v12(parsed, body)
            plan = _plan_from_llm_payload(
                parsed,
                raw_input=raw_input,
                planning_selection=decision_snapshot,
                selected_hint=selected_hint,
            )
            return replace(plan, planningAudit=_live_planning_audit(
                provider=provider,
                requested_model=model,
                response_body=dict(body),
            ))
    elif provider == "openai_responses":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ResearchStartPlannerUnavailable("OpenAI planner provider configuration is incomplete")
        model = _planner_openai_model()
        schema_name = (
            "planner_semantic_repair_v2"
            if settings.research_orchestration_v1 and repair_context is not None
            else "planner_semantic_proposal_v2"
            if settings.research_orchestration_v1
            else "research_start_draft_plan"
        )
        response_format = structured_response_format(
            provider,
            schema_name=schema_name,
        )
        payload = _build_responses_payload(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output_schema=output_schema,
            schema_name=schema_name,
            max_completion_tokens=_planner_max_tokens(),
            reasoning_effort=os.environ.get("OPENAIR_RESEARCH_START_PLANNER_REASONING_EFFORT") or None,
            temperature=_planner_temperature(),
        )
        endpoint = f"{os.environ.get('OPENAI_API_BASE_URL', 'https://api.openai.com').rstrip('/')}/v1/responses"

        def decode(body: Mapping[str, Any]) -> Any:
            parsed = _parse_planner_json_object(read_provider_content(_extract_responses_output_text, body))
            if settings.research_orchestration_v1:
                return decode_v12(parsed, body)
            plan = _plan_from_llm_payload(
                parsed,
                raw_input=raw_input,
                planning_selection=decision_snapshot,
                selected_hint=selected_hint,
            )
            return replace(plan, planningAudit=_live_planning_audit(
                provider=provider,
                requested_model=model,
                response_body=dict(body),
            ))
    else:
        raise ResearchStartPlannerUnavailable(f"unsupported research start planner provider: {provider}")

    payload, _ = _prepare_payload_for_http(payload)
    return PreparedPlannerAttempt(
        phase="research_planner",
        ordinal=ordinal,
        provider=provider,
        model=model,
        endpoint=endpoint,
        payload=payload,
        response_format=response_format,
        timeout_seconds=research_start_planner_task_budget_seconds(),
        decode=decode,
        provider_schema=output_schema,
    )

def execute_planner_attempt(
    prepared: (
        PreparedPlannerAttempt
        | PreparedHybridWorkflowPlannerAttempt
        | PreparedDeterministicPvdPlannerAttempt
        | PreparedDeterministicMaterialsPlannerAttempt
    ),
    *,
    transport: ResearchStartHttpTransport | None = None,
) -> Any:
    """Execute one Planner attempt; Workflow semantics use the normal provider failure boundary."""
    if isinstance(prepared, PreparedDeterministicPvdPlannerAttempt):
        return prepared.execute()
    if isinstance(prepared, PreparedDeterministicMaterialsPlannerAttempt):
        return prepared.execute()
    if isinstance(prepared, PreparedHybridWorkflowPlannerAttempt):
        return prepared.execute(transport=transport)
    return execute_prepared_attempt(prepared, transport=transport)


def _is_repairable_planner_error(error: ResearchStartPlannerError) -> bool:
    message = str(error)
    return (
        message.startswith("LLM planner claimed completed work:")
        or "conflicts with taskType" in message
    )


def _run_live_research_start_planner(
    *,
    raw_input: str,
    planning_selection: Any | None,
    source_page: str,
    selected_hint: str | None,
) -> ResearchStartPlan:
    provider = _planner_provider()
    if provider == "huiyan_chat_completions":
        runner = _run_chat_completions_planner
        payload, planning_audit = runner(
            raw_input=raw_input,
            planning_selection=planning_selection,
            source_page=source_page,
            selected_hint=selected_hint,
        )
    elif provider == "openai_responses":
        runner = _run_openai_responses_planner
        payload, planning_audit = runner(
            raw_input=raw_input,
            planning_selection=planning_selection,
            source_page=source_page,
            selected_hint=selected_hint,
        )
    else:
        raise ResearchStartPlannerUnavailable(f"unsupported research start planner provider: {provider}")
    try:
        plan = _plan_from_llm_payload(
            payload,
            raw_input=raw_input,
            planning_selection=planning_selection,
            selected_hint=selected_hint,
        )
    except ResearchStartPlannerError as error:
        if not _is_repairable_planner_error(error):
            raise
        original_run_id = planning_audit.get("runId")
        payload, planning_audit = runner(
            raw_input=raw_input,
            planning_selection=planning_selection,
            source_page=source_page,
            selected_hint=selected_hint,
            repair_instruction=_planner_repair_instruction(payload, error),
        )
        plan = _plan_from_llm_payload(
            payload,
            raw_input=raw_input,
            planning_selection=planning_selection,
            selected_hint=selected_hint,
        )
        planning_audit = {
            **planning_audit,
            "repairCount": "1",
            **({"repairedFromRunId": original_run_id} if original_run_id else {}),
        }
    return replace(plan, planningAudit=planning_audit)


def _deterministic_research_start_plan(
    *,
    raw_input: str,
    planning_selection: Any | None = None,
    selected_hint: str | None = None,
) -> ResearchStartPlan:
    task_type, research_type = _task_type(raw_input, selected_hint)
    planning_reason = _planning_selection_reason(planning_selection)
    if not planning_reason:
        planning_reason = (
            "formal_semantic_authority_unavailable;"
            " generic_research_planning_fallback"
        )

    understanding = ResearchStartUnderstanding(
        taskType=task_type,
        userIntent=(
            "整理已有材料中的证据、推断、冲突和缺口"
            if task_type == "evidence_organizing"
            else "从可读材料中抽取 claim、证据、推断和缺口"
            if task_type == "evidence_extraction"
            else "把已有证据整理成机制链、争议点、推断边和缺口"
            if task_type == "mechanism_mapping"
            else "把宽泛研究方向收敛成可研究、可验证的问题候选"
            if task_type == "question_refinement"
            else "基于已有材料识别有依据的研究空缺和证据缺口"
            if task_type == "gap_identification"
            else "把待验证假设拆成变量、对照、基线和分析计划"
            if task_type == "validation_design"
            else "审计结果、记录、脚本和证据链是否可追溯可复现"
            if task_type == "result_audit"
            else "准备关键文献定位启动草案"
            if task_type == "key_literature_positioning"
            else "按正式来源边界组织结构化文献对比任务"
            if task_type == "comparative_literature_analysis"
            else "把研究目标整理成阶段计划、输入清单、产物定义和确认点"
        ),
        materialSummary=_material_summary(task_type),
        targetOutputs=_target_outputs(task_type),
        evidenceBoundaries=_evidence_boundaries(task_type),
        missingInputs=_missing_inputs(task_type),
        riskNotes=_risk_notes(task_type),
        planningRationale=planning_reason,
    )
    plan = ResearchStartPlan(
        planningSource="deterministic_fallback",
        researchType=research_type,
        understanding=understanding,
        selectedCapabilities=_planned_capabilities(
            task_type,
            selected_hint,
        ),
        stageDescriptions=_with_goal_framing(
            _stage_descriptions(task_type),
            raw_input,
        ),
    )
    build_creation_plan_stages(plan, pack="sdr")
    return plan

def _is_transient_live_planner_unavailable(error: ResearchStartPlannerUnavailable) -> bool:
    """Only infrastructure unavailability may fall back to a recoverable draft."""
    detail = str(error).lower()
    return any(
        marker in detail
        for marker in (
            "timed out",
            "timeout",
            "connection",
            "network",
            "api_key environment variable not set",
            "api_base_url environment variable not set",
        )
    )


def _is_safe_live_planner_fallback(error: Exception) -> bool:
    """Permit only discarded, unsafe completion claims to use a safe draft."""
    return isinstance(error, ResearchStartPlannerError) and str(error).startswith(
        "LLM planner claimed completed work:"
    )


def plan_research_start_draft(
    *,
    raw_input: str,
    planning_selection: Any | None = None,
    source_page: str = "home",
    selected_hint: str | None = None,
) -> ResearchStartPlan:
    """Create a validated draft planning contract.

    Semantic task identity comes from an explicit selected hint or the live semantic
    Planner. Deterministic fallback only
    projects those formal facts into a safe execution graph; it never reclassifies
    free text.
    """

    if settings.research_orchestration_v1:
        fallback = _deterministic_research_start_plan(
            raw_input=raw_input,
            planning_selection=planning_selection,
            selected_hint=selected_hint,
        )
        formal_task_type = _expected_task_type_from_hint(selected_hint)
        return replace(
            fallback,
            planningAudit={
                "mode": "deterministic_fallback",
                "reason": (
                    "formal_hint_projection"
                    if formal_task_type
                    else "semantic_authority_unavailable_generic_fallback"
                ),
            },
        )

    if _expected_task_type_from_hint(selected_hint):
        return _deterministic_research_start_plan(
            raw_input=raw_input,
            planning_selection=planning_selection,
            selected_hint=selected_hint,
        )

    if should_use_live_research_start_planner():
        try:
            return _run_live_research_start_planner(
                raw_input=raw_input,
                planning_selection=planning_selection,
                source_page=source_page,
                selected_hint=selected_hint,
            )
        except (
            ResearchStartPlannerUnavailable,
            ResearchStartPlannerError,
        ) as exc:
            if not (
                isinstance(exc, ResearchStartPlannerUnavailable)
                and _is_transient_live_planner_unavailable(exc)
            ) and not _is_safe_live_planner_fallback(exc):
                raise ResearchStartPlannerUnavailable(
                    f"live research-start planner unavailable: {exc}"
                ) from exc

            fallback = _deterministic_research_start_plan(
                raw_input=raw_input,
                planning_selection=planning_selection,
                selected_hint=selected_hint,
            )
            return replace(
                fallback,
                planningAudit={
                    "mode": "deterministic_fallback",
                    "reason": (
                        "live_planner_unsafe_completion_claim"
                        if _is_safe_live_planner_fallback(exc)
                        else "live_planner_unavailable"
                    ),
                },
            )
        except Exception as exc:
            if _live_planner_required():
                raise ResearchStartPlannerUnavailable(
                    f"live research-start planner unavailable: {exc}"
                ) from exc

    return _deterministic_research_start_plan(
        raw_input=raw_input,
        planning_selection=planning_selection,
        selected_hint=selected_hint,
    )

def _capability_substage_rows(capability: PlannedCapability) -> list[dict[str, Any]]:
    if capability.substages:
        return [
            {
                "substage": substage,
                "skills": capability.skillIds,
                "rationale": capability.rationale,
                "expectedArtifactTypes": capability.expectedArtifactTypes,
                "confirmationRequired": capability.confirmationRequired,
            }
            for substage in capability.substages
        ]

    rows = resolve_skill_substage_items(capability.skillIds)
    for row in rows:
        row["rationale"] = capability.rationale
        row["expectedArtifactTypes"] = list(capability.expectedArtifactTypes)
        row["confirmationRequired"] = capability.confirmationRequired
    return rows


def build_creation_plan_stages(plan: ResearchStartPlan, *, pack: str = "sdr") -> list[dict[str, Any]]:
    try:
        selected: list[dict[str, Any]] = []
        for capability in plan.selectedCapabilities:
            _validate_durable_skill_ids(capability.skillIds)
            selected.extend(_capability_substage_rows(capability))
        return build_creation_plan_stages_from_substages(
            selected,
            pack=pack,
            stage_descriptions=plan.stageDescriptions,
        )
    except ExecutionGraphError as exc:
        raise ResearchStartPlannerError(str(exc)) from exc
