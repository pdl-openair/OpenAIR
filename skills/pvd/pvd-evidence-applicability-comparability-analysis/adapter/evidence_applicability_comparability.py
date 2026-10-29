from __future__ import annotations

import base64
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

PLANNER_SKILL_ID = "pvd_evidence_applicability_global_planning"
DEEP_SKILL_ID = "pvd_evidence_record_deep_verification"
SYNTHESIS_SKILL_ID = "pvd_evidence_applicability_final_synthesis"
MAX_DEEP_RECORDS = 10
MAX_DEEP_WORKERS = 4
_ALLOWED_USES = {
    "mechanism",
    "trend_direction",
    "magnitude_reference",
    "direct_numeric",
}
_SUPPORT_LEVELS = {
    "direct",
    "directional",
    "mechanism_only",
    "background_only",
    "not_transferable",
    "insufficient",
}
_MEASUREMENT_COMPATIBILITY = {
    "compatible",
    "partially_compatible",
    "incompatible",
    "unknown",
}
_DECISION_VALUE = {
    "decision_relevant",
    "context_only",
    "no_material_value",
    "uncertain_value",
}
_UNCERTAINTY_LEVEL = {"low", "moderate", "high", "unknown"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _api_imports() -> Any:
    api_root = _repo_root() / "services" / "api"
    if str(api_root) not in sys.path:
        sys.path.insert(0, str(api_root))
    from app.execution import durable_skill_runtime

    return durable_skill_runtime


class ChildSkillFailure(RuntimeError):
    """Retain only bounded child-call failure facts."""

    def __init__(self, result: Mapping[str, Any]) -> None:
        self.classification = str(result.get("failureClassification") or "skill_execution_failed")
        self.summary = str(result.get("summary") or "child Skill failed")
        self.failed_stage = str(result.get("failedStage") or "")
        self.first_mismatch = str(result.get("firstMismatch") or "")
        super().__init__(f"{self.classification}:{self.summary}")


def _decode_artifact(result: Mapping[str, Any]) -> dict[str, Any]:
    if result.get("state") != "succeeded":
        raise ChildSkillFailure(result)
    artifact = result.get("artifactPayload")
    encoded = artifact.get("contentBase64") if isinstance(artifact, Mapping) else None
    if not isinstance(encoded, str) or not encoded:
        raise RuntimeError("child_skill_artifact_missing")
    value = json.loads(base64.b64decode(encoded).decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("child_skill_artifact_invalid")
    return value


def _llm_child(skill_id: str, parameters: dict[str, Any], *, adapter_name: str) -> dict[str, Any]:
    runtime = _api_imports()
    result = runtime.execute_new_durable_llm_child(
        skill_id=skill_id,
        parameters=parameters,
        parent_capability_call_id=str(os.environ.get("OPENAIR_PARENT_CAPABILITY_CALL_ID") or ""),
        adapter_name=adapter_name,
    )
    return _decode_artifact(result)


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label}_must_be_object")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label}_must_be_array")
    return value


def _string_list(value: Any, label: str) -> list[str]:
    rows = _require_list(value, label)
    result: list[str] = []
    for item in rows:
        result.append(item)
    return result


def _unique_identity_map(rows: Sequence[Any], key: str, label: str) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for raw in rows:
        row = _require_mapping(raw, label)
        identity = str(row.get(key) or "").strip()
        result[identity] = row
    return result


def _input_authorities(payload: Mapping[str, Any]) -> tuple[
    dict[str, Mapping[str, Any]],
    dict[str, Mapping[str, Any]],
    dict[str, Mapping[str, Any]],
    dict[str, Mapping[str, Any]],
]:
    records = _unique_identity_map(
        _require_list(payload.get("research_records"), "research_records"),
        "record_id",
        "research_record",
    )
    support_target = _require_mapping(payload.get("support_target"), "support_target")
    targets = _unique_identity_map(
        _require_list(support_target.get("evidence_targets"), "support_target.evidence_targets"),
        "target_id",
        "support_target",
    )
    evidence_packet = _require_mapping(payload.get("evidence_packet"), "evidence_packet")
    evidence = _unique_identity_map(
        _require_list(evidence_packet.get("evidence_items"), "evidence_packet.evidence_items"),
        "evidence_id",
        "evidence_item",
    )
    sources = _unique_identity_map(
        _require_list(payload.get("source_provenance"), "source_provenance"),
        "source_id",
        "source_provenance",
    )
    _require_mapping(payload.get("pvd_project_context"), "pvd_project_context")
    _require_mapping(payload.get("coverage"), "coverage")
    _require_list(payload.get("unresolved_gaps"), "unresolved_gaps")
    _require_list(payload.get("conditional_evidence_linkage"), "conditional_evidence_linkage")
    return records, targets, evidence, sources


def _planner_input(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "pvd_project_context": deepcopy(payload["pvd_project_context"]),
        "research_records": deepcopy(payload["research_records"]),
        "source_provenance": deepcopy(payload["source_provenance"]),
        "support_target": deepcopy(payload["support_target"]),
        "coverage": deepcopy(payload["coverage"]),
        "unresolved_gaps": deepcopy(payload["unresolved_gaps"]),
        "conditional_evidence_linkage": deepcopy(payload["conditional_evidence_linkage"]),
    }


def _normalize_planner(
    draft: Mapping[str, Any],
    *,
    records: Mapping[str, Mapping[str, Any]],
    targets: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    status = str(draft.get("planning_status") or "").strip()
    selected = _require_list(draft.get("selected_records"), "planner.selected_records")
    normalized: list[dict[str, Any]] = []
    seen_records: set[str] = set()
    for raw in selected:
        row = _require_mapping(raw, "planner.selected_record")
        record_id = str(row.get("record_id") or "").strip()
        seen_records.add(record_id)
        target_ids = _string_list(row.get("support_target_ids"), "planner.support_target_ids")
        focus = _string_list(row.get("review_focus"), "planner.review_focus")
        relation_questions: list[dict[str, str]] = []
        for relation_raw in _require_list(row.get("relation_questions"), "planner.relation_questions"):
            relation = _require_mapping(relation_raw, "planner.relation_question")
            factor = str(relation.get("factor") or "").strip()
            response = str(relation.get("response") or "").strip()
            question = str(relation.get("question") or "").strip()
            relation_questions.append({"factor": factor, "response": response, "question": question})
        normalized.append(
            {
                "record_id": record_id,
                "support_target_ids": target_ids,
                "review_focus": focus,
                "relation_questions": relation_questions,
            }
        )
    return {
        "planning_status": status,
        "selected_records": normalized,
        "global_review_focus": _string_list(
            draft.get("global_review_focus"), "planner.global_review_focus"
        ),
        "uncertainty": _string_list(draft.get("uncertainty"), "planner.uncertainty"),
    }


def _record_evidence(
    record: Mapping[str, Any], evidence: Mapping[str, Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    refs = record.get("provenance_refs") or []
    material: list[dict[str, Any]] = []
    gaps: list[str] = []
    seen: set[str] = set()
    for raw in refs:
        ref = str(raw or "").strip()
        if not ref or ref in seen:
            continue
        seen.add(ref)
        row = evidence.get(ref)
        if row is None:
            gaps.append(ref)
        else:
            material.append(deepcopy(dict(row)))
    return material, gaps


def _deep_input(
    payload: Mapping[str, Any],
    plan_row: Mapping[str, Any],
    *,
    records: Mapping[str, Mapping[str, Any]],
    targets: Mapping[str, Mapping[str, Any]],
    evidence: Mapping[str, Mapping[str, Any]],
    sources: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    record_id = str(plan_row["record_id"])
    record = records[record_id]
    evidence_rows, evidence_gaps = _record_evidence(record, evidence)
    source_id = str(record.get("source_id") or "").strip()
    source = sources.get(source_id)
    return {
        "pvd_project_context": deepcopy(payload["pvd_project_context"]),
        "record": deepcopy(dict(record)),
        "source_provenance": deepcopy(dict(source)) if source is not None else None,
        "provenance_gap": source is None,
        "evidence_items": evidence_rows,
        "missing_evidence_refs": evidence_gaps,
        "support_target_slots": [
            deepcopy(dict(targets[target_id])) for target_id in plan_row["support_target_ids"]
        ],
        "review_focus": deepcopy(list(plan_row["review_focus"])),
        "relation_questions": deepcopy(list(plan_row["relation_questions"])),
        "coverage": deepcopy(payload["coverage"]),
        "unresolved_gaps": deepcopy(payload["unresolved_gaps"]),
        "conditional_evidence_linkage": deepcopy(payload["conditional_evidence_linkage"]),
    }


def _contains_key(value: Any, forbidden: str) -> bool:
    if isinstance(value, Mapping):
        return forbidden in value or any(_contains_key(child, forbidden) for child in value.values())
    if isinstance(value, list):
        return any(_contains_key(child, forbidden) for child in value)
    return False


def _validate_uncertainty(value: Any, label: str) -> dict[str, Any]:
    row = _require_mapping(value, label)
    level = str(row.get("level") or "").strip()
    reasons = _string_list(row.get("reasons"), f"{label}.reasons")
    return {"level": level, "reasons": reasons}


def _validate_evidence_refs(value: Any, allowed: set[str], label: str) -> list[str]:
    refs = _string_list(value, label)
    return refs


def _materialize_deep(
    draft: Mapping[str, Any],
    *,
    plan_row: Mapping[str, Any],
    record: Mapping[str, Any],
    evidence_ids: set[str],
) -> dict[str, Any]:
    decision_value = str(draft.get("decision_value") or "").strip()
    decision_summary = str(draft.get("decision_summary") or "").strip()
    measurement = str(draft.get("measurement_compatibility") or "").strip()
    uncertainty = _validate_uncertainty(draft.get("uncertainty"), "deep.uncertainty")
    key_matches = _string_list(draft.get("key_matches"), "deep.key_matches")
    important_differences = _string_list(
        draft.get("important_differences"), "deep.important_differences"
    )
    transfer_limits = _string_list(draft.get("transfer_limits"), "deep.transfer_limits")
    conflicts = _string_list(draft.get("conflicts"), "deep.conflicts")

    target_ids = list(plan_row["support_target_ids"])
    raw_findings = _require_list(draft.get("support_findings"), "deep.support_findings")
    findings: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_findings):
        target_id = target_ids[index] if index < len(target_ids) else raw.get("target_id")
        row = _require_mapping(raw, "deep.support_finding")
        support_level = str(row.get("support_level") or "").strip()
        support_basis = str(row.get("support_basis") or "").strip()
        finding_measurement = str(row.get("measurement_compatibility") or "").strip()
        findings.append(
            {
                "target_id": target_id,
                "support_level": support_level,
                "support_basis": support_basis,
                "conditions": _string_list(row.get("conditions"), "deep.support.conditions"),
                "key_matches": _string_list(row.get("key_matches"), "deep.support.key_matches"),
                "important_differences": _string_list(
                    row.get("important_differences"), "deep.support.important_differences"
                ),
                "measurement_compatibility": finding_measurement,
                "transfer_limits": _string_list(
                    row.get("transfer_limits"), "deep.support.transfer_limits"
                ),
                "uncertainty": _validate_uncertainty(
                    row.get("uncertainty"), "deep.support.uncertainty"
                ),
                "evidence_refs_used": _validate_evidence_refs(
                    row.get("evidence_refs_used"), evidence_ids, "deep.support.evidence_refs_used"
                ),
            }
        )

    relations: list[dict[str, Any]] = []
    for raw in _require_list(draft.get("verified_relations"), "deep.verified_relations"):
        row = _require_mapping(raw, "deep.verified_relation")
        factor = str(row.get("factor") or "").strip()
        response = str(row.get("response") or "").strip()
        relation_type = str(row.get("relation_type") or "").strip()
        factor_change = str(row.get("factor_change") or "").strip()
        response_change = str(row.get("response_change") or "").strip()
        objective_alignment = str(row.get("objective_alignment") or "").strip()
        relations.append(
            {
                "relation_type": relation_type,
                "factor": factor,
                "factor_change": factor_change,
                "response": response,
                "response_change": response_change,
                "objective_alignment": objective_alignment,
                "conditions": _string_list(row.get("conditions"), "deep.relation.conditions"),
                "context_factors": _string_list(
                    row.get("context_factors"), "deep.relation.context_factors"
                ),
                "confounders": _string_list(row.get("confounders"), "deep.relation.confounders"),
                "evidence_refs_used": _validate_evidence_refs(
                    row.get("evidence_refs_used"), evidence_ids, "deep.relation.evidence_refs_used"
                ),
            }
        )

    return {
        "record_id": str(record["record_id"]),
        "source_id": str(record.get("source_id") or ""),
        "decision_value": decision_value,
        "decision_summary": decision_summary,
        "key_matches": key_matches,
        "important_differences": important_differences,
        "measurement_compatibility": measurement,
        "transfer_limits": transfer_limits,
        "uncertainty": uncertainty,
        "support_findings": findings,
        "verified_relations": relations,
        "conflicts": conflicts,
    }


def _deep_failure(record_id: str, exc: Exception) -> dict[str, str]:
    if isinstance(exc, ChildSkillFailure):
        classification = exc.classification
    elif isinstance(exc, ValueError):
        classification = "deep_output_contract_invalid"
    else:
        classification = "deep_verification_unavailable"
    return {"record_id": record_id, "classification": classification}


def _record_informativeness(record: Mapping[str, Any]) -> int:
    """Rank records for source-coverage backfill: known facts and carried context."""
    count = 0
    for section in ("material", "process"):
        for fact in (record.get(section) or {}).values():
            if isinstance(fact, Mapping) and fact.get("fact_status") == "known":
                count += 1
    count += len(record.get("outcomes") or [])
    count += len(record.get("other_context") or [])
    return count


def _enforce_planner_bounds(
    plan: Mapping[str, Any],
    *,
    records: Mapping[str, Mapping[str, Any]],
    targets: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Keep the deep-verification scope inside the contract bounds.

    The planner may under-select (observed in production: 1 of 16 records on a
    temperature-0 provider) or over-select past the runtime bound.  When the
    plan is active, deterministically enforce both edges: truncate to
    MAX_DEEP_RECORDS, and while capacity remains, backfill the most
    informative record of every supplied source the planner left uncovered.
    The planner's scientific choices are preserved; backfill only adds
    source-level coverage and is marked in review_focus so downstream
    reasoning can see which rows were not planner-selected.
    """
    selected = [dict(row) for row in plan["selected_records"][:MAX_DEEP_RECORDS]]
    selected_ids = {str(row["record_id"]) for row in selected}
    covered_sources = {
        str(records[record_id].get("source_id") or "")
        for record_id in selected_ids
        if record_id in records
    }
    by_source: dict[str, list[Mapping[str, Any]]] = {}
    for record_id, record in records.items():
        if record_id in selected_ids:
            continue
        by_source.setdefault(str(record.get("source_id") or ""), []).append(record)
    backfilled = 0
    for source_id in sorted(by_source):
        if source_id in covered_sources or len(selected) >= MAX_DEEP_RECORDS:
            continue
        best = max(by_source[source_id], key=_record_informativeness)
        selected.append(
            {
                "record_id": str(best.get("record_id") or ""),
                "support_target_ids": list(targets),
                "review_focus": [
                    "planner_underselection_backfill:source_level_coverage"
                ],
                "relation_questions": [],
            }
        )
        backfilled += 1
    uncertainty = list(plan["uncertainty"])
    if backfilled:
        uncertainty.append(f"planner_underselection_backfill:{backfilled}")
    return {
        "planning_status": plan["planning_status"],
        "selected_records": selected,
        "global_review_focus": list(plan["global_review_focus"]),
        "uncertainty": uncertainty,
    }


def _run_deep_verification(
    payload: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    records: Mapping[str, Mapping[str, Any]],
    targets: Mapping[str, Mapping[str, Any]],
    evidence: Mapping[str, Mapping[str, Any]],
    sources: Mapping[str, Mapping[str, Any]],
    llm_child: Callable[..., dict[str, Any]],
    max_workers: int,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    plan_rows = list(plan["selected_records"])
    if not plan_rows:
        return [], []

    def execute(index: int, plan_row: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
        record_id = str(plan_row["record_id"])
        child_input = _deep_input(
            payload,
            plan_row,
            records=records,
            targets=targets,
            evidence=evidence,
            sources=sources,
        )
        draft = llm_child(
            DEEP_SKILL_ID,
            child_input,
            adapter_name="pvd_t05_record_deep_verification",
        )
        record_evidence_ids = {
            str(row.get("evidence_id"))
            for row in child_input["evidence_items"]
            if isinstance(row, Mapping) and row.get("evidence_id")
        }
        materialized = _materialize_deep(
            draft,
            plan_row=plan_row,
            record=records[record_id],
            evidence_ids=record_evidence_ids,
        )
        return index, materialized

    successes: dict[int, dict[str, Any]] = {}
    failures: dict[int, dict[str, str]] = {}
    worker_count = max(1, min(int(max_workers), MAX_DEEP_WORKERS, len(plan_rows)))
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = {
            pool.submit(execute, index, plan_row): (index, str(plan_row["record_id"]))
            for index, plan_row in enumerate(plan_rows)
        }
        for future in as_completed(futures):
            index, record_id = futures[future]
            try:
                _, row = future.result()
                successes[index] = row
            except Exception as exc:  # isolate one record; do not cancel siblings
                failures[index] = _deep_failure(record_id, exc)
    return (
        [successes[index] for index in sorted(successes)],
        [failures[index] for index in sorted(failures)],
    )


def _used_evidence_refs(verified: Sequence[Mapping[str, Any]]) -> set[str]:
    result: set[str] = set()
    for analysis in verified:
        for finding in analysis.get("support_findings") or []:
            if isinstance(finding, Mapping):
                result.update(str(ref) for ref in finding.get("evidence_refs_used") or [])
        for relation in analysis.get("verified_relations") or []:
            if isinstance(relation, Mapping):
                result.update(str(ref) for ref in relation.get("evidence_refs_used") or [])
    return {ref for ref in result if ref}


def _synthesis_input(
    payload: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    verified: Sequence[Mapping[str, Any]],
    failures: Sequence[Mapping[str, Any]],
    evidence: Mapping[str, Mapping[str, Any]],
    sources: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    evidence_refs = _used_evidence_refs(verified)
    source_ids = {str(row.get("source_id") or "") for row in verified if row.get("source_id")}
    selected_ids = [str(row["record_id"]) for row in plan["selected_records"]]
    verified_ids = {str(row["record_id"]) for row in verified}
    return {
        "pvd_project_context": deepcopy(payload["pvd_project_context"]),
        "support_target": deepcopy(payload["support_target"]),
        "planning": deepcopy(dict(plan)),
        "verified_record_analyses": deepcopy(list(verified)),
        "failed_record_verifications": deepcopy(list(failures)),
        "unverified_selected_record_ids": [row for row in selected_ids if row not in verified_ids],
        "raw_evidence_items": [deepcopy(dict(evidence[ref])) for ref in sorted(evidence_refs) if ref in evidence],
        "source_provenance": [deepcopy(dict(sources[ref])) for ref in sorted(source_ids) if ref in sources],
        "coverage": deepcopy(payload["coverage"]),
        "unresolved_gaps": deepcopy(payload["unresolved_gaps"]),
        "conditional_evidence_linkage": deepcopy(payload["conditional_evidence_linkage"]),
    }


def _target_finding(
    analysis: Mapping[str, Any], target_id: str
) -> Mapping[str, Any] | None:
    for finding in analysis.get("support_findings") or []:
        if isinstance(finding, Mapping) and str(finding.get("target_id") or "") == target_id:
            return finding
    return None


def _validate_synthesis(
    draft: Mapping[str, Any],
    *,
    verified: Sequence[Mapping[str, Any]],
    target_ids: set[str],
) -> dict[str, Any]:
    status = str(draft.get("analysis_status") or "").strip()
    verified_by_record = {str(row["record_id"]): row for row in verified}
    verified_record_ids = set(verified_by_record)
    verified_target_ids = {
        str(finding.get("target_id"))
        for row in verified
        for finding in row.get("support_findings") or []
        if isinstance(finding, Mapping) and finding.get("target_id")
    }

    assessments: list[dict[str, Any]] = []
    seen_record_ids: set[str] = set()
    for raw in _require_list(draft.get("evidence_assessments"), "synthesis.evidence_assessments"):
        row = _require_mapping(raw, "synthesis.evidence_assessment")
        record_id = str(row.get("record_id") or "").strip()
        seen_record_ids.add(record_id)
        measurement = str(row.get("measurement_compatibility") or "").strip()
        source = verified_by_record.get(record_id) or row
        assessments.append(
            {
                "record_id": record_id,
                "source_id": str(source.get("source_id") or ""),
                "evidence_refs": sorted(_used_evidence_refs([source])),
                "key_matches": _string_list(row.get("key_matches"), "synthesis.key_matches"),
                "important_differences": _string_list(
                    row.get("important_differences"), "synthesis.important_differences"
                ),
                "measurement_compatibility": measurement,
                "transfer_limits": _string_list(
                    row.get("transfer_limits"), "synthesis.transfer_limits"
                ),
                "uncertainty": _string_list(row.get("uncertainty"), "synthesis.uncertainty"),
            }
        )

    conclusions: list[dict[str, Any]] = []
    seen_targets: set[str] = set()
    for raw in _require_list(draft.get("support_conclusions"), "synthesis.support_conclusions"):
        row = _require_mapping(raw, "synthesis.support_conclusion")
        target_id = str(row.get("judgment_target") or "").strip()
        seen_targets.add(target_id)
        support_level = str(row.get("support_level") or "").strip()
        allowed_uses = _string_list(row.get("allowed_uses"), "synthesis.allowed_uses")

        record_refs = _string_list(
            row.get("evidence_record_refs"), "synthesis.evidence_record_refs"
        )

        evidence_refs: set[str] = set()
        for record_id in record_refs:
            finding = _target_finding(verified_by_record.get(record_id) or {}, target_id) or {}
            evidence_refs.update(
                str(ref) for ref in finding.get("evidence_refs_used") or [] if str(ref)
            )

        # Final Synthesis owns the scientific authorization, but every positive
        # authorization must be grounded in a verified record/target pair and
        # concrete T04 Evidence provenance. Code validates and materializes only.

        rationale = str(row.get("rationale") or "").strip()
        conclusions.append(
            {
                "conclusion_id": f"support_conclusion:{target_id}",
                "judgment_target": target_id,
                "evidence_record_refs": record_refs,
                "evidence_refs": sorted(evidence_refs),
                "support_level": support_level,
                "allowed_uses": allowed_uses,
                "conditions": _string_list(row.get("conditions"), "synthesis.conditions"),
                "transfer_limits": _string_list(
                    row.get("transfer_limits"), "synthesis.conclusion.transfer_limits"
                ),
                "conflicts": _string_list(
                    row.get("conflicts"), "synthesis.conclusion.conflicts"
                ),
                "important_unknowns": _string_list(
                    row.get("important_unknowns"), "synthesis.conclusion.important_unknowns"
                ),
                "rationale": rationale,
            }
        )

    summary = str(draft.get("summary") or "").strip()
    return {
        "analysis_status": status,
        "evidence_assessments": assessments,
        "support_conclusions": conclusions,
        "conflicts": _string_list(draft.get("conflicts"), "synthesis.conflicts"),
        "important_unknowns": _string_list(
            draft.get("important_unknowns"), "synthesis.important_unknowns"
        ),
        "summary": summary,
    }


def _safe_without_authorization(
    *,
    verified: Sequence[Mapping[str, Any]],
    failures: Sequence[Mapping[str, Any]],
    reason: str,
) -> dict[str, Any]:
    assessments = [
        {
            "record_id": str(row["record_id"]),
            "source_id": str(row.get("source_id") or ""),
            "evidence_refs": sorted(_used_evidence_refs([row])),
            "key_matches": deepcopy(list(row.get("key_matches") or [])),
            "important_differences": deepcopy(list(row.get("important_differences") or [])),
            "measurement_compatibility": str(row.get("measurement_compatibility") or "unknown"),
            "transfer_limits": deepcopy(list(row.get("transfer_limits") or [])),
            "uncertainty": deepcopy(list((row.get("uncertainty") or {}).get("reasons") or [])),
        }
        for row in verified
    ]
    system_unknowns = [reason]
    system_unknowns.extend(
        f"记录 {row['record_id']} 的逐记录深入核验不可用。"
        for row in failures
        if row.get("record_id")
    )
    conflicts: list[str] = []
    for row in verified:
        for conflict in row.get("conflicts") or []:
            if isinstance(conflict, str) and conflict and conflict not in conflicts:
                conflicts.append(conflict)
    return {
        "analysis_status": "partial" if verified else "insufficient",
        "evidence_assessments": assessments,
        "support_conclusions": [],
        "conflicts": conflicts,
        "important_unknowns": system_unknowns,
        "summary": "最终综合授权不可用；已保留完成的逐记录科研核验，但本轮不形成任何下游证据使用授权。",
    }


def run_evidence_applicability_comparability(
    payload: dict[str, Any],
    *,
    llm_child: Callable[..., dict[str, Any]] | None = None,
    max_workers: int = MAX_DEEP_WORKERS,
) -> dict[str, Any]:
    """Run the migrated T05 research chain without introducing a second fact authority.

    Scientific semantics are owned by three reasoning stages.  This adapter only
    freezes identities, projects inputs, schedules independent record calls,
    validates references, joins verified results, and fail-closes authorization.
    """

    records, targets, evidence, sources = _input_authorities(payload)
    llm_child = llm_child or _llm_child

    try:
        planner_draft = llm_child(
            PLANNER_SKILL_ID,
            _planner_input(payload),
            adapter_name="pvd_t05_global_planning",
        )
        plan = _normalize_planner(planner_draft, records=records, targets=targets)
    except Exception:
        return _safe_without_authorization(
            verified=[],
            failures=[],
            reason="全局科研规划不可用；本轮未形成可验证的证据授权链。",
        )
    if plan["selected_records"] or str(plan["planning_status"]) == "planned":
        plan = _enforce_planner_bounds(plan, records=records, targets=targets)

    verified, failures = _run_deep_verification(
        payload,
        plan,
        records=records,
        targets=targets,
        evidence=evidence,
        sources=sources,
        llm_child=llm_child,
        max_workers=max_workers,
    )

    # Deep Verification may judge science, but never owns downstream-use authorization.
    # If every selected record failed, Final Synthesis has no verified basis to sign.
    if plan["selected_records"] and not verified:
        return _safe_without_authorization(
            verified=[],
            failures=failures,
            reason="所有被选记录的深入核验均不可用；未经核验的内容不会进入正式授权。",
        )

    try:
        synthesis_draft = llm_child(
            SYNTHESIS_SKILL_ID,
            _synthesis_input(
                payload,
                plan=plan,
                verified=verified,
                failures=failures,
                evidence=evidence,
                sources=sources,
            ),
            adapter_name="pvd_t05_final_synthesis",
        )
        return _validate_synthesis(
            synthesis_draft,
            verified=verified,
            target_ids=set(targets),
        )
    except Exception:
        return _safe_without_authorization(
            verified=verified,
            failures=failures,
            reason="最终综合授权不可用；未经最终综合签署的证据不获得下游使用授权。",
        )
