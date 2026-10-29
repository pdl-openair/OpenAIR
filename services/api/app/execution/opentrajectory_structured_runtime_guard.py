"""Deterministic runtime guard for OpenTrajectory structured Skills.

The guard runs after LLM JSON parsing and before JSON Schema validation.
Recoverable model defects are sanitized or isolated while preserving the
remaining useful output. ``RuntimeGuardRejected`` is reserved for failures that
leave no structurally safe Artifact for the current capability contract.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any

GUARD_VERSION = "p06c-v1"

_PHASE_BY_SKILL_ID = {
    "problem-framing-generation": "problem_framing",
    "problem_framing_generation": "problem_framing",
    "structured-requirement-generation": "phase1",
    "structured_requirement_generation": "phase1",
    "constraint-interpretation-skill": "phase2",
    "constraint_interpretation_skill": "phase2",
    "retrieval-matrix-generation": "phase3",
    "retrieval_matrix_generation": "phase3",
    "evidence-node-extraction-skill": "p07c_evidence_node_extraction",
    "evidence_node_extraction": "p07c_evidence_node_extraction",
    "mechanism-chain-synthesis-skill": "p07d_mechanism_chain_synthesis",
    "mechanism_chain_synthesis": "p07d_mechanism_chain_synthesis",
    "research-lead-hypothesis-generation-skill": "p0_final_synthesis",
    "research_lead_hypothesis_generation": "p0_final_synthesis",
}

# Only these identity/shape failures may reject an entire managed output.
# Every other check is recoverable by default and must preserve usable content.
_FATAL_CHECK_IDS = frozenset({
    "OT-R00",
    "P1-R02",
    "PF-R02",
})

class RuntimeGuardRejected(RuntimeError):
    """Raised only when no structurally safe Artifact can be formed."""

    def __init__(self, diagnostics: dict[str, Any]):
        self.diagnostics = diagnostics
        failures = diagnostics.get("fatal_failures") or diagnostics.get("hard_gate_failures") or []
        super().__init__("OpenTrajectory runtime guard rejected output: " + ", ".join(str(item) for item in failures))


def _clean_string(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _sanitize_representation(value: Any, path: str, changes: list[str]) -> Any:
    if isinstance(value, str):
        cleaned = _clean_string(value)
        if cleaned != value:
            changes.append(path)
        return cleaned
    if isinstance(value, list):
        cleaned_items = [_sanitize_representation(item, f"{path}[{index}]", changes) for index, item in enumerate(value)]
        if all(isinstance(item, str) for item in cleaned_items):
            seen: set[str] = set()
            deduped: list[str] = []
            for item in cleaned_items:
                if item not in seen:
                    seen.add(item)
                    deduped.append(item)
            if len(deduped) != len(cleaned_items):
                changes.append(path + ":ordered_dedupe")
            return deduped
        return cleaned_items
    if isinstance(value, dict):
        return {str(key): _sanitize_representation(item, f"{path}.{key}", changes) for key, item in value.items()}
    return value


def _new_diagnostics(phase: str, managed: bool = True) -> dict[str, Any]:
    return {
        "guard_version": GUARD_VERSION,
        "managed": managed,
        "phase": phase,
        "status": "passed",
        "downstream_execution_allowed": True,
        "sanitizer_applied": False,
        "sanitizer_changes": [],
        "fatal_failures": [],
        "hard_gate_failures": [],
        "recoverable_failures": [],
        "manual_review_triggers": [],
        "warnings": [],
        "degraded": False,
        "fallback_used": False,
        "checks": [],
        "result_protocol": {
            "status": "ok",
            "accepted_items": [],
            "quarantined_items": [],
            "repaired_items": [],
            "warnings": [],
            "coverage": {},
        },
    }


def _protocol_quarantine(
    diagnostics: dict[str, Any],
    *,
    path: str,
    reason: str,
) -> None:
    diagnostics["result_protocol"]["quarantined_items"].append({
        "path": path,
        "reason": reason,
    })


def _protocol_repair(
    diagnostics: dict[str, Any],
    *,
    path: str,
    action: str,
) -> None:
    diagnostics["result_protocol"]["repaired_items"].append({
        "path": path,
        "action": action,
    })


def _record(
    diagnostics: dict[str, Any],
    check_id: str,
    status: str,
    message: str,
    *,
    evidence: dict[str, Any] | None = None,
    failure_level: str = "recoverable",
) -> None:
    if status in {"passed", "not_applicable"}:
        disposition = "none"
    elif check_id in _FATAL_CHECK_IDS and failure_level in {"hard_gate", "fatal"}:
        disposition = "fatal"
        failure_level = "fatal"
    elif failure_level == "warning" or status in {"warning", "manual_review_trigger"}:
        disposition = "advisory"
        failure_level = "warning"
    else:
        disposition = "recoverable"
        failure_level = "recoverable"
    row = {
        "check_id": check_id,
        "status": status,
        "failure_level": failure_level,
        "disposition": disposition,
        "message": message,
        "evidence": evidence or {},
    }
    diagnostics["checks"].append(row)
    if status == "failed" and disposition == "fatal":
        diagnostics["fatal_failures"].append(check_id)
        diagnostics["hard_gate_failures"].append(check_id)
    elif status in {"failed", "recovered"} and disposition == "recoverable":
        diagnostics["recoverable_failures"].append(check_id)
        diagnostics["warnings"].append(check_id)
        diagnostics["degraded"] = True
    if status == "manual_review_trigger":
        diagnostics["manual_review_triggers"].append(check_id)


def _is_nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _require_nonempty(diagnostics: dict[str, Any], payload: dict[str, Any], check_id: str, paths: list[str]) -> None:
    missing = [path for path in paths if path not in payload or not _is_nonempty(payload.get(path))]
    if missing:
        _record(diagnostics, check_id, "failed", "core output fields are missing or empty", evidence={"paths": missing})
    else:
        _record(diagnostics, check_id, "passed", "core output fields are non-empty")


def _finalize(diagnostics: dict[str, Any]) -> None:
    for key in (
        "fatal_failures",
        "hard_gate_failures",
        "recoverable_failures",
        "manual_review_triggers",
        "warnings",
        "sanitizer_changes",
    ):
        diagnostics[key] = list(dict.fromkeys(diagnostics.get(key) or []))

    if diagnostics["fatal_failures"]:
        diagnostics["status"] = "rejected"
        diagnostics["downstream_execution_allowed"] = False
        diagnostics["result_protocol"]["status"] = "fatal"
        diagnostics["result_protocol"]["warnings"] = diagnostics["warnings"]
        raise RuntimeGuardRejected(diagnostics)

    advisory_degradation = [
        *diagnostics["recoverable_failures"],
        *diagnostics["manual_review_triggers"],
    ]
    if advisory_degradation or diagnostics["result_protocol"]["quarantined_items"] or diagnostics["result_protocol"]["repaired_items"]:
        diagnostics["status"] = "degraded"
        diagnostics["downstream_execution_allowed"] = True
        diagnostics["degraded"] = True
        diagnostics["warnings"] = list(dict.fromkeys([
            *diagnostics["warnings"],
            *advisory_degradation,
        ]))
        diagnostics["result_protocol"]["status"] = "degraded"
    elif diagnostics["warnings"]:
        # Advisory-only scientific uncertainty does not change the legacy
        # guard execution status, but it does make the materialized result
        # explicitly degraded under ResultProtocolV1.
        diagnostics["result_protocol"]["status"] = "degraded"
    else:
        diagnostics["result_protocol"]["status"] = "ok"
    diagnostics["result_protocol"]["warnings"] = diagnostics["warnings"]


def _guard_phase1(input_payload: dict[str, Any], output: dict[str, Any], diagnostics: dict[str, Any]) -> None:
    _require_nonempty(diagnostics, output, "P1-R01", ["original_user_query", "research_object"])
    expected_query = _clean_string(str(input_payload.get("user_query") or ""))
    actual_query = _clean_string(str(output.get("original_user_query") or ""))
    if actual_query != expected_query:
        _record(
            diagnostics,
            "P1-R02",
            "failed",
            "original_user_query must preserve the input query after whitespace normalization",
            evidence={"expected": expected_query, "actual": actual_query},
            failure_level="fatal" if actual_query else "recoverable",
        )
    else:
        _record(diagnostics, "P1-R02", "passed", "original_user_query preserves the input query")

    confidence = output.get("requirement_confidence")
    ambiguities = output.get("ambiguities")
    if isinstance(confidence, (int, float)) and confidence < 0.6 and not _is_nonempty(ambiguities):
        _record(diagnostics, "P1-R03", "failed", "low-confidence output must expose ambiguities")
    else:
        _record(diagnostics, "P1-R03", "passed", "low-confidence ambiguity disclosure is consistent")

    prohibited = sorted({"search_matrix", "expanded_query_variants", "interpreted_constraints"}.intersection(output))
    for key in prohibited:
        output.pop(key, None)
        _protocol_quarantine(diagnostics, path=key, reason="downstream_structure_leak")
    if prohibited:
        diagnostics["sanitizer_applied"] = True
        diagnostics["sanitizer_changes"].append("phase1_downstream_structure_leak")
        _record(diagnostics, "P1-R04", "recovered", "Phase1 downstream structures were quarantined", evidence={"keys": prohibited})
    else:
        _record(diagnostics, "P1-R04", "passed", "Phase1 output does not leak downstream structures")


_PROBLEM_FRAMING_UNRESOLVED_STATES = {"unknown", "ambiguous", "conflicting"}


def _ignore_deprecated_filter_rules(
    constraints: Any,
    *,
    path_prefix: str,
    diagnostics: dict[str, Any],
) -> list[str]:
    """Remove the deprecated field without ever accessing its value."""
    ignored: list[str] = []
    if not isinstance(constraints, list):
        return ignored
    for index, constraint in enumerate(constraints):
        if not isinstance(constraint, dict) or "downstream_filter_rules" not in constraint:
            continue
        del constraint["downstream_filter_rules"]
        path = f"{path_prefix}[{index}].downstream_filter_rules"
        ignored.append(path)
        diagnostics["sanitizer_changes"].append(path)
    if ignored:
        diagnostics["sanitizer_applied"] = True
    return ignored


def _record_deprecated_filter_rules(
    diagnostics: dict[str, Any],
    *,
    check_id: str,
    ignored: list[str],
) -> None:
    if not ignored:
        _record(
            diagnostics,
            check_id,
            "passed",
            "deprecated filter-rule field is absent",
        )
        return
    _record(
        diagnostics,
        check_id,
        "recovered",
        "deprecated filter-rule field was ignored without inspecting its content",
        evidence={"ignored_paths": ignored},
        failure_level="recoverable",
    )


def _guard_problem_framing(
    input_payload: dict[str, Any],
    output: dict[str, Any],
    diagnostics: dict[str, Any],
) -> None:
    intent = output.get("intent_understanding")
    criteria = output.get("constraints_criteria")
    if not isinstance(intent, dict) or not isinstance(criteria, dict):
        _record(
            diagnostics,
            "PF-R01",
            "failed",
            "problem framing requires intent_understanding and constraints_criteria objects",
        )
        return
    _record(diagnostics, "PF-R01", "passed", "professional problem-framing views exist")

    expected_query = _clean_string(str(input_payload.get("user_query") or ""))
    actual_query = _clean_string(str(intent.get("original_user_query") or ""))
    if actual_query != expected_query:
        _record(
            diagnostics,
            "PF-R02",
            "failed",
            "original_user_query must preserve the input query after whitespace normalization",
            evidence={"expected": expected_query, "actual": actual_query},
            failure_level="fatal" if actual_query else "recoverable",
        )
    else:
        _record(diagnostics, "PF-R02", "passed", "original_user_query preserves the input query")

    prohibited_locations: list[str] = []
    for field in ("requirement_confidence", "confidence", "coverage_level"):
        if field in intent:
            prohibited_locations.append(f"intent_understanding.{field}")
    for field in (
        "quality_report",
        "confidence",
        "coverage_level",
        "needs_manual_review",
        "needs_user_confirmation",
    ):
        if field in criteria:
            prohibited_locations.append(f"constraints_criteria.{field}")
    constraints = criteria.get("interpreted_constraints")
    if isinstance(constraints, list):
        for index, constraint in enumerate(constraints):
            if not isinstance(constraint, dict):
                continue
            for field in ("confidence", "needs_manual_review", "needs_user_confirmation"):
                if field in constraint:
                    prohibited_locations.append(
                        f"constraints_criteria.interpreted_constraints[{index}].{field}"
                    )
    if prohibited_locations:
        for field in ("requirement_confidence", "confidence", "coverage_level"):
            intent.pop(field, None)
        for field in ("quality_report", "confidence", "coverage_level", "needs_manual_review", "needs_user_confirmation"):
            criteria.pop(field, None)
        if isinstance(constraints, list):
            for constraint in constraints:
                if isinstance(constraint, dict):
                    for field in ("confidence", "needs_manual_review", "needs_user_confirmation"):
                        constraint.pop(field, None)
        diagnostics["sanitizer_applied"] = True
        diagnostics["sanitizer_changes"].extend(prohibited_locations)
        for path in prohibited_locations:
            _protocol_quarantine(diagnostics, path=path, reason="model_routing_authority_removed")
        _record(
            diagnostics,
            "PF-R03",
            "recovered",
            "model self-evaluation and routing declarations were removed without blocking the Artifact",
            evidence={"paths": prohibited_locations},
        )
    else:
        _record(
            diagnostics,
            "PF-R03",
            "passed",
            "model output contains no self-evaluation or routing declarations",
        )

    ignored_filters = _ignore_deprecated_filter_rules(
        constraints,
        path_prefix="constraints_criteria.interpreted_constraints",
        diagnostics=diagnostics,
    )
    unresolved_constraints: list[str] = []
    if isinstance(constraints, list):
        for index, constraint in enumerate(constraints):
            if not isinstance(constraint, dict):
                continue
            state = str(constraint.get("semantic_state") or "")
            if state not in _PROBLEM_FRAMING_UNRESOLVED_STATES:
                continue
            name = _clean_string(str(constraint.get("constraint_name") or ""))
            unresolved_constraints.append(name or f"constraint[{index}]")
    _record_deprecated_filter_rules(
        diagnostics,
        check_id="PF-R04",
        ignored=ignored_filters,
    )

    routing_reasons: list[str] = []
    if _is_nonempty(intent.get("ambiguities")):
        routing_reasons.append("intent_ambiguities")
    if _is_nonempty(criteria.get("missing_information")):
        routing_reasons.append("missing_information")
    if _is_nonempty(criteria.get("research_risks")):
        routing_reasons.append("research_risks")
    routing_reasons.extend(f"unresolved_constraint:{name}" for name in unresolved_constraints)

    # Ambiguity, missing information, research risk, and unresolved constraints
    # are scientific content. This guard preserves them as diagnostics but has
    # no authority to create a reviewer or stop the frozen execution graph.
    # New runtime consumers use result_protocol.warnings
    # as the sole formal advisory channel.
    diagnostics["routing_reasons"] = routing_reasons
    if routing_reasons:
        diagnostics["warnings"].extend(["PF-W01", *routing_reasons])
        _record(
            diagnostics,
            "PF-W01",
            "warning",
            "problem-framing uncertainty is preserved for downstream research",
            evidence={"routing_reasons": routing_reasons},
            failure_level="warning",
        )
    else:
        _record(
            diagnostics,
            "PF-G01",
            "passed",
            "formal problem-framing state satisfies the deterministic gate",
        )


def _structural_string_list(
    value: Any,
    *,
    path: str,
    diagnostics: dict[str, Any],
) -> list[str]:
    """Keep usable strings without judging language or research meaning."""
    if not isinstance(value, list):
        _protocol_quarantine(diagnostics, path=path, reason="collection_not_array")
        _protocol_repair(diagnostics, path=path, action="replaced_with_empty_array")
        return []
    output: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, str) or not item.strip():
            _protocol_quarantine(diagnostics, path=item_path, reason="non_empty_string_required")
            continue
        key = _clean_string(item).casefold()
        if key in seen:
            _protocol_quarantine(diagnostics, path=item_path, reason="exact_normalized_duplicate")
            continue
        seen.add(key)
        output.append(_clean_string(item))
    return output


def _guard_phase2(input_payload: dict[str, Any], output: dict[str, Any], diagnostics: dict[str, Any]) -> None:
    constraints = output.get("interpreted_constraints")
    report = output.get("quality_report")
    if not isinstance(constraints, list) or not isinstance(report, dict):
        _record(
            diagnostics,
            "P2-R01",
            "failed",
            "Phase2 requires interpreted_constraints array and quality_report object",
        )
        return
    _record(diagnostics, "P2-R01", "passed", "Phase2 core structures exist")

    ignored_filters = _ignore_deprecated_filter_rules(
        constraints,
        path_prefix="interpreted_constraints",
        diagnostics=diagnostics,
    )
    accepted_constraints: list[dict[str, Any]] = []
    for index, constraint in enumerate(constraints):
        path = f"interpreted_constraints[{index}]"
        if not isinstance(constraint, dict):
            _protocol_quarantine(diagnostics, path=path, reason="constraint_not_object")
            continue
        constraint["downstream_search_terms"] = _structural_string_list(
            constraint.get("downstream_search_terms"),
            path=f"{path}.downstream_search_terms",
            diagnostics=diagnostics,
        )
        accepted_constraints.append(constraint)
    if len(accepted_constraints) != len(constraints):
        output["interpreted_constraints"] = accepted_constraints
        diagnostics["sanitizer_applied"] = True
        diagnostics["sanitizer_changes"].append("interpreted_constraints:local_quarantine")
        _record(
            diagnostics,
            "P2-R02",
            "recovered",
            "invalid constraint rows were quarantined while usable constraints were preserved",
            evidence={"accepted_count": len(accepted_constraints), "input_count": len(constraints)},
        )
    else:
        _record(
            diagnostics,
            "P2-R02",
            "passed",
            "constraint rows are structurally usable; language and scientific meaning are not runtime decisions",
        )

    _record_deprecated_filter_rules(
        diagnostics,
        check_id="P2-R03",
        ignored=ignored_filters,
    )

    uncertainty_reasons: list[str] = []
    for index, constraint in enumerate(accepted_constraints):
        if constraint.get("needs_user_confirmation") is True:
            uncertainty_reasons.append(f"constraint[{index}].needs_user_confirmation")
        confidence = constraint.get("confidence")
        if isinstance(confidence, (int, float)) and confidence < 0.6:
            uncertainty_reasons.append(f"constraint[{index}].confidence")
    if report.get("needs_manual_review") is True:
        uncertainty_reasons.append("quality_report.needs_manual_review")
    if str(report.get("coverage_level") or "") in {"none", "partial"}:
        uncertainty_reasons.append("quality_report.coverage_level")
    if uncertainty_reasons or report.get("risk_flags") or report.get("missing_inputs"):
        _record(
            diagnostics,
            "P2-W01",
            "warning",
            "Phase2 uncertainty is preserved as research content and never creates runtime blocking authority",
            evidence={"reasons": uncertainty_reasons},
            failure_level="warning",
        )

    prohibited = sorted({"search_matrix", "expanded_query_variants"}.intersection(output))
    for key in prohibited:
        output.pop(key, None)
        _protocol_quarantine(diagnostics, path=key, reason="downstream_structure_leak")
    if prohibited:
        diagnostics["sanitizer_applied"] = True
        diagnostics["sanitizer_changes"].append("phase2_downstream_structure_leak")
        _record(
            diagnostics,
            "P2-R06",
            "recovered",
            "Phase3 structures were removed from the Phase2 Artifact",
            evidence={"keys": prohibited},
        )
    else:
        _record(diagnostics, "P2-R06", "passed", "Phase2 output does not leak Phase3 structures")


def _query_key(query: str) -> str:
    return _clean_string(query).casefold()


def _boolean_issue(query: str) -> str | None:
    """Pure Boolean syntax check. It does not classify query meaning or language."""
    depth = 0
    in_quote = False
    escaped = False
    for char in query:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_quote = not in_quote
            continue
        if in_quote:
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return "closing_parenthesis_without_opening"
    if in_quote:
        return "unbalanced_double_quotes"
    if depth != 0:
        return "unbalanced_parentheses"
    return None


def _canonical_query(input_payload: dict[str, Any]) -> str:
    intent = input_payload.get("intent_understanding")
    if isinstance(intent, dict):
        value = intent.get("original_user_query")
        if isinstance(value, str) and value.strip():
            return _clean_string(value)
    value = input_payload.get("user_query")
    return _clean_string(value) if isinstance(value, str) else ""


def _guard_phase3(input_payload: dict[str, Any], output: dict[str, Any], diagnostics: dict[str, Any]) -> None:
    _require_nonempty(
        diagnostics,
        output,
        "P3-R01",
        ["search_matrix", "expanded_query_variants", "quality_report", "assembled_facets"],
    )
    variants = output.get("expanded_query_variants")
    rows = variants if isinstance(variants, list) else []
    if not isinstance(variants, list):
        _protocol_quarantine(diagnostics, path="expanded_query_variants", reason="collection_not_array")

    # Funnel: scan once. Only when the collection contains a defect do we
    # narrow to the affected item. Healthy collections take the fast path.
    accepted: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(rows):
        path = f"expanded_query_variants[{index}]"
        if not isinstance(item, dict):
            _protocol_quarantine(diagnostics, path=path, reason="variant_not_object")
            continue
        query = item.get("query")
        if not isinstance(query, str) or not query.strip():
            _protocol_quarantine(diagnostics, path=f"{path}.query", reason="empty_query")
            continue
        query = _clean_string(query)
        issue = _boolean_issue(query)
        if issue:
            _protocol_quarantine(diagnostics, path=f"{path}.query", reason=issue)
            continue
        key = _query_key(query)
        if key in seen:
            _protocol_quarantine(diagnostics, path=path, reason="exact_normalized_duplicate")
            continue
        seen.add(key)
        row = copy.deepcopy(item)
        row["query"] = query
        accepted.append(row)

    if not accepted:
        fallback = _canonical_query(input_payload)
        if fallback:
            accepted.append({
                "query": fallback,
                "variant_type": "fallback",
                "search_intent": "core_topic",
                "source_constraints": [],
                "rationale": "Canonical user query fallback after generated queries were quarantined.",
                "priority": 1,
            })
            diagnostics["fallback_used"] = True
            _protocol_repair(
                diagnostics,
                path="expanded_query_variants",
                action="canonical_user_query_fallback",
            )
    if accepted != rows:
        output["expanded_query_variants"] = accepted
        diagnostics["sanitizer_applied"] = True
        diagnostics["sanitizer_changes"].append("expanded_query_variants:local_quarantine")
        _record(
            diagnostics,
            "P3-R02",
            "recovered",
            "invalid query variants were quarantined while usable variants were preserved",
            evidence={"accepted_count": len(accepted), "input_count": len(rows)},
        )
    else:
        _record(
            diagnostics,
            "P3-R02",
            "passed",
            "query variants are structurally usable; language and research meaning are not runtime decisions",
        )
    _record(
        diagnostics,
        "P3-R03",
        "passed" if len(accepted) == len(rows) else "recovered",
        "exact normalized duplicate queries are locally quarantined",
    )
    _record(
        diagnostics,
        "P3-R04",
        "not_applicable",
        "candidate use belongs to the Retrieval Strategy Skill; runtime performs no text matching",
    )
    _record(
        diagnostics,
        "P3-R05",
        "not_applicable",
        "comparison coverage belongs to the Retrieval Strategy Skill; runtime performs no name matching",
    )
    _record(
        diagnostics,
        "P3-R06",
        "not_applicable",
        "fallback quality belongs to the Retrieval Strategy Skill; runtime performs no keyword classification",
    )

    quality_report = output.get("quality_report")
    if isinstance(quality_report, dict):
        quality_report["search_matrix_count"] = len(output.get("search_matrix") or [])
        quality_report["expanded_query_variants_count"] = len(accepted)
        if diagnostics["fallback_used"]:
            quality_report["fallback_used"] = True
    diagnostics["result_protocol"]["accepted_items"] = [
        {"path": "expanded_query_variants", "count": len(accepted)},
    ]
    diagnostics["result_protocol"]["coverage"] = {
        "accepted_query_count": len(accepted),
        "input_query_count": len(rows),
        "quarantined_item_count": len(diagnostics["result_protocol"]["quarantined_items"]),
    }


_P07C_SUPPORTED_EVIDENCE_TYPES = {
    "material_composition",
    "experimental_condition",
    "quantitative_result",
    "mechanism_claim",
    "characterization_observation",
    "computation_result",
    "comparison",
    "limitation",
}
_P07C_TARGET_MATCH_STATUSES = {
    "matched",
    "partial",
    "conflict",
    "not_comparable",
}


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _p07c_anchor_index(
    input_payload: dict[str, Any],
    diagnostics: dict[str, Any],
) -> dict[str, dict[str, str]]:
    anchors: dict[str, dict[str, str]] = {}
    for packet_index, packet in enumerate(input_payload.get("paper_packets") or []):
        if not isinstance(packet, dict):
            _protocol_quarantine(
                diagnostics,
                path=f"paper_packets[{packet_index}]",
                reason="packet_not_object",
            )
            continue
        if packet.get("material_status") != "MATERIAL_READY":
            _protocol_quarantine(
                diagnostics,
                path=f"paper_packets[{packet_index}]",
                reason="material_not_ready",
            )
            _record(
                diagnostics,
                "P07C-R00-MATERIAL",
                "recovered",
                "non-ready paper packet was quarantined without blocking other evidence",
            )
            continue
        candidate_id = _clean_string(str(packet.get("candidate_id") or ""))
        arxiv_id = _clean_string(str(packet.get("arxiv_id") or ""))
        section_content: dict[tuple[str, str], str] = {}
        for section in packet.get("sections") or []:
            if not isinstance(section, dict):
                continue
            section_name = _clean_string(str(section.get("section_name") or ""))
            content_hash = _clean_string(str(section.get("content_hash") or ""))
            content = _clean_string(str(section.get("content") or ""))
            if section_name and content_hash and content:
                section_content[(section_name, content_hash)] = content
        for anchor_index, anchor in enumerate(packet.get("source_anchors") or []):
            if not isinstance(anchor, dict):
                continue
            anchor_id = _clean_string(str(anchor.get("anchor_id") or ""))
            section_name = _clean_string(str(anchor.get("section_name") or ""))
            content_hash = _clean_string(str(anchor.get("content_hash") or ""))
            content = section_content.get((section_name, content_hash), "")
            if not anchor_id or not section_name or not content_hash or not content:
                _protocol_quarantine(
                    diagnostics,
                    path=f"paper_packets[{packet_index}].source_anchors[{anchor_index}]",
                    reason="incomplete_anchor",
                )
                continue
            row = {
                "anchor_id": anchor_id,
                "candidate_id": candidate_id or _clean_string(str(anchor.get("candidate_id") or "")),
                "arxiv_id": arxiv_id or _clean_string(str(anchor.get("arxiv_id") or "")),
                "section_name": section_name,
                "content_hash": content_hash,
                "content": content,
            }
            previous = anchors.get(anchor_id)
            if previous is not None and previous != row:
                _protocol_quarantine(
                    diagnostics,
                    path=f"paper_packets[{packet_index}].source_anchors[{anchor_index}]",
                    reason="conflicting_duplicate_anchor_id",
                )
                _record(
                    diagnostics,
                    "P07C-R00",
                    "recovered",
                    "conflicting duplicate source anchor was quarantined; first stable anchor retained",
                    evidence={"anchor_id": anchor_id},
                )
                continue
            anchors[anchor_id] = row
    return anchors


def _p07c_canonical_evidence_id(item: dict[str, Any]) -> str:
    stable = {
        "source_anchor_id": item.get("source_anchor_id"),
        "evidence_type": item.get("evidence_type"),
        "claim": item.get("claim"),
        "evidence_quote": item.get("evidence_quote"),
    }
    return "ev:" + _sha256_text(_stable_json(stable))[:20]


def _p07c_evidence_schema_context(input_payload: dict[str, Any]) -> dict[str, Any] | None:
    if "evidence_schema" not in input_payload:
        return None
    schema = input_payload.get("evidence_schema")
    if not isinstance(schema, dict):
        return None
    schema_id = _clean_string(str(schema.get("schema_id") or ""))
    if not schema_id:
        return None
    whitelist: set[str] = set()
    required_target_ids: list[str] = []
    for target in schema.get("evidence_targets") or []:
        if not isinstance(target, dict):
            continue
        target_id = _clean_string(str(target.get("target_id") or ""))
        if not target_id:
            continue
        whitelist.add(target_id)
        if _clean_string(str(target.get("requiredness") or "")).lower() == "required":
            required_target_ids.append(target_id)
    return {
        "schema_id": schema_id,
        "target_whitelist": whitelist,
        "required_target_ids": required_target_ids,
        "target_id_count": len(whitelist),
    }


def _guard_p07c_evidence_node_extraction(
    input_payload: dict[str, Any],
    output: dict[str, Any],
    diagnostics: dict[str, Any],
) -> None:
    anchor_index = _p07c_anchor_index(input_payload, diagnostics)
    schema_context = _p07c_evidence_schema_context(input_payload)
    items = output.get("evidence_items")
    if not isinstance(items, list):
        _record(diagnostics, "P07C-R01", "failed", "evidence_items must be an array")
        return
    _record(diagnostics, "P07C-R01", "passed", "evidence_items array exists")

    invalid: list[dict[str, Any]] = []
    canonical_items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    dropped_duplicate_count = 0
    for index, raw_item in enumerate(items):
        if not isinstance(raw_item, dict):
            invalid.append({"index": index, "reason": "item_not_object"})
            continue
        item = copy.deepcopy(raw_item)
        anchor_id = _clean_string(str(item.get("source_anchor_id") or ""))
        anchor = anchor_index.get(anchor_id)
        if anchor is None:
            invalid.append({"index": index, "reason": "unknown_source_anchor_id", "source_anchor_id": anchor_id})
            continue
        evidence_type = _clean_string(str(item.get("evidence_type") or ""))
        if evidence_type not in _P07C_SUPPORTED_EVIDENCE_TYPES:
            invalid.append({"index": index, "reason": "unsupported_evidence_type", "evidence_type": evidence_type})
            continue
        quote = _clean_string(str(item.get("evidence_quote") or ""))
        claim = _clean_string(str(item.get("claim") or ""))
        if len(quote) < 12:
            invalid.append({"index": index, "reason": "evidence_quote_too_short", "source_anchor_id": anchor_id})
            continue
        if quote not in anchor["content"]:
            invalid.append({"index": index, "reason": "quote_not_found_in_source_section", "source_anchor_id": anchor_id, "quote": quote[:240]})
            continue
        if not claim:
            invalid.append({"index": index, "reason": "empty_claim", "source_anchor_id": anchor_id})
            continue
        confidence = item.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
            invalid.append({"index": index, "reason": "invalid_confidence", "source_anchor_id": anchor_id})
            continue
        metric = item.get("metric")
        if evidence_type == "quantitative_result" and not (
            isinstance(metric, dict)
            and _is_nonempty(metric.get("name"))
            and _is_nonempty(metric.get("value_text"))
        ):
            invalid.append({"index": index, "reason": "quantitative_result_requires_metric", "source_anchor_id": anchor_id})
            continue
        mechanism = item.get("mechanism")
        if evidence_type == "mechanism_claim" and not (
            isinstance(mechanism, dict)
            and _is_nonempty(mechanism.get("cause"))
            and _is_nonempty(mechanism.get("effect"))
        ):
            invalid.append({"index": index, "reason": "mechanism_claim_requires_cause_and_effect", "source_anchor_id": anchor_id})
            continue
        if schema_context is not None:
            matched_target_ids = item.get("matched_target_ids")
            if not isinstance(matched_target_ids, list):
                invalid.append({"index": index, "reason": "matched_target_ids_required_when_evidence_schema_present", "source_anchor_id": anchor_id})
                continue
            cleaned_target_ids: list[str] = []
            unknown_target_ids: list[str] = []
            for raw_target_id in matched_target_ids:
                target_id = _clean_string(str(raw_target_id))
                if not target_id:
                    continue
                if target_id not in schema_context["target_whitelist"]:
                    unknown_target_ids.append(target_id)
                elif target_id not in cleaned_target_ids:
                    cleaned_target_ids.append(target_id)
            if unknown_target_ids:
                invalid.append({
                    "index": index,
                    "reason": "matched_target_ids_reference_unknown_target",
                    "source_anchor_id": anchor_id,
                    "unknown_target_ids": unknown_target_ids,
                })
                continue
            target_match_status = _clean_string(str(item.get("target_match_status") or ""))
            if cleaned_target_ids and not target_match_status:
                invalid.append({
                    "index": index,
                    "reason": "target_match_status_required_when_matched_target_ids_non_empty",
                    "source_anchor_id": anchor_id,
                    "matched_target_ids": cleaned_target_ids,
                })
                continue
            if cleaned_target_ids and target_match_status not in _P07C_TARGET_MATCH_STATUSES:
                invalid.append({
                    "index": index,
                    "reason": "unsupported_target_match_status",
                    "source_anchor_id": anchor_id,
                    "target_match_status": target_match_status,
                })
                continue
            item["matched_target_ids"] = cleaned_target_ids
        item["source_anchor_id"] = anchor_id
        item["evidence_type"] = evidence_type
        item["evidence_quote"] = quote
        item["claim"] = claim
        item["candidate_id"] = anchor["candidate_id"]
        item["arxiv_id"] = anchor["arxiv_id"]
        item["section_name"] = anchor["section_name"]
        item["content_hash"] = anchor["content_hash"]
        item["evidence_id"] = _p07c_canonical_evidence_id(item)
        if item["evidence_id"] in seen_ids:
            dropped_duplicate_count += 1
            continue
        seen_ids.add(item["evidence_id"])
        canonical_items.append(item)

    if invalid:
        _record(
            diagnostics,
            "P07C-R02",
            "failed",
            "every evidence item must be anchored to a verbatim quote from one DeepXiv source section",
            evidence={"invalid_items": invalid},
        )
    else:
        _record(diagnostics, "P07C-R02", "passed", "all evidence items are source-anchor quote grounded")

    if "evidence_schema" in input_payload and schema_context is None:
        _record(
            diagnostics,
            "P07C-R04",
            "failed",
            "input evidence_schema requires a non-empty schema_id",
            evidence={"evidence_schema": input_payload.get("evidence_schema")},
        )
    elif schema_context is not None:
        output_schema_id = _clean_string(str(output.get("schema_id") or ""))
        if output_schema_id != schema_context["schema_id"]:
            _record(
                diagnostics,
                "P07C-R04",
                "failed",
                "output schema_id must exactly match input evidence_schema.schema_id",
                evidence={"expected": schema_context["schema_id"], "actual": output_schema_id},
            )
        else:
            _record(diagnostics, "P07C-R04", "passed", "output schema_id matches input evidence_schema.schema_id")

        matched_target_union = sorted({
            target_id
            for item in canonical_items
            for target_id in (item.get("matched_target_ids") or [])
            if _clean_string(str(target_id))
        })
        missing_required_target_ids = [
            target_id
            for target_id in schema_context["required_target_ids"]
            if target_id not in matched_target_union
        ]
        diagnostics["schema_bridge_enabled"] = True
        diagnostics["schema_id"] = schema_context["schema_id"]
        diagnostics["target_id_count"] = schema_context["target_id_count"]
        diagnostics["matched_target_ids"] = matched_target_union
        diagnostics["missing_required_target_ids"] = missing_required_target_ids
        if missing_required_target_ids:
            _record(
                diagnostics,
                "P07C-R06",
                "warning",
                "required evidence targets were not matched by any grounded evidence item",
                evidence={"missing_required_target_ids": missing_required_target_ids},
                failure_level="warning",
            )
            diagnostics["warnings"].append("P07C-W01")
        else:
            _record(diagnostics, "P07C-R06", "passed", "all required evidence targets were matched or none were required")
        schema_binding_failures = [
            row for row in invalid
            if str(row.get("reason") or "").startswith((
                "matched_target_ids",
                "target_match_status",
            )) or str(row.get("reason") or "") == "unsupported_target_match_status"
        ]
        if schema_binding_failures:
            _record(
                diagnostics,
                "P07C-R05",
                "failed",
                "evidence items must carry schema-bound matched_target_ids from the input target whitelist",
                evidence={"schema_binding_failures": schema_binding_failures},
            )
        else:
            _record(diagnostics, "P07C-R05", "passed", "evidence schema target binding constraints were satisfied")

    type_counts: dict[str, int] = {}
    for item in canonical_items:
        key = str(item.get("evidence_type") or "")
        type_counts[key] = type_counts.get(key, 0) + 1
    warnings = [str(item) for item in (output.get("extraction_warnings") or []) if str(item).strip()]
    if not canonical_items and anchor_index:
        warnings.append("No grounded evidence item was extracted from the supplied source sections.")
    if dropped_duplicate_count:
        warnings.append(f"Dropped {dropped_duplicate_count} exact duplicate grounded evidence item(s).")
    output["evidence_items"] = canonical_items
    output["extraction_warnings"] = warnings
    output["summary"] = {
        "input_paper_packet_count": len(input_payload.get("paper_packets") or []),
        "input_source_anchor_count": len(anchor_index),
        "evidence_item_count": len(canonical_items),
        "evidence_type_counts": type_counts,
        "dropped_duplicate_count": dropped_duplicate_count,
        "degraded": bool(warnings),
    }
    output["audit"] = {
        "skill_id": "evidence_node_extraction",
        "traceability_mode": "source_anchor_quote_guard",
        "guard_version": GUARD_VERSION,
        "input_fingerprint": _sha256_text(_stable_json(input_payload.get("paper_packets") or [])),
        "supported_evidence_types": sorted(_P07C_SUPPORTED_EVIDENCE_TYPES),
    }
    _record(diagnostics, "P07C-R03", "passed", "canonical provenance and deterministic evidence ids were injected")


_P07D_GUARD_VERSION = "p07d-v1"
_P07D_EDGE_STATUSES = {"support", "conflict", "gap"}
_P07D_NODE_TYPES = {
    "composition",
    "condition",
    "structure",
    "interphase",
    "property",
    "performance",
    "observation",
    "calculation",
    "limitation",
    "unknown",
}
_P07D_PREDICATES = {
    "promotes",
    "suppresses",
    "increases",
    "decreases",
    "enables",
    "limits",
    "associated_with",
    "contradicts",
    "reveals_gap",
}


def _p07d_research_record_index(
    input_payload: dict[str, Any],
    diagnostics: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    rows = input_payload.get("research_records") or []
    records: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows if isinstance(rows, list) else []):
        if not isinstance(row, dict):
            _protocol_quarantine(diagnostics, path=f"research_records[{index}]", reason="record_not_object")
            continue
        record_id = _clean_string(str(row.get("record_id") or ""))
        if not record_id:
            _protocol_quarantine(diagnostics, path=f"research_records[{index}]", reason="record_id_missing")
            continue
        canonical = copy.deepcopy(row)
        previous = records.get(record_id)
        if previous is not None and previous != canonical:
            _protocol_quarantine(
                diagnostics,
                path=f"research_records[{index}]",
                reason="conflicting_duplicate_record_id",
            )
            _record(
                diagnostics,
                "P07D-R00",
                "recovered",
                "conflicting duplicate ResearchRecord was quarantined; first stable record retained",
                evidence={"record_id": record_id},
            )
            continue
        records[record_id] = canonical
    return records


def _p07d_node(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    node_type = _clean_string(str(raw.get("node_type") or ""))
    label = _clean_string(str(raw.get("label") or ""))
    if node_type not in _P07D_NODE_TYPES or not label:
        return None
    result: dict[str, str] = {"node_type": node_type, "label": label}
    node_id = _clean_string(str(raw.get("node_id") or ""))
    if node_id:
        result["node_id"] = node_id
    return result


def _p07d_relation(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    predicate = _clean_string(str(raw.get("predicate") or ""))
    description = _clean_string(str(raw.get("description") or ""))
    if predicate not in _P07D_PREDICATES or not description:
        return None
    return {"predicate": predicate, "description": description}


def _p07d_canonical_edge_id(item: dict[str, Any]) -> str:
    def _node_stable_key(n: Any) -> dict[str, str] | None:
        """Extract stable (node_type, label) for hashing, excluding optional node_id.

        node_id is excluded so that the same logical edge produces a stable
        edge_id regardless of whether producers include node_id in payloads.
        """
        if not isinstance(n, dict):
            return None
        return {"node_type": n.get("node_type"), "label": n.get("label")}

    stable = {
        "edge_status": item.get("edge_status"),
        "source_node": _node_stable_key(item.get("source_node")),
        "relation": item.get("relation"),
        "target_node": _node_stable_key(item.get("target_node")),
        "evidence_ids": sorted(item.get("evidence_ids") or []),
        "rationale": item.get("rationale"),
    }
    return "me:" + _sha256_text(_stable_json(stable))[:20]


def _p07d_canonical_chain_id(item: dict[str, Any]) -> str:
    stable = {
        "title": item.get("title"),
        "edge_ids": item.get("edge_ids") or [],
        "chain_summary": item.get("chain_summary"),
    }
    return "mc:" + _sha256_text(_stable_json(stable))[:20]


def _guard_p07d_mechanism_chain_synthesis(
    input_payload: dict[str, Any],
    output: dict[str, Any],
    diagnostics: dict[str, Any],
) -> None:
    record_index = _p07d_research_record_index(input_payload, diagnostics)
    prohibited = sorted({"hypotheses", "research_leads", "recommendations", "proposed_experiments"}.intersection(output))
    if prohibited:
        _record(diagnostics, "P07D-R01", "failed", "mechanism synthesis must not leak hypothesis or recommendation structures", evidence={"keys": prohibited})
    else:
        _record(diagnostics, "P07D-R01", "passed", "mechanism synthesis does not leak downstream hypothesis structures")

    raw_edges = output.get("mechanism_edges")
    if not isinstance(raw_edges, list):
        _record(diagnostics, "P07D-R02", "failed", "mechanism_edges must be an array")
        return
    _record(diagnostics, "P07D-R02", "passed", "mechanism_edges array exists")

    invalid: list[dict[str, Any]] = []
    canonical_edges: list[dict[str, Any]] = []
    edge_by_local_key: dict[str, str] = {}
    seen_edge_ids: set[str] = set()
    dropped_duplicate_edge_count = 0
    for index, raw_edge in enumerate(raw_edges):
        if not isinstance(raw_edge, dict):
            invalid.append({"index": index, "reason": "edge_not_object"})
            continue
        edge = copy.deepcopy(raw_edge)
        local_edge_key = _clean_string(str(edge.get("local_edge_key") or ""))
        edge_status = _clean_string(str(edge.get("edge_status") or ""))
        source_node = _p07d_node(edge.get("source_node"))
        relation = _p07d_relation(edge.get("relation"))
        target_node = _p07d_node(edge.get("target_node"))
        evidence_ids = sorted({_clean_string(str(item)) for item in (edge.get("evidence_ids") or []) if _clean_string(str(item))})
        rationale = _clean_string(str(edge.get("rationale") or ""))
        confidence = edge.get("confidence")
        if not local_edge_key:
            invalid.append({"index": index, "reason": "missing_local_edge_key"}); continue
        if local_edge_key in edge_by_local_key:
            invalid.append({"index": index, "reason": "duplicate_local_edge_key", "local_edge_key": local_edge_key}); continue
        if edge_status not in _P07D_EDGE_STATUSES:
            invalid.append({"index": index, "reason": "unsupported_edge_status", "edge_status": edge_status}); continue
        if source_node is None or target_node is None:
            invalid.append({"index": index, "reason": "invalid_source_or_target_node"}); continue
        # P08B-1E-c: self-loop edge rejection
        # Semantic self-loop: source and target refer to the same conceptual node.
        # - When both sides carry node_id and they match → structural self-loop.
        # - When (node_type, label) matches AND at least one side lacks node_id
        #   → semantic self-loop (cannot distinguish the two nodes without IDs).
        # - When both sides have distinct node_ids → NOT a self-loop even if
        #   (node_type, label) coincides (they are structurally different nodes).
        source_id = source_node.get("node_id")
        target_id = target_node.get("node_id")
        source_key = (_clean_string(str(source_node.get("node_type") or "")).lower(), _clean_string(str(source_node.get("label") or "")).lower())
        target_key = (_clean_string(str(target_node.get("node_type") or "")).lower(), _clean_string(str(target_node.get("label") or "")).lower())
        id_match = bool(source_id and target_id and source_id == target_id)
        semantic_match = (source_key == target_key) and not (source_id and target_id and source_id != target_id)
        is_self_loop = id_match or semantic_match
        if is_self_loop:
            invalid.append({"index": index, "reason": "self_loop_edge_rejected"}); continue
        if relation is None:
            invalid.append({"index": index, "reason": "invalid_relation"}); continue
        if not evidence_ids:
            invalid.append({"index": index, "reason": "edge_requires_research_record_ids"}); continue
        unknown = [item for item in evidence_ids if item not in record_index]
        if unknown:
            invalid.append({"index": index, "reason": "unknown_research_record_id", "evidence_ids": unknown}); continue
        if edge_status == "conflict" and (relation["predicate"] != "contradicts" or len(evidence_ids) < 2):
            invalid.append({"index": index, "reason": "conflict_requires_contradicts_and_two_research_record_ids"}); continue
        if edge_status == "gap" and relation["predicate"] != "reveals_gap":
            invalid.append({"index": index, "reason": "gap_requires_reveals_gap_predicate"}); continue
        if edge_status == "support" and relation["predicate"] in {"contradicts", "reveals_gap"}:
            invalid.append({"index": index, "reason": "support_edge_uses_non_support_predicate"}); continue
        if not rationale:
            invalid.append({"index": index, "reason": "empty_rationale"}); continue
        if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
            invalid.append({"index": index, "reason": "invalid_confidence"}); continue
        edge["local_edge_key"] = local_edge_key
        edge["edge_status"] = edge_status
        edge["source_node"] = source_node
        edge["relation"] = relation
        edge["target_node"] = target_node
        edge["evidence_ids"] = evidence_ids
        edge["rationale"] = rationale
        edge["edge_id"] = _p07d_canonical_edge_id(edge)
        edge_by_local_key[local_edge_key] = edge["edge_id"]
        if edge["edge_id"] in seen_edge_ids:
            dropped_duplicate_edge_count += 1
            continue
        seen_edge_ids.add(edge["edge_id"])
        canonical_edges.append(edge)

    if invalid:
        _record(diagnostics, "P07D-R03", "failed", "every mechanism edge must be grounded in known ResearchRecord ids and satisfy status-specific gates", evidence={"invalid_edges": invalid})
    else:
        _record(diagnostics, "P07D-R03", "passed", "all mechanism edges are ResearchRecord-id grounded")

    raw_chains = output.get("mechanism_chains")
    if not isinstance(raw_chains, list):
        _record(diagnostics, "P07D-R04", "failed", "mechanism_chains must be an array")
        return
    canonical_chains: list[dict[str, Any]] = []
    invalid_chains: list[dict[str, Any]] = []
    seen_chain_ids: set[str] = set()
    dropped_duplicate_chain_count = 0
    for index, raw_chain in enumerate(raw_chains):
        if not isinstance(raw_chain, dict):
            invalid_chains.append({"index": index, "reason": "chain_not_object"}); continue
        chain = copy.deepcopy(raw_chain)
        local_chain_key = _clean_string(str(chain.get("local_chain_key") or ""))
        title = _clean_string(str(chain.get("title") or ""))
        chain_summary = _clean_string(str(chain.get("chain_summary") or ""))
        local_edge_keys = [_clean_string(str(item)) for item in (chain.get("local_edge_keys") or []) if _clean_string(str(item))]
        confidence = chain.get("confidence")
        if not local_chain_key or not title or not chain_summary:
            invalid_chains.append({"index": index, "reason": "missing_chain_identity_or_summary"}); continue
        if not local_edge_keys:
            invalid_chains.append({"index": index, "reason": "chain_requires_local_edge_keys"}); continue
        unknown_keys = [item for item in local_edge_keys if item not in edge_by_local_key]
        if unknown_keys:
            invalid_chains.append({"index": index, "reason": "unknown_local_edge_key", "local_edge_keys": unknown_keys}); continue
        if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
            invalid_chains.append({"index": index, "reason": "invalid_confidence"}); continue
        edge_ids: list[str] = []
        for item in local_edge_keys:
            edge_id = edge_by_local_key[item]
            if edge_id not in edge_ids:
                edge_ids.append(edge_id)
        chain["local_chain_key"] = local_chain_key
        chain["title"] = title
        chain["chain_summary"] = chain_summary
        chain["local_edge_keys"] = local_edge_keys
        chain["edge_ids"] = edge_ids
        chain["chain_id"] = _p07d_canonical_chain_id(chain)
        if chain["chain_id"] in seen_chain_ids:
            dropped_duplicate_chain_count += 1
            continue
        seen_chain_ids.add(chain["chain_id"])
        canonical_chains.append(chain)

    if invalid_chains:
        _record(diagnostics, "P07D-R05", "failed", "every mechanism chain must reference validated local edge keys", evidence={"invalid_chains": invalid_chains})
    else:
        _record(diagnostics, "P07D-R05", "passed", "all mechanism chains reference validated edges")

    warnings = [str(item) for item in (output.get("synthesis_warnings") or []) if str(item).strip()]
    if record_index and not canonical_edges:
        warnings.append("No grounded mechanism edge was synthesized from the supplied ResearchRecords.")
    if dropped_duplicate_edge_count:
        warnings.append(f"Dropped {dropped_duplicate_edge_count} exact duplicate mechanism edge(s).")
    if dropped_duplicate_chain_count:
        warnings.append(f"Dropped {dropped_duplicate_chain_count} exact duplicate mechanism chain(s).")
    status_counts = {status: 0 for status in sorted(_P07D_EDGE_STATUSES)}
    for edge in canonical_edges:
        status_counts[str(edge["edge_status"])] += 1
    output["mechanism_edges"] = canonical_edges
    output["mechanism_chains"] = canonical_chains
    output["conflicts"] = [{"edge_id": edge["edge_id"], "evidence_ids": edge["evidence_ids"], "rationale": edge["rationale"]} for edge in canonical_edges if edge["edge_status"] == "conflict"]
    output["gaps"] = [{"edge_id": edge["edge_id"], "evidence_ids": edge["evidence_ids"], "rationale": edge["rationale"]} for edge in canonical_edges if edge["edge_status"] == "gap"]
    output["synthesis_warnings"] = warnings
    output["summary"] = {
        "input_evidence_item_count": len(record_index),
        "mechanism_edge_count": len(canonical_edges),
        "mechanism_chain_count": len(canonical_chains),
        "edge_status_counts": status_counts,
        "dropped_duplicate_edge_count": dropped_duplicate_edge_count,
        "dropped_duplicate_chain_count": dropped_duplicate_chain_count,
        "degraded": bool(warnings),
    }
    output["audit"] = {
        "skill_id": "mechanism_chain_synthesis",
        "traceability_mode": "evidence_id_guard",
        "guard_version": _P07D_GUARD_VERSION,
        "input_fingerprint": _sha256_text(_stable_json(input_payload.get("research_records") or [])),
        "allowed_edge_statuses": sorted(_P07D_EDGE_STATUSES),
        "allowed_predicates": sorted(_P07D_PREDICATES),
    }
    _record(diagnostics, "P07D-R06", "passed", "canonical mechanism provenance and deterministic ids were injected")




_FINAL_SYNTHESIS_GUARD_VERSION = "lane-a-p0-final-v1"
_FINAL_SYNTHESIS_MAX_ITEMS = 8


def _final_synthesis_record_index(
    input_payload: dict[str, Any],
    diagnostics: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    rows = input_payload.get("research_records")
    if not isinstance(rows, list) or not rows:
        _record(
            diagnostics,
            "P0F-R01",
            "failed",
            "final synthesis requires at least one ResearchRecord",
        )
        return {}
    records: dict[str, dict[str, Any]] = {}
    invalid: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            invalid.append({"index": index, "reason": "record_not_object"})
            continue
        record_id = _clean_string(str(row.get("record_id") or ""))
        if not record_id:
            invalid.append({"index": index, "reason": "missing_record_id"})
            continue
        if record_id in records:
            invalid.append({"index": index, "reason": "duplicate_record_id", "record_id": record_id})
            continue
        records[record_id] = copy.deepcopy(row)
    if invalid:
        _record(
            diagnostics,
            "P0F-R01",
            "failed",
            "ResearchRecord identities must be present and unique",
            evidence={"invalid_records": invalid},
        )
    else:
        _record(
            diagnostics,
            "P0F-R01",
            "passed",
            "final synthesis has direct ResearchRecord evidence",
            evidence={"record_count": len(records)},
        )
    return records


def _final_synthesis_mechanism_index(
    input_payload: dict[str, Any],
    record_index: dict[str, dict[str, Any]],
    diagnostics: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    packet = input_payload.get("mechanism_packet")
    if packet is None:
        _record(
            diagnostics,
            "P0F-R02",
            "passed",
            "mechanism packet is absent and treated as optional enhancement",
        )
        return {}
    if not isinstance(packet, dict) or not isinstance(packet.get("mechanism_edges"), list):
        _record(
            diagnostics,
            "P0F-R02",
            "failed",
            "optional mechanism packet is malformed",
        )
        return {}
    edges: dict[str, dict[str, Any]] = {}
    invalid: list[dict[str, Any]] = []
    for index, row in enumerate(packet.get("mechanism_edges") or []):
        if not isinstance(row, dict):
            invalid.append({"index": index, "reason": "edge_not_object"})
            continue
        edge_id = _clean_string(str(row.get("edge_id") or ""))
        evidence_ids = [
            _clean_string(str(value))
            for value in (row.get("evidence_ids") or [])
            if _clean_string(str(value))
        ]
        if not edge_id:
            invalid.append({"index": index, "reason": "missing_edge_id"})
            continue
        if edge_id in edges:
            invalid.append({"index": index, "reason": "duplicate_edge_id", "edge_id": edge_id})
            continue
        unknown = [value for value in evidence_ids if value not in record_index]
        if unknown:
            invalid.append({
                "index": index,
                "reason": "mechanism_edge_unknown_record_id",
                "edge_id": edge_id,
                "record_ids": unknown,
            })
            continue
        edges[edge_id] = copy.deepcopy(row)
    if invalid:
        _record(
            diagnostics,
            "P0F-R02",
            "failed",
            "mechanism enhancement may reference only supplied ResearchRecords",
            evidence={"invalid_edges": invalid},
        )
    else:
        _record(
            diagnostics,
            "P0F-R02",
            "passed",
            "optional mechanism packet is ResearchRecord-grounded",
            evidence={"edge_count": len(edges)},
        )
    return edges


def _final_synthesis_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for raw in value:
        item = _clean_string(str(raw))
        if item and item not in result:
            result.append(item)
    return result


def _final_synthesis_validate_refs(
    *,
    rows: Any,
    row_label: str,
    record_fields: tuple[str, ...],
    mechanism_fields: tuple[str, ...],
    record_index: dict[str, dict[str, Any]],
    mechanism_index: dict[str, dict[str, Any]],
    invalid: list[dict[str, Any]],
) -> None:
    if not isinstance(rows, list):
        invalid.append({"row": row_label, "reason": "rows_not_array"})
        return
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            invalid.append({"row": row_label, "index": index, "reason": "row_not_object"})
            continue
        for field in record_fields:
            values = _final_synthesis_ids(row.get(field))
            unknown = [value for value in values if value not in record_index]
            if unknown:
                invalid.append({
                    "row": row_label,
                    "index": index,
                    "field": field,
                    "reason": "unknown_record_id",
                    "values": unknown,
                })
        for field in mechanism_fields:
            values = _final_synthesis_ids(row.get(field))
            unknown = [value for value in values if value not in mechanism_index]
            if unknown:
                invalid.append({
                    "row": row_label,
                    "index": index,
                    "field": field,
                    "reason": "unknown_mechanism_edge_id",
                    "values": unknown,
                })


def _final_synthesis_stable_id(prefix: str, payload: dict[str, Any]) -> str:
    return f"{prefix}:" + _sha256_text(_stable_json(payload))[:20]


def _guard_p0_final_synthesis(
    input_payload: dict[str, Any],
    output: dict[str, Any],
    diagnostics: dict[str, Any],
) -> None:
    record_index = _final_synthesis_record_index(input_payload, diagnostics)
    mechanism_index = _final_synthesis_mechanism_index(
        input_payload, record_index, diagnostics
    )
    if diagnostics["hard_gate_failures"]:
        return

    required_arrays = (
        "evidence_supported_conclusions",
        "conflicts",
        "uncertainties",
        "evidence_gaps",
        "research_leads",
        "hypotheses",
        "generation_warnings",
    )
    missing = [
        key
        for key in required_arrays
        if not isinstance(output.get(key), list)
    ]
    if not isinstance(output.get("direct_answer"), dict):
        missing.append("direct_answer")
    if missing:
        _record(
            diagnostics,
            "P0F-R03",
            "failed",
            "final synthesis core output structures are missing",
            evidence={"fields": missing},
        )
        return

    invalid_refs: list[dict[str, Any]] = []
    direct_answer = output.get("direct_answer") or {}
    answer_record_ids = _final_synthesis_ids(direct_answer.get("supporting_record_ids"))
    unknown_answer_records = [
        value for value in answer_record_ids if value not in record_index
    ]
    if unknown_answer_records:
        invalid_refs.append({
            "row": "direct_answer",
            "field": "supporting_record_ids",
            "reason": "unknown_record_id",
            "values": unknown_answer_records,
        })

    _final_synthesis_validate_refs(
        rows=output.get("evidence_supported_conclusions"),
        row_label="evidence_supported_conclusions",
        record_fields=("supporting_record_ids",),
        mechanism_fields=("supporting_mechanism_edge_ids",),
        record_index=record_index,
        mechanism_index=mechanism_index,
        invalid=invalid_refs,
    )
    _final_synthesis_validate_refs(
        rows=output.get("conflicts"),
        row_label="conflicts",
        record_fields=("record_ids",),
        mechanism_fields=("mechanism_context_edge_ids",),
        record_index=record_index,
        mechanism_index=mechanism_index,
        invalid=invalid_refs,
    )
    _final_synthesis_validate_refs(
        rows=output.get("evidence_gaps"),
        row_label="evidence_gaps",
        record_fields=("related_record_ids",),
        mechanism_fields=("mechanism_context_edge_ids",),
        record_index=record_index,
        mechanism_index=mechanism_index,
        invalid=invalid_refs,
    )
    _final_synthesis_validate_refs(
        rows=output.get("research_leads"),
        row_label="research_leads",
        record_fields=("supporting_record_ids",),
        mechanism_fields=("mechanism_context_edge_ids",),
        record_index=record_index,
        mechanism_index=mechanism_index,
        invalid=invalid_refs,
    )
    _final_synthesis_validate_refs(
        rows=output.get("hypotheses"),
        row_label="hypotheses",
        record_fields=("supporting_record_ids",),
        mechanism_fields=("mechanism_context_edge_ids",),
        record_index=record_index,
        mechanism_index=mechanism_index,
        invalid=invalid_refs,
    )

    for index, row in enumerate(output.get("evidence_supported_conclusions") or []):
        if not isinstance(row, dict):
            continue
        supplied_claim_type = _clean_string(str(row.get("claim_type") or ""))
        if supplied_claim_type and supplied_claim_type != "evidence_supported_conclusion":
            invalid_refs.append({
                "row": "evidence_supported_conclusions",
                "index": index,
                "reason": "claim_layer_mismatch",
            })
        supporting_records = _final_synthesis_ids(row.get("supporting_record_ids"))
        if not supporting_records:
            invalid_refs.append({
                "row": "evidence_supported_conclusions",
                "index": index,
                "reason": "conclusion_requires_record_id",
            })
        if row.get("epistemic_status") == "conflicting" and len(supporting_records) < 2:
            invalid_refs.append({
                "row": "evidence_supported_conclusions",
                "index": index,
                "reason": "conflicting_conclusion_requires_two_records",
            })
        for edge_id in _final_synthesis_ids(row.get("supporting_mechanism_edge_ids")):
            edge = mechanism_index.get(edge_id) or {}
            if edge.get("edge_status") == "gap":
                invalid_refs.append({
                    "row": "evidence_supported_conclusions",
                    "index": index,
                    "reason": "gap_edge_cannot_support_conclusion",
                    "edge_id": edge_id,
                })
            edge_records = set(_final_synthesis_ids(edge.get("evidence_ids")))
            if edge_records and not edge_records.intersection(supporting_records):
                invalid_refs.append({
                    "row": "evidence_supported_conclusions",
                    "index": index,
                    "reason": "mechanism_edge_not_grounded_by_cited_records",
                    "edge_id": edge_id,
                })

    for index, row in enumerate(output.get("conflicts") or []):
        if isinstance(row, dict) and len(_final_synthesis_ids(row.get("record_ids"))) < 2:
            invalid_refs.append({
                "row": "conflicts",
                "index": index,
                "reason": "conflict_requires_two_records",
            })

    for index, row in enumerate(output.get("research_leads") or []):
        if not isinstance(row, dict):
            continue
        supplied_claim_type = _clean_string(str(row.get("claim_type") or ""))
        if supplied_claim_type and supplied_claim_type != "research_lead":
            invalid_refs.append({
                "row": "research_leads",
                "index": index,
                "reason": "claim_layer_mismatch",
            })
        if not _final_synthesis_ids(row.get("supporting_record_ids")):
            invalid_refs.append({
                "row": "research_leads",
                "index": index,
                "reason": "research_lead_requires_record_id",
            })

    for index, row in enumerate(output.get("hypotheses") or []):
        if not isinstance(row, dict):
            continue
        supplied_claim_type = _clean_string(str(row.get("claim_type") or ""))
        if supplied_claim_type and supplied_claim_type != "unvalidated_hypothesis":
            invalid_refs.append({
                "row": "hypotheses",
                "index": index,
                "reason": "claim_layer_mismatch",
            })
        if not _final_synthesis_ids(row.get("supporting_record_ids")):
            invalid_refs.append({
                "row": "hypotheses",
                "index": index,
                "reason": "hypothesis_requires_record_basis",
            })
        if not _final_synthesis_ids(row.get("validation_conditions")):
            invalid_refs.append({
                "row": "hypotheses",
                "index": index,
                "reason": "hypothesis_requires_validation_conditions",
            })
        if not _final_synthesis_ids(row.get("falsification_conditions")):
            invalid_refs.append({
                "row": "hypotheses",
                "index": index,
                "reason": "hypothesis_requires_falsification_conditions",
            })

    if invalid_refs:
        _record(
            diagnostics,
            "P0F-R04",
            "failed",
            "final synthesis may cite only known ResearchRecords and optional known mechanism edges",
            evidence={"invalid_references": invalid_refs},
        )
        return
    _record(
        diagnostics,
        "P0F-R04",
        "passed",
        "all explicit final-synthesis provenance references are grounded",
    )

    config = input_payload.get("generation_config")
    config = config if isinstance(config, dict) else {}
    max_conclusions = int(config.get("max_conclusions", _FINAL_SYNTHESIS_MAX_ITEMS))
    max_leads = int(config.get("max_research_leads", _FINAL_SYNTHESIS_MAX_ITEMS))
    max_hypotheses = int(config.get("max_hypotheses", _FINAL_SYNTHESIS_MAX_ITEMS))

    conclusions: list[dict[str, Any]] = []
    conclusion_ids: set[str] = set()
    dropped_duplicate_conclusions = 0
    dropped_over_limit_conclusions = 0
    for raw in output.get("evidence_supported_conclusions") or []:
        if not isinstance(raw, dict):
            continue
        row = copy.deepcopy(raw)
        row["supporting_record_ids"] = _final_synthesis_ids(row.get("supporting_record_ids"))
        row["supporting_mechanism_edge_ids"] = _final_synthesis_ids(
            row.get("supporting_mechanism_edge_ids")
        )
        row["claim_type"] = "evidence_supported_conclusion"
        row["conclusion_id"] = _final_synthesis_stable_id(
            "fc",
            {
                "statement": row.get("statement"),
                "records": row["supporting_record_ids"],
                "scope_boundary": row.get("scope_boundary"),
            },
        )
        if row["conclusion_id"] in conclusion_ids:
            dropped_duplicate_conclusions += 1
            continue
        conclusion_ids.add(row["conclusion_id"])
        if len(conclusions) >= max_conclusions:
            dropped_over_limit_conclusions += 1
            continue
        row["local_conclusion_key"] = f"conclusion-{len(conclusions) + 1}"
        conclusions.append(row)

    leads: list[dict[str, Any]] = []
    lead_ids: set[str] = set()
    lead_by_raw_key: dict[str, dict[str, Any] | None] = {}
    dropped_duplicate_leads = 0
    dropped_over_limit_leads = 0
    seen_raw_lead_keys: set[str] = set()
    for raw in output.get("research_leads") or []:
        if not isinstance(raw, dict):
            continue
        raw_key = _clean_string(str(raw.get("local_lead_key") or ""))
        if not raw_key or raw_key in seen_raw_lead_keys:
            _record(
                diagnostics,
                "P0F-R05",
                "failed",
                "research lead local keys must be present and unique",
                evidence={"local_lead_key": raw_key},
            )
            return
        seen_raw_lead_keys.add(raw_key)
        row = copy.deepcopy(raw)
        row["supporting_record_ids"] = _final_synthesis_ids(row.get("supporting_record_ids"))
        row["mechanism_context_edge_ids"] = _final_synthesis_ids(
            row.get("mechanism_context_edge_ids")
        )
        row["claim_type"] = "research_lead"
        row["novelty_boundary"] = "research_direction_not_hypothesis_or_novelty_claim"
        row["research_lead_id"] = _final_synthesis_stable_id(
            "rl",
            {
                "research_question": row.get("research_question"),
                "investigation_target": row.get("investigation_target"),
                "records": row["supporting_record_ids"],
            },
        )
        if row["research_lead_id"] in lead_ids:
            dropped_duplicate_leads += 1
            lead_by_raw_key[raw_key] = next(
                item for item in leads
                if item["research_lead_id"] == row["research_lead_id"]
            )
            continue
        lead_ids.add(row["research_lead_id"])
        if len(leads) >= max_leads:
            dropped_over_limit_leads += 1
            lead_by_raw_key[raw_key] = None
            continue
        row["local_lead_key"] = f"lead-{len(leads) + 1}"
        leads.append(row)
        lead_by_raw_key[raw_key] = row

    hypotheses: list[dict[str, Any]] = []
    hypothesis_ids: set[str] = set()
    dropped_duplicate_hypotheses = 0
    dropped_over_limit_hypotheses = 0
    seen_raw_hypothesis_keys: set[str] = set()
    for raw in output.get("hypotheses") or []:
        if not isinstance(raw, dict):
            continue
        raw_hypothesis_key = _clean_string(str(raw.get("local_hypothesis_key") or ""))
        source_raw_key = _clean_string(str(raw.get("source_local_lead_key") or ""))
        if not raw_hypothesis_key or raw_hypothesis_key in seen_raw_hypothesis_keys:
            _record(
                diagnostics,
                "P0F-R06",
                "failed",
                "hypothesis local keys must be present and unique",
                evidence={"local_hypothesis_key": raw_hypothesis_key},
            )
            return
        seen_raw_hypothesis_keys.add(raw_hypothesis_key)
        if source_raw_key not in lead_by_raw_key:
            _record(
                diagnostics,
                "P0F-R06",
                "failed",
                "every hypothesis must reference one generated research lead",
                evidence={"source_local_lead_key": source_raw_key},
            )
            return
        retained_lead = lead_by_raw_key[source_raw_key]
        if retained_lead is None:
            dropped_over_limit_hypotheses += 1
            continue
        row = copy.deepcopy(raw)
        row["supporting_record_ids"] = _final_synthesis_ids(row.get("supporting_record_ids"))
        row["mechanism_context_edge_ids"] = _final_synthesis_ids(
            row.get("mechanism_context_edge_ids")
        )
        row["source_local_lead_key"] = retained_lead["local_lead_key"]
        row["research_lead_id"] = retained_lead["research_lead_id"]
        row["claim_type"] = "unvalidated_hypothesis"
        row["novelty_boundary"] = "derived_from_evidence_not_established_by_sources"
        row["hypothesis_id"] = _final_synthesis_stable_id(
            "hy",
            {
                "statement": row.get("statement"),
                "independent_variable": row.get("independent_variable"),
                "dependent_variable": row.get("dependent_variable"),
                "expected_direction": row.get("expected_direction"),
                "records": row["supporting_record_ids"],
            },
        )
        if row["hypothesis_id"] in hypothesis_ids:
            dropped_duplicate_hypotheses += 1
            continue
        hypothesis_ids.add(row["hypothesis_id"])
        if len(hypotheses) >= max_hypotheses:
            dropped_over_limit_hypotheses += 1
            continue
        row["local_hypothesis_key"] = f"hypothesis-{len(hypotheses) + 1}"
        hypotheses.append(row)

    output["direct_answer"]["supporting_record_ids"] = answer_record_ids
    output["evidence_supported_conclusions"] = conclusions
    output["research_leads"] = leads
    output["hypotheses"] = hypotheses

    for row in output.get("conflicts") or []:
        if isinstance(row, dict):
            row["record_ids"] = _final_synthesis_ids(row.get("record_ids"))
            row["mechanism_context_edge_ids"] = _final_synthesis_ids(
                row.get("mechanism_context_edge_ids")
            )
    for row in output.get("evidence_gaps") or []:
        if isinstance(row, dict):
            row["related_record_ids"] = _final_synthesis_ids(row.get("related_record_ids"))
            row["mechanism_context_edge_ids"] = _final_synthesis_ids(
                row.get("mechanism_context_edge_ids")
            )

    warnings = [
        _clean_string(str(value))
        for value in (output.get("generation_warnings") or [])
        if _clean_string(str(value))
    ]
    if input_payload.get("mechanism_packet") is None:
        warnings.append("mechanism_packet_unavailable_optional")
    degraded = bool(max_conclusions > 0 and not conclusions)
    if degraded:
        warnings.append("no_evidence_supported_conclusion")
    output["generation_warnings"] = list(dict.fromkeys(warnings))[:24]
    output["summary"] = {
        "input_record_count": len(record_index),
        "mechanism_available": input_payload.get("mechanism_packet") is not None,
        "mechanism_edge_count": len(mechanism_index),
        "conclusion_count": len(conclusions),
        "conflict_count": len(output.get("conflicts") or []),
        "evidence_gap_count": len(output.get("evidence_gaps") or []),
        "research_lead_count": len(leads),
        "hypothesis_count": len(hypotheses),
        "dropped_duplicate_conclusion_count": dropped_duplicate_conclusions,
        "dropped_duplicate_lead_count": dropped_duplicate_leads,
        "dropped_duplicate_hypothesis_count": dropped_duplicate_hypotheses,
        "dropped_over_limit_conclusion_count": dropped_over_limit_conclusions,
        "dropped_over_limit_lead_count": dropped_over_limit_leads,
        "dropped_over_limit_hypothesis_count": dropped_over_limit_hypotheses,
        "degraded": degraded,
    }
    output["audit"] = {
        "skill_id": "research_lead_hypothesis_generation",
        "task_id": _clean_string(str(input_payload.get("task_id") or "")),
        "traceability_mode": "research_record_direct_grounding",
        "guard_version": _FINAL_SYNTHESIS_GUARD_VERSION,
        "input_fingerprint": _sha256_text(_stable_json({
            "research_context": input_payload.get("research_context"),
            "research_records": input_payload.get("research_records"),
            "mechanism_packet": input_payload.get("mechanism_packet"),
        })),
        "claim_layers": [
            "evidence_supported_conclusion",
            "research_lead",
            "unvalidated_hypothesis",
        ],
        "mechanism_role": "optional_enhancement",
    }
    _record(
        diagnostics,
        "P0F-R07",
        "passed",
        "final synthesis separates supported conclusions, research leads, and unvalidated hypotheses",
    )

def apply_opentrajectory_runtime_guard(
    *,
    skill_id: str,
    input_payload: dict[str, Any],
    output_payload: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Sanitize representation-only details and enforce deterministic Phase1-3 gates."""
    phase = _PHASE_BY_SKILL_ID.get(str(skill_id))
    if phase is None:
        diagnostics = _new_diagnostics("unmanaged", managed=False)
        if not isinstance(output_payload, dict):
            _record(diagnostics, "OT-R00", "failed", "structured LLM output must be a JSON object", failure_level="fatal")
            _finalize(diagnostics)
        return copy.deepcopy(output_payload), diagnostics

    diagnostics = _new_diagnostics(phase)
    if not isinstance(output_payload, dict):
        _record(diagnostics, "OT-R00", "failed", "structured LLM output must be a JSON object", failure_level="fatal")
        _finalize(diagnostics)

    changes: list[str] = []
    output = _sanitize_representation(copy.deepcopy(output_payload), "$", changes)
    diagnostics["sanitizer_applied"] = bool(changes)
    diagnostics["sanitizer_changes"] = changes
    _record(diagnostics, "OT-R01", "passed", "LLM output is a JSON object")

    if phase == "problem_framing":
        _guard_problem_framing(input_payload, output, diagnostics)
    elif phase == "phase1":
        _guard_phase1(input_payload, output, diagnostics)
    elif phase == "phase2":
        _guard_phase2(input_payload, output, diagnostics)
    elif phase == "phase3":
        _guard_phase3(input_payload, output, diagnostics)
    elif phase == "p07c_evidence_node_extraction":
        _guard_p07c_evidence_node_extraction(input_payload, output, diagnostics)
    elif phase == "p07d_mechanism_chain_synthesis":
        _guard_p07d_mechanism_chain_synthesis(input_payload, output, diagnostics)
    elif phase == "p0_final_synthesis":
        _guard_p0_final_synthesis(input_payload, output, diagnostics)
    _finalize(diagnostics)
    return output, diagnostics
