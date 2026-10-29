from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / "skills/opentrajectory/opentrajectory-source-screening-selection"


def _yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _load_finalizer():
    import sys

    path = SKILL / "scripts/finalize.py"
    module_name = "ot_t12_finalizer"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    previous = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous
        raise
    return module


def _input(*, with_evidence_schema: bool = True):
    payload = {
        "task_id": "task-t12",
        "user_query": "Find the papers most worth reading for this research question.",
        "research_context": {"research_object": "AlScN d33 optimization"},
        "candidates": [
            {
                "candidate_id": "c1",
                "title": "Core AlScN sputtering study",
                "doi": "10.1000/core",
                "provenance": [{"source_name": "openalex"}],
            },
            {
                "candidate_id": "c2",
                "title": "Unrelated robotics study",
                "doi": "10.1000/noise",
                "provenance": [{"source_name": "arxiv"}],
            },
            {
                "candidate_id": "c3",
                "title": "AlScN mechanism study",
                "doi": "10.1000/mechanism",
                "provenance": [{"source_name": "semantic_scholar"}],
            },
        ],
    }
    if with_evidence_schema:
        payload["evidence_schema"] = {
            "evidence_targets": [
                {"target_id": "t_core"},
                {"target_id": "t_mechanism"},
            ]
        }
    return payload


def _model():
    return {
        "judgments": [
            {
                "candidate_id": "c1",
                "decision": "priority",
                "reading_priority": 96,
                "evidence_roles": ["core"],
                "relevant_target_ids": ["t_core"],
                "reason": "Directly addresses the core process-property question.",
            },
            {
                "candidate_id": "c2",
                "decision": "exclude",
                "reading_priority": None,
                "reason": "Outside the current research domain.",
            },
            {
                "candidate_id": "c3",
                "decision": "supporting",
                "reading_priority": 78,
                "evidence_roles": ["mechanism"],
                "relevant_target_ids": ["t_mechanism"],
                "reason": "Useful for mechanism interpretation.",
            },
        ]
    }


def test_ot_node6_is_one_llm_skill_and_old_rule_chain_is_not_ot_authority() -> None:
    # The generated substage-skill map is no longer the execution Authority for this
    # workflow responsibility. Registry + Binding v2 own the production mapping.
    registry = _yaml(ROOT / "skills/registry.yaml")
    by_id = {row["skill_id"]: row for row in registry["skills"]}
    node6 = by_id["opentrajectory_source_screening_selection"]
    assert node6["skill_type"] == "atomic"
    assert node6["runtime"] == "llm_structured"
    assert node6["runtime_contract"]["kind"] == "llm_reasoning"
    assert node6["review_policy"] == {
        "contract_version": 1,
        "mode": "schema_validated_only",
    }
    assert node6["finalization"]["entrypoint"] == "scripts.finalize:finalize"

    node6_skill = _yaml(SKILL / "skill.yaml")
    assert node6_skill["risk_level"] == "low"
    assert node6_skill["llm"]["provider_message_contract"] == "scientific_minimal_v1"
    assert node6_skill["durable_llm_execution_policy"]["task_budget_seconds"] == 135
    assert node6_skill["durable_llm_execution_policy"]["structured_output_recovery_attempts"] == 0
    assert node6_skill["durable_llm_batch_policy"] == {
        "contract_version": 1,
        "input_array_field": "candidates",
        "output_array_field": "judgments",
        "identity_field": "candidate_id",
        "max_items_per_batch": 30,
        "max_parallel": 4,
        "partial_failure_mode": "preserve_successes",
    }

    for folder in (
        "title-abstract-screening-skill",
        "candidate-source-scoring",
        "candidate-tiering",
        "topk-source-selection",
    ):
        old = _yaml(ROOT / "skills/opentrajectory" / folder / "skill.yaml")
        assert "source_screening_selection" not in (old.get("substages") or [])


def test_ot_node6_binding_has_no_fixed_selection_count_and_evidence_schema_is_optional() -> None:
    payload = _yaml(ROOT / "skills/skill-bindings.yaml")
    rows = [
        row for row in payload["bindings"]
        if row["definitionId"] == "opentrajectory:source_screening_selection"
    ]
    assert [(row["skillId"], row["bindingId"]) for row in rows] == [
        (
            "opentrajectory_source_screening_selection",
            "bind:opentrajectory:source_screening_selection:"
            "skill:opentrajectory_source_screening_selection",
        )
    ]
    row = rows[0]
    sources = {
        item["targetField"]: item
        for item in row["inputSources"]["fields"]
    }
    runtime = {
        item["targetField"]: item
        for item in row["runtimeInputs"]["fields"]
    }
    assert sources["evidence_schema"]["sourcePresence"] == "optional"
    assert set(runtime) == {"task_id", "user_query"}

    progressive = next(
        item for item in payload["bindings"]
        if item["definitionId"] == "opentrajectory:progressive_reading"
        and item["skillId"] == "deepxiv_progressive_reading"
    )
    source = next(
        item["source"]
        for item in progressive["inputSources"]["fields"]
        if item["targetField"] == "topk_selection"
    )
    assert source["bindingId"].endswith(
        "skill:opentrajectory_source_screening_selection"
    )


def test_node6_schema_has_no_private_count_or_uncertainty_contract() -> None:
    input_schema = _json(SKILL / "input.schema.json")
    model_schema = _json(SKILL / "model_output.schema.json")
    output_schema = _json(SKILL / "output.schema.json")

    assert "selection_limit" not in input_schema["properties"]
    assert "selection_limit" not in input_schema["required"]
    assert "maxItems" not in input_schema["properties"]["candidates"]
    assert "maxItems" not in model_schema["properties"]["judgments"]

    judgment = model_schema["properties"]["judgments"]["items"]
    assert "uncertainty" not in judgment["properties"]
    assert "uncertainty" not in judgment["required"]
    assert "evidence_roles" not in judgment["required"]
    assert "relevant_target_ids" not in judgment["required"]
    assert judgment["properties"]["reading_priority"]["minimum"] == 1
    assert judgment["properties"]["reading_priority"]["maximum"] == 100

    stats = output_schema["properties"]["topk_selection"]["properties"]["selection_stats"]
    assert "selection_limit" not in stats["properties"]
    assert "selection_limit" not in stats["required"]
    assert "low_selection_count" not in output_schema["properties"]["topk_selection"]["properties"]["risk_summary"]["properties"]


def test_finalizer_preserves_source_facts_and_keeps_all_model_selected_candidates() -> None:
    module = _load_finalizer()
    result = module.finalize(_input(), _model())
    assert result.output is not None
    selected = result.output["topk_selection"]["selected_for_p03"]
    assert [row["candidate_id"] for row in selected] == ["c1", "c3"]
    assert [row["doi"] for row in selected] == [
        "10.1000/core", "10.1000/mechanism"
    ]
    assert result.output["topk_selection"]["extended_selected"] == []
    assert result.output["topk_selection"]["excluded_examples"][0]["candidate_id"] == "c2"


def test_optional_roles_and_optional_evidence_schema_are_supported() -> None:
    module = _load_finalizer()
    payload = _input(with_evidence_schema=False)
    model = _model()
    for row in model["judgments"]:
        row.pop("evidence_roles", None)
        row.pop("relevant_target_ids", None)

    result = module.finalize(payload, model)
    assert result.output is not None
    for row in result.output["screening_decisions"]:
        assert row["evidence_roles"] == []
        assert row["relevant_target_ids"] == []


def test_relevant_target_ids_without_evidence_schema_are_dropped_locally() -> None:
    module = _load_finalizer()
    payload = _input(with_evidence_schema=False)
    model = _model()
    result = module.finalize(payload, model)
    assert result.output is not None
    assert result.status == "degraded"
    assert [
        row["candidate_id"]
        for row in result.output["topk_selection"]["selected_for_p03"]
    ] == ["c1", "c3"]
    assert all(
        row["relevant_target_ids"] == []
        for row in result.output["screening_decisions"]
    )
    assert any(
        "relevant_target_ids_dropped_without_evidence_schema" in str(error)
        for error in result.errors
    )


def test_missing_model_judgment_is_preserved_as_unreviewed_not_excluded() -> None:
    module = _load_finalizer()
    model = _model()
    model["judgments"] = model["judgments"][:2]
    result = module.finalize(_input(), model)
    assert result.output is not None
    deferred = result.output["topk_selection"]["deferred_candidates"]
    assert any(
        row["candidate_id"] == "c3"
        and row["selection_decision"] == "unreviewed"
        for row in deferred
    )
    assert result.output["topk_selection"]["degraded"] is True


def test_zero_selected_does_not_trigger_an_arbitrary_minimum_failure() -> None:
    module = _load_finalizer()
    model = _model()
    for row in model["judgments"]:
        row["decision"] = "exclude"
        row["reading_priority"] = None
    result = module.finalize(_input(), model)
    assert result.status == "succeeded"
    assert result.output is not None
    assert result.output["topk_selection"]["selected_for_p03"] == []
    assert result.output["topk_selection"]["degraded"] is False


def test_excluded_candidates_are_not_arbitrarily_truncated() -> None:
    module = _load_finalizer()
    payload = _input(with_evidence_schema=False)
    payload["candidates"] = [
        {
            "candidate_id": f"c{i}",
            "title": f"Noise {i}",
            "provenance": [{"source_name": "test"}],
        }
        for i in range(12)
    ]
    model = {
        "judgments": [
            {
                "candidate_id": f"c{i}",
                "decision": "exclude",
                "reading_priority": None,
                "reason": "Not relevant.",
            }
            for i in range(12)
        ]
    }
    result = module.finalize(payload, model)
    assert result.output is not None
    assert len(result.output["topk_selection"]["excluded_examples"]) == 12


def test_unknown_model_candidate_is_ignored_without_losing_real_candidates() -> None:
    module = _load_finalizer()
    model = _model()
    model["judgments"].append(
        {
            "candidate_id": "invented",
            "decision": "priority",
            "reading_priority": 96,
            "reason": "Invented candidate must never become canonical.",
        }
    )
    result = module.finalize(_input(), model)
    assert result.output is not None
    assert result.status == "degraded"
    selected_ids = {
        row["candidate_id"]
        for row in result.output["topk_selection"]["selected_for_p03"]
    }
    assert selected_ids == {"c1", "c3"}
    assert all(
        row["candidate_id"] != "invented"
        for row in result.output["screening_decisions"]
    )
    assert any("unknown_candidate_ignored" in str(error) for error in result.errors)


def test_unknown_target_ids_are_removed_without_losing_scientific_judgment() -> None:
    module = _load_finalizer()
    model = _model()
    model["judgments"][0]["relevant_target_ids"] = ["t_core", "invented_target"]
    result = module.finalize(_input(), model)
    assert result.output is not None
    assert result.status == "degraded"
    c1 = next(
        row for row in result.output["screening_decisions"]
        if row["candidate_id"] == "c1"
    )
    assert c1["decision"] == "priority"
    assert c1["relevant_target_ids"] == ["t_core"]
    assert any("unknown_target_ids_dropped" in str(error) for error in result.errors)


def test_duplicate_model_judgment_marks_only_that_candidate_unreviewed() -> None:
    module = _load_finalizer()
    model = _model()
    model["judgments"].append(dict(model["judgments"][0]))
    result = module.finalize(_input(), model)
    assert result.output is not None
    assert result.status == "degraded"
    c1 = next(
        row for row in result.output["screening_decisions"]
        if row["candidate_id"] == "c1"
    )
    assert c1["decision"] == "unreviewed"
    assert result.output["topk_selection"]["selection_stats"]["unreviewed_count"] == 1
    assert any(
        "duplicate_candidate_marked_unreviewed" in str(error)
        for error in result.errors
    )


def test_missing_reading_priority_preserves_selection_and_degrades_only_ranking() -> None:
    module = _load_finalizer()
    model = _model()
    model["judgments"][0]["reading_priority"] = None
    result = module.finalize(_input(), model)
    assert result.output is not None
    assert result.status == "degraded"
    c1 = next(
        row for row in result.output["screening_decisions"]
        if row["candidate_id"] == "c1"
    )
    assert c1["decision"] == "priority"
    assert c1["priority_rank"] is None
    assert any(
        "reading_priority_missing_preserved_without_rank" in str(error)
        for error in result.errors
    )



def test_model_owned_reading_priority_is_globally_ranked_deterministically() -> None:
    module = _load_finalizer()
    payload = _input()
    model = _model()
    model["judgments"][0]["reading_priority"] = 80
    model["judgments"][2]["reading_priority"] = 95
    # `priority` remains ahead of `supporting`; within each decision class the
    # model-owned scientific reading priority defines order.
    result = module.finalize(payload, model)
    assert result.status == "succeeded"
    assert result.output is not None
    selected = result.output["topk_selection"]["selected_for_p03"]
    assert [row["candidate_id"] for row in selected] == ["c1", "c3"]
    assert [row["priority_rank"] for row in selected] == [1, 2]


def test_node6_finalizer_result_is_accepted_by_public_runtime_contract() -> None:
    from app.execution import skill_finalization_runtime

    skill = _yaml(SKILL / "skill.yaml")
    frozen = skill_finalization_runtime.build_frozen_finalization_contract(
        skill["skill_path"],
        skill["finalization"],
        repo_root=ROOT,
    )
    result = skill_finalization_runtime.finalize_and_validate_registered_output(
        repo_root=ROOT,
        skill_path=skill["skill_path"],
        frozen_contract=frozen,
        input_payload=_input(),
        draft_payload=_model(),
        final_schema=_json(SKILL / "output.schema.json"),
    )
    assert result.status == "succeeded"
    assert result.output["topk_selection"]["degraded"] is False
