from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.modules.pvd_scientific_state_materialization import (
    materialize_experiment_feedback,
    materialize_experimental_system_context,
    materialize_research_request_understanding,
    materialize_round_experiment_recommendation,
)
from jsonschema import Draft202012Validator


def _context(target: str | None = None) -> dict:
    target_known = target is not None
    def _metadata(*, known: bool) -> dict:
        if known:
            return {
                "status": "known",
                "provenance": [
                    {
                        "source_id": "user-input-1",
                        "raw_expression": str(target),
                    }
                ],
            }
        return {
            "status": "missing",
            "reason": "not reported",
            "provenance": [],
        }

    return {
        "contract": "pvd-experimental-system-context/v1",
        "context_status": "partial",
        "target_material": target,
        "substrate": None,
        "target_substrate_distance_mm": None,
        "power_supply_temperature": None,
        "target_angle": None,
        "field_metadata": {
            "target_material": _metadata(known=target_known),
            "substrate": _metadata(known=False),
            "target_substrate_distance_mm": _metadata(known=False),
            "power_supply_temperature": _metadata(known=False),
            "target_angle": _metadata(known=False),
        },
        "additional_facts": [],
    }


def _base_payload() -> dict:
    return {
        "taskId": "task-pvd",
        "status": "running",
        "finishedAt": None,
        "scientificStates": [],
        "finalResultAuthority": None,
    }


def _validate_frozen_pvd_facts(facts: dict) -> None:
    repo = Path(__file__).parents[3]
    state_schema = json.loads(
        (repo / "schemas" / "pvd" / "pvd-scientific-state-facts-v1.schema.json")
        .read_text(encoding="utf-8")
    )
    errors = list(Draft202012Validator(state_schema).iter_errors(facts))
    assert not errors, [error.message for error in errors]


def test_initial_context_creates_one_canonical_state() -> None:
    result = materialize_experimental_system_context(
        task_payload=_base_payload(),
        projected_facts={"experimental_system_context": _context()},
        source_artifact_id="artifact-node1-a",
        research_design_version=1,
        occurred_at=datetime(2026, 8, 31, tzinfo=UTC),
    )

    assert len(result["scientificStates"]) == 1
    state = result["scientificStates"][0]
    facts = state["scientificFacts"]
    assert state["scientificStateVersion"] == 1
    assert state["sourceRefs"] == ["artifact-node1-a"]
    assert len(facts["experimental_system_contexts"]) == 1
    assert facts["experiment_history"] == []
    assert (
        facts["current_experimental_system_context_ref"]
        == facts["experimental_system_contexts"][0][
            "experimental_system_context_ref"
        ]
    )
    _validate_frozen_pvd_facts(facts)


def test_initial_context_appends_after_formal_empty_scientific_state() -> None:
    payload = _base_payload()
    payload["scientificStates"] = [
        {
            "scientificStateVersion": 1,
            "researchDesignVersion": 1,
            "scientificFacts": {},
            "executionState": {},
            "sourceRefs": [],
            "createdAt": "2026-08-31T00:00:00+00:00",
        }
    ]

    result = materialize_experimental_system_context(
        task_payload=payload,
        projected_facts={"experimental_system_context": _context()},
        source_artifact_id="artifact-node1-a",
        research_design_version=1,
        occurred_at=datetime(2026, 8, 31, 0, 1, tzinfo=UTC),
    )

    assert [
        state["scientificStateVersion"] for state in result["scientificStates"]
    ] == [1, 2]
    assert result["scientificStates"][0] == payload["scientificStates"][0]
    current = result["scientificStates"][-1]
    assert current["researchDesignVersion"] == 1
    assert current["executionState"] == {}
    assert current["sourceRefs"] == ["artifact-node1-a"]
    _validate_frozen_pvd_facts(current["scientificFacts"])


def test_same_context_reuses_identity_without_new_state_version() -> None:
    first = materialize_experimental_system_context(
        task_payload=_base_payload(),
        projected_facts={"experimental_system_context": _context()},
        source_artifact_id="artifact-node1-a",
        research_design_version=1,
        occurred_at=datetime(2026, 8, 31, tzinfo=UTC),
    )
    second = materialize_experimental_system_context(
        task_payload=first,
        projected_facts={"experimental_system_context": _context()},
        source_artifact_id="artifact-node1-b",
        research_design_version=1,
        occurred_at=datetime(2026, 8, 31, 0, 1, tzinfo=UTC),
    )

    assert len(second["scientificStates"]) == 1
    assert (
        second["scientificStates"][0]["scientificFacts"]
        == first["scientificStates"][0]["scientificFacts"]
    )


def test_changed_context_appends_context_and_preserves_history() -> None:
    first = materialize_experimental_system_context(
        task_payload=_base_payload(),
        projected_facts={"experimental_system_context": _context("AlSc")},
        source_artifact_id="artifact-node1-a",
        research_design_version=1,
        occurred_at=datetime(2026, 8, 31, tzinfo=UTC),
    )
    first["scientificStates"][0]["scientificFacts"]["experiment_history"].append(
        {
            "experiment_ref": "experiment-1",
            "round_ref": None,
            "experimental_system_context_ref": first["scientificStates"][0][
                "scientificFacts"
            ]["current_experimental_system_context_ref"],
            "system_recommendation": None,
            "planned_execution": None,
            "actual_execution": None,
            "measurements": [],
            "process_events": [],
        }
    )

    second = materialize_experimental_system_context(
        task_payload=first,
        projected_facts={"experimental_system_context": _context("AlN")},
        source_artifact_id="artifact-node1-b",
        research_design_version=1,
        occurred_at=datetime(2026, 8, 31, 0, 1, tzinfo=UTC),
    )

    assert [row["scientificStateVersion"] for row in second["scientificStates"]] == [1, 2]
    latest = second["scientificStates"][-1]["scientificFacts"]
    assert len(latest["experimental_system_contexts"]) == 2
    assert latest["experiment_history"] == first["scientificStates"][0]["scientificFacts"][
        "experiment_history"
    ]
    assert latest["current_experimental_system_context_ref"] != first[
        "scientificStates"
    ][0]["scientificFacts"]["current_experimental_system_context_ref"]
    _validate_frozen_pvd_facts(latest)


def test_unrelated_existing_scientific_facts_are_preserved() -> None:
    payload = _base_payload()
    payload["scientificStates"] = [
        {
            "scientificStateVersion": 1,
            "researchDesignVersion": 1,
            "scientificFacts": {
                "evidence_supported_conclusions": [{"statement": "keep"}],
                "evidence_gaps": [{"gap": "keep-gap"}],
            },
            "executionState": {},
            "sourceRefs": ["artifact-old"],
            "createdAt": "2026-08-31T00:00:00+00:00",
        }
    ]

    result = materialize_experimental_system_context(
        task_payload=payload,
        projected_facts={"experimental_system_context": _context()},
        source_artifact_id="artifact-node1",
        research_design_version=1,
        occurred_at=datetime(2026, 8, 31, 0, 1, tzinfo=UTC),
    )

    facts = result["scientificStates"][-1]["scientificFacts"]
    assert facts["evidence_supported_conclusions"] == [{"statement": "keep"}]
    assert facts["evidence_gaps"] == [{"gap": "keep-gap"}]
    _validate_frozen_pvd_facts(facts)


def test_partial_existing_pvd_fact_slice_fails_closed() -> None:
    payload = _base_payload()
    payload["scientificStates"] = [
        {
            "scientificStateVersion": 1,
            "researchDesignVersion": 1,
            "scientificFacts": {
                "evidence_supported_conclusions": [{"statement": "keep"}],
                "experimental_system_contexts": [],
            },
            "executionState": {},
            "sourceRefs": ["artifact-old"],
            "createdAt": "2026-08-31T00:00:00+00:00",
        }
    ]

    with pytest.raises(
        ValueError,
        match="pvd_scientific_state_existing_facts_invalid",
    ):
        materialize_experimental_system_context(
            task_payload=payload,
            projected_facts={"experimental_system_context": _context()},
            source_artifact_id="artifact-node1",
            research_design_version=1,
            occurred_at=datetime(2026, 8, 31, 0, 1, tzinfo=UTC),
        )



def _task_with_context() -> dict:
    return materialize_experimental_system_context(
        task_payload=_base_payload(),
        projected_facts={"experimental_system_context": _context("Ti")},
        source_artifact_id="artifact-context",
        research_design_version=1,
        occurred_at=datetime(2026, 9, 25, tzinfo=UTC),
    )


def _recommendation(*, experiment_id: str, bias: int) -> dict:
    return {
        "pvd_recommendation_decision_status": "recommendation",
        "pvd_recommendation_first_run_ranking_status": "ranked",
        "pvd_recommendation_experiments": [
            {
                "experiment_id": experiment_id,
                "priority": 1,
                "parameters": [
                    {
                        "parameter_key": "substrate_bias",
                        "display_name": "Substrate bias",
                        "recommended_value": bias,
                        "unit": "V",
                        "candidate_direction": "decrease",
                        "admission_decision": "release",
                        "direction_ref": "direction:substrate_bias",
                        "confidence": "medium",
                        "rationale": "Artifact-only analysis detail",
                        "basis_refs": ["evidence:1"],
                    }
                ],
                "design_rationale": "Artifact-only portfolio rationale",
                "validation_goal": "Test whether stress decreases without losing texture.",
                "failure_signal": "Texture quality falls below the accepted boundary.",
                "feasibility_conditions": ["Keep substrate temperature fixed."],
                "recommendation_risks": ["May reduce deposition rate."],
                "candidate_lineage_refs": ["candidate:1"],
            }
        ],
        "pvd_recommendation_blocked_experiments": [
            {
                "experiment_id": "experiment-blocked",
                "reason_codes": ["hard_boundary_violation:substrate_bias:minimum"],
            }
        ],
        "pvd_recommendation_next_step": (
            "Run the recommended experiment and measure stress and texture."
        ),
        "pvd_recommendation_important_unknowns": [
            "The stress response at the recommended bias remains unmeasured."
        ],
    }


def _no_recommendation(*, unknown: str, next_step: str) -> dict:
    result = {
        "decision_status": "no_recommendation",
        "first_run_ranking_status": "not_applicable",
        "experiment_count": 0,
        "experiments": [],
        "blocked_experiments": [],
        "limited_resource_first_to_run_experiment_ids": [],
        "direction_change_signals": [],
        "next_step": next_step,
        "shared_conditions": [],
        "shared_risks": [],
        "important_unknowns": [unknown],
        "portfolio_note": "Evidence is insufficient for a parameter recommendation.",
        "global_optimum_claimed": False,
        "audit": {},
    }
    repo = Path(__file__).parents[3]
    output_schema = json.loads(
        (
            repo
            / "skills"
            / "pvd"
            / "pvd-round-experiment-plan-parameter-recommendation"
            / "output.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator(output_schema).validate(result)
    return {
        "pvd_recommendation_decision_status": result["decision_status"],
        "pvd_recommendation_first_run_ranking_status": result[
            "first_run_ranking_status"
        ],
        "pvd_recommendation_experiments": result["experiments"],
        "pvd_recommendation_blocked_experiments": result["blocked_experiments"],
        "pvd_recommendation_next_step": result["next_step"],
        "pvd_recommendation_important_unknowns": result["important_unknowns"],
    }


def test_recommendation_materializes_dynamic_system_recommendation_only() -> None:
    task = materialize_round_experiment_recommendation(
        task_payload=_task_with_context(),
        projected_facts=_recommendation(experiment_id="experiment-bias-1", bias=-50),
        source_artifact_id="artifact-recommendation-a",
        research_design_version=1,
        occurred_at=datetime(2026, 9, 25, 0, 1, tzinfo=UTC),
    )

    state = task["scientificStates"][-1]
    assert state["scientificFacts"]["current_recommendation_decision"] == {
        "decision_status": "recommendation",
        "first_run_ranking_status": "ranked",
        "blocked_experiments": [
            {
                "experiment_id": "experiment-blocked",
                "reason_codes": ["hard_boundary_violation:substrate_bias:minimum"],
            }
        ],
        "important_unknowns": [
            "The stress response at the recommended bias remains unmeasured."
        ],
        "next_step": "Run the recommended experiment and measure stress and texture.",
        "provenance": {
            "origin": "system",
            "source_ref": "artifact-recommendation-a",
        },
    }
    record = state["scientificFacts"]["experiment_history"][0]
    assert record == {
        "experiment_ref": "experiment-bias-1",
        "round_ref": None,
        "experimental_system_context_ref": state["scientificFacts"][
            "current_experimental_system_context_ref"
        ],
        "system_recommendation": {
            "parameters": [
                {
                    "parameter_key": "substrate_bias",
                    "display_name": "Substrate bias",
                    "recommended_value": -50,
                    "unit": "V",
                }
            ],
            "validation_goal": "Test whether stress decreases without losing texture.",
            "failure_signal": "Texture quality falls below the accepted boundary.",
            "next_step": "Run the recommended experiment and measure stress and texture.",
            "provenance": {
                "origin": "system",
                "source_ref": "artifact-recommendation-a",
            },
        },
        "planned_execution": None,
        "actual_execution": None,
        "measurements": [],
        "process_events": [],
    }
    serialized = json.dumps(state["scientificFacts"], ensure_ascii=False)
    for undeclared_analysis in (
        "Artifact-only analysis detail",
        "Artifact-only portfolio rationale",
        "Keep substrate temperature fixed.",
        "May reduce deposition rate.",
        "candidate:1",
    ):
        assert undeclared_analysis not in serialized
    _validate_frozen_pvd_facts(state["scientificFacts"])


def test_no_recommendation_updates_decision_without_fabricating_experiment() -> None:
    recommended = materialize_round_experiment_recommendation(
        task_payload=_task_with_context(),
        projected_facts=_recommendation(
            experiment_id="experiment-existing",
            bias=-50,
        ),
        source_artifact_id="artifact-recommendation-existing",
        research_design_version=1,
        occurred_at=datetime(2026, 9, 25, 0, 1, tzinfo=UTC),
    )
    existing_history = deepcopy(
        recommended["scientificStates"][-1]["scientificFacts"]["experiment_history"]
    )

    result = materialize_round_experiment_recommendation(
        task_payload=recommended,
        projected_facts=_no_recommendation(
            unknown="Chamber pressure stability has not been measured.",
            next_step="Measure chamber pressure stability.",
        ),
        source_artifact_id="artifact-no-recommendation-pressure",
        research_design_version=1,
        occurred_at=datetime(2026, 9, 25, 0, 2, tzinfo=UTC),
    )

    facts = result["scientificStates"][-1]["scientificFacts"]
    assert facts["current_recommendation_decision"] == {
        "decision_status": "no_recommendation",
        "first_run_ranking_status": "not_applicable",
        "blocked_experiments": [],
        "important_unknowns": [
            "Chamber pressure stability has not been measured."
        ],
        "next_step": "Measure chamber pressure stability.",
        "provenance": {
            "origin": "system",
            "source_ref": "artifact-no-recommendation-pressure",
        },
    }
    assert facts["experiment_history"] == existing_history
    assert len(facts["experiment_history"]) == 1
    assert facts["experiment_history"][0]["planned_execution"] is None
    assert facts["experiment_history"][0]["actual_execution"] is None
    assert facts["experiment_history"][0]["measurements"] == []
    _validate_frozen_pvd_facts(facts)


def test_different_no_recommendations_create_different_states() -> None:
    initial = _task_with_context()
    pressure = materialize_round_experiment_recommendation(
        task_payload=initial,
        projected_facts=_no_recommendation(
            unknown="Pressure is unknown.",
            next_step="Measure pressure.",
        ),
        source_artifact_id="artifact-no-recommendation-pressure",
        research_design_version=1,
        occurred_at=datetime(2026, 9, 25, 0, 1, tzinfo=UTC),
    )
    temperature = materialize_round_experiment_recommendation(
        task_payload=initial,
        projected_facts=_no_recommendation(
            unknown="Temperature is unknown.",
            next_step="Measure temperature.",
        ),
        source_artifact_id="artifact-no-recommendation-temperature",
        research_design_version=1,
        occurred_at=datetime(2026, 9, 25, 0, 2, tzinfo=UTC),
    )

    pressure_facts = pressure["scientificStates"][-1]["scientificFacts"]
    temperature_facts = temperature["scientificStates"][-1]["scientificFacts"]
    assert pressure_facts != temperature_facts
    assert pressure_facts["experiment_history"] == []
    assert temperature_facts["experiment_history"] == []
    _validate_frozen_pvd_facts(pressure_facts)
    _validate_frozen_pvd_facts(temperature_facts)


def test_invalid_recommendation_projection_does_not_mutate_existing_state() -> None:
    task = _task_with_context()
    before = deepcopy(task)
    invalid_projection = _no_recommendation(
        unknown="Pressure is unknown.",
        next_step="Measure pressure.",
    )
    invalid_projection.pop("pvd_recommendation_important_unknowns")

    result = materialize_round_experiment_recommendation(
        task_payload=task,
        projected_facts=invalid_projection,
        source_artifact_id="artifact-invalid",
        research_design_version=1,
        occurred_at=datetime(2026, 9, 25, 0, 1, tzinfo=UTC),
    )
    assert result == before
    assert task == before


def test_different_recommendation_results_create_different_states() -> None:
    initial = _task_with_context()
    first = materialize_round_experiment_recommendation(
        task_payload=initial,
        projected_facts=_recommendation(experiment_id="experiment-bias-a", bias=-25),
        source_artifact_id="artifact-recommendation-a",
        research_design_version=1,
        occurred_at=datetime(2026, 9, 25, 0, 1, tzinfo=UTC),
    )
    second = materialize_round_experiment_recommendation(
        task_payload=initial,
        projected_facts=_recommendation(experiment_id="experiment-bias-b", bias=-75),
        source_artifact_id="artifact-recommendation-b",
        research_design_version=1,
        occurred_at=datetime(2026, 9, 25, 0, 2, tzinfo=UTC),
    )
    assert first["scientificStates"][-1]["scientificFacts"] != second[
        "scientificStates"
    ][-1]["scientificFacts"]


def test_recommendation_replay_is_idempotent_and_conflict_does_not_overwrite() -> None:
    first = materialize_round_experiment_recommendation(
        task_payload=_task_with_context(),
        projected_facts=_recommendation(experiment_id="experiment-bias-1", bias=-50),
        source_artifact_id="artifact-recommendation-a",
        research_design_version=1,
        occurred_at=datetime(2026, 9, 25, 0, 1, tzinfo=UTC),
    )
    replayed = materialize_round_experiment_recommendation(
        task_payload=first,
        projected_facts=_recommendation(experiment_id="experiment-bias-1", bias=-50),
        source_artifact_id="artifact-recommendation-a",
        research_design_version=1,
        occurred_at=datetime(2026, 9, 25, 0, 2, tzinfo=UTC),
    )
    assert replayed == first

    before_conflict = deepcopy(first)
    with pytest.raises(ValueError, match="pvd_recommendation_conflict"):
        materialize_round_experiment_recommendation(
            task_payload=first,
            projected_facts=_recommendation(experiment_id="experiment-bias-1", bias=-75),
            source_artifact_id="artifact-recommendation-b",
            research_design_version=1,
            occurred_at=datetime(2026, 9, 25, 0, 3, tzinfo=UTC),
        )
    assert first == before_conflict


def test_research_request_materializes_dynamic_query_history_and_skips_planned_rounds() -> None:
    structured_context = {
        "experimental_system_context": _context("Ti"),
        "rounds": [
            {
                "status": "measured",
                "round_ref": None,
                "source_record_ref": "pvd-query-source-record:sha256:111111111111111111111111",
                "actual_execution": [
                    {
                        "parameter_key": "power",
                        "value_type": "continuous",
                        "value": 100,
                        "unit": "W",
                        "unit_identity": "power:W",
                        "unit_dimension": "power",
                    },
                    {
                        "parameter_key": "pulse_count",
                        "value_type": "integer",
                        "value": 10,
                        "unit": "cycle",
                        "unit_identity": "count:cycle",
                        "unit_dimension": "count",
                    },
                    {
                        "parameter_key": "target_type",
                        "value_type": "categorical",
                        "value": "A",
                        "unit": None,
                        "unit_identity": None,
                        "unit_dimension": None,
                    },
                    {
                        "parameter_key": "bias_enabled",
                        "value_type": "boolean",
                        "value": True,
                        "unit": None,
                        "unit_identity": None,
                        "unit_dimension": None,
                    },
                ],
                "measurements": [
                    {
                        "metric": "resistivity",
                        "quantity": {"value_numeric": 2.1, "value_raw": "2.1"},
                    }
                ],
            },
            {
                "status": "planned",
                "round_ref": None,
                "source_record_ref": "pvd-query-source-record:sha256:222222222222222222222222",
                "actual_execution": [],
                "measurements": [],
            },
        ],
    }

    result = materialize_research_request_understanding(
        task_payload=_base_payload(),
        projected_facts={"pvd_research_request_context": structured_context},
        source_artifact_id="artifact-request-a",
        research_design_version=1,
        occurred_at=datetime(2026, 9, 16, tzinfo=UTC),
    )

    facts = result["scientificStates"][-1]["scientificFacts"]
    assert len(facts["experiment_history"]) == 1
    record = facts["experiment_history"][0]
    assert record["planned_execution"] is None
    assert record["actual_execution"]["status"] == "executed"
    assert [
        row["parameter_key"]
        for row in record["actual_execution"]["parameters"]
    ] == ["power", "pulse_count", "target_type", "bias_enabled"]
    assert record["measurements"][0]["value"] == 2.1
    assert record["measurements"][0]["validity"] == "unknown"
    _validate_frozen_pvd_facts(facts)

    replay = materialize_research_request_understanding(
        task_payload=result,
        projected_facts={"pvd_research_request_context": structured_context},
        source_artifact_id="artifact-request-a",
        research_design_version=1,
        occurred_at=datetime(2026, 9, 16, 0, 1, tzinfo=UTC),
    )
    assert len(replay["scientificStates"]) == 1
    assert len(replay["scientificStates"][0]["scientificFacts"]["experiment_history"]) == 1


def test_identical_physical_experiments_remain_distinct_and_replay_is_idempotent() -> None:
    actual_execution = [
        {
            "parameter_key": "power",
            "value_type": "continuous",
            "value": 100,
            "unit": "W",
            "unit_identity": "power:W",
            "unit_dimension": "power",
        }
    ]
    measurements = [
        {
            "metric": "resistivity",
            "quantity": {"value_numeric": 2.1, "value_raw": "2.1"},
        }
    ]
    structured_context = {
        "experimental_system_context": _context("Ti"),
        "rounds": [
            {
                "status": "measured",
                "round_ref": None,
                "source_record_ref": "pvd-query-source-record:sha256:aaaaaaaaaaaaaaaaaaaaaaaa",
                "actual_execution": actual_execution,
                "measurements": measurements,
            },
            {
                "status": "measured",
                "round_ref": None,
                "source_record_ref": "pvd-query-source-record:sha256:bbbbbbbbbbbbbbbbbbbbbbbb",
                "actual_execution": actual_execution,
                "measurements": measurements,
            },
        ],
    }

    first = materialize_research_request_understanding(
        task_payload=_base_payload(),
        projected_facts={"pvd_research_request_context": structured_context},
        source_artifact_id="artifact-request-a",
        research_design_version=1,
        occurred_at=datetime(2026, 9, 16, tzinfo=UTC),
    )
    history = first["scientificStates"][-1]["scientificFacts"]["experiment_history"]

    assert len(history) == 2
    assert len({row["experiment_ref"] for row in history}) == 2
    assert all(row["experiment_ref"].startswith("pvd-experiment:") for row in history)
    assert [row["actual_execution"]["provenance"]["source_ref"] for row in history] == [
        "pvd-query-source-record:sha256:aaaaaaaaaaaaaaaaaaaaaaaa",
        "pvd-query-source-record:sha256:bbbbbbbbbbbbbbbbbbbbbbbb",
    ]

    replay = materialize_research_request_understanding(
        task_payload=first,
        projected_facts={"pvd_research_request_context": structured_context},
        source_artifact_id="artifact-request-b",
        research_design_version=1,
        occurred_at=datetime(2026, 9, 16, 0, 1, tzinfo=UTC),
    )

    assert len(replay["scientificStates"]) == 1
    assert replay["scientificStates"][0]["scientificFacts"]["experiment_history"] == history


def test_same_source_experiment_cannot_silently_change_on_replay() -> None:
    source_ref = "pvd-query-source-record:sha256:cccccccccccccccccccccccc"
    structured_context = {
        "experimental_system_context": _context("Ti"),
        "rounds": [
            {
                "status": "measured",
                "round_ref": None,
                "source_record_ref": source_ref,
                "actual_execution": [
                    {
                        "parameter_key": "power",
                        "value_type": "continuous",
                        "value": 100,
                        "unit": "W",
                        "unit_identity": "power:W",
                        "unit_dimension": "power",
                    }
                ],
                "measurements": [
                    {
                        "metric": "resistivity",
                        "quantity": {"value_numeric": 2.1, "value_raw": "2.1"},
                    }
                ],
            }
        ],
    }
    first = materialize_research_request_understanding(
        task_payload=_base_payload(),
        projected_facts={"pvd_research_request_context": structured_context},
        source_artifact_id="artifact-request-a",
        research_design_version=1,
        occurred_at=datetime(2026, 9, 16, tzinfo=UTC),
    )
    changed = json.loads(json.dumps(structured_context))
    changed["rounds"][0]["measurements"][0]["quantity"] = {
        "value_numeric": 2.2,
        "value_raw": "2.2",
    }

    with pytest.raises(ValueError, match="pvd_research_request_history_conflict"):
        materialize_research_request_understanding(
            task_payload=first,
            projected_facts={"pvd_research_request_context": changed},
            source_artifact_id="artifact-request-b",
            research_design_version=1,
            occurred_at=datetime(2026, 9, 16, 0, 1, tzinfo=UTC),
        )


def _recommended_task() -> dict:
    return materialize_round_experiment_recommendation(
        task_payload=_task_with_context(),
        projected_facts=_recommendation(experiment_id='experiment-feedback-1', bias=-50),
        source_artifact_id='artifact-recommendation-feedback',
        research_design_version=1,
        occurred_at=datetime(2026, 9, 26, tzinfo=UTC),
    )


def _parameter(value: int, source_ref: str) -> dict:
    return {
        'parameter_key': 'substrate_bias',
        'value_type': 'integer',
        'value': value,
        'unit': 'V',
        'unit_identity': 'voltage:V',
        'unit_dimension': 'voltage',
        'provenance': {'origin': 'user', 'source_ref': source_ref},
    }


def _feedback(
    *,
    source_ref: str,
    planned: int | None = None,
    actual: int | None = None,
    measurement: dict | None = None,
) -> dict:
    provenance = {'origin': 'user', 'source_ref': source_ref}
    return {
        'experiment_ref': 'experiment-feedback-1',
        'provenance': provenance,
        'planned_execution': (
            {
                'parameters': [_parameter(planned, source_ref)],
                'other_process_conditions': [],
                'provenance': provenance,
            }
            if planned is not None
            else None
        ),
        'actual_execution': (
            {
                'execution_ref': 'execution-feedback-1',
                'status': 'executed',
                'validity': 'valid',
                'parameters': [_parameter(actual, source_ref)],
                'other_process_conditions': [],
                'provenance': provenance,
            }
            if actual is not None
            else None
        ),
        'measurements': [measurement] if measurement is not None else [],
        'process_events': [],
    }


def test_feedback_preserves_recommendation_plan_actual_layers() -> None:
    recommended = _recommended_task()
    planned = materialize_experiment_feedback(
        task_payload=recommended,
        feedback_facts=_feedback(source_ref='user-plan-1', planned=-45),
        source_ref='user-plan-1',
        research_design_version=1,
        occurred_at=datetime(2026, 9, 26, 0, 1, tzinfo=UTC),
    )
    record = planned['scientificStates'][-1]['scientificFacts']['experiment_history'][0]

    assert record['system_recommendation']['parameters'][0]['recommended_value'] == -50
    assert record['planned_execution']['parameters'][0]['value'] == -45
    assert record['actual_execution'] is None
    assert record['measurements'] == []
    assert len(planned['scientificStates']) == len(recommended['scientificStates']) + 1


def test_followup_feedback_updates_same_experiment_in_new_state_version() -> None:
    planned = materialize_experiment_feedback(
        task_payload=_recommended_task(),
        feedback_facts=_feedback(source_ref='user-plan-1', planned=-45),
        source_ref='user-plan-1',
        research_design_version=1,
        occurred_at=datetime(2026, 9, 26, 0, 1, tzinfo=UTC),
    )
    measurement = {
        'measurement_ref': 'measurement-feedback-1',
        'metric_key': 'residual_stress',
        'value': 1.7,
        'unit': 'GPa',
        'status': 'final',
        'validity': 'valid',
        'method': 'wafer_curvature',
        'provenance': {'origin': 'user', 'source_ref': 'user-result-1'},
    }
    updated = materialize_experiment_feedback(
        task_payload=planned,
        feedback_facts=_feedback(
            source_ref='user-result-1',
            actual=-42,
            measurement=measurement,
        ),
        source_ref='user-result-1',
        research_design_version=1,
        occurred_at=datetime(2026, 9, 26, 0, 2, tzinfo=UTC),
    )

    old_record = planned['scientificStates'][-1]['scientificFacts']['experiment_history'][0]
    record = updated['scientificStates'][-1]['scientificFacts']['experiment_history'][0]
    assert len(updated['scientificStates']) == len(planned['scientificStates']) + 1
    assert len(record['measurements']) == 1
    assert len(updated['scientificStates'][-1]['scientificFacts']['experiment_history']) == 1
    assert old_record['actual_execution'] is None
    assert record['planned_execution']['parameters'][0]['value'] == -45
    assert record['actual_execution']['parameters'][0]['value'] == -42
    assert record['measurements'][0]['value'] == 1.7


def test_feedback_rejects_prediction_or_analysis_as_measurement() -> None:
    predicted = {
        'measurement_ref': 'prediction-1',
        'metric_key': 'predicted_stress',
        'value': 1.5,
        'unit': 'GPa',
        'status': 'provisional',
        'validity': 'unknown',
        'prediction_score': 0.9,
        'provenance': {'origin': 'user', 'source_ref': 'user-result-2'},
    }
    with pytest.raises(ValueError, match='pvd_experiment_feedback_invalid'):
        materialize_experiment_feedback(
            task_payload=_recommended_task(),
            feedback_facts=_feedback(
                source_ref='user-result-2',
                measurement=predicted,
            ),
            source_ref='user-result-2',
            research_design_version=1,
            occurred_at=datetime(2026, 9, 26, 0, 3, tzinfo=UTC),
        )


def test_feedback_cannot_overwrite_existing_plan_or_duplicate_experiment() -> None:
    planned = materialize_experiment_feedback(
        task_payload=_recommended_task(),
        feedback_facts=_feedback(source_ref='user-plan-1', planned=-45),
        source_ref='user-plan-1',
        research_design_version=1,
        occurred_at=datetime(2026, 9, 26, 0, 1, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match='planned_execution_conflict'):
        materialize_experiment_feedback(
            task_payload=planned,
            feedback_facts=_feedback(source_ref='user-plan-2', planned=-40),
            source_ref='user-plan-2',
            research_design_version=1,
            occurred_at=datetime(2026, 9, 26, 0, 2, tzinfo=UTC),
        )
    assert len(planned['scientificStates'][-1]['scientificFacts']['experiment_history']) == 1


def test_partial_decision_and_bad_experiment_preserve_valid_recommendation() -> None:
    projection = _recommendation(experiment_id="valid-experiment", bias=-25)
    projection.pop("pvd_recommendation_important_unknowns")
    projection["pvd_recommendation_experiments"].insert(0, {"experiment_id": "bad"})
    projection["pvd_recommendation_experiments"][1]["parameters"].append({"bad": "parameter"})
    result = materialize_round_experiment_recommendation(
        task_payload=_task_with_context(), projected_facts=projection,
        source_artifact_id="artifact-partial", research_design_version=1,
        occurred_at=datetime(2026, 9, 25, tzinfo=UTC),
    )
    facts = result["scientificStates"][-1]["scientificFacts"]
    assert "current_recommendation_decision" not in facts
    assert [row["experiment_ref"] for row in facts["experiment_history"]] == ["valid-experiment"]
    recommendation = facts["experiment_history"][0]["system_recommendation"]
    assert len(recommendation["parameters"]) == 1
    assert recommendation["parameters"][0]["recommended_value"] == -25


def test_request_context_survives_missing_rounds() -> None:
    result = materialize_research_request_understanding(
        task_payload={"taskId": "partial-context", "scientificStates": []},
        projected_facts={"pvd_research_request_context": {
            "experimental_system_context": _context("a")}},
        source_artifact_id="artifact-partial", research_design_version=1,
        occurred_at=datetime(2026, 9, 25, tzinfo=UTC),
    )
    facts = result["scientificStates"][-1]["scientificFacts"]
    assert facts["experimental_system_contexts"][0]["experimental_system_context"] == _context("a")
    assert facts["experiment_history"] == []


def test_query_history_isolates_bad_rows_without_inventing_measurements() -> None:
    context = {"experimental_system_context": _context("Ti"), "rounds": [
        None,
        {"status": {"bad": "shape"}},
        {"status": "measured",
         "source_record_ref": "pvd-query-source-record:sha256:111111111111111111111111",
         "actual_execution": [None, {"parameter_key": "bad", "value_type": {}},
                              {"parameter_key": "power",
                              "value_type": "continuous", "value": 100}],
         "measurements": [None, {"metric": "resistivity"},
                          {"metric": "stress", "quantity": {"value_numeric": 2.1}}]},
        {"status": "measured",
         "source_record_ref": "pvd-query-source-record:sha256:222222222222222222222222",
         "actual_execution": [None], "measurements": [None]},
    ]}
    result = materialize_research_request_understanding(
        task_payload=_base_payload(),
        projected_facts={"pvd_research_request_context": context},
        source_artifact_id="artifact-partial-history", research_design_version=1,
        occurred_at=datetime(2026, 9, 25, tzinfo=UTC),
    )
    history = result["scientificStates"][-1]["scientificFacts"]["experiment_history"]
    assert len(history) == 1
    assert history[0]["actual_execution"]["parameters"][0]["value"] == 100
    assert len(history[0]["measurements"]) == 1
    assert history[0]["measurements"][0]["value"] == 2.1


def test_bad_recommendation_metadata_shape_does_not_raise_or_fill_decision() -> None:
    projection = _recommendation(experiment_id="valid", bias=-25)
    projection["pvd_recommendation_decision_status"] = {"bad": "shape"}
    before = _task_with_context()
    result = materialize_round_experiment_recommendation(
        task_payload=before, projected_facts=projection,
        source_artifact_id="artifact-bad-status", research_design_version=1,
        occurred_at=datetime(2026, 9, 25, tzinfo=UTC),
    )
    assert result == before
