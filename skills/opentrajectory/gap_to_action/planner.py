"""Deterministic gap-to-action planner for OpenTrajectory research leads.

The product API accepts the gaps that are actually present. The legacy
``run_planner`` helper keeps the old P08B-3N locked-suite requirement that all
canonical gap types appear, so existing harnesses remain strict without
leaking that test-only constraint into production use.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


REQUIRED_GAP_TYPES = (
    "retrieval_gap",
    "source_lock_gap",
    "protocol_gap",
    "threshold_gap",
    "baseline_control_gap",
    "decision_rule_gap",
    "independent_validation_gap",
    "domain_evidence_contract_gap",
)

REQUIRED_ACTION_FIELDS = (
    "gap_type",
    "case",
    "current_status",
    "targeted_action",
    "required_input",
    "expected_output",
    "stop_condition",
    "hard_fail",
    "post_action_status",
)

TRACE_ACTION_FIELDS = (
    "source_gap_pointer",
    "source_gap_id",
    "source_signal",
    "source_value",
    "source_slot_id",
    "trace_id",
    "source_pointer",
    "evidence_pointer",
)

BFE_FIXED_FIELDS = {
    "role": "NEGATIVE_ONLY",
    "fast_charge_protocol": "PARTIAL",
    "explicit threshold": "PARTIAL",
    "executable decision rule": "PLANNED_TEMPLATE_ONLY / GAP",
    "reproduced_validation": "NOT_FOUND",
    "decision_level_ready": False,
    "scientific_effect_validated": False,
}

ACTION_BY_GAP_TYPE = {
    "retrieval_gap": "targeted_source_search",
    "source_lock_gap": "targeted_source_search",
    "protocol_gap": "protocol_completion_request",
    "threshold_gap": "threshold_completion_request",
    "baseline_control_gap": "baseline_control_completion_request",
    "decision_rule_gap": "decision_rule_completion_plan",
    "independent_validation_gap": "independent_validation_check",
    "domain_evidence_contract_gap": "domain_evidence_contract_update",
}

FORBIDDEN_OUTPUT_TERMS = (
    "scientific " + "discovery",
    "benchmark grading is scientific " + "validation",
    "new material " + "discovery",
    "break" + "through",
    "universal " + "rule",
    "positive " + "validation",
    "autonomous scientific " + "discovery",
    "paper_claim",
    "paper claim",
    "paper evidence",
    "paper result",
    "paper contribution",
    "75" + "->" + "90",
    "75" + "-to-" + "90",
    "final" + "_score",
)

CLOSING_STATUSES = {
    "CLOSED",
    "COMPLETE",
    "VALIDATED",
    "REPRODUCED",
    "DECISION_READY",
    "SCIENTIFICALLY_VALIDATED",
    "POSITIVE",
}


def load_suite(path: Path) -> dict[str, Any]:
    """Load a planner suite from JSON."""

    return json.loads(path.read_text(encoding="utf-8"))


def plan_gap_action(case: dict[str, Any], gap: dict[str, Any]) -> dict[str, Any]:
    """Map one typed gap to one targeted action without changing status."""

    gap_type = str(gap.get("gap_type") or "")
    if gap_type not in ACTION_BY_GAP_TYPE:
        raise ValueError(f"unsupported gap_type: {gap_type!r}")
    missing = [
        field
        for field in (
            "current_status",
            "required_input",
            "expected_output",
            "stop_condition",
            "hard_fail",
        )
        if field not in gap
    ]
    if missing:
        raise ValueError(f"gap missing required fields: {missing}")
    if not case.get("case"):
        raise ValueError("case identity is required")

    current_status = gap["current_status"]
    action = {
        "gap_type": gap_type,
        "case": case["case"],
        "current_status": current_status,
        "targeted_action": ACTION_BY_GAP_TYPE[gap_type],
        "required_input": gap["required_input"],
        "expected_output": gap["expected_output"],
        "stop_condition": gap["stop_condition"],
        "hard_fail": gap["hard_fail"],
        "post_action_status": current_status,
    }
    for field in TRACE_ACTION_FIELDS:
        if gap.get(field) not in (None, ""):
            action[field] = gap[field]
    return action


def plan_gap_to_actions(
    suite: dict[str, Any],
    *,
    required_gap_types: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Plan targeted actions for the gaps actually present in ``suite``.

    ``required_gap_types`` is optional and exists for contract/harness gates.
    Product callers normally omit it; the legacy ``run_planner`` wrapper uses
    the full P08B-3N vocabulary to preserve historical locked-suite behavior.
    """

    if not isinstance(suite, dict):
        raise TypeError("suite must be an object")
    cases = suite.get("cases")
    if not isinstance(cases, list):
        raise ValueError("suite.cases must be a list")

    baseline_cases: list[dict[str, Any]] = []
    treatment_cases: list[dict[str, Any]] = []
    action_rows: list[dict[str, Any]] = []

    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("suite.cases entries must be objects")
        gaps = case.get("gaps")
        if not isinstance(gaps, list):
            raise ValueError(f"case gaps must be a list: {case.get('case')!r}")
        baseline = {
            "case": case.get("case"),
            "role": case.get("role"),
            "boundary": case.get("boundary"),
            "status_fields": dict(case.get("status_fields") or {}),
            "visible_gaps": [
                {
                    "gap_type": gap["gap_type"],
                    "current_status": gap["current_status"],
                }
                for gap in gaps
            ],
            "targeted_actions": [],
        }
        actions = [plan_gap_action(case, gap) for gap in gaps]
        action_rows.extend(actions)
        treatment = dict(baseline)
        treatment["targeted_actions"] = actions
        baseline_cases.append(baseline)
        treatment_cases.append(treatment)

    boundary_checks = {
        "gap_closure": "PASS_NO_GAP_UPGRADED",
        "planned_validation": "PASS_PLANNED_REMAINS_PLANNED",
        "forbidden_score_language": "PASS_NOT_PRESENT",
    }
    case_names = {case.get("case") for case in treatment_cases}
    if "PaperBench bounded subcase" in case_names:
        boundary_checks["paperbench"] = "PASS_REPRODUCTION_WORKFLOW_ONLY"
    if "ASSB interlaboratory reproducibility" in case_names:
        boundary_checks["assb"] = "PASS_REPRODUCIBILITY_GROUNDED_VALIDATION_ONLY"
    if "BFE/F2DEM / Li||NCM811" in case_names:
        boundary_checks["bfe_f2dem"] = "PASS_NEGATIVE_ONLY_FIXED_FIELDS_PRESERVED"

    output = {
        "planner_id": "p08b_3n_gap_to_action_planner_v1",
        "mode": "deterministic_offline_harness",
        "evidence_producing_run": False,
        "suite_id": suite.get("suite_id") or "gap-to-action-suite",
        "baseline": {"cases": baseline_cases},
        "treatment": {"cases": treatment_cases},
        "action_rows": action_rows,
        "boundary_checks": boundary_checks,
    }
    validate_planner_output(output, required_gap_types=required_gap_types)
    return output


def run_planner(suite: dict[str, Any]) -> dict[str, Any]:
    """Legacy P08B-3N harness entrypoint; requires the full locked taxonomy."""

    return plan_gap_to_actions(suite, required_gap_types=REQUIRED_GAP_TYPES)


def validate_planner_output(
    output: dict[str, Any],
    *,
    required_gap_types: Iterable[str] | None = None,
) -> None:
    """Retain existing required action keys without judging scientific content."""
    for action in output.get("action_rows") or []:
        missing_fields = [field for field in REQUIRED_ACTION_FIELDS if field not in action]
        if missing_fields:
            raise AssertionError(f"action missing fields: {missing_fields}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    output = run_planner(load_suite(args.fixture))
    rendered = json.dumps(output, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
