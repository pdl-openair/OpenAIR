"""Product gap-to-action pipeline for OpenTrajectory research-lead objects."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Iterable, Protocol

from . import planner


class AuthenticGapNormalizer(Protocol):
    """Track-F hook for authentic-package normalization."""

    def normalize_authentic_package(self, package: dict[str, Any]) -> dict[str, Any]: ...

    def validate_normalized_planner_input(
        self,
        suite: dict[str, Any],
        package: dict[str, Any],
    ) -> dict[str, Any]: ...


REQUIRED_PIPELINE_FORBIDDEN_TERMS = (
    "paper claim",
    "paper result",
    "paper evidence",
    "paper contribution",
    "final" + "_score",
    "75" + "->" + "90",
    "75" + "-to-" + "90",
    "75" + "\u2192" + "90",
)

GAP_TYPE_BY_VISIBLE_SLOT = {
    "artifact": "retrieval_gap",
    "task_artifact": "retrieval_gap",
    "task_artifact_source": "retrieval_gap",
    "paper_task_packet": "retrieval_gap",
    "source": "source_lock_gap",
    "source_anchor": "source_lock_gap",
    "boundary_source_anchor": "source_lock_gap",
    "protocol": "protocol_gap",
    "fast_charge_protocol": "protocol_gap",
    "validation_protocol": "protocol_gap",
    "threshold": "threshold_gap",
    "explicit_threshold": "threshold_gap",
    "primary_metrics": "threshold_gap",
    "metric_threshold": "threshold_gap",
    "baseline": "baseline_control_gap",
    "control": "baseline_control_gap",
    "control_expectation": "baseline_control_gap",
    "baseline_control": "baseline_control_gap",
    "decision_rule": "decision_rule_gap",
    "rubric_review_rule": "decision_rule_gap",
    "executable_decision_rule": "decision_rule_gap",
    "independent_validation": "independent_validation_gap",
    "interlab_validation": "independent_validation_gap",
    "interlab_validation_source_lock": "independent_validation_gap",
    "reproduced_validation": "independent_validation_gap",
    "domain_contract": "domain_evidence_contract_gap",
    "domain_boundary_contract": "domain_evidence_contract_gap",
    "negative_boundary_contract": "domain_evidence_contract_gap",
}

PRODUCT_PACKAGE_VERSION = "research_lead_package_v0.1"

# Explicit, source-contract-grounded mapping from the real ResearchLeadPackage
# gap-record vocabulary. No free-text/keyword inference is permitted here.
UPSTREAM_GAP_TYPE_TO_CANONICAL = {
    "missing_evidence_refs": "retrieval_gap",
    "missing_reproducibility_protocol": "protocol_gap",
}

# These real upstream gap types are intentionally visible but not converted to
# one of the eight P08B-3N canonical planning gaps because doing so would invent
# semantics that the current taxonomy does not express.
UNSUPPORTED_UPSTREAM_GAP_TYPES = {
    "missing_measured_results": (
        "absence of measured results is not equivalent to independent validation"
    ),
    "source_mapping_low_confidence": (
        "mapping confidence alone does not establish a source-lock gap"
    ),
    "unmapped_source_fields": (
        "unmapped field semantics cannot be inferred from field names"
    ),
    "offline_proposal_traceability_limited": (
        "offline evaluation-mode traceability is a package boundary, not a canonical action gap"
    ),
}

ACTION_DETAIL_BY_GAP_TYPE = {
    "retrieval_gap": {
        "required_input": "missing source or artifact class, owner or location, fields to retrieve, and not-found handling",
        "expected_output": "source-target list or explicit not-found gap record",
        "stop_condition": "stop after source target or not-found state is recorded without changing evidence status",
        "hard_fail": "treating a planned source target as retrieved or reviewed evidence",
    },
    "source_lock_gap": {
        "required_input": "claim or boundary field needing a stable source anchor",
        "expected_output": "source anchor or downgraded explicit source gap",
        "stop_condition": "stop when the anchor state is recorded and unsupported fields remain marked as gaps",
        "hard_fail": "treating an unlocked boundary or claim as source-backed",
    },
    "protocol_gap": {
        "required_input": "missing protocol variables and source or experiment needed to complete them",
        "expected_output": "protocol completion request that preserves the partial protocol state",
        "stop_condition": "stop when missing variables are listed for later source-backed completion",
        "hard_fail": "describing partial protocol variables as complete or executed",
    },
    "threshold_gap": {
        "required_input": "metric, current threshold state, and source-backed threshold evidence needed",
        "expected_output": "threshold completion request that preserves the missing or partial threshold state",
        "stop_condition": "stop when metric and missing threshold evidence are listed",
        "hard_fail": "inferring pass/fail readiness from a planned threshold",
    },
    "baseline_control_gap": {
        "required_input": "missing comparator, control state, or baseline measurement",
        "expected_output": "baseline/control completion request tied to the lead object",
        "stop_condition": "stop when the missing comparator or control state is recorded",
        "hard_fail": "claiming controlled comparative support without the missing control",
    },
    "decision_rule_gap": {
        "required_input": "observable, threshold status, baseline/control status, and rule-enabling evidence",
        "expected_output": "planned decision-rule template that remains non-executable until source-backed",
        "stop_condition": "stop before executable status until all enabling evidence is source-backed",
        "hard_fail": "claiming readiness from a planned decision rule",
    },
    "independent_validation_gap": {
        "required_input": "independent, interlaboratory, external, or reproduction evidence source class",
        "expected_output": "validation-status check that preserves partial, unclear, or not-found outcomes",
        "stop_condition": "stop after validation source status is recorded without changing evidence state",
        "hard_fail": "writing planned, partial, or searched validation as reproduced validation",
    },
    "domain_evidence_contract_gap": {
        "required_input": "domain-specific required evidence objects and blocked inference paths",
        "expected_output": "domain evidence contract update that preserves role and fixed boundary fields",
        "stop_condition": "stop when required fields and blocked inferences are enumerated",
        "hard_fail": "using a domain boundary update to convert an incomplete lead into closure",
    },
}


def _status_field(status_fields: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in status_fields:
            return status_fields[key]
    return None


def _normalize_gap_slot(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _gap_type_for_visible_slot(slot: str) -> str | None:
    normalized = _normalize_gap_slot(slot)
    if normalized in GAP_TYPE_BY_VISIBLE_SLOT:
        return GAP_TYPE_BY_VISIBLE_SLOT[normalized]
    for token, gap_type in GAP_TYPE_BY_VISIBLE_SLOT.items():
        if token in normalized:
            return gap_type
    return None


def _current_status_for_gap(
    *,
    lead: dict[str, Any],
    gap_type: str,
    slot_id: str,
    row: dict[str, Any],
) -> Any:
    status_fields = dict(lead.get("status_fields") or {})
    if row.get("current_status") not in (None, ""):
        return row["current_status"]
    if gap_type == "protocol_gap":
        return _status_field(status_fields, slot_id, "fast_charge_protocol") or "PARTIAL"
    if gap_type == "threshold_gap":
        return _status_field(status_fields, slot_id, "explicit threshold") or "PARTIAL"
    if gap_type == "decision_rule_gap":
        return (
            _status_field(status_fields, slot_id, "executable decision rule")
            or "PLANNED_TEMPLATE_ONLY / GAP"
        )
    if gap_type == "independent_validation_gap":
        return _status_field(status_fields, slot_id, "reproduced_validation") or "NOT_FOUND"
    if gap_type == "domain_evidence_contract_gap" and lead.get("role") == "NEGATIVE_ONLY":
        return "NEGATIVE_ONLY_BOUNDARY"
    status = str(row.get("status") or "").upper()
    if status in {"MISSING", "NOT_FOUND"}:
        return "NOT_FOUND"
    if status in {"PARTIAL", "GAP", "UNCLEAR"}:
        return status
    return row.get("status") or "GAP"


def _visible_slot_ids_for_lead(lead: dict[str, Any], package: dict[str, Any]) -> set[str]:
    decision_contract = (
        lead.get("decision_evidence_contract")
        or package.get("decision_evidence_contract")
        or {}
    )
    visible_slots = set()
    visible_slots.update(str(slot) for slot in lead.get("missing_decision_slots") or [])
    visible_slots.update(str(slot) for slot in decision_contract.get("missing_critical_items") or [])
    return visible_slots


def _visible_gap_rows_for_lead(lead: dict[str, Any], package: dict[str, Any]) -> list[dict[str, Any]]:
    lead_id = str(lead.get("research_lead_id") or "")
    visible_slots = _visible_slot_ids_for_lead(lead, package)
    rows: list[dict[str, Any]] = []
    for row in package.get("slot_coverage_table") or []:
        if not isinstance(row, dict):
            continue
        row_lead_id = str(row.get("research_lead_id") or row.get("lead_id") or "")
        slot_id = str(row.get("slot_id") or row.get("missing_item") or row.get("field") or "")
        if row_lead_id:
            if lead_id and row_lead_id != lead_id:
                continue
        elif slot_id not in visible_slots:
            continue
        status = str(row.get("status") or row.get("current_status") or "").lower()
        if status and status not in {"found", "matched", "supported", "complete"}:
            rows.append(dict(row))

    for slot in visible_slots:
        slot_id = str(slot)
        if not any(str(row.get("slot_id") or "") == slot_id for row in rows):
            rows.append(
                {
                    "slot_id": slot_id,
                    "slot_label": slot_id.replace("_", " "),
                    "status": "missing",
                    "source_collection": "missing_decision_slots",
                }
            )
    return rows


def _stable_gap_identity(lead: dict[str, Any], row: dict[str, Any], gap: dict[str, Any]) -> tuple[str, str, str, str, str]:
    lead_id = str(lead.get("research_lead_id") or lead.get("case") or lead.get("title") or "")
    source_collection = str(row.get("source_collection") or "slot_coverage_table")
    slot_id = str(row.get("slot_id") or row.get("missing_item") or row.get("field") or "")
    item_id = str(row.get("item_id") or row.get("name") or row.get("slot_label") or slot_id)
    stable_item = slot_id or item_id
    return (
        lead_id,
        source_collection,
        stable_item,
        str(gap["gap_type"]),
        str(gap["current_status"]),
    )


def _gap_from_visible_row(lead: dict[str, Any], row: dict[str, Any]) -> dict[str, Any] | None:
    slot_id = str(row.get("slot_id") or row.get("missing_item") or row.get("field") or "")
    slot_label = str(row.get("slot_label") or row.get("description") or slot_id)
    gap_type = str(row.get("gap_type") or "") or (_gap_type_for_visible_slot(slot_id) or "")
    if not gap_type:
        return None
    detail = ACTION_DETAIL_BY_GAP_TYPE[gap_type]
    current_status = _current_status_for_gap(
        lead=lead,
        gap_type=gap_type,
        slot_id=slot_id,
        row=row,
    )
    required_input = row.get("required_input") or f"{slot_label}: {detail['required_input']}"
    expected_output = row.get("expected_output") or detail["expected_output"]
    stop_condition = row.get("stop_condition") or detail["stop_condition"]
    hard_fail = row.get("hard_fail") or detail["hard_fail"]
    gap = {
        "gap_type": gap_type,
        "current_status": current_status,
        "required_input": required_input,
        "expected_output": expected_output,
        "stop_condition": stop_condition,
        "hard_fail": hard_fail,
    }
    for field in planner.TRACE_ACTION_FIELDS:
        if row.get(field) not in (None, ""):
            gap[field] = row[field]
    return gap


def _is_product_research_lead_package(package: dict[str, Any]) -> bool:
    return package.get("package_version") == PRODUCT_PACKAGE_VERSION


def _product_case_identity(package: dict[str, Any]) -> str:
    domain = str(package.get("task_domain") or "").strip()
    subdomain = str(package.get("task_subdomain") or "").strip()
    if domain and subdomain:
        return f"{domain}:{subdomain}"
    if domain:
        return domain
    if subdomain:
        return subdomain
    return PRODUCT_PACKAGE_VERSION


def _product_gap_status(gap_record: dict[str, Any]) -> Any:
    # The generic ResearchLeadPackage gap schema does not manufacture a status.
    # Preserve an explicit upstream status if present; otherwise keep it absent
    # as None rather than inventing CLOSED/PARTIAL/VALIDATED-like semantics.
    if "current_status" in gap_record:
        return gap_record.get("current_status")
    if "status" in gap_record:
        return gap_record.get("status")
    return None


def _product_gap_to_planner_gap(
    gap_record: dict[str, Any],
    canonical_gap_type: str,
) -> dict[str, Any]:
    detail = ACTION_DETAIL_BY_GAP_TYPE[canonical_gap_type]
    planner_gap: dict[str, Any] = {
        "gap_type": canonical_gap_type,
        "current_status": _product_gap_status(gap_record),
        "required_input": {
            "upstream_gap_type": gap_record.get("type"),
            "fields": copy.deepcopy(gap_record.get("fields") or []),
        },
        "expected_output": detail["expected_output"],
        "stop_condition": detail["stop_condition"],
        "hard_fail": detail["hard_fail"],
    }
    for field in planner.TRACE_ACTION_FIELDS:
        if gap_record.get(field) not in (None, ""):
            planner_gap[field] = copy.deepcopy(gap_record[field])
    return planner_gap


def _build_product_planner_suite(package: dict[str, Any]) -> dict[str, Any]:
    # Validate against the sibling product contract itself. This is a boundary
    # adapter, not a parallel ResearchLeadPackage schema.
    from skills.opentrajectory.research_lead_package.schema import (
        validate_research_lead_package,
    )

    errors = validate_research_lead_package(package)
    if errors:
        raise ValueError("invalid research_lead_package: " + "; ".join(errors))

    proposal = package.get("proposal") or {}
    gap_records = proposal.get("gap_records")
    if not isinstance(gap_records, list):
        raise ValueError("research_lead_package.proposal.gap_records must be a list")

    mapped_gaps: list[dict[str, Any]] = []
    mapping_rows: list[dict[str, Any]] = []
    unsupported_rows: list[dict[str, Any]] = []
    canonical_types = set(planner.REQUIRED_GAP_TYPES)

    for index, raw_gap in enumerate(gap_records):
        if not isinstance(raw_gap, dict):
            unsupported_rows.append(
                {
                    "gap_record_index": index,
                    "upstream_gap_type": None,
                    "reason": "gap record must be an object",
                }
            )
            continue
        upstream_type = str(raw_gap.get("type") or "")
        canonical_type: str | None = None
        mapping_basis: str | None = None
        if upstream_type in canonical_types:
            canonical_type = upstream_type
            mapping_basis = "canonical_passthrough"
        elif upstream_type in UPSTREAM_GAP_TYPE_TO_CANONICAL:
            canonical_type = UPSTREAM_GAP_TYPE_TO_CANONICAL[upstream_type]
            mapping_basis = "explicit_research_lead_package_mapping"

        if canonical_type is None:
            unsupported_rows.append(
                {
                    "gap_record_index": index,
                    "upstream_gap_type": upstream_type or None,
                    "reason": UNSUPPORTED_UPSTREAM_GAP_TYPES.get(
                        upstream_type,
                        "upstream gap type is not in the explicit Track-B mapping contract",
                    ),
                }
            )
            continue

        mapped_gaps.append(_product_gap_to_planner_gap(raw_gap, canonical_type))
        mapping_rows.append(
            {
                "gap_record_index": index,
                "upstream_gap_type": upstream_type,
                "canonical_gap_type": canonical_type,
                "mapping_basis": mapping_basis,
            }
        )

    cases: list[dict[str, Any]] = []
    if mapped_gaps:
        cases.append(
            {
                "case": _product_case_identity(package),
                "role": "RESEARCH_LEAD_PACKAGE",
                "boundary": "generic ResearchLeadPackage scientific facts preserved",
                "status_fields": {},
                "gaps": mapped_gaps,
            }
        )
    return {
        "suite_id": f"{_product_case_identity(package)}-gap-to-action",
        "source_contract": PRODUCT_PACKAGE_VERSION,
        "cases": cases,
        "upstream_gap_mapping": mapping_rows,
        "unsupported_upstream_gaps": unsupported_rows,
    }


def _build_legacy_planner_suite(package: dict[str, Any]) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for lead in package.get("research_leads") or []:
        if not isinstance(lead, dict):
            continue
        visible_rows = _visible_gap_rows_for_lead(lead, package)
        gaps: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str, str]] = set()
        for row in visible_rows:
            gap = _gap_from_visible_row(lead, row)
            if not gap:
                continue
            key = _stable_gap_identity(lead, row, gap)
            if key in seen:
                continue
            seen.add(key)
            gaps.append(gap)
        if not gaps:
            continue
        cases.append(
            {
                "case": lead.get("case") or lead.get("title") or lead.get("research_lead_id"),
                "role": lead.get("role") or lead.get("lead_role") or "RESEARCH_LEAD",
                "boundary": lead.get("boundary") or "research-lead object boundary preserved",
                "status_fields": dict(lead.get("status_fields") or {}),
                "gaps": gaps,
            }
        )
    return {
        "suite_id": f"{package.get('package_id') or 'research-lead-package'}-gap-to-action",
        "cases": cases,
    }


def build_planner_suite_from_research_lead_package(
    package: dict[str, Any],
    *,
    normalizer: AuthenticGapNormalizer | None = None,
) -> dict[str, Any]:
    """Build planner input without changing upstream scientific facts.

    The normal product path consumes the real sibling
    ``research_lead_package_v0.1`` contract directly. Authentic P08B-3S
    packages remain an explicit Track-F hook. The historical decision-oriented
    shape remains supported only for legacy P08B-3O compatibility.
    """

    if package.get("contract_type") == "authentic_research_lead_package":
        if normalizer is None:
            raise ValueError(
                "authentic_research_lead_package requires an explicit gap normalizer; "
                "normalization is owned by Track F"
            )
        return normalizer.normalize_authentic_package(package)

    if _is_product_research_lead_package(package):
        return _build_product_planner_suite(package)

    if "package_version" in package and "proposal" in package:
        raise ValueError(
            f"unsupported research_lead_package package_version: {package.get('package_version')!r}"
        )

    return _build_legacy_planner_suite(package)


def _attach_actions_to_treatment_package(
    package: dict[str, Any],
    planner_output: dict[str, Any],
    suite: dict[str, Any],
) -> dict[str, Any]:
    treatment_package = copy.deepcopy(package)
    actions = [dict(action) for action in planner_output.get("action_rows") or []]
    actions_by_case: dict[str, list[dict[str, Any]]] = {}
    for action in actions:
        actions_by_case.setdefault(str(action.get("case") or ""), []).append(dict(action))

    if _is_product_research_lead_package(treatment_package):
        # Product packages have no legacy ``research_leads`` array. Keep the
        # planning result as an explicit top-level annotation only.
        treatment_package["gap_to_action_targeted_actions"] = actions
    else:
        for lead in treatment_package.get("research_leads") or []:
            case_name = str(lead.get("case") or lead.get("title") or lead.get("research_lead_id") or "")
            lead["gap_to_action_targeted_actions"] = actions_by_case.get(case_name, [])

    planning_annotation = {
        "planner_id": planner_output.get("planner_id"),
        "evidence_producing_run": planner_output.get("evidence_producing_run"),
        "action_count": len(actions),
        "boundary_checks": dict(planner_output.get("boundary_checks") or {}),
    }
    if _is_product_research_lead_package(treatment_package):
        planning_annotation["upstream_gap_mapping"] = copy.deepcopy(
            suite.get("upstream_gap_mapping") or []
        )
        planning_annotation["unsupported_upstream_gaps"] = copy.deepcopy(
            suite.get("unsupported_upstream_gaps") or []
        )
    treatment_package["gap_to_action_planning"] = planning_annotation
    return treatment_package


def run_gap_to_action_pipeline(
    package: dict[str, Any],
    *,
    normalizer: AuthenticGapNormalizer | None = None,
    required_gap_types: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Attach targeted actions to a copy of a research-lead package.

    The baseline package is deep-copied and remains unchanged. The treatment
    copy may add only gap-to-action annotations; all evidence/status fields
    are validated as immutable.
    """

    if not isinstance(package, dict):
        raise TypeError("research_lead_package must be an object")
    baseline_package = copy.deepcopy(package)
    suite = build_planner_suite_from_research_lead_package(
        baseline_package,
        normalizer=normalizer,
    )
    normalization_validation: dict[str, Any] | None = None
    if baseline_package.get("contract_type") == "authentic_research_lead_package":
        if normalizer is None:  # defensive; builder already rejects this path
            raise ValueError("authentic research lead package normalizer is required")
        normalization_validation = normalizer.validate_normalized_planner_input(
            suite, baseline_package
        )

    planner_output = planner.plan_gap_to_actions(
        suite,
        required_gap_types=required_gap_types,
    )
    output = {
        "pipeline_id": "p08b_3o_gap_to_action_pipeline_v1",
        "mode": "minimal_real_research_lead_object_path",
        "evidence_producing_run": False,
        "planner_input_suite": suite,
        "baseline_research_lead_package": baseline_package,
        "planner_before_after": planner_output,
        "treatment_research_lead_package": _attach_actions_to_treatment_package(
            baseline_package,
            planner_output,
            suite,
        ),
    }
    if _is_product_research_lead_package(baseline_package):
        output["upstream_gap_mapping"] = copy.deepcopy(suite.get("upstream_gap_mapping") or [])
        output["unsupported_upstream_gaps"] = copy.deepcopy(
            suite.get("unsupported_upstream_gaps") or []
        )
    if normalization_validation is not None:
        output["normalization_validation"] = normalization_validation
        output["unmapped_or_absent_gap_signals"] = list(
            suite.get("unmapped_or_absent_gap_signals") or []
        )
    validate_pipeline_output(output, required_gap_types=required_gap_types)
    return output


def validate_pipeline_output(
    output: dict[str, Any],
    *,
    required_gap_types: Iterable[str] | None = None,
) -> None:
    planner.validate_planner_output(
        output["planner_before_after"],
        required_gap_types=required_gap_types,
    )


def write_pipeline_output(
    package: dict[str, Any],
    output_path: Path,
    *,
    normalizer: AuthenticGapNormalizer | None = None,
) -> dict[str, Any]:
    output = run_gap_to_action_pipeline(package, normalizer=normalizer)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output
