from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from jsonschema import validators


ROOT = Path(__file__).resolve().parents[1]
EVALS = json.loads((ROOT / "evals" / "evals.json").read_text(encoding="utf-8"))
INPUT_SCHEMA = json.loads((ROOT / "input.schema.json").read_text(encoding="utf-8"))
MODEL_SCHEMA = json.loads((ROOT / "model_output.schema.json").read_text(encoding="utf-8"))
INPUT_VALIDATOR = validators.validator_for(INPUT_SCHEMA)(INPUT_SCHEMA)
MODEL_VALIDATOR = validators.validator_for(MODEL_SCHEMA)(MODEL_SCHEMA)


def _load_finalizer():
    path = ROOT / "scripts" / "finalize.py"
    spec = importlib.util.spec_from_file_location("screening_finalize", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _candidate_ids(case: dict) -> list[str]:
    return [row["candidate_id"] for row in case["prompt"]["candidates"]]


def _oracle_reference_output(case: dict) -> dict:
    """Build one schema-valid output satisfying the deterministic parts of the oracle.

    This is not a model-quality result. It only proves that the eval oracle is representable
    by the current model/output/finalization contracts.
    """
    oracle = case["oracle"]
    priority = list(oracle.get("must_priority") or [])
    exclude = set(oracle.get("must_exclude") or [])
    allowed = oracle.get("allowed_decisions") or {}
    target_expectations = oracle.get("target_expectations") or {}

    judgments = []
    rank = 1
    for candidate_id in _candidate_ids(case):
        if candidate_id in priority:
            decision = "priority"
            reading_priority = max(1, 101 - rank)
            rank += 1
        elif candidate_id in exclude:
            decision = "exclude"
            reading_priority = None
        elif candidate_id in allowed:
            # Choose the lowest-commitment allowed state for contract validation.
            decision = "defer" if "defer" in allowed[candidate_id] else allowed[candidate_id][0]
            reading_priority = None if decision in {"defer", "exclude"} else max(1, 101 - rank)
            if reading_priority is not None:
                rank += 1
        else:
            decision = "supporting"
            reading_priority = max(1, 101 - rank)
            rank += 1

        row = {
            "candidate_id": candidate_id,
            "decision": decision,
            "reading_priority": reading_priority,
            "reason": "Eval reference judgment used only to validate the formal contract.",
        }
        if not oracle.get("forbid_any_target_ids") and candidate_id in target_expectations:
            row["relevant_target_ids"] = list(target_expectations[candidate_id])
        judgments.append(row)
    return {"judgments": judgments}


def test_eval_suite_has_required_scenario_coverage():
    assert len(EVALS["evals"]) >= 10
    coverage = {tag for case in EVALS["evals"] for tag in case["coverage"]}
    required = {
        "materials_battery",
        "cs_agent",
        "theoretical_materials",
        "explicit_comparison",
        "mechanism",
        "missing_abstract",
        "title_abstract_conflict",
        "review_vs_primary",
        "negative_result",
        "evidence_schema_missing",
        "partial_evidence_schema",
        "access_status",
    }
    assert required <= coverage


def test_all_eval_inputs_satisfy_current_input_contract():
    for case in EVALS["evals"]:
        errors = list(INPUT_VALIDATOR.iter_errors(case["prompt"]))
        assert not errors, f"{case['id']}: {[error.message for error in errors]}"


def test_eval_oracles_only_reference_real_candidates_and_targets():
    for case in EVALS["evals"]:
        candidate_ids = set(_candidate_ids(case))
        oracle = case["oracle"]
        for key in ("must_priority", "must_exclude", "must_not_exclude", "not_priority"):
            assert set(oracle.get(key) or []) <= candidate_ids, f"{case['id']}:{key}"
        for candidate_id in (oracle.get("allowed_decisions") or {}):
            assert candidate_id in candidate_ids

        evidence_schema = case["prompt"].get("evidence_schema") or {}
        known_targets = {
            row["target_id"] for row in evidence_schema.get("evidence_targets") or []
        }
        for candidate_id, target_ids in (oracle.get("target_expectations") or {}).items():
            assert candidate_id in candidate_ids
            assert set(target_ids) <= known_targets


def test_oracle_reference_outputs_fit_model_and_downstream_contracts():
    finalizer = _load_finalizer()
    for case in EVALS["evals"]:
        model_output = _oracle_reference_output(case)
        model_errors = list(MODEL_VALIDATOR.iter_errors(model_output))
        assert not model_errors, f"{case['id']}: {[error.message for error in model_errors]}"
        result = finalizer.finalize(case["prompt"], model_output)
        assert result.status == "succeeded", f"{case['id']}: {result.errors}"
        assert result.output is not None
