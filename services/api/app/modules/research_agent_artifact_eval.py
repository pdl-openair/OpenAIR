from __future__ import annotations

from dataclasses import dataclass
import json
import re
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ArtifactEvalIssue:
    code: str
    message: str


@dataclass(frozen=True)
class GeneratedArtifactEvalResult:
    scenarioName: str
    outputPath: str
    passed: bool
    issues: list[ArtifactEvalIssue]


@dataclass(frozen=True)
class GeneratedArtifactEvalReport:
    status: str
    checkedCount: int
    passedCount: int
    failedCount: int
    results: list[GeneratedArtifactEvalResult]


@dataclass(frozen=True)
class ScenarioRubricScore:
    scenarioName: str
    agent: str
    score: int
    cap: str | None
    recommendation: str
    reason: str


@dataclass(frozen=True)
class AgentRubricSummary:
    agent: str
    scores: list[int]
    median: int
    recommendation: str
    empirical90: bool


CONTROLLED_SCENARIO_SCORES: dict[str, tuple[int, str]] = {
    "evidence_messy_results_captions": (86, "traceable rows, but still controlled output and not live noisy extraction"),
    "evidence_abstract_caption_only": (85, "good fail-closed abstract-only boundary"),
    "evidence_topic_only": (90, "strong topic-only fail-closed behavior"),
    "gap_messy_review_notes": (84, "useful but false-gap risk remains high with weak notes"),
    "gap_source_and_evidence_table": (88, "traceable G-IDs from supplied source/evidence rows"),
    "gap_topic_only": (90, "strong topic-only gap/novelty refusal"),
    "audit_records_available": (86, "traceability matrix exists, but broken-link severity is controlled not live-proven"),
    "audit_figure_only": (90, "correct insufficient-materials verdict without raw data"),
    "audit_no_records_pass_fail_request": (90, "correct refusal of pass/fail without records"),
    "validation_sei_additive": (89, "strong variable/control artifact, but live feasibility review still needed"),
    "validation_graphrag_no_dataset": (87, "good missing-dataset handling, not yet execution-ready"),
    "validation_execution_request": (90, "strong design-only fail-closed behavior"),
}


def score_controlled_generated_outputs(scenarios: list[dict[str, Any]]) -> list[ScenarioRubricScore]:
    scores: list[ScenarioRubricScore] = []
    for scenario in scenarios:
        artifact = build_controlled_generated_artifact(scenario)
        issues = validate_research_agent_artifact(scenario, artifact)
        score, reason = CONTROLLED_SCENARIO_SCORES[str(scenario.get("name"))]
        if issues:
            score = min(score, 70)
            reason = f"validator issues: {[issue.code for issue in issues]}"
        scores.append(
            ScenarioRubricScore(
                scenarioName=str(scenario.get("name")),
                agent=str(scenario.get("agent")),
                score=score,
                cap="controlled_output_not_live_agent",
                recommendation="available_not_featured",
                reason=reason,
            )
        )
    return scores


def summarize_agent_rubric_scores(scores: list[ScenarioRubricScore]) -> list[AgentRubricSummary]:
    by_agent: dict[str, list[int]] = {}
    for row in scores:
        by_agent.setdefault(row.agent, []).append(row.score)
    summaries: list[AgentRubricSummary] = []
    for agent, values in sorted(by_agent.items()):
        ordered = sorted(values)
        median = ordered[len(ordered) // 2]
        summaries.append(
            AgentRubricSummary(
                agent=agent,
                scores=values,
                median=median,
                recommendation="available_not_featured",
                empirical90=False,
            )
        )
    return summaries


def score_reviewed_generated_outputs(scenarios: list[dict[str, Any]], output_dir: Path) -> list[ScenarioRubricScore]:
    """Read checked-in generated outputs with human rubric review metadata.

    These scores describe reviewed artifact quality for the scenario output.
    They do not by themselves prove empirical 90+ because the provenance may be
    a local recorded fixture rather than a live Agent run.
    """

    scenario_by_name = {str(row.get("name")): row for row in scenarios}
    scores: list[ScenarioRubricScore] = []
    for output_path in sorted(output_dir.glob("*.json")):
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        scenario_name = str(payload.get("scenarioName") or "")
        scenario = scenario_by_name.get(scenario_name)
        artifact = payload.get("artifact")
        if scenario is None or not isinstance(artifact, dict):
            continue
        issues = validate_research_agent_artifact(scenario, artifact)
        review = payload.get("review") if isinstance(payload.get("review"), dict) else {}
        score_value = review.get("rubricScore")
        if not isinstance(score_value, int):
            score_value = 70
        score_value = max(0, min(100, score_value))
        if issues:
            score_value = min(score_value, 70)
        scores.append(
            ScenarioRubricScore(
                scenarioName=scenario_name,
                agent=str(scenario.get("agent")),
                score=score_value,
                cap=str(review.get("cap") or "reviewed_generated_output"),
                recommendation=str(review.get("recommendation") or "available_not_featured"),
                reason=str(review.get("mainReason") or review.get("reason") or "reviewed generated output"),
            )
        )
    return scores


def build_controlled_generated_artifact(scenario: dict[str, Any]) -> dict[str, Any]:
    """Build a deterministic scenario artifact for local rubric evaluation.

    This is a controlled harness output, not a live LLM/Agent run. It is useful
    for exercising artifact contracts and scoring low-rework shape before a live
    generated-output path is available.
    """

    agent = scenario.get("agent")
    if agent == "paper_evidence_extraction":
        return _controlled_evidence_artifact(scenario)
    if agent == "research_gap_identification":
        return _controlled_gap_artifact(scenario)
    if agent == "result_reliability_audit":
        return _controlled_audit_artifact(scenario)
    if agent == "validation_design":
        return _controlled_validation_artifact(scenario)
    return {"status": "unsupported_agent"}


def evaluate_controlled_generated_outputs(scenarios: list[dict[str, Any]]) -> GeneratedArtifactEvalReport:
    results: list[GeneratedArtifactEvalResult] = []
    for scenario in scenarios:
        artifact = build_controlled_generated_artifact(scenario)
        issues = validate_research_agent_artifact(scenario, artifact)
        if artifact == scenario.get("artifact"):
            issues = [*issues, _issue("reference_artifact_reused", "Controlled output must not copy the reference artifact verbatim.")]
        results.append(
            GeneratedArtifactEvalResult(
                scenarioName=str(scenario.get("name")),
                outputPath="<controlled_generator>",
                passed=not issues,
                issues=issues,
            )
        )
    failed_count = sum(1 for row in results if not row.passed)
    return GeneratedArtifactEvalReport(
        status="failed" if failed_count else "passed",
        checkedCount=len(results),
        passedCount=len(results) - failed_count,
        failedCount=failed_count,
        results=results,
    )


def validate_research_agent_artifact(scenario: dict[str, Any], artifact: dict[str, Any] | None = None) -> list[ArtifactEvalIssue]:
    """Validate one research Agent scenario artifact against v2 eval guardrails.

    This is an artifact-level contract for generated-output eval. It does not
    score quality or prove empirical 90+. It only catches missing traceability,
    fail-closed violations, and execution/result overclaim.
    """

    artifact = artifact if artifact is not None else scenario.get("artifact", {})
    agent = scenario.get("agent")
    if agent == "paper_evidence_extraction":
        return _validate_evidence(scenario, artifact)
    if agent == "research_gap_identification":
        return _validate_gap(scenario, artifact)
    if agent == "result_reliability_audit":
        return _validate_audit(scenario, artifact)
    if agent == "validation_design":
        return _validate_validation(scenario, artifact)
    return [_issue("unknown_agent", f"Unknown research Agent: {agent}")]


def evaluate_generated_output_dir(scenarios: list[dict[str, Any]], output_dir: Path) -> GeneratedArtifactEvalReport:
    """Validate generated Agent outputs checked into an eval directory.

    Empty directories are reported as ``no_outputs``. Passing outputs are only a
    prerequisite for rubric scoring; they are not enough to claim empirical 90+.
    """

    scenario_by_name = {str(row.get("name")): row for row in scenarios}
    results: list[GeneratedArtifactEvalResult] = []
    for output_path in sorted(output_dir.glob("*.json")):
        issues: list[ArtifactEvalIssue] = []
        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            issues.append(_issue("invalid_json", f"Generated output is not valid JSON: {exc}"))
            results.append(_result(output_path, "<invalid>", issues))
            continue

        scenario_name = str(payload.get("scenarioName") or "")
        scenario = scenario_by_name.get(scenario_name)
        artifact = payload.get("artifact")
        if scenario is None:
            issues.append(_issue("unknown_scenario", f"Generated output references unknown scenarioName: {scenario_name}"))
        if not isinstance(artifact, dict):
            issues.append(_issue("missing_artifact", "Generated output must contain an artifact object."))
        if scenario is not None and isinstance(artifact, dict):
            provenance = payload.get("provenance") if isinstance(payload.get("provenance"), dict) else {}
            provenance_kind = str(provenance.get("kind") or "")
            allow_reference_expected_behavior = provenance_kind.endswith("_fail_closed_no_task")
            if artifact == scenario.get("artifact") and not allow_reference_expected_behavior:
                issues.append(_issue("reference_artifact_reused", "Generated output must not be the reference artifact copied verbatim."))
            issues.extend(validate_research_agent_artifact(scenario, artifact))
        results.append(_result(output_path, scenario_name, issues))

    failed_count = sum(1 for row in results if not row.passed)
    passed_count = len(results) - failed_count
    if not results:
        status = "no_outputs"
    elif failed_count:
        status = "failed"
    else:
        status = "passed"
    return GeneratedArtifactEvalReport(
        status=status,
        checkedCount=len(results),
        passedCount=passed_count,
        failedCount=failed_count,
        results=results,
    )


def _materials(scenario: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in scenario.get("providedMaterials", []) if isinstance(row, dict)]


def _controlled_evidence_artifact(scenario: dict[str, Any]) -> dict[str, Any]:
    if scenario.get("failClosed"):
        return {
            "status": "requires_existing_inputs",
            "material_readiness": {"usable": [], "unusable": ["topic/abstract-only input"]},
            "claim_evidence_table": [],
            "minimum_usable_input": ["readable result text", "source locator", "claim target"],
            "unsupported_or_missing": ["paper excerpt", "figure/table locator", "methods or measurement context"],
            "forbidden_output": ["evidence rows", "mechanism proof", "paper claims"],
        }
    rows = []
    claim_inventory = []
    for index, material in enumerate(_materials(scenario), start=1):
        locator = material.get("locator")
        claim_id = f"C{index}"
        claim_inventory.append(
            {
                "claim_id": claim_id,
                "source_type": material.get("type"),
                "source_locator": locator,
            }
        )
        rows.append(
            {
                "evidence_id": f"E{index}",
                "claim_id": claim_id,
                "support_tier": "direct" if material.get("type") != "discussion_excerpt" else "author_inference",
                "evidence_quote_or_observation": str(material.get("content", ""))[:160],
                "source_locator": locator,
                "uncertainty": "requires original paper context and methods check",
                "conflict_or_gap": "raw data/statistics/method details not confirmed",
            }
        )
    return {
        "material_readiness": {
            "usable": [row.get("locator") for row in _materials(scenario)],
            "boundary": "controlled generator only used pasted materials; no external paper retrieval",
        },
        "claim_inventory": claim_inventory,
        "claim_evidence_table": rows,
        "unsupported_or_missing": ["raw data", "statistics", "full methods", "paper-level context"],
        "author_inference_or_model_inference": ["causal interpretations remain inference unless directly tested in provided material"],
        "handoff": ["gap analysis", "result reliability audit", "validation design"],
    }


def _controlled_gap_artifact(scenario: dict[str, Any]) -> dict[str, Any]:
    if scenario.get("failClosed"):
        return {
            "status": "requires_existing_inputs",
            "gap_list": [],
            "missing_materials": ["review notes", "source table", "evidence table", "gap criteria"],
            "needs_source": ["candidate papers", "benchmark reports", "negative findings"],
            "evidence_needed": ["source/evidence rows before assigning G-IDs"],
            "forbidden_output": ["publishable gaps", "novelty claims"],
        }
    materials = _materials(scenario)
    material_ids = [str(row.get("material_id")) for row in materials if row.get("material_id")]
    evidence_ids = [str(row.get("material_id")) for row in materials if "evidence" in str(row.get("type", ""))]
    return {
        "material_coverage": [
            {
                "material_id": row.get("material_id"),
                "covered_subtopic": row.get("type"),
                "coverage_confidence": "medium" if row.get("content") else "low",
            }
            for row in materials
        ],
        "gap_list": [
            {
                "G-ID": "G1",
                "gap_statement": "Supplied materials indicate an evidence or validation gap that needs source-level confirmation.",
                "gap_type": "validation_gap",
                "linked_material_ids": material_ids[:2] or material_ids,
                "linked_evidence_ids": evidence_ids,
                "evidence_strength": "medium" if evidence_ids else "weak",
                "confidence": "medium" if evidence_ids else "low",
            }
        ],
        "not_a_gap": ["Uncovered in the pasted material is not equivalent to absent from the field."],
        "needs_source": ["broader source table", "negative or counterexample records"],
        "evidence_needed": ["claim-evidence rows for every proposed G-ID", "advisor-approved gap criteria"],
        "advisor_review_questions": ["Which scope boundary should decide whether this is a real gap?"],
    }


def _controlled_audit_artifact(scenario: dict[str, Any]) -> dict[str, Any]:
    if scenario.get("failClosed"):
        return {
            "status": "insufficient_materials",
            "audit_scope": {"inspected": [], "not_inspected": ["raw data", "scripts", "logs", "source locators"]},
            "traceability_matrix": [],
            "missing_records": ["result locator", "raw data", "analysis script", "run log", "audit criterion"],
            "minimum_usable_input": ["result claim", "result locator", "raw data or experiment record", "analysis method"],
            "verdict": "insufficient_materials",
            "next_record_to_inspect": "raw data or source packet underlying the claim",
            "forbidden_output": ["pass", "fail", "needs_fix as if records were inspected"],
        }
    material_ids = [str(row.get("material_id")) for row in _materials(scenario) if row.get("material_id")]
    return {
        "audit_scope": {
            "inspected": [row.get("locator") for row in _materials(scenario)],
            "not_inspected": ["external replication", "unprovided characterization raw data"],
        },
        "traceability_matrix": [
            {
                "claim_id": "C1",
                "trace_status": "linked",
                "linked_material_ids": material_ids,
                "break_reason": None,
            }
        ],
        "issues": [
            {
                "issue_id": "I1",
                "severity": "medium",
                "evidence": "controlled audit still requires missing-record inspection before endorsement",
                "repair_action": "inspect missing raw/method records",
            }
        ],
        "missing_records": ["unprovided raw characterization data", "analysis environment snapshot"],
        "reproducibility_checklist": {"data_version": "partial", "seed": "unknown", "command": "partial"},
        "verdict": "needs_fix",
        "next_record_to_inspect": "the first missing raw data or method record",
    }


def _controlled_validation_artifact(scenario: dict[str, Any]) -> dict[str, Any]:
    if scenario.get("failClosed"):
        return {
            "status": "design_only",
            "execution_status": "not_started",
            "clarification_questions": ["What claim should be tested?", "What metric defines success?", "What baseline is required?"],
            "minimum_viable_validation_inputs": ["hypothesis", "metric", "baseline/control", "resource and safety confirmation"],
            "forbidden_output": ["experiment result", "effectiveness conclusion", "resource launch"],
        }
    return {
        "testable_hypothesis": "A bounded intervention or retrieval condition changes a measurable outcome under fixed conditions.",
        "variable_table": [
            {"variable_id": "V1", "type": "independent_variable", "name": "intervention condition", "measurement": "protocol record"},
            {"variable_id": "V2", "type": "dependent_variable", "name": "primary outcome metric", "measurement": "predefined analysis"},
            {"variable_id": "V3", "type": "control_variable", "name": "fixed context", "measurement": "run/protocol metadata"},
        ],
        "controls_and_baselines": ["no-intervention baseline", "same-system control", "fixed protocol/corpus snapshot"],
        "confounders": ["batch or corpus coverage", "measurement noise", "protocol drift"],
        "ablation_or_comparison_matrix": ["baseline vs intervention", "minimal sweep only after protocol confirmation"],
        "analysis_plan": ["lock metric before execution", "report uncertainty and failure criteria", "separate mediator from outcome"],
        "stop_conditions": ["missing baseline", "unconfirmed metric", "resource/safety gate not approved"],
        "confirmation_gates": ["dataset/material access", "safety or license approval", "sample count or prompt set", "cost/schedule"],
        "execution_status": "not_started",
    }


def _issue(code: str, message: str) -> ArtifactEvalIssue:
    return ArtifactEvalIssue(code=code, message=message)


def _result(output_path: Path, scenario_name: str, issues: list[ArtifactEvalIssue]) -> GeneratedArtifactEvalResult:
    return GeneratedArtifactEvalResult(
        scenarioName=scenario_name,
        outputPath=str(output_path),
        passed=not issues,
        issues=issues,
    )


def _provided_material_ids(scenario: dict[str, Any]) -> set[str]:
    return {str(row.get("material_id")) for row in scenario.get("providedMaterials", []) if row.get("material_id")}


def _provided_material_locators(scenario: dict[str, Any]) -> set[str]:
    return {str(row.get("locator")) for row in scenario.get("providedMaterials", []) if row.get("locator")}


def _provided_material_id_or_locator_refs(scenario: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    for row in scenario.get("providedMaterials", []):
        for key in ("material_id", "locator"):
            value = row.get(key)
            if value:
                refs.add(str(value))
    return refs


def _is_traceable_material_ref(value: Any, scenario: dict[str, Any]) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    refs = _provided_material_id_or_locator_refs(scenario)
    if value in refs:
        return True
    for row in scenario.get("providedMaterials", []):
        material_id = row.get("material_id")
        locator = row.get("locator")
        if isinstance(locator, str) and locator and locator in value:
            return True
        if isinstance(material_id, str) and material_id:
            if re.search(rf"(?<![A-Za-z0-9]){re.escape(material_id)}(?![A-Za-z0-9])", value):
                return True
    parts = [part.strip() for part in value.replace(",", "+").replace(";", "+").split("+")]
    parts = [part for part in parts if part]
    return bool(parts) and all(part in refs for part in parts)


def _missing_material_content(scenario: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for row in scenario.get("providedMaterials", []):
        material_id = str(row.get("material_id") or row.get("locator") or "<unknown>")
        content = row.get("content")
        if not isinstance(content, str) or not content.strip():
            missing.append(material_id)
    return missing


def _missing_keys(value: dict[str, Any], required: set[str]) -> list[ArtifactEvalIssue]:
    return [_issue("missing_key", f"Missing artifact key: {key}") for key in sorted(required - set(value))]


def _validate_evidence(scenario: dict[str, Any], artifact: dict[str, Any]) -> list[ArtifactEvalIssue]:
    issues: list[ArtifactEvalIssue] = []
    if scenario.get("failClosed"):
        if artifact.get("claim_evidence_table"):
            issues.append(_issue("fail_closed_evidence_rows", "Fail-closed evidence scenario must not emit claim_evidence_table."))
        if artifact.get("status") != "requires_existing_inputs":
            issues.append(_issue("missing_requires_inputs_status", "Fail-closed evidence scenario must return requires_existing_inputs."))
        return issues

    required = {"material_readiness", "claim_inventory", "claim_evidence_table", "unsupported_or_missing", "handoff"}
    issues.extend(_missing_keys(artifact, required))
    rows = artifact.get("claim_evidence_table") or []
    if not rows:
        issues.append(_issue("missing_evidence_rows", "Non-fail-closed evidence scenario needs traceable evidence rows."))
        return issues

    material_refs = _provided_material_id_or_locator_refs(scenario)
    if not material_refs:
        issues.append(_issue("missing_provided_materials", "Evidence rows require explicit providedMaterials locators."))
    missing_content = _missing_material_content(scenario)
    if missing_content:
        issues.append(_issue("missing_material_content", f"Evidence scenario materials need readable content: {missing_content}"))
    for row in rows:
        for key in ("evidence_id", "claim_id", "support_tier", "evidence_quote_or_observation", "source_locator", "uncertainty"):
            if key not in row:
                issues.append(_issue("missing_evidence_row_field", f"Evidence row missing {key}."))
        locator = row.get("source_locator")
        if not _is_traceable_material_ref(locator, scenario):
            issues.append(_issue("untraceable_evidence_locator", f"Evidence row source_locator is not provided: {locator}"))
    return issues


def _validate_gap(scenario: dict[str, Any], artifact: dict[str, Any]) -> list[ArtifactEvalIssue]:
    issues: list[ArtifactEvalIssue] = []
    if scenario.get("failClosed"):
        if artifact.get("gap_list"):
            issues.append(_issue("fail_closed_gap_list", "Fail-closed gap scenario must not emit gap_list."))
        if artifact.get("status") != "requires_existing_inputs":
            issues.append(_issue("missing_requires_inputs_status", "Fail-closed gap scenario must return requires_existing_inputs."))
        return issues

    required = {"material_coverage", "gap_list", "not_a_gap", "needs_source", "evidence_needed"}
    issues.extend(_missing_keys(artifact, required))
    rows = artifact.get("gap_list") or []
    if not rows:
        issues.append(_issue("missing_gap_rows", "Non-fail-closed gap scenario needs traceable gap rows."))
        return issues

    material_ids = _provided_material_ids(scenario)
    if not material_ids:
        issues.append(_issue("missing_provided_materials", "Gap rows require explicit providedMaterials IDs."))
    missing_content = _missing_material_content(scenario)
    if missing_content:
        issues.append(_issue("missing_material_content", f"Gap scenario materials need readable content: {missing_content}"))
    for row in rows:
        for key in ("G-ID", "gap_statement", "gap_type", "confidence"):
            if key not in row:
                issues.append(_issue("missing_gap_row_field", f"Gap row missing {key}."))
        linked_ids = set(row.get("linked_material_ids", []))
        linked_evidence_ids = set(row.get("linked_evidence_ids", []))
        if not linked_ids and not linked_evidence_ids:
            issues.append(_issue("unlinked_gap_row", "Gap row needs linked_material_ids or linked_evidence_ids."))
        if not linked_ids <= material_ids:
            issues.append(_issue("unknown_gap_material_id", f"Gap row links unknown material IDs: {sorted(linked_ids - material_ids)}"))
        if not linked_evidence_ids <= material_ids:
            issues.append(_issue("unknown_gap_evidence_id", f"Gap row links unknown evidence IDs: {sorted(linked_evidence_ids - material_ids)}"))
    return issues


def _validate_audit(scenario: dict[str, Any], artifact: dict[str, Any]) -> list[ArtifactEvalIssue]:
    issues: list[ArtifactEvalIssue] = []
    required = {"audit_scope", "traceability_matrix", "missing_records", "verdict", "next_record_to_inspect"}
    issues.extend(_missing_keys(artifact, required))
    if scenario.get("failClosed"):
        if artifact.get("verdict") != "insufficient_materials":
            issues.append(_issue("fail_closed_audit_verdict", "Fail-closed audit scenario must return insufficient_materials."))
        return issues

    rows = artifact.get("traceability_matrix") or []
    if not rows:
        issues.append(_issue("missing_traceability_rows", "Non-fail-closed audit scenario needs traceability rows."))
        return issues

    material_ids = _provided_material_ids(scenario)
    if not material_ids:
        issues.append(_issue("missing_provided_materials", "Audit rows require explicit providedMaterials IDs."))
    missing_content = _missing_material_content(scenario)
    if missing_content:
        issues.append(_issue("missing_material_content", f"Audit scenario materials need readable content: {missing_content}"))
    for row in rows:
        for key in ("claim_id", "trace_status", "linked_material_ids"):
            if key not in row:
                issues.append(_issue("missing_audit_row_field", f"Audit traceability row missing {key}."))
        linked_ids = set(row.get("linked_material_ids", []))
        if not linked_ids:
            issues.append(_issue("unlinked_audit_row", "Audit traceability row needs linked_material_ids."))
        if not linked_ids <= material_ids:
            issues.append(_issue("unknown_audit_material_id", f"Audit row links unknown material IDs: {sorted(linked_ids - material_ids)}"))
    for row in artifact.get("issues") or []:
        severity = row.get("severity")
        if severity not in {"blocker", "high", "medium", "low"}:
            issues.append(_issue("invalid_audit_issue_severity", f"Audit issue severity must be blocker/high/medium/low: {severity}"))
    return issues


def _validate_validation(scenario: dict[str, Any], artifact: dict[str, Any]) -> list[ArtifactEvalIssue]:
    issues: list[ArtifactEvalIssue] = []
    if artifact.get("execution_status") != "not_started":
        issues.append(_issue("execution_status_not_started", "Validation Design artifact must keep execution_status as not_started."))

    if scenario.get("failClosed"):
        forbidden_result_keys = {"experiment_result", "effectiveness_conclusion", "result_verdict", "conclusion"}
        present = sorted(forbidden_result_keys & set(artifact))
        if present:
            issues.append(_issue("fail_closed_validation_result", f"Fail-closed validation scenario emitted result keys: {present}"))
        if "minimum_viable_validation_inputs" not in artifact:
            issues.append(_issue("missing_minimum_viable_validation_inputs", "Fail-closed validation scenario needs minimum_viable_validation_inputs."))
        return issues

    required = {
        "testable_hypothesis",
        "variable_table",
        "controls_and_baselines",
        "confounders",
        "analysis_plan",
        "stop_conditions",
        "confirmation_gates",
    }
    issues.extend(_missing_keys(artifact, required))
    if not artifact.get("variable_table"):
        issues.append(_issue("missing_variable_rows", "Validation Design artifact needs variable_table rows."))
    return issues
