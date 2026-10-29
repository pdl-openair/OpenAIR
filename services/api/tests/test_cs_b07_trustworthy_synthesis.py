from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from jsonschema import Draft202012Validator, ValidationError
import pytest
import yaml

from app.execution.python_adapter_runner import run_python_skill_adapter
from app.execution.research_input_contract_gate import (
    SkillInputRequest,
    resolve_registered_skill_input,
)
from app.modules.research_orchestration_resolver import build_registry_snapshot
from app.modules.scientific_state_fact_projection import (
    materialize_scientific_state_projection,
    project_validated_structured_result,
)


ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = ROOT / "skills" / "cs_research" / "reproduction-conclusion-closure"
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))
# Several cs_research skills expose a bare-top-level `adapter` package; in the
# shared per-group pytest process whichever skill imported first owns the name.
# Drop any earlier binding so this import always resolves to THIS skill's
# package regardless of file order (sync-2 accommodation).
sys.modules.pop("adapter", None)

from adapter.reproduction_conclusion_closure_adapter import (  # noqa: E402
    run_reproduction_conclusion_closure,
)


def _next_step(validation_id: str = "validation-1") -> dict:
    return {
        "step_id": "step-1",
        "action": "Run the declared validation.",
        "reason": "Resolve the remaining formal uncertainty.",
        "expected_judgment_effect": "May refine the B05 comparison inputs.",
        "target_definition_id": (
            "cs_reproduction:result_comparability_judgment"
        ),
        "requires_design_change": False,
        "source_validation_id": validation_id,
    }


def _draft(answer_refs: list[str] | None = None) -> dict:
    return {
        "answer": {
            "text": "The tested scope meets the frozen comparison standard.",
            "evidence_refs": answer_refs if answer_refs is not None else ["comparison-1"],
        },
        "scope": {"text": "Frozen revision and metric scope.", "evidence_refs": []},
        "limitations": [
            {"text": "Only one seed was executed.", "evidence_refs": []}
        ],
        "unknowns": [
            {
                "text": "Residual variance remains unresolved.",
                "evidence_refs": ["diagnosis-evidence-1"],
            }
        ],
        "next_steps": [_next_step()],
    }


def _payload(*, verdict: str = "meets_standard", draft: object = ...) -> dict:
    payload = {
        "reproduction_baseline_and_criteria": {
            "description": "Ordinary prose mentions fake-record-in-prose."
        },
        "reproduction_experiment_design": {"repository": "example/repository"},
        "reproduction_execution_record": {
            "status": "succeeded",
            "failure_code": "not-evidence-code",
            "unknowns": ["Execution environment patch level is unknown."],
        },
        "actual_metric_records": {
            "execution_record_ref": "execution-1",
            "metrics": [{"metric_name": "Avg.", "value": 81.58}],
            "unknowns": ["One metric confidence interval is unavailable."],
        },
        "reproduction_comparison_judgment": {
            "overall_verdict": verdict,
            "comparability": {
                "status": "comparable" if verdict != "indeterminate" else "not_comparable",
                "conditions": {
                    "data": {"evidence_refs": ["condition-1", "condition-1"]}
                },
            },
            "metric_judgments": [{"metric_name": "Avg."}],
            "evidence_refs": ["comparison-1", "comparison-1"],
            "unknowns": ["Reference confidence interval is unavailable."],
        },
        "reproduction_deviation_diagnosis": {
            "evidence_supported_causes": [
                {"evidence_refs": ["diagnosis-evidence-1"]}
            ],
            "validation_actions": [{"validation_id": "validation-1"}],
            "remaining_unknowns": [
                {"statement": "Run-to-run variance is unresolved."}
            ],
        },
    }
    if draft is ...:
        payload["reproduction_conclusion_draft"] = _draft()
    elif draft is not None:
        payload["reproduction_conclusion_draft"] = draft
    return payload


def _isolated(result: dict, segment_type: str, reason_code: str) -> bool:
    return any(
        item["segment_type"] == segment_type
        and item["reason_code"] == reason_code
        for item in result["isolated_synthesis_segments"]
    )


def _artifact(source_binding: dict, document: dict) -> dict:
    return {
        "sourceCapabilityType": "skill",
        "sourceCapabilityId": source_binding["skillId"],
        "sourceCapabilityDefinitionVersion": source_binding["skillDefinitionVersion"],
        "sourceBindingRefs": [
            {
                "bindingId": source_binding["bindingId"],
                "bindingVersion": source_binding["bindingVersion"],
            }
        ],
        "mimeType": "application/json",
        "content": json.dumps(document).encode("utf-8"),
    }


def test_missing_b07_yields_b05_only_output() -> None:
    result = run_reproduction_conclusion_closure(_payload(draft=None))

    assert result["verdict"] == "reproduced"
    assert result["answer"] is None
    assert result["scope"] is None
    assert result["limitations"] == []
    assert result["unknowns"] == []
    assert result["next_steps"] == []
    assert result["synthesis_gaps"] == [
        "synthesis_draft_missing_or_invalid",
        "answer_missing_or_ungrounded",
    ]


def test_b07_claim_cannot_override_indeterminate_not_comparable_b05() -> None:
    draft = _draft()
    draft["answer"]["text"] = "Comparable and reproduced."

    result = run_reproduction_conclusion_closure(
        _payload(verdict="indeterminate", draft=draft)
    )

    assert result["verdict"] == "indeterminate"
    assert result["comparison"]["overall_verdict"] == "indeterminate"
    assert result["comparison"]["comparability"]["status"] == "not_comparable"
    assert result["comparison_source"] == "reproduction_comparison_judgment"


def test_upstream_unknowns_remain_separate_and_do_not_gain_evidence_identity() -> None:
    result = run_reproduction_conclusion_closure(_payload())

    assert result["unknowns"] == [
        {
            "text": "Residual variance remains unresolved.",
            "evidence_refs": ["diagnosis-evidence-1"],
        }
    ]
    assert {item["source"] for item in result["upstream_unknowns"]} == {
        "reproduction_comparison_judgment",
        "reproduction_execution_record",
        "actual_metric_records",
        "reproduction_deviation_diagnosis",
    }
    assert "Run-to-run variance is unresolved." not in result["evidence_refs"]


def test_formal_schemas_validate_complete_partial_and_b05_only_outputs() -> None:
    input_schema = json.loads(
        (SKILL_ROOT / "input.schema.json").read_text(encoding="utf-8")
    )
    closure_schema = json.loads(
        (SKILL_ROOT / "output.schema.json").read_text(encoding="utf-8")
    )
    synthesis_schema = json.loads(
        (
            ROOT
            / "skills"
            / "cs_research"
            / "reproduction-conclusion-synthesis-v2"
            / "output.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(input_schema)
    Draft202012Validator.check_schema(closure_schema)
    Draft202012Validator.check_schema(synthesis_schema)
    Draft202012Validator(synthesis_schema).validate(_draft())
    Draft202012Validator(input_schema).validate(_payload(draft=None))
    with pytest.raises(ValidationError):
        Draft202012Validator(input_schema).validate(_payload(draft="wrong type"))

    complete = run_reproduction_conclusion_closure(_payload())
    partial_draft = _draft()
    partial_draft["limitations"].append(
        {"text": "Unsupported limitation.", "evidence_refs": ["invented-ref"]}
    )
    partial = run_reproduction_conclusion_closure(_payload(draft=partial_draft))
    b05_only = run_reproduction_conclusion_closure(_payload(draft=None))

    validator = Draft202012Validator(closure_schema)
    for output in (complete, partial, b05_only):
        validator.validate(output)


def test_production_input_composition_runs_b05_only_without_b07_artifact() -> None:
    snapshot = json.loads(build_registry_snapshot().canonical_payload)
    bindings = snapshot["bindings"]["skills"]
    binding_id = (
        "bind:cs_reproduction:reproduction_conclusion_recommendation:"
        "skill:reproduction_conclusion_closure"
    )
    candidates = [row for row in bindings if row["bindingId"] == binding_id]
    binding = max(candidates, key=lambda row: row["bindingVersion"])
    assert (binding["bindingVersion"], binding["definitionVersion"]) == (4, 4)

    source_rows = {
        row["targetField"]: row
        for row in binding["inputSources"]["fields"]
    }
    assert source_rows["reproduction_conclusion_draft"]["sourcePresence"] == "optional"
    assert source_rows["reproduction_deviation_diagnosis"]["sourcePresence"] == "optional"
    assert {
        target
        for target, row in source_rows.items()
        if row["sourcePresence"] == "required"
    } == {
        "reproduction_baseline_and_criteria",
        "reproduction_experiment_design",
        "reproduction_execution_record",
        "actual_metric_records",
        "reproduction_comparison_judgment",
    }
    legacy = next(
        row
        for row in candidates
        if row["bindingVersion"] == 1 and row["definitionVersion"] == 3
    )
    legacy_draft = next(
        row
        for row in legacy["inputSources"]["fields"]
        if row["targetField"] == "reproduction_conclusion_draft"
    )
    assert legacy_draft["sourcePresence"] == "required"

    comparison = {
        "overall_verdict": "indeterminate",
        "comparability": {"status": "not_comparable", "conditions": {}},
        "metric_judgments": [],
        "condition_mismatches": [],
        "rule_application": {},
        "evidence_refs": ["comparison-evidence-1"],
        "unknowns": [],
    }
    documents = {
        "reproduction_baseline_and_criteria": {
            "status": "complete",
            "reproduction_target": {
                "claim": "Frozen claim",
                "implementation": "Frozen implementation",
                "scope": "Frozen scope",
            },
            "reference_sources": [],
            "reference_metric_records": [],
            "reference_metric_comparison_requests": [],
            "comparison_criteria": [],
            "unknowns": [],
            "conflicts": [],
        },
        "reproduction_experiment_design": {
            "status": "ready",
            "target_experiments": [],
            "protocol": {},
            "resource_acquisition_requests": [],
            "requirements": [],
            "verify_imports": [],
            "argv": [],
            "environment_variables": {},
            "actual_metric_requests": [],
            "resource_and_cost_boundary": {},
            "source_required_facts": [],
            "system_selected_details": [],
            "project_constraints": [],
            "intentional_deviations": [],
            "unknowns": [],
        },
        "reproduction_execution_record": {
            "status": "succeeded",
            "execution": {},
            "frozen_input_consistency": {},
            "raw_output_trace": [],
            "actual_result_provenance": {},
            "partial_failures": [],
            "unknowns": [],
        },
        "actual_metric_records": {
            "status": "complete",
            "execution_record_ref": "execution-record-1",
            "metrics": [],
            "reference_values_used": False,
            "partial_failures": [],
            "unknowns": [],
        },
        "reproduction_comparison_judgment": comparison,
    }
    source_bindings = {
        (row["bindingId"], row["bindingVersion"]): row for row in bindings
    }
    artifacts = []
    for target, document in documents.items():
        source = source_rows[target]["source"]
        source_binding = source_bindings[(source["bindingId"], source["bindingVersion"])]
        artifacts.append(_artifact(source_binding, document))

    resolved = resolve_registered_skill_input(
        snapshot=snapshot,
        request=SkillInputRequest(
            task_id="task-cs-b05-only-production",
            strategy="specialist_agent",
            skill_id="reproduction_conclusion_closure",
            definition_version=1,
        ),
        input_context={"artifacts": artifacts},
        skill_binding=binding,
    )
    assert resolved == documents
    assert "reproduction_conclusion_draft" not in resolved

    result = run_python_skill_adapter(
        repo_root=ROOT,
        skill_id="reproduction_conclusion_closure",
        skill_path="skills/cs_research/reproduction-conclusion-closure",
        adapter_id="python_adapter:reproduction_conclusion_closure",
        input_payload=resolved,
    )
    assert result["verdict"] == "indeterminate"
    assert result["comparison"] == comparison
    assert result["comparison_source"] == "reproduction_comparison_judgment"
    assert result["answer"] is None
    assert result["synthesis_gaps"] == [
        "synthesis_draft_missing_or_invalid",
        "answer_missing_or_ungrounded",
    ]


def test_closure_projection_makes_accepted_segments_and_gaps_visible_once() -> None:
    declaration = yaml.safe_load(
        (SKILL_ROOT / "skill.yaml").read_text(encoding="utf-8")
    )["scientific_state_projection"]
    result = run_reproduction_conclusion_closure(_payload(draft=None))
    projected = project_validated_structured_result(
        json.dumps(result, ensure_ascii=False), declaration
    )

    assert projected["reproduction_verdict"] == "reproduced"
    assert projected["reproduction_comparison"] == result["comparison"]
    assert projected["reproduction_comparison"]["comparability"] == {
        "status": "comparable",
        "conditions": {"data": {"evidence_refs": ["condition-1", "condition-1"]}},
    }
    assert "reproduction_comparability" not in projected
    assert "reproduction_comparison_verdict" not in projected
    assert projected["reproduction_conclusion_answer"] is None
    assert projected["reproduction_synthesis_gaps"] == [
        "synthesis_draft_missing_or_invalid",
        "answer_missing_or_ungrounded",
    ]

    task = materialize_scientific_state_projection(
        task_payload={
            "taskId": "task-b05-only",
            "status": "running",
            "finishedAt": None,
            "finalResultAuthority": None,
            "scientificStates": [],
        },
        declaration=declaration,
        projected_facts=projected,
        source_artifact_id="artifact-conclusion",
        research_design_version=1,
        occurred_at=datetime(2026, 9, 30, tzinfo=timezone.utc),
    )
    facts = task["scientificStates"][-1]["scientificFacts"]
    assert facts["reproduction_comparison"] == result["comparison"]
    assert facts["reproduction_synthesis_gaps"] == result["synthesis_gaps"]
    assert facts["reproduction_isolated_synthesis_segments"] == []


def test_conclusion_preserves_model_segments_and_next_steps_without_catalog_rejection():
    draft = _draft(["摘要关联记录", ""])
    draft["answer"]["text"] = ""
    draft["next_steps"] = [_next_step("模型提供的验证编号")]
    result = run_reproduction_conclusion_closure(_payload(draft=draft))
    assert result["answer"] == draft["answer"]
    assert result["next_steps"] == draft["next_steps"]
    assert not result["isolated_synthesis_segments"]
