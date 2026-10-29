from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ResearchAgentId = Literal[
    "paper_evidence_extraction",
    "result_reliability_audit",
    "validation_design",
    "research_gap_identification",
]

ReadinessStatus = Literal["ready", "design_only", "insufficient"]


class SelectedExcerpt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    materialId: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    excerptType: str = Field(min_length=1)
    text: str = Field(min_length=1)
    relevanceScore: float | None = Field(default=None, ge=0, le=1)


class ExcludedMaterial(BaseModel):
    model_config = ConfigDict(extra="forbid")

    materialId: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class MaterialReadinessReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ReadinessStatus
    failClosed: bool
    reason: str = Field(min_length=1)
    usableMaterialIds: list[str] = Field(default_factory=list)
    missingMaterials: list[str] = Field(default_factory=list)
    minimumUsableInput: list[str] = Field(default_factory=list)
    excludedMaterials: list[ExcludedMaterial] = Field(default_factory=list)
    readinessChecks: list[str] = Field(default_factory=list)


class ContextBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["route_only", "draft_only", "evidence_bound", "retrieval_bound", "full_execution", "critique_only"]
    maxInputTokens: int = Field(gt=0)
    maxOutputTokens: int = Field(gt=0)
    maxWallTimeSec: int = Field(gt=0)
    maxToolCalls: int = Field(ge=0)


class ResearchAgentOutputContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contractId: str = Field(min_length=1)
    artifactKind: str = Field(min_length=1)
    requiredFields: list[str] = Field(min_length=1)
    traceabilityRules: list[str] = Field(min_length=1)
    failClosedStatus: str = Field(min_length=1)


class ResearchAgentContextPack(BaseModel):
    """Bounded input envelope passed from orchestration to research specialist Agents."""

    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["research_agent_context_pack.v1"] = "research_agent_context_pack.v1"
    contextPackId: str = Field(min_length=1)
    taskId: str | None = None
    subgoal: str = Field(min_length=1)
    targetAgent: ResearchAgentId
    sdrStage: str = Field(min_length=1)
    substage: str = Field(min_length=1)
    allowedMaterialIds: list[str]
    selectedExcerpts: list[SelectedExcerpt]
    knownGaps: list[str]
    forbiddenOutputs: list[str] = Field(min_length=1)
    outputContract: ResearchAgentOutputContract
    budget: ContextBudget
    requiredValidationChecks: list[str] = Field(min_length=1)
    materialReadinessReport: MaterialReadinessReport

    @model_validator(mode="after")
    def validate_selected_material_boundary(self) -> "ResearchAgentContextPack":
        allowed = set(self.allowedMaterialIds)
        excerpt_ids = {excerpt.materialId for excerpt in self.selectedExcerpts}
        unknown = sorted(excerpt_ids - allowed)
        if unknown:
            raise ValueError(f"selectedExcerpts reference material ids outside allowedMaterialIds: {unknown}")
        if self.materialReadinessReport.failClosed:
            raise ValueError("fail-closed readiness reports must not be wrapped as executable context packs")
        if self.targetAgent != "validation_design" and not self.selectedExcerpts:
            raise ValueError(f"{self.targetAgent} context packs require at least one selected excerpt")
        return self


class ContextPackBuildResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targetAgent: ResearchAgentId
    status: Literal["ready", "fail_closed"]
    contextPack: ResearchAgentContextPack | None = None
    materialReadinessReport: MaterialReadinessReport


class _AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targetAgent: ResearchAgentId
    sdrStage: str
    substage: str
    subgoalPrefix: str
    missingMaterials: list[str]
    minimumUsableInput: list[str]
    forbiddenOutputs: list[str]
    outputContract: ResearchAgentOutputContract
    budget: ContextBudget
    requiredValidationChecks: list[str]


AGENT_CONTEXT_CONFIG: dict[ResearchAgentId, _AgentConfig] = {
    "paper_evidence_extraction": _AgentConfig(
        targetAgent="paper_evidence_extraction",
        sdrStage="problem_modeling",
        substage="evidence_extraction",
        subgoalPrefix="Extract source-bounded claim-evidence rows from selected readable materials.",
        missingMaterials=["source packet", "readable excerpts", "claim target", "source locator"],
        minimumUsableInput=["one readable paper/result excerpt", "paper or material ID", "source locator"],
        forbiddenOutputs=[
            "evidence rows from unselected materials",
            "mechanism proof",
            "paper-level conclusion without a selected source locator",
        ],
        outputContract=ResearchAgentOutputContract(
            contractId="paper_evidence_extraction_artifact_v1",
            artifactKind="claim_evidence_table",
            requiredFields=["material_readiness", "claim_inventory", "claim_evidence_table", "unsupported_or_missing"],
            traceabilityRules=[
                "Every evidence row must cite a selected materialId or locator.",
                "Direct evidence and author/model inference must be separated.",
            ],
            failClosedStatus="requires_existing_inputs",
        ),
        budget=ContextBudget(mode="evidence_bound", maxInputTokens=2500, maxOutputTokens=1800, maxWallTimeSec=90, maxToolCalls=3),
        requiredValidationChecks=[
            "selected excerpt ids are a subset of allowedMaterialIds",
            "no claim_evidence_table row uses an unselected source",
            "unsupported_or_missing records absent statistics, methods, raw data, or locators",
            "forbiddenOutputs are not emitted",
        ],
    ),
    "result_reliability_audit": _AgentConfig(
        targetAgent="result_reliability_audit",
        sdrStage="execution_validation",
        substage="result_acceptance",
        subgoalPrefix="Audit traceability and reproducibility only from selected result records.",
        missingMaterials=["result locator", "raw data", "analysis script or notebook", "run log", "audit criterion"],
        minimumUsableInput=["result claim", "result locator", "raw data or experiment record", "analysis method"],
        forbiddenOutputs=[
            "pass/fail verdict without raw records",
            "reproducibility conclusion without scripts/logs",
            "claim repair based on invented records",
        ],
        outputContract=ResearchAgentOutputContract(
            contractId="result_reliability_audit_artifact_v1",
            artifactKind="quality_audit_report",
            requiredFields=["audit_scope", "traceability_matrix", "missing_records", "verdict", "next_record_to_inspect"],
            traceabilityRules=[
                "Every traceability row must link only selected materialIds.",
                "Verdict must be insufficient_materials unless minimum records are selected.",
            ],
            failClosedStatus="insufficient_materials",
        ),
        budget=ContextBudget(mode="evidence_bound", maxInputTokens=3500, maxOutputTokens=1800, maxWallTimeSec=90, maxToolCalls=4),
        requiredValidationChecks=[
            "raw data or experiment record is present before issuing needs_fix/pass/fail",
            "traceability_matrix linked_material_ids are selected material ids",
            "missing_records lists every absent raw/script/log/statistics dependency",
            "forbiddenOutputs are not emitted",
        ],
    ),
    "validation_design": _AgentConfig(
        targetAgent="validation_design",
        sdrStage="solution_design",
        substage="validation_design",
        subgoalPrefix="Draft a validation design without claiming execution or results.",
        missingMaterials=["hypothesis", "metric", "baseline/control", "resource and safety confirmation"],
        minimumUsableInput=["hypothesis or research claim", "outcome metric", "baseline/control"],
        forbiddenOutputs=[
            "experiment result",
            "effectiveness conclusion",
            "resource launch or execution claim",
        ],
        outputContract=ResearchAgentOutputContract(
            contractId="validation_design_artifact_v1",
            artifactKind="validation_plan",
            requiredFields=[
                "testable_hypothesis",
                "variable_table",
                "controls_and_baselines",
                "analysis_plan",
                "execution_status",
            ],
            traceabilityRules=[
                "execution_status must remain not_started",
                "unknown metrics, datasets, sample sizes, and thresholds must be marked provisional",
            ],
            failClosedStatus="design_only",
        ),
        budget=ContextBudget(mode="draft_only", maxInputTokens=2200, maxOutputTokens=2200, maxWallTimeSec=90, maxToolCalls=2),
        requiredValidationChecks=[
            "execution_status is not_started",
            "no result or effectiveness conclusion appears in output",
            "missing inputs and confirmation gates are explicit",
            "forbiddenOutputs are not emitted",
        ],
    ),
    "research_gap_identification": _AgentConfig(
        targetAgent="research_gap_identification",
        sdrStage="problem_modeling",
        substage="gap_identification",
        subgoalPrefix="Identify candidate gaps only from selected source/evidence materials.",
        missingMaterials=["review notes", "source table", "evidence table", "gap criteria"],
        minimumUsableInput=["review/source/evidence excerpt", "material ID", "gap criteria or scope boundary"],
        forbiddenOutputs=[
            "field-wide novelty claim",
            "publishable gap without selected evidence",
            "claim that the field has not studied an area",
        ],
        outputContract=ResearchAgentOutputContract(
            contractId="research_gap_identification_artifact_v1",
            artifactKind="gap_list",
            requiredFields=["material_coverage", "gap_list", "not_a_gap", "needs_source", "evidence_needed"],
            traceabilityRules=[
                "Every G-ID must link to selected materialIds or selected evidence IDs.",
                "Uncovered-by-selected-materials must not be framed as absent-from-field.",
            ],
            failClosedStatus="requires_existing_inputs",
        ),
        budget=ContextBudget(mode="evidence_bound", maxInputTokens=3000, maxOutputTokens=2000, maxWallTimeSec=90, maxToolCalls=3),
        requiredValidationChecks=[
            "gap rows cite only selected material ids",
            "candidate gap confidence is capped when coverage is partial",
            "not_a_gap separates missing coverage from real research gaps",
            "forbiddenOutputs are not emitted",
        ],
    ),
}


TARGET_AGENT_ALIASES: dict[str, ResearchAgentId] = {
    "paper_evidence_extraction": "paper_evidence_extraction",
    "result_reliability_audit": "result_reliability_audit",
    "validation_design": "validation_design",
    "validation_design_planning": "validation_design",
    "research_gap_identification": "research_gap_identification",
}

RAW_INPUT_MARKERS = {"raw_input", "rawUserInput", "userInput", "allMaterials", "materials"}
RECORD_TYPES = {"raw_data", "analysis_notebook", "analysis_script", "run_log", "method", "experiment_record"}
RESULT_TYPES = {"claim", "figure", "results_excerpt", "table", "figure_caption"}


def build_research_agent_context_pack(
    *,
    target_agent: str,
    user_input: str,
    materials: list[dict[str, Any]] | None = None,
    task_id: str | None = None,
    top_k: int = 4,
) -> ContextPackBuildResult:
    """Build a bounded context envelope or a fail-closed readiness report.

    The builder intentionally does not expose raw user input or an unbounded
    material list. Specialist Agents receive only derived subgoal text,
    allowed material ids, selected excerpts, known gaps, and output guardrails.
    """

    agent = _normalize_target_agent(target_agent)
    config = AGENT_CONTEXT_CONFIG[agent]
    normalized_materials, invalid_materials = _normalize_materials(materials or [])
    selected = _select_materials(normalized_materials, top_k=max(1, top_k))
    readiness = _build_readiness_report(
        agent=agent,
        config=config,
        available_materials=normalized_materials,
        selected_materials=selected,
        invalid_materials=invalid_materials,
    )
    if readiness.failClosed:
        return ContextPackBuildResult(
            targetAgent=agent,
            status="fail_closed",
            contextPack=None,
            materialReadinessReport=readiness,
        )

    selected_excerpts = [
        SelectedExcerpt(
            materialId=material["materialId"],
            locator=material["locator"],
            excerptType=material["excerptType"],
            text=_excerpt_text(material["text"]),
            relevanceScore=material.get("relevanceScore"),
        )
        for material in selected
    ]
    allowed_ids = [excerpt.materialId for excerpt in selected_excerpts]
    pack = ResearchAgentContextPack(
        contextPackId=_context_pack_id(agent=agent, task_id=task_id, selected=selected_excerpts, user_input=user_input),
        taskId=task_id,
        subgoal=_derive_subgoal(config, user_input),
        targetAgent=agent,
        sdrStage=config.sdrStage,
        substage=config.substage,
        allowedMaterialIds=allowed_ids,
        selectedExcerpts=selected_excerpts,
        knownGaps=_known_gaps(agent, selected, readiness),
        forbiddenOutputs=list(config.forbiddenOutputs),
        outputContract=config.outputContract,
        budget=config.budget,
        requiredValidationChecks=list(config.requiredValidationChecks),
        materialReadinessReport=readiness,
    )
    _assert_no_raw_payload_keys(pack)
    return ContextPackBuildResult(
        targetAgent=agent,
        status="ready",
        contextPack=pack,
        materialReadinessReport=readiness,
    )


def research_agent_context_pack_json_schema() -> dict[str, Any]:
    """Return the public JSON Schema for the MVP Context Pack contract."""

    return ResearchAgentContextPack.model_json_schema()


def _normalize_target_agent(target_agent: str) -> ResearchAgentId:
    agent = TARGET_AGENT_ALIASES.get(str(target_agent))
    if agent is None:
        supported = ", ".join(sorted(TARGET_AGENT_ALIASES))
        raise ValueError(f"unsupported research context pack target agent {target_agent!r}; supported: {supported}")
    return agent


def _normalize_materials(
    materials: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[ExcludedMaterial]]:
    normalized: list[dict[str, Any]] = []
    invalid: list[ExcludedMaterial] = []
    for index, material in enumerate(materials, start=1):
        material_id = str(
            material.get("material_id")
            or material.get("materialId")
            or f"M{index}"
        ).strip()
        locator = str(material.get("locator") or material_id).strip()
        text = str(material.get("content") or material.get("text") or "").strip()
        excerpt_type = str(
            material.get("type")
            or material.get("excerptType")
            or "material"
        ).strip()

        if not material_id:
            invalid.append(
                ExcludedMaterial(
                    materialId=f"<row-{index}>",
                    reason="missing material id",
                )
            )
            continue
        if not text:
            invalid.append(
                ExcludedMaterial(
                    materialId=material_id,
                    reason="missing readable content",
                )
            )
            continue

        raw_relevance = material.get(
            "relevanceScore",
            material.get("relevance_score"),
        )
        relevance_score: float | None = None
        if (
            isinstance(raw_relevance, (int, float))
            and not isinstance(raw_relevance, bool)
        ):
            numeric = float(raw_relevance)
            if 0.0 <= numeric <= 1.0:
                relevance_score = numeric

        normalized.append(
            {
                "materialId": material_id,
                "locator": locator or material_id,
                "excerptType": excerpt_type or "material",
                "text": text,
                "relevanceScore": relevance_score,
            }
        )
    return normalized, invalid



def _select_materials(
    materials: list[dict[str, Any]],
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    # Apply only a deterministic context-material budget. Scientific relevance
    # remains the target Specialist Agent's responsibility.
    return [dict(material) for material in materials[:top_k]]



def _build_readiness_report(
    *,
    agent: ResearchAgentId,
    config: _AgentConfig,
    available_materials: list[dict[str, Any]],
    selected_materials: list[dict[str, Any]],
    invalid_materials: list[ExcludedMaterial],
) -> MaterialReadinessReport:
    checks = [
        "material ids are explicit",
        "selected excerpts have readable text and locators",
        "material order is preserved under the deterministic context budget",
        "output is constrained by forbiddenOutputs and outputContract",
        "scientific relevance and evidence gaps remain Specialist Agent judgments",
    ]
    fail_closed = False
    status: ReadinessStatus = "ready"
    reason = (
        "bounded specialist context is structurally ready; "
        "scientific relevance remains for the target Agent"
    )
    required_now: list[str] = []

    if agent == "validation_design":
        status = "design_only"
        reason = (
            "validation design may proceed as design-only; "
            "execution and result claims remain forbidden"
        )
    elif not selected_materials:
        fail_closed = True
        status = "insufficient"
        reason = (
            "no readable material is available for this evidence-bound "
            "specialist Agent"
        )
        required_now = list(config.minimumUsableInput)
    elif (
        agent == "result_reliability_audit"
        and not _has_structural_audit_materials(selected_materials)
    ):
        fail_closed = True
        status = "insufficient"
        reason = (
            "result audit requires the explicit structured material types "
            "defined by its input contract"
        )
        required_now = [
            "result claim or result locator",
            "raw data, experiment record, script, notebook, run log, or method record",
        ]
    elif (
        agent == "paper_evidence_extraction"
        and _only_abstract_level_materials(selected_materials)
    ):
        fail_closed = True
        status = "insufficient"
        reason = (
            "direct claim-evidence extraction requires a source-bounded "
            "non-abstract excerpt type"
        )
        required_now = [
            "source-bounded non-abstract excerpt with a material ID and locator"
        ]

    if fail_closed:
        checks.append(
            "fail closed when an explicit structural input contract is unmet"
        )

    selected_ids = {
        material["materialId"] for material in selected_materials
    }
    outside_budget = [
        ExcludedMaterial(
            materialId=material["materialId"],
            reason="outside deterministic context material budget",
        )
        for material in available_materials
        if material["materialId"] not in selected_ids
    ]

    return MaterialReadinessReport(
        status=status,
        failClosed=fail_closed,
        reason=reason,
        usableMaterialIds=[
            material["materialId"] for material in selected_materials
        ],
        missingMaterials=required_now,
        minimumUsableInput=list(config.minimumUsableInput),
        excludedMaterials=invalid_materials + outside_budget,
        readinessChecks=checks,
    )



def _has_structural_audit_materials(
    selected_materials: list[dict[str, Any]],
) -> bool:
    types = {
        str(material["excerptType"]).strip().lower()
        for material in selected_materials
    }
    return bool(types & RESULT_TYPES) and bool(types & RECORD_TYPES)



def _only_abstract_level_materials(
    selected_materials: list[dict[str, Any]],
) -> bool:
    if not selected_materials:
        return False
    types = {
        str(material["excerptType"]).strip().lower()
        for material in selected_materials
    }
    return bool(types) and types <= {
        "abstract",
        "summary",
        "press_release",
    }









def _known_gaps(
    agent: ResearchAgentId,
    selected: list[dict[str, Any]],
    readiness: MaterialReadinessReport,
) -> list[str]:
    _ = (selected, readiness)
    if agent == "validation_design":
        return [
            "Execution and result claims remain outside this design-only context."
        ]
    return []



def _derive_subgoal(config: _AgentConfig, user_input: str) -> str:
    synopsis = _compact_user_goal(user_input)
    if synopsis:
        return f"{config.subgoalPrefix} User goal synopsis: {synopsis}"
    return config.subgoalPrefix


def _compact_user_goal(user_input: str) -> str:
    text = re.sub(r"\s+", " ", str(user_input or "")).strip()
    text = re.split(r"\n\s*Provided materials\s*:", text, flags=re.IGNORECASE)[0]
    if not text:
        return ""
    if len(text) > 220:
        text = text[:217].rstrip() + "..."
    return text


def _excerpt_text(text: str, limit: int = 800) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 15].rstrip() + " ...[truncated]"


def _context_pack_id(
    *,
    agent: ResearchAgentId,
    task_id: str | None,
    selected: list[SelectedExcerpt],
    user_input: str,
) -> str:
    seed = "|".join([agent, task_id or "", ",".join(excerpt.materialId for excerpt in selected), _compact_user_goal(user_input)])
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
    return f"ctx_{agent}_{digest}"


def _assert_no_raw_payload_keys(pack: ResearchAgentContextPack) -> None:
    payload = pack.model_dump()
    leaked = RAW_INPUT_MARKERS & set(payload)
    if leaked:
        raise ValueError(f"context pack leaked raw payload fields: {sorted(leaked)}")
