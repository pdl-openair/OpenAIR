from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from app.auth import RequestIdentity, resolve_request_identity
from app.config import settings
from app.models.schemas import (
    ArtifactOut,
    ReleaseGatedStarterOut,
    StarterListOut,
    StarterUseIn,
    StarterUseOut,
    TaskOut,
)
from app.modules.literature_live import markdown_artifacts, run_literature_live
from app.modules.release_gated_starters import find_starter, list_public_starters
from app.modules.task_draft_builder import build_research_start_draft, clean_research_input, derive_research_title
from app.modules.task_store import store

router = APIRouter(prefix="/starters", tags=["starters"])


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _starter_title(starter: dict, body: StarterUseIn) -> str:
    provided = (body.title or "").strip()
    if provided:
        return provided
    return f"{starter['title']}草案"


def _task_title_from_goal(starter: dict, body: StarterUseIn, goal: str) -> str:
    provided = (body.title or "").strip()
    if provided:
        return derive_research_title(provided)
    cleaned_goal = clean_research_input(_clean_starter_goal(goal))
    if cleaned_goal:
        cleaned_goal = re.sub(r"^评估", "", cleaned_goal).strip() or cleaned_goal
        return cleaned_goal[:48]
    return _starter_title(starter, body)


_EXAMPLE_POLLUTION_PATTERNS = [
    re.compile(r"示例中的科研\s*agent", re.IGNORECASE),
    re.compile(r"demo", re.IGNORECASE),
    re.compile(r"fixture", re.IGNORECASE),
    re.compile(r"task_cs_ope_001", re.IGNORECASE),
    re.compile(r"OPE\s*方法在训练动态中的可靠性研究", re.IGNORECASE),
]


def _clean_starter_goal(value: str) -> str:
    cleaned = clean_research_input(value)
    for pattern in _EXAMPLE_POLLUTION_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"[：:，,；;。.\s]+$", "", cleaned)
    return cleaned.strip()


def _starter_goal(starter: dict, body: StarterUseIn) -> str:
    provided = _clean_starter_goal((body.goalSummary or "").strip())
    if provided:
        return provided
    return f"从“{starter['title']}”起点生成可审阅的下一步计划。"


def _workflow_plan(goal: str) -> dict:
    return {
        "goalSummary": goal,
        "domain": "research_task",
        "skillPack": "sdr",
        "researchType": "workflow_planning",
        "clarifications": [
            "确认研究问题、目标用户和交付物边界。",
            "补充已有来源、数据或实验材料；缺失时保持为待补充项。",
            "确认后再创建正式任务并进入工作台执行。",
        ],
        "stages": [
            {
                "stageKey": "problem_modeling",
                "description": "明确研究目标、输入材料和成功标准。",
                "subStages": ["intent_intake", "constraint_mapping"],
                "substages": [
                    {"substage": "intent_intake", "skills": ["new-task-planning"]},
                    {"substage": "constraint_mapping", "skills": ["task-breakdown"]},
                ],
            },
            {
                "stageKey": "solution_design",
                "description": "形成阶段化工作流和产物清单。",
                "subStages": ["workflow_design", "artifact_contract"],
                "substages": [
                    {"substage": "workflow_design", "skills": ["task-breakdown"]},
                    {"substage": "artifact_contract", "skills": ["quality-audit"]},
                ],
            },
            {
                "stageKey": "execution_validation",
                "description": "确认前置输入、失败边界和人工复核点。",
                "subStages": ["readiness_check"],
                "substages": [{"substage": "readiness_check", "skills": ["result-acceptance"]}],
            },
            {
                "stageKey": "insight_generation",
                "description": "确认下一阶段可执行动作。",
                "subStages": ["next_step_confirmation"],
                "substages": [{"substage": "next_step_confirmation", "skills": ["quality-audit"]}],
            },
        ],
        "readyToConfirm": True,
    }


def _workflow_draft_markdown(starter: dict, goal: str, task_id: str) -> str:
    return "\n".join(
        [
            "# 工作流草案",
            "",
            f"起点：{starter['title']}",
            "任务：当前任务",
            "",
            "## 用户目标",
            goal,
            "",
            "## 建议阶段",
            "1. 问题建模：明确研究问题、材料、约束和成功标准。",
            "2. 方案设计：选择工作流、阶段产物和人工确认点。",
            "3. 执行校验：检查前置输入、失败边界和来源可信度。",
            "4. 洞察生成：确认下一阶段动作，不把草案当作科研结论。",
            "",
            "## 需要确认",
            "- 是否已有来源、数据、实验结果或代码仓库。",
            "- 哪些产物需要人工复核后才能继续。",
            "- 是否允许创建正式任务进入工作台执行。",
            "",
            "## 边界",
            "- 本草案没有调用外部 API、模型训练平台或外部科研能力。",
            "- 本草案不是文献综述、证据抽取或训练结果。",
            "- 确认前不会进入正式执行。",
        ]
    )


def _gap_markdown(starter: dict, missing: list[str]) -> str:
    lines = [
        "# 准备缺口",
        "",
        f"起点：{starter['title']}",
        "",
        "## 当前不能继续的原因",
        "这个起点需要先补齐前置条件；系统不会创建空任务冒充能力执行。",
        "",
        "## 缺少内容",
    ]
    lines.extend(f"- {item}" for item in missing)
    lines.extend(
        [
            "",
            "## 下一步",
            "补齐上述材料后再继续，或先使用工作流规划起点生成可审阅计划。",
        ]
    )
    return "\n".join(lines)


def _failure_state(starter: dict, kind: str) -> dict:
    for state in starter.get("failureStates") or []:
        if state.get("state") == kind:
            return state
    states = starter.get("failureStates") or []
    return states[0] if states else {}


def _create_local_record(task_id: str, starter: dict, *, artifact_id: str | None = None) -> str:
    started_at = _utc_now()
    call = store.add_capability_call(
        task_id,
        skill_id="release_gated_starter_packaging",
        status="running",
        runtime="openair_local_release_gate",
        started_at=started_at,
        stage_key="problem_modeling",
    )
    if not call:
        raise HTTPException(status_code=500, detail="starter run record creation failed")
    store.patch_capability_call(
        task_id,
        call["id"],
        status="completed",
        result_summary=(
            f"Release-gated starter outcome created for {starter['starterId']}; "
            "local packaging record only, no external API/model call."
        ),
        ended_at=_utc_now(),
        artifact_refs=[artifact_id] if artifact_id else [],
        output_artifact_refs=[artifact_id] if artifact_id else [],
        stage_key="problem_modeling",
        metadata={
            "callVisibility": "internal",
            "audience": "internal",
            "sourceMode": "none",
            "partialFailureImpact": "Starter packaging only; no scientific capability was executed.",
        },
    )
    return call["id"]


def _create_literature_record(task_id: str, starter: dict, run_summary: str) -> str:
    started_at = _utc_now()
    call = store.add_capability_call(
        task_id,
        skill_id="traceable_literature_live_path",
        status="running",
        runtime="openair_literature_live_adapter",
        started_at=started_at,
        stage_key="problem_modeling",
    )
    if not call:
        raise HTTPException(status_code=500, detail="literature run record creation failed")
    store.patch_capability_call(
        task_id,
        call["id"],
        status="completed",
        result_summary=run_summary,
        ended_at=_utc_now(),
        stage_key="problem_modeling",
        metadata={
            "starterId": starter["starterId"],
            "runMode": "live",
            "sourceMode": "live",
            "provider": "OpenAlex/arXiv",
            "adapter": "traceable_literature_live",
            "partialFailureImpact": "metadata/abstract-bounded literature research only; no fulltext parsing.",
        },
    )
    return call["id"]


def _create_literature_gap_task(
    starter: dict,
    body: StarterUseIn,
    identity: RequestIdentity,
    goal: str,
    run,
    failure: dict,
) -> StarterUseOut:
    starter_out = ReleaseGatedStarterOut.model_validate(starter)
    task = store.create_task(
        {
            "title": body.title or f"{starter['title']}：{goal[:40]}",
            "goalSummary": goal,
            "domain": "research_task",
            "skillPack": "sdr",
            "currentStage": "problem_modeling",
            "status": "running",
            "creationStatus": "confirmed",
            "resultSource": "live",
            "ownerId": identity["ownerId"],
            "workspaceId": identity["workspaceId"],
            "createdBy": identity["createdBy"],
        }
    )
    task_id = task["taskId"]
    call_id = _create_literature_record(
        task_id,
        starter,
        "Literature starter created a task with a bounded readiness gap state; no literature result was claimed.",
    )
    gap_content = markdown_artifacts(run)["gaps_next"]
    artifact = store.add_artifact(
        task_id,
        kind="literature_readiness_gap",
        title="文献调研准备缺口",
        summary="已记录检索尝试、相关性门禁和下一步补充建议；这不是文献综述结果。",
        format="markdown",
        related_stage="problem_modeling",
        related_skill_id="traceable_literature_live_path",
        source_capability_call_id=call_id,
        storage_type="inline",
        inline_content=gap_content,
        extra_fields={
            "origin": "live",
            "failureKind": failure.get("kind"),
            "relevanceGate": failure.get("relevanceGate") or {},
        },
    )
    task = store.save_stage_result(
        task_id,
        "problem_modeling",
        {
            "status": "running",
            "summary": "已接收研究问题，并完成一次来源准备检查。",
            "risks": "当前没有足够可靠的候选来源可直接作为文献调研结果。",
            "nextSteps": "可继续补充关键词、来源范围或种子论文；这些是增强项，不是创建任务的必填项。",
            "artifactRefs": [artifact["id"]] if artifact else [],
            "subStages": [
                {
                    "subStage": "literature_readiness",
                    "subStageInstanceId": "literature_readiness_gap",
                    "parentStageKey": "problem_modeling",
                    "label": "来源准备检查",
                    "status": "running",
                    "summary": "保留当前检索缺口和下一步建议。",
                    "artifactRefs": [artifact["id"]] if artifact else [],
                }
            ],
        },
    ) or store.get_task(task_id) or task
    return StarterUseOut(
        starter=starter_out,
        outcome="gap_state",
        task=TaskOut.model_validate(task),
        artifact=ArtifactOut.model_validate(artifact) if artifact else None,
        state={
            "kind": "literature_readiness_gap",
            "missingInputs": [],
            "suggestedInputs": ["关键词", "来源范围", "种子论文、DOI 或 arXiv ID"],
            "artifactExpectation": starter["artifactExpectation"],
            "failureState": failure,
            "failureBoundary": "已创建任务并保留准备缺口；不会把低相关候选伪装成文献调研结果。",
            "artifactShell": gap_content,
            "attemptedSourceApis": failure.get("attemptedSourceApis", []),
            "attemptedQueries": failure.get("attemptedQueries", []),
            "retrievalCount": failure.get("retrievalCount", run.source_count),
            "dedupedCount": failure.get("dedupedCount", run.deduped_count),
            "relevantCandidateCount": failure.get("relevantCandidateCount", 0),
            "topKRelevantCount": failure.get("topKRelevantCount", 0),
            "relevanceGate": failure.get("relevanceGate", run.relevance_gate),
            "partialArtifacts": failure.get("partialArtifacts", []),
        },
        nextAction="已进入工作台；可查看准备缺口，也可以继续补充关键词或种子论文提高检索质量。",
    )


def _literature_summary_markdown(run) -> str:
    return "\n".join(
        [
            "# 文献调研运行摘要",
            "",
            f"- topic: {run.topic}",
            f"- source APIs attempted: {', '.join(a.source_api for a in run.attempted_apis)}",
            f"- retrieval result count: {run.source_count}",
            f"- deduped result count: {run.deduped_count}",
            f"- top_k count: {len(run.top_k)}",
            "- source mode boundary: metadata/abstract-bounded; no fixture/replay used as live evidence.",
        ]
    )


LITERATURE_WORKFLOW_SUBSTAGES = {
    "problem_modeling": [
        {
            "key": "research_plan",
            "label": "检索策略",
            "description": "记录查询策略、关键词和来源优先级。",
            "artifactKind": "literature_search_strategy",
        },
        {
            "key": "source_table",
            "label": "来源表",
            "description": "人工筛选前的可追溯候选来源表。",
            "artifactKind": "source_table",
        },
        {
            "key": "recommended_readings",
            "label": "候选来源",
            "description": "真实检索候选；科研相关性和阅读优先级待正式 Agent 判断。",
            "artifactKind": "top_k_source_set",
        },
        {
            "key": "literature_synthesis",
            "label": "候选摘要摘录",
            "description": "仅摘录真实候选的题名/摘要，不形成科研综合判断。",
            "artifactKind": "short_literature_review_draft",
        },
        {
            "key": "gap_next_steps",
            "label": "缺口与下一步",
            "description": "记录来源缺口、失败边界和下一步建议。",
            "artifactKind": "literature_gaps_next_steps",
        },
    ],
    "solution_design": [
        {
            "key": "literature_search",
            "label": "补充来源",
            "description": "根据人工复核结果补充关键来源或记录允许带缺口继续。",
        },
    ],
    "execution_validation": [
        {
            "key": "evidence_extraction",
            "label": "整理证据",
            "description": "将确认来源中的证据整理为可回溯条目。",
        },
    ],
    "insight_generation": [
        {
            "key": "report_generation",
            "label": "形成产物",
            "description": "生成报告或结论包，并标注证据边界和未解决缺口。",
        },
    ],
}


LITERATURE_WORKFLOW_STAGES = [
    {
        "stageKey": "problem_modeling",
        "label": "调研与证据整理",
        "entryCondition": "创建 GraphRAG / 文献调研任务后自动进入。",
        "completionCondition": "已生成真实来源表和候选摘要，并由正式 Agent 或人工完成科研筛选。",
        "lockedReason": None,
        "primaryAction": "先让正式 key_literature_positioning Agent 或人工筛选真实来源表。",
        "canSkip": False,
        "canReview": True,
        "canLock": False,
    },
    {
        "stageKey": "solution_design",
        "label": "补充来源",
        "entryCondition": "调研与证据整理阶段完成来源复核，或明确标记允许带缺口继续。",
        "completionCondition": "关键来源缺口已补充，失败来源已有影响说明。",
        "lockedReason": "当前来源仍待人工筛选，不能直接进入补充来源阶段。",
        "primaryAction": "回到调研与证据整理，先筛选推荐来源并记录缺口。",
        "canSkip": False,
        "canReview": True,
        "canLock": True,
    },
    {
        "stageKey": "execution_validation",
        "label": "整理证据",
        "entryCondition": "候选来源已确认，且需要抽取的证据字段已明确。",
        "completionCondition": "证据条目可回溯到来源，冲突和不确定性已标注。",
        "lockedReason": "来源包尚未确认，证据整理阶段未解锁。",
        "primaryAction": "先完成来源补充或确认现有来源足够。",
        "canSkip": False,
        "canReview": True,
        "canLock": True,
    },
    {
        "stageKey": "insight_generation",
        "label": "形成产物",
        "entryCondition": "证据整理完成，且关键结论已有可追溯依据。",
        "completionCondition": "报告/结论包明确可用边界、证据来源、未解决缺口和后续动作。",
        "lockedReason": "证据尚未整理完成，不能形成最终产物。",
        "primaryAction": "先整理证据并处理缺口，再生成可交付产物。",
        "canSkip": False,
        "canReview": True,
        "canLock": True,
    },
]


def _literature_substages(artifacts: list[dict]) -> list[dict]:
    by_kind = {artifact.get("kind"): artifact.get("id") for artifact in artifacts}
    return [
        {
            "subStage": spec["key"],
            "label": spec["label"],
            "status": "completed",
            "artifactRefs": [artifact_id],
        }
        for spec in LITERATURE_WORKFLOW_SUBSTAGES["problem_modeling"]
        if (artifact_id := by_kind.get(spec.get("artifactKind")))
    ]


def _literature_stage_substages(stage_key: str, *, status: str = "not_started") -> list[dict]:
    return [
        {
            "subStage": spec["key"],
            "label": spec["label"],
            "status": status,
            "artifactRefs": [],
            "summary": spec.get("description"),
        }
        for spec in LITERATURE_WORKFLOW_SUBSTAGES.get(stage_key, [])
        if not spec.get("artifactKind")
    ]


def _literature_stage_catalog() -> list[dict]:
    return [
        {
            "stageKey": stage["stageKey"],
            "allowedSubStages": [
                {
                    "key": spec["key"],
                    "stageKey": stage["stageKey"],
                    "label": spec["label"],
                    "description": spec.get("description", ""),
                    "enabled": True,
                }
                for spec in LITERATURE_WORKFLOW_SUBSTAGES.get(stage["stageKey"], [])
            ],
        }
        for stage in LITERATURE_WORKFLOW_STAGES
    ]


def _add_literature_artifacts(task_id: str, call_id: str, run) -> list[dict]:
    artifact_specs = [
        ("literature_search_strategy", "检索策略", "查询策略、关键词、来源优先级。", "search_strategy"),
        ("source_table", "来源表", "可追溯来源表，包含 provenance 和可读性。", "source_table"),
        ("top_k_source_set", "候选来源清单", "真实检索候选；尚未做科研语义排序。", "top_k"),
        ("source_readiness", "可读性说明", "来源可读性和全文边界说明。", "readiness"),
        ("short_literature_review_draft", "候选摘要摘录", "仅摘录题名/摘要，不形成科研综述判断。", "bounded_review"),
        ("literature_gaps_next_steps", "缺口与下一步", "bounded failure、attempted APIs 和下一步建议。", "gaps_next"),
    ]
    contents = markdown_artifacts(run)
    artifacts = []
    for kind, title, summary, key in artifact_specs:
        artifact = store.add_artifact(
            task_id,
            kind=kind,
            title=title,
            summary=summary,
            format="markdown",
            related_stage="problem_modeling",
            related_skill_id="traceable_literature_live_path",
            source_capability_call_id=call_id,
            storage_type="inline",
            inline_content=contents[key],
            extra_fields={
                "origin": "live",
                "externalProvider": "OpenAlex/arXiv",
            },
        )
        if artifact:
            artifacts.append(artifact)
    return artifacts


def _create_literature_task(starter: dict, body: StarterUseIn, identity: RequestIdentity) -> StarterUseOut:
    goal = _starter_goal(starter, body)
    run = run_literature_live(goal, top_k=8)
    starter_out = ReleaseGatedStarterOut.model_validate(starter)

    if run.failure_state:
        failure = run.failure_state or {}
        if failure.get("kind") != "missing_topic":
            return _create_literature_gap_task(starter, body, identity, goal, run, failure)
        return StarterUseOut(
            starter=starter_out,
            outcome="gap_state",
            state={
                "kind": "literature_bounded_failure",
                "missingInputs": ["可检索 topic、关键词或种子论文"],
                "artifactExpectation": starter["artifactExpectation"],
                "failureState": {
                    "state": "gap_state",
                    "reason": failure.get("reason", "外部文献 API 没有返回可用来源。"),
                    "impact": failure.get("impact", "无法形成完整来源表和短综述。"),
                    "nextAction": failure.get("nextAction", "补充关键词或改写研究问题后重试。"),
                    "retryPolicy": failure.get("retryPolicy", "稍后重试或降低 Top-K。"),
                    "partialOutputStatus": failure.get("partialOutputStatus", "保留检索策略和 attempted APIs。"),
                },
                "failureBoundary": "没有可用来源时不会创建空任务冒充文献调研成功。",
                "artifactShell": markdown_artifacts(run)["gaps_next"],
                "attemptedSourceApis": failure.get("attemptedSourceApis", []),
                "attemptedQueries": failure.get("attemptedQueries", []),
                "retrievalCount": failure.get("retrievalCount", run.source_count),
                "dedupedCount": failure.get("dedupedCount", run.deduped_count),
                "relevantCandidateCount": failure.get("relevantCandidateCount", 0),
                "topKRelevantCount": failure.get("topKRelevantCount", 0),
                "relevanceGate": failure.get("relevanceGate", run.relevance_gate),
                "partialArtifacts": failure.get("partialArtifacts", []),
            },
            nextAction="补充关键词、种子论文或缩小研究问题后重试。",
        )

    task = store.create_task(
        {
            "title": body.title or f"{starter['title']}：{goal[:40]}",
            "goalSummary": goal,
            "domain": "research_task",
            "skillPack": "sdr",
            "currentStage": "problem_modeling",
            "status": "awaiting_confirmation",
            "creationStatus": "confirmed",
            "ownerId": identity["ownerId"],
            "workspaceId": identity["workspaceId"],
            "createdBy": identity["createdBy"],
        }
    )
    task_id = task["taskId"]
    call_id = _create_literature_record(
        task_id,
        starter,
        f"Traceable literature retrieval completed with {run.deduped_count} deduped sources; semantic screening remains pending.",
    )
    artifacts = _add_literature_artifacts(task_id, call_id, run)
    task = store.patch_task(
        task_id,
        {
            "status": "awaiting_confirmation",
            "resultSource": "live",
            "stageCatalog": _literature_stage_catalog(),
        },
    ) or task
    task = store.save_stage_result(
        task_id,
        "problem_modeling",
        {
            "status": "running",
            "summary": f"已形成 {run.deduped_count} 条真实去重来源；科研相关性、角色和阅读优先级尚待正式 Agent 判断。",
            "risks": "当前只有题录/摘要/provenance；没有代码生成的科研相关性排序，也未解析全文。",
            "nextSteps": "唯一下一步：由正式 key_literature_positioning Agent 或人工筛选来源表。",
            "artifactRefs": [a["id"] for a in artifacts],
            "subStages": _literature_substages(artifacts),
            **LITERATURE_WORKFLOW_STAGES[0],
        },
    ) or task
    for stage in LITERATURE_WORKFLOW_STAGES[1:]:
        task = store.save_stage_result(
            task_id,
            stage["stageKey"],
            {
                "status": "not_started",
                "summary": None,
                "risks": stage["lockedReason"],
                "nextSteps": stage["primaryAction"],
                "artifactRefs": [],
                "subStages": _literature_stage_substages(stage["stageKey"]),
                "setCurrentStage": False,
                **stage,
            },
        ) or task
    task = store.patch_task(
        task_id,
        {
            "currentStage": "problem_modeling",
            "status": "awaiting_confirmation",
            "resultSource": "live",
        },
    ) or task
    first_artifact = artifacts[0] if artifacts else None
    return StarterUseOut(
        starter=starter_out,
        outcome="literature_artifacts",
        task=TaskOut.model_validate(task),
        artifact=ArtifactOut.model_validate(first_artifact) if first_artifact else None,
        state={
            "kind": "literature_artifacts",
            "readyToConfirm": False,
            "sourceApisAttempted": [a.__dict__ for a in run.attempted_apis],
            "retrievalResultCount": run.source_count,
            "dedupedResultCount": run.deduped_count,
            "topKCount": 0,
            "failureBoundary": "metadata/abstract-bounded；未使用 fixture/replay 冒充 live；未解析任意 PDF。",
            "artifactShell": _literature_summary_markdown(run),
        },
        nextAction="在工作台查看真实来源表和候选摘要，并进入正式科研筛选。",
    )


@router.get("", response_model=StarterListOut)
@router.get("/", response_model=StarterListOut, include_in_schema=False)
def list_starters() -> StarterListOut:
    return StarterListOut(starters=[ReleaseGatedStarterOut.model_validate(s) for s in list_public_starters()])


@router.post("/{starter_id}/use", response_model=StarterUseOut, status_code=201)
def use_starter(
    starter_id: str,
    body: StarterUseIn,
    identity: RequestIdentity = Depends(resolve_request_identity),
) -> StarterUseOut:
    starter = find_starter(starter_id)
    if not starter:
        raise HTTPException(status_code=404, detail="starter not found")
    if starter["visibility"] != "public_invited_trial" or starter["releaseDecision"] in {"hidden", "internal_only"}:
        raise HTTPException(status_code=404, detail="starter not available")
    if settings.research_orchestration_v1:
        # This standalone T02 branch has no trusted T03 DefinitionSource. Stop
        # before live literature, capability-call, Artifact, or Task creation.
        raise HTTPException(
            status_code=503,
            detail="trusted DefinitionSource is not integrated for v1.2 starter creation",
        )

    starter_out = ReleaseGatedStarterOut.model_validate(starter)
    mode = starter["creationMode"]

    if starter["starterId"] == "project_research_direction_review_v0_3":
        return _create_literature_task(starter, body, identity)

    if mode == "create_workflow_draft_then_confirm":
        goal = _starter_goal(starter, body)
        draft = build_research_start_draft(goal, title_input=body.title)
        task = store.create_task(
            {
                "title": draft.title,
                "goalSummary": draft.goal,
                "domain": "research_task",
                "skillPack": "sdr",
                "currentStage": "problem_modeling",
                "status": "draft",
                "creationStatus": "awaiting_confirmation",
                "resultSource": "live",
                "creationPlan": draft.plan,
                "ownerId": identity["ownerId"],
                "workspaceId": identity["workspaceId"],
                "createdBy": identity["createdBy"],
            }
        )
        task_id = task["taskId"]
        plan = draft.plan
        artifact_id = f"artifact_{uuid4().hex[:12]}"
        call_id = _create_local_record(task_id, starter, artifact_id=artifact_id)
        artifact = store.add_artifact(
            task_id,
            artifact_id=artifact_id,
            kind="research_start_draft",
            title="研究起点草案",
            summary=f"已根据输入识别：{'、'.join(draft.keywords[:4])}；请确认计划或补充材料。",
            format="markdown",
            related_stage="problem_modeling",
            related_skill_id="research_start_draft",
            source_capability_call_id=call_id,
            storage_type="inline",
            inline_content=draft.markdown,
        )
        task = store.save_stage_result(
            task_id,
            "problem_modeling",
            {
                "status": "running",
                "summary": f"已理解研究问题：{draft.goal}",
                "risks": "尚未读取外部论文、数据或代码；当前草案只能用于确认计划，不能作为最终科研结论。",
                "nextSteps": draft.next_action,
                "artifactRefs": [artifact["id"]] if artifact else [],
            },
        ) or store.get_task(task_id) or task
        return StarterUseOut(
            starter=starter_out,
            outcome="workflow_draft",
            task=TaskOut.model_validate(task),
            artifact=ArtifactOut.model_validate(artifact) if artifact else None,
            state={
                "kind": "workflow_draft",
                "readyToConfirm": True,
                "failureState": _failure_state(starter, "workflow_draft"),
                "failureBoundary": "确认前不执行科研能力；草案可取消或修订。",
            },
            nextAction=draft.next_action,
        )

    if mode == "requires_existing_inputs":
        missing = [
            "来源包或已筛选论文列表",
            "证据目标或机制链分析问题",
            "人工复核标准",
        ]
        return StarterUseOut(
            starter=starter_out,
            outcome="requires_existing_inputs",
            state={
                "kind": "requires_existing_inputs",
                "missingInputs": missing,
                "artifactExpectation": starter["artifactExpectation"],
                "failureState": _failure_state(starter, "requires_existing_inputs"),
                "failureBoundary": "缺少上游输入时不会创建任务。",
                "artifactShell": _gap_markdown(starter, missing),
            },
            nextAction="补齐上游材料，或先使用工作流规划起点。",
        )

    if mode == "prep_check_only":
        missing = [
            "A 线外部依赖核验结果",
            "正式数据源、许可或平台运行边界",
            "可复核的输入材料",
        ]
        outcome = "prep_check" if starter["releaseDecision"] == "prep_check_only" else "gap_state"
        return StarterUseOut(
            starter=starter_out,
            outcome=outcome,
            state={
                "kind": outcome,
                "missingInputs": missing,
                "artifactExpectation": starter["artifactExpectation"],
                "failureState": _failure_state(starter, outcome),
                "failureBoundary": "当前只返回准备检查或缺口状态，不创建空任务。",
                "artifactShell": _gap_markdown(starter, missing),
            },
            nextAction="完成外部依赖和输入材料核验后再继续。",
        )

    raise HTTPException(
        status_code=409,
        detail=f"starter creation mode {mode} is not enabled by this release gate",
    )


def _add_literature_artifacts(task_id: str, call_id: str, run) -> list[dict]:
    artifact_specs = [
        ("literature_search_strategy", "检索策略", "查询策略、关键词和来源优先级。", "search_strategy"),
        ("source_table", "来源表", "可追溯来源表，包含完整 provenance 和可读性线索。", "source_table"),
        ("top_k_source_set", "候选来源清单", "真实检索候选；尚未做科研语义排序。", "top_k"),
        ("source_readiness", "可读性说明", "来源可读性和全文边界说明。", "readiness"),
        ("short_literature_review_draft", "候选摘要摘录", "仅摘录题名/摘要，不形成科研综述判断。", "bounded_review"),
        ("literature_gaps_next_steps", "缺口与下一步", "检索覆盖、相关性闸门和下一步建议。", "gaps_next"),
    ]
    contents = markdown_artifacts(run)
    artifacts = []
    for kind, title, summary, key in artifact_specs:
        artifact = store.add_artifact(
            task_id,
            kind=kind,
            title=title,
            summary=summary,
            format="markdown",
            related_stage="problem_modeling",
            related_skill_id="traceable_literature_live_path",
            source_capability_call_id=call_id,
            storage_type="inline",
            inline_content=contents[key],
            extra_fields={
                "origin": "live",
                "externalProvider": "OpenAlex/arXiv/Semantic Scholar/Unpaywall",
            },
        )
        if artifact:
            artifacts.append(artifact)
    return artifacts
