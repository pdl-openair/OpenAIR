from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[2]


def _module():
    spec = importlib.util.spec_from_file_location("pvd_schema_finalize", ROOT / "scripts/finalize.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _input():
    pending = [{"status": "pending_evidence", "user_intent": "evaluate_for_admission", "parameter_key": "substrate_temperature"}]
    return {
        "structured_context": {"research_goal": "Optimize the current PVD decision."},
        "recommendation_parameter_set": {"parameters": [{"parameter_key": "pulse_power"}, {"parameter_key": "n2_flow"}]},
        "parameter_admission_judgments": pending,
        "retrieval_design": {"contract": "pvd-evidence-retrieval-design/v1", "mode": "core", "query_variants": [{"covered_parameter_keys": ["pulse_power", "n2_flow", "substrate_temperature"]}]},
    }


def _draft():
    return {
        "evidence_targets": [{
            "target_type": "experimental_condition",
            "scientific_role": "condition",
            "description": "Parameter-condition-result evidence under comparable PVD systems.",
            "requiredness": "required",
            "completion_criteria": "At least one traceable source reports condition and result together.",
            "covered_parameter_keys": ["pulse_power", "n2_flow", "substrate_temperature"],
            "comparison_dimensions": ["target material", "substrate", "deposition method"],
            "provenance_requirements": ["source URL or DOI", "reported condition", "reported result"],
        }],
        "gap_taxonomy": ["missing_condition", "not_comparable"],
        "uncertainty_rules": ["Keep unreported conditions unknown."],
        "design_warnings": [],
    }


def test_evidence_schema_covers_formal_and_pending_without_promoting_pending():
    output = _module().finalize(_input(), _draft()).output
    jsonschema.validate(output, json.loads((ROOT / "output.schema.json").read_text(encoding="utf-8")))
    assert output["evidence_schema"]["evidence_targets"][0]["target_id"] == "target_001"
    assert output["coverage_target"]["formal_parameter_keys"] == ["pulse_power", "n2_flow"]
    assert output["pending_parameter_evidence_identities"] == ["substrate_temperature"]




def test_evidence_schema_accepts_merged_retrieval():
    payload = _input()
    payload["retrieval_design"]["mode"] = "core_plus_supplemental"
    output = _module().finalize(payload, _draft()).output
    jsonschema.validate(output, json.loads((ROOT / "output.schema.json").read_text(encoding="utf-8")))
    assert output["coverage_target"]["all_parameter_keys"] == ["pulse_power", "n2_flow", "substrate_temperature"]


def test_finalize_missing_required_inputs_fails_closed_not_child_exception():
    # Production defect (task_51845983): when the binding source for
    # recommendation_parameter_set was not frozen into the WorkUnit input, the
    # finalizer crashed with AttributeError (None.get) inside _identities,
    # producing finalizer_child_exception instead of a fail-closed result. The
    # finalizer must reject missing required inputs with a failure code.
    module = _module()
    payload = _input()
    del payload["recommendation_parameter_set"]
    result = module.finalize(payload, _draft())
    assert result.status == "failed"
    assert result.output is None
    assert result.errors == ("finalize_missing_input_recommendation_parameter_set",)

    payload = _input()
    payload["parameter_admission_judgments"] = None
    result = module.finalize(payload, _draft())
    assert result.status == "failed"
    assert result.errors == ("finalize_missing_input_parameter_admission_judgments",)

    payload = _input()
    del payload["structured_context"]
    result = module.finalize(payload, _draft())
    assert result.status == "failed"
    assert result.errors == ("finalize_missing_input_structured_context",)
