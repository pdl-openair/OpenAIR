from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[3]


def _registry_rows():
    return yaml.safe_load((ROOT / "skills/registry.yaml").read_text(encoding="utf-8"))["skills"]


def _binding_rows():
    data = yaml.safe_load((ROOT / "skills/skill-bindings.yaml").read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data["bindings"]


def _load_module(name: str, rel: str):
    path = ROOT / rel
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pvd_registered_bindings_cover_all_required_top_level_skill_inputs():
    registry = {row["skill_id"]: row for row in _registry_rows()}
    problems = []
    for binding in _binding_rows():
        if not str(binding.get("definitionId", "")).startswith("pvd:"):
            continue
        skill = registry[binding["skillId"]]
        schema_path = ROOT / skill["skill_path"] / "input.schema.json"
        if not schema_path.is_file():
            continue
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        required = set(schema.get("required") or [])
        supplied = {
            field["targetField"]
            for group in ("inputSources", "runtimeInputs")
            for field in (binding.get(group) or {}).get("fields", [])
        }
        missing = sorted(required - supplied)
        if missing:
            problems.append((binding["bindingId"], missing))
    assert problems == []


def test_pvd_scoring_context_bridge_matches_candidate_scoring_contract():
    module = _load_module(
        "pvd_scoring_context",
        "skills/pvd/pvd-candidate-scoring-context-assembly/adapter/context_assembly.py",
    )
    payload = {
        "user_query": "fallback question",
        "retrieval_strategy_design": {
            "assembled_facets": {
                "query_facets": {
                    "object": ["AlScN"],
                    "context": ["Mo substrate"],
                    "metric": ["piezoelectric response"],
                    "mechanism": ["Sc-induced lattice distortion"],
                    "evaluation": ["same process conditions"],
                },
                "research_object_en": ["AlScN thin film"],
                "core_entities": ["AlScN"],
                "conditions": ["reactive sputtering"],
                "target_metrics_en": ["d33"],
                "must_have_terms": ["power"],
                "nice_to_have_terms": ["N2 flow"],
                "negative_contexts": ["battery"],
                "off_domain_signals": ["electrochemistry"],
            }
        },
        "evidence_schema": {
            "task_context": {"research_question": "Optimize AlScN PVD parameters"},
            "evidence_targets": [{"description": "Power-process-result relation"}],
        },
    }
    output = module.run_pvd_candidate_scoring_context_assembly(payload)
    own_schema = json.loads(
        (ROOT / "skills/pvd/pvd-candidate-scoring-context-assembly/output.schema.json").read_text(
            encoding="utf-8"
        )
    )
    jsonschema.validate(output, own_schema)
    scoring_schema = json.loads(
        (ROOT / "skills/opentrajectory/candidate-source-scoring/input.schema.json").read_text(
            encoding="utf-8"
        )
    )
    jsonschema.validate(output["research_context"], scoring_schema["properties"]["research_context"])
    assert output["research_context"]["research_question"] == "Optimize AlScN PVD parameters"
    assert output["research_context"]["research_objects"] == ["AlScN", "AlScN thin film"]
    assert "Power-process-result relation" in output["research_context"]["critical_evidence_requirements"]


def test_pvd_recommendation_mode_resolution_matches_frozen_node9_examples():
    module = _load_module(
        "pvd_recommendation_mode",
        "skills/pvd/pvd-recommendation-mode-resolution/adapter/mode_resolution.py",
    )
    examples = ROOT / "skills/pvd/pvd-four-parameter-recommendation/examples"
    requests = sorted(examples.glob("*_request.json"))
    assert requests
    for path in requests:
        payload = json.loads(path.read_text(encoding="utf-8"))
        expected = payload["mode"]
        actual = module.run_pvd_recommendation_mode_resolution(
            {"scientific_state_facts": payload["scientific_state_facts"]}
        )
        assert actual["mode"] == expected, path.name


def test_pvd_request_understanding_is_one_standard_capability_with_query_only_binding():
    mapping = yaml.safe_load((ROOT / "schemas/pvd/substage-skill-map.yaml").read_text(encoding="utf-8"))
    assert mapping["research_goal_equipment_constraints"] == [
        "pvd_research_request_understanding"
    ]

    rows = {row["bindingId"]: row for row in _binding_rows()}
    binding = rows[
        "bind:pvd:research_goal_equipment_constraints:skill:pvd_research_request_understanding"
    ]
    assert "inputSources" not in binding
    assert binding["runtimeInputs"] == {
        "contractVersion": 2,
        "fields": [
            {
                "targetField": "query",
                "source": {
                    "kind": "invocationContext",
                    "field": "canonical_user_query",
                },
            }
        ],
    }


def test_pvd_evidence_acquisition_design_uses_m1_then_call02_then_call03_without_v2_node():
    mapping = yaml.safe_load((ROOT / "schemas/pvd/substage-skill-map.yaml").read_text(encoding="utf-8"))
    assert mapping["research_retrieval_experiment_design"] == [
        "pvd_research_request_understanding",
        "pvd_evidence_retrieval_design",
        "pvd_evidence_schema_design",
    ]
    registry = {row["skill_id"]: row for row in _registry_rows()}
    for skill_id in ("pvd_evidence_retrieval_design", "pvd_evidence_schema_design"):
        assert registry[skill_id]["finalization"] == {
            "contract_version": 1,
            "entrypoint": "scripts.finalize:finalize",
            "draft_schema": "provider.schema.json",
        }
    workflow = yaml.safe_load((ROOT / "schemas/workflows/pvd_process_optimization_workflow_v2.yaml").read_text(encoding="utf-8"))
    assert "research_retrieval_experiment_design" not in {row["nodeKey"] for row in workflow["nodes"]}

    rows = {row["bindingId"]: row for row in _binding_rows()}
    retrieval = rows["bind:pvd:research_retrieval_experiment_design:skill:pvd_evidence_retrieval_design"]
    assert {row["targetField"]: row["source"]["field"] for row in retrieval["inputSources"]["fields"]} == {
        "structured_context": "structured_context",
        "recommendation_parameter_set": "recommendation_parameter_set",
        "parameter_admission_judgments": "structured_context.parameter_admission_judgments",
    }
    evidence_schema = rows["bind:pvd:research_retrieval_experiment_design:skill:pvd_evidence_schema_design"]
    fields = {row["targetField"]: row["source"] for row in evidence_schema["inputSources"]["fields"]}
    assert fields["parameter_admission_judgments"] == {
        "kind": "artifact",
        "bindingId": "bind:pvd:research_retrieval_experiment_design:skill:pvd_research_request_understanding",
        "bindingVersion": 1,
        "field": "structured_context.parameter_admission_judgments",
    }
    assert fields["retrieval_design"] == {
        "kind": "artifact",
        "bindingId": "bind:pvd:research_retrieval_experiment_design:skill:pvd_evidence_retrieval_design",
        "bindingVersion": 1,
        "field": "retrieval_design",
    }


def test_pvd_composition_orders_deterministic_bridges_before_consumers():
    mapping = yaml.safe_load((ROOT / "schemas/pvd/substage-skill-map.yaml").read_text(encoding="utf-8"))
    literature = mapping["literature_experiment_record_verification"]
    assert literature.index("pvd_candidate_scoring_context_assembly") < literature.index(
        "candidate_source_scoring"
    )
    recommendation = mapping["four_parameter_recommendation"]
    assert recommendation == [
        "pvd_recommendation_mode_resolution",
        "pvd_four_parameter_recommendation",
    ]

    rows = {row["bindingId"]: row for row in _binding_rows()}
    scorer = rows[
        "bind:pvd:literature_experiment_record_verification:skill:candidate_source_scoring"
    ]
    assert {row["targetField"] for row in scorer["inputSources"]["fields"]} == {
        "screened_candidates",
        "research_context",
    }
    reading = rows[
        "bind:pvd:literature_experiment_record_verification:skill:deepxiv_progressive_reading"
    ]
    assert {row["targetField"] for row in reading["inputSources"]["fields"]} == {
        "topk_selection",
        "evidence_schema",
    }

    recommendation_binding = rows[
        "bind:pvd:four_parameter_recommendation:skill:pvd_four_parameter_recommendation"
    ]
    mode_rows = [
        row
        for row in recommendation_binding["inputSources"]["fields"]
        if row["targetField"] == "mode"
    ]
    assert len(mode_rows) == 1
    assert mode_rows[0]["source"] == {
        "kind": "artifact",
        "bindingId": "bind:pvd:four_parameter_recommendation:skill:pvd_recommendation_mode_resolution",
        "bindingVersion": 1,
        "field": "mode",
    }
    assert "runtimeInputs" not in recommendation_binding
