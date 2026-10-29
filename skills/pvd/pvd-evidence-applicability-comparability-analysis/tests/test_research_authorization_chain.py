from __future__ import annotations

import importlib.util
import json
import threading
from pathlib import Path

from jsonschema.validators import validator_for

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PATH = ROOT / "adapter" / "evidence_applicability_comparability.py"
SPEC = importlib.util.spec_from_file_location("pvd_t05_chain", ADAPTER_PATH)
assert SPEC is not None and SPEC.loader is not None
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)
OUTPUT_SCHEMA = json.loads((ROOT / "output.schema.json").read_text(encoding="utf-8"))
OUTPUT_VALIDATOR = validator_for(OUTPUT_SCHEMA)(OUTPUT_SCHEMA)


def _assert_formal_output(payload: dict) -> None:
    OUTPUT_VALIDATOR.validate(payload)


def _payload(*, two_targets: bool = False) -> dict:
    targets = [
        {
            "target_id": "target_001",
            "target_type": "experimental_condition",
            "scientific_role": "condition",
            "description": "Power-related evidence",
            "requiredness": "required",
            "completion_criteria": "Verified factor-response evidence",
            "covered_parameter_keys": ["power"],
            "comparison_dimensions": ["material", "measurement"],
            "provenance_requirements": ["source"],
        }
    ]
    if two_targets:
        targets.append(
            {
                "target_id": "target_002",
                "target_type": "outcome",
                "scientific_role": "result",
                "description": "Outcome evidence",
                "requiredness": "preferred",
                "completion_criteria": "Verified outcome evidence",
                "covered_parameter_keys": [],
                "comparison_dimensions": ["measurement"],
                "provenance_requirements": ["source"],
            }
        )
    return {
        "pvd_project_context": {
            "structured_context": {"material": "AlScN", "substrate": "Mo"},
            "recommendation_parameter_set": {"parameters": ["power", "n2", "ar", "time"]},
            "run_preferences": {},
        },
        "research_records": [
            {
                "record_id": "record_1",
                "source_id": "source_1",
                "material": {"name": "AlScN"},
                "process": {"power": "100 W"},
                "outcomes": [{"name": "response", "value": "A"}],
                "provenance_refs": ["evidence_1"],
            },
            {
                "record_id": "record_2",
                "source_id": "source_2",
                "material": {"name": "AlScN"},
                "process": {"power": "150 W"},
                "outcomes": [{"name": "response", "value": "B"}],
                "provenance_refs": ["evidence_2"],
            },
        ],
        "evidence_packet": {
            "evidence_items": [
                {"evidence_id": "evidence_1", "candidate_id": "source_1", "claim": "claim one"},
                {"evidence_id": "evidence_2", "candidate_id": "source_2", "claim": "claim two"},
            ]
        },
        "source_provenance": [
            {"source_id": "source_1", "source_type": "journal_article", "citation": "Paper 1"},
            {"source_id": "source_2", "source_type": "journal_article", "citation": "Paper 2"},
        ],
        "coverage": {"required": {"covered": 1}},
        "unresolved_gaps": ["target-system distance unknown"],
        "conditional_evidence_linkage": [],
        "support_target": {
            "contract": "pvd-evidence-schema/v1",
            "evidence_targets": targets,
            "gap_taxonomy": ["missing_condition"],
            "uncertainty_rules": ["unknown remains unknown"],
        },
    }


def _planner(record_ids=("record_1", "record_2"), target_id="target_001") -> dict:
    return {
        "planning_status": "planned",
        "selected_records": [
            {
                "record_id": record_id,
                "support_target_ids": [target_id],
                "review_focus": ["verify transfer boundary"],
                "relation_questions": [
                    {"factor": "power", "response": "response", "question": "Is direction supported?"}
                ],
            }
            for record_id in record_ids
        ],
        "global_review_focus": ["cross-system comparability"],
        "uncertainty": ["target-system distance unknown"],
    }


def _deep(evidence_ref: str, *, allowed_uses: bool = False) -> dict:
    result = {
        "decision_value": "decision_relevant",
        "decision_summary": "This record materially informs the requested factor-response judgment.",
        "key_matches": ["same material family"],
        "important_differences": ["geometry differs"],
        "measurement_compatibility": "partially_compatible",
        "transfer_limits": ["do not directly copy a numeric setting"],
        "uncertainty": {"level": "moderate", "reasons": ["geometry differs"]},
        "support_findings": [
            {
                "support_level": "directional",
                "support_basis": "The cited record contains a factor-response contrast with a geometry transfer limit.",
                "conditions": ["treat as directional only"],
                "key_matches": ["same material family"],
                "important_differences": ["geometry differs"],
                "measurement_compatibility": "partially_compatible",
                "transfer_limits": ["no direct numeric transfer"],
                "uncertainty": {"level": "moderate", "reasons": ["geometry differs"]},
                "evidence_refs_used": [evidence_ref],
            }
        ],
        "verified_relations": [
            {
                "relation_type": "matched_contrast",
                "factor": "power",
                "factor_change": "higher",
                "response": "response",
                "response_change": "changed",
                "objective_alignment": "relevant",
                "conditions": ["reported study conditions"],
                "context_factors": ["geometry"],
                "confounders": [],
                "evidence_refs_used": [evidence_ref],
            }
        ],
        "conflicts": [],
    }
    if allowed_uses:
        result["allowed_uses"] = ["trend_direction"]
    return result


def _synthesis(*, records=("record_1", "record_2"), target="target_001") -> dict:
    return {
        "analysis_status": "complete" if len(records) == 2 else "partial",
        "evidence_assessments": [
            {
                "record_id": record_id,
                "key_matches": ["same material family"],
                "important_differences": ["geometry differs"],
                "measurement_compatibility": "partially_compatible",
                "transfer_limits": ["no direct numeric transfer"],
                "uncertainty": ["geometry differs"],
            }
            for record_id in records
        ],
        "support_conclusions": [
            {
                "judgment_target": target,
                "evidence_record_refs": list(records),
                "support_level": "directional",
                "allowed_uses": ["trend_direction"],
                "conditions": ["retain target-system uncertainty"],
                "transfer_limits": ["no direct numeric transfer"],
                "conflicts": [],
                "important_unknowns": ["target-system distance unknown"],
                "rationale": "Verified record-level contrasts support direction but not numeric transfer.",
            }
        ],
        "conflicts": [],
        "important_unknowns": ["target-system distance unknown"],
        "summary": "Verified evidence supports directional use only.",
    }


def test_three_stage_chain_runs_one_deep_call_per_selected_record_and_synthesis_signs_authorization() -> None:
    lock = threading.Lock()
    calls: list[tuple[str, str | None]] = []

    def child(skill_id, parameters, **_):
        if skill_id == MOD.PLANNER_SKILL_ID:
            with lock:
                calls.append((skill_id, None))
            return _planner()
        if skill_id == MOD.DEEP_SKILL_ID:
            record_id = parameters["record"]["record_id"]
            with lock:
                calls.append((skill_id, record_id))
            return _deep("evidence_1" if record_id == "record_1" else "evidence_2")
        assert skill_id == MOD.SYNTHESIS_SKILL_ID
        with lock:
            calls.append((skill_id, None))
        assert {row["record_id"] for row in parameters["verified_record_analyses"]} == {
            "record_1",
            "record_2",
        }
        assert parameters["failed_record_verifications"] == []
        return _synthesis()

    result = MOD.run_evidence_applicability_comparability(_payload(), llm_child=child, max_workers=2)

    _assert_formal_output(result)
    assert result["support_conclusions"][0]["allowed_uses"] == ["trend_direction"]
    deep_calls = [row for row in calls if row[0] == MOD.DEEP_SKILL_ID]
    assert sorted(record for _, record in deep_calls) == ["record_1", "record_2"]
    assert len(deep_calls) == 2
    assert calls[0][0] == MOD.PLANNER_SKILL_ID
    assert calls[-1][0] == MOD.SYNTHESIS_SKILL_ID


def test_one_deep_failure_is_local_and_successful_sibling_reaches_final_synthesis() -> None:
    def child(skill_id, parameters, **_):
        if skill_id == MOD.PLANNER_SKILL_ID:
            return _planner()
        if skill_id == MOD.DEEP_SKILL_ID:
            if parameters["record"]["record_id"] == "record_2":
                raise RuntimeError("provider unavailable")
            return _deep("evidence_1")
        assert skill_id == MOD.SYNTHESIS_SKILL_ID
        assert [row["record_id"] for row in parameters["verified_record_analyses"]] == ["record_1"]
        assert parameters["failed_record_verifications"] == [
            {"record_id": "record_2", "classification": "deep_verification_unavailable"}
        ]
        return _synthesis(records=("record_1",))

    result = MOD.run_evidence_applicability_comparability(_payload(), llm_child=child, max_workers=2)

    _assert_formal_output(result)
    assert result["analysis_status"] == "partial"
    assert result["evidence_assessments"][0]["record_id"] == "record_1"
    assert result["support_conclusions"][0]["allowed_uses"] == ["trend_direction"]


def test_final_synthesis_failure_retains_verified_record_but_never_authorizes_use() -> None:
    def child(skill_id, parameters, **_):
        if skill_id == MOD.PLANNER_SKILL_ID:
            return _planner()
        if skill_id == MOD.DEEP_SKILL_ID:
            record_id = parameters["record"]["record_id"]
            return _deep("evidence_1" if record_id == "record_1" else "evidence_2")
        raise RuntimeError("synthesis provider unavailable")

    result = MOD.run_evidence_applicability_comparability(_payload(), llm_child=child)

    _assert_formal_output(result)
    assert result["analysis_status"] == "partial"
    assert [row["record_id"] for row in result["evidence_assessments"]] == ["record_1", "record_2"]
    assert result["support_conclusions"] == []
    assert "不形成任何下游证据使用授权" in result["summary"]






def test_child_contracts_are_valid_and_deep_contract_has_no_allowed_uses_field() -> None:
    child_dirs = [
        ROOT.parent / "pvd-evidence-applicability-global-planning",
        ROOT.parent / "pvd-evidence-record-deep-verification",
        ROOT.parent / "pvd-evidence-applicability-final-synthesis",
    ]
    for child_dir in child_dirs:
        for name in ("input.schema.json", "output.schema.json"):
            schema = json.loads((child_dir / name).read_text(encoding="utf-8"))
            validator_for(schema).check_schema(schema)

    deep_schema = json.loads(
        (ROOT.parent / "pvd-evidence-record-deep-verification" / "output.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert "allowed_uses" not in json.dumps(deep_schema, ensure_ascii=False)

def test_positive_authorization_materializes_stable_conclusion_and_exact_evidence_provenance() -> None:
    def child(skill_id, parameters, **_):
        if skill_id == MOD.PLANNER_SKILL_ID:
            return _planner(record_ids=("record_1",), target_id="target_001")
        if skill_id == MOD.DEEP_SKILL_ID:
            return _deep("evidence_1")
        return _synthesis(records=("record_1",), target="target_001")

    result = MOD.run_evidence_applicability_comparability(_payload(), llm_child=child)

    conclusion = result["support_conclusions"][0]
    assert conclusion["conclusion_id"] == "support_conclusion:target_001"
    assert conclusion["judgment_target"] == "target_001"
    assert conclusion["evidence_record_refs"] == ["record_1"]
    assert conclusion["evidence_refs"] == ["evidence_1"]
    assert conclusion["transfer_limits"] == ["no direct numeric transfer"]
    assert conclusion["important_unknowns"] == ["target-system distance unknown"]


def test_planner_underselection_backfills_every_uncovered_source() -> None:
    """Production observed 1-of-16 selection; the runtime must restore coverage."""
    lock = threading.Lock()
    deep_records: list[str] = []
    backfill_rows: list[list[str]] = []

    def child(skill_id, parameters, **_):
        if skill_id == MOD.PLANNER_SKILL_ID:
            return _planner(record_ids=("record_1",), target_id="target_001")
        if skill_id == MOD.DEEP_SKILL_ID:
            record_id = parameters["record"]["record_id"]
            with lock:
                deep_records.append(record_id)
                if any(
                    "planner_underselection_backfill" in focus
                    for focus in parameters["review_focus"]
                ):
                    backfill_rows.append([record_id])
            return _deep("evidence_1" if record_id == "record_1" else "evidence_2")
        assert skill_id == MOD.SYNTHESIS_SKILL_ID
        assert {row["record_id"] for row in parameters["verified_record_analyses"]} == {
            "record_1",
            "record_2",
        }
        assert "planner_underselection_backfill:1" in parameters["planning"]["uncertainty"]
        return _synthesis()

    result = MOD.run_evidence_applicability_comparability(
        _payload(), llm_child=child, max_workers=2
    )

    _assert_formal_output(result)
    assert sorted(deep_records) == ["record_1", "record_2"]
    assert backfill_rows == [["record_2"]]


def test_planner_overselection_is_truncated_to_the_runtime_bound() -> None:
    payload = _payload()
    payload["research_records"] = [
        {
            "record_id": f"record_{index:02d}",
            "source_id": f"source_{index:02d}",
            "material": {"name": "AlScN"},
            "process": {"power": "100 W"},
            "provenance_refs": [f"evidence_{index:02d}"],
        }
        for index in range(12)
    ]
    payload["evidence_packet"]["evidence_items"] = [
        {"evidence_id": f"evidence_{index:02d}", "candidate_id": f"source_{index:02d}"}
        for index in range(12)
    ]
    payload["source_provenance"] = [
        {"source_id": f"source_{index:02d}", "source_type": "journal_article"}
        for index in range(12)
    ]
    deep_count = {"n": 0}

    def child(skill_id, parameters, **_):
        if skill_id == MOD.PLANNER_SKILL_ID:
            return _planner(record_ids=tuple(f"record_{index:02d}" for index in range(12)))
        if skill_id == MOD.DEEP_SKILL_ID:
            deep_count["n"] += 1
            return _deep(parameters["record"]["provenance_refs"][0])
        assert skill_id == MOD.SYNTHESIS_SKILL_ID
        return _synthesis(records=tuple(f"record_{index:02d}" for index in range(10)))

    MOD.run_evidence_applicability_comparability(payload, llm_child=child, max_workers=2)

    assert deep_count["n"] == MOD.MAX_DEEP_RECORDS


def test_backfill_selects_the_most_informative_record_of_an_uncovered_source() -> None:
    payload = _payload()
    payload["research_records"] = [
        {
            "record_id": "record_1",
            "source_id": "source_1",
            "material": {"name": "AlScN"},
            "provenance_refs": ["evidence_1"],
        },
        {
            "record_id": "record_thin",
            "source_id": "source_2",
            "provenance_refs": ["evidence_2"],
        },
        {
            "record_id": "record_rich",
            "source_id": "source_2",
            "process": {
                "power": {"fact_status": "known", "value": 200, "provenance_refs": ["evidence_2"]}
            },
            "outcomes": [{"metric": "FWHM", "value": "1.9 deg"}],
            "provenance_refs": ["evidence_2"],
        },
    ]
    payload["evidence_packet"]["evidence_items"] = [
        {"evidence_id": "evidence_1", "candidate_id": "source_1"},
        {"evidence_id": "evidence_2", "candidate_id": "source_2"},
    ]
    deep_records: list[str] = []

    def child(skill_id, parameters, **_):
        if skill_id == MOD.PLANNER_SKILL_ID:
            return _planner(record_ids=("record_1",))
        if skill_id == MOD.DEEP_SKILL_ID:
            deep_records.append(parameters["record"]["record_id"])
            return _deep(parameters["record"]["provenance_refs"][0])
        return _synthesis(records=("record_1", "record_rich"))

    MOD.run_evidence_applicability_comparability(payload, llm_child=child)

    assert sorted(deep_records) == ["record_1", "record_rich"]
