from __future__ import annotations

from app.modules.skill_binding_source import SKILL_BINDINGS_PATH, load_skill_binding_source_rows


BINDINGS_PATH = SKILL_BINDINGS_PATH


def _binding(definition_id: str, skill_id: str) -> dict:
    rows = [
        row
        for row in load_skill_binding_source_rows(BINDINGS_PATH)
        if row["definitionId"] == definition_id and row["skillId"] == skill_id
    ]
    assert len(rows) == 1
    return rows[0]


def _source_map(binding: dict) -> dict[str, dict]:
    return {
        entry["targetField"]: entry
        for entry in binding.get("inputSources", {}).get("fields", [])
    }


def _runtime_map(binding: dict) -> dict[str, dict]:
    return {
        entry["targetField"]: entry
        for entry in binding.get("runtimeInputs", {}).get("fields", [])
    }


def test_problem_framing_binding_consumes_only_canonical_user_query() -> None:
    row = _binding("opentrajectory:problem_framing", "problem_framing_generation")
    assert "inputSources" not in row
    assert _runtime_map(row) == {
        "user_query": {
            "targetField": "user_query",
            "source": {
                "kind": "invocationContext",
                "field": "canonical_user_query",
            },
        }
    }


def test_retrieval_matrix_binding_consumes_single_problem_framing_artifact() -> None:
    row = _binding("opentrajectory:retrieval_strategy_design", "retrieval_matrix_generation")
    sources = _source_map(row)

    assert set(sources) == {"intent_understanding", "constraints_criteria"}
    assert sources["intent_understanding"]["source"] == {
        "kind": "artifact",
        "bindingId": "bind:opentrajectory:problem_framing:skill:problem_framing_generation",
        "bindingVersion": 1,
        "field": "intent_understanding",
    }
    assert sources["constraints_criteria"]["source"] == {
        "kind": "artifact",
        "bindingId": "bind:opentrajectory:problem_framing:skill:problem_framing_generation",
        "bindingVersion": 1,
        "field": "constraints_criteria",
    }

def test_source_acquisition_binding_is_single_structured_handoff() -> None:
    row = _binding(
        "opentrajectory:source_acquisition",
        "opentrajectory_source_acquisition",
    )
    sources = _source_map(row)
    runtime = _runtime_map(row)

    assert set(sources) == {"search_matrix", "expanded_query_variants"}
    assert sources["search_matrix"]["source"]["bindingId"] == (
        "bind:opentrajectory:retrieval_strategy_design:skill:retrieval_matrix_generation"
    )
    assert sources["expanded_query_variants"]["source"]["field"] == "expanded_query_variants"
    assert runtime["user_query"]["source"] == {
        "kind": "invocationContext",
        "field": "canonical_user_query",
    }
    assert runtime["task_id"]["source"] == {
        "kind": "executionIdentity",
        "field": "task_id",
    }

    screening = _binding(
        "opentrajectory:source_screening_selection",
        "opentrajectory_source_screening_selection",
    )
    screening_sources = _source_map(screening)
    screening_runtime = _runtime_map(screening)
    assert set(screening_sources) == {
        "candidates", "research_context", "evidence_schema"
    }
    assert screening_sources["candidates"]["source"] == {
        "kind": "artifact",
        "bindingId": "bind:opentrajectory:source_acquisition:skill:opentrajectory_source_acquisition",
        "bindingVersion": 1,
        "field": "candidates",
    }
    assert screening_sources["evidence_schema"]["sourcePresence"] == "optional"
    assert screening_runtime["user_query"]["source"] == {
        "kind": "invocationContext",
        "field": "canonical_user_query",
    }
    assert screening_runtime["task_id"]["source"] == {
        "kind": "executionIdentity",
        "field": "task_id",
    }
    assert set(screening_runtime) == {"task_id", "user_query"}

def test_final_synthesis_binding_reads_research_records_directly() -> None:
    row = _binding(
        "opentrajectory:findings_synthesis",
        "research_lead_hypothesis_generation",
    )
    sources = _source_map(row)

    assert sources["research_context"] == {
        "targetField": "research_context",
        "sourcePresence": "required",
        "source": {
            "kind": "artifact",
            "bindingId": "bind:opentrajectory:problem_framing:skill:problem_framing_generation",
            "bindingVersion": 1,
            "field": "intent_understanding",
        },
    }
    assert sources["research_records"] == {
        "targetField": "research_records",
        "sourcePresence": "required",
        "source": {
            "kind": "artifact",
            "bindingId": "bind:opentrajectory:evidence_extraction:skill:evidence_node_extraction",
            "bindingVersion": 1,
            "field": "research_records",
        },
    }
    assert _runtime_map(row)["task_id"]["source"] == {
        "kind": "executionIdentity",
        "field": "task_id",
    }


def test_final_three_call_bindings_are_collapsed_to_one_active_binding() -> None:
    final_definition_ids = {
        "opentrajectory:findings_synthesis",
        "opentrajectory:research_lead_generation",
        "opentrajectory:hypothesis_update",
    }
    rows = [
        row
        for row in load_skill_binding_source_rows(BINDINGS_PATH)
        if row["definitionId"] in final_definition_ids
    ]
    assert [
        (row["definitionId"], row["skillId"])
        for row in rows
    ] == [
        ("opentrajectory:findings_synthesis", "research_lead_hypothesis_generation")
    ]




def test_evidence_schema_binding_consumes_three_formal_upstreams() -> None:
    row = _binding("opentrajectory:evidence_schema_design", "evidence_schema_design")
    sources = _source_map(row)
    assert sources["intent_understanding"]["source"] == {
        "kind": "artifact",
        "bindingId": "bind:opentrajectory:problem_framing:skill:problem_framing_generation",
        "bindingVersion": 1,
        "field": "intent_understanding",
    }
    assert sources["constraints_criteria"]["source"] == {
        "kind": "artifact",
        "bindingId": "bind:opentrajectory:problem_framing:skill:problem_framing_generation",
        "bindingVersion": 1,
        "field": "constraints_criteria",
    }
    assert sources["retrieval_strategy_design"]["source"]["bindingId"] == (
        "bind:opentrajectory:retrieval_strategy_design:skill:retrieval_matrix_generation"
    )
    assert _runtime_map(row)["task_id"]["source"] == {"kind": "executionIdentity", "field": "task_id"}


def test_progressive_reading_binding_consumes_topk_and_evidence_schema() -> None:
    row = _binding("opentrajectory:progressive_reading", "deepxiv_progressive_reading")
    sources = _source_map(row)
    assert sources["topk_selection"]["source"] == {
        "kind": "artifact",
        "bindingId": (
            "bind:opentrajectory:source_screening_selection:"
            "skill:opentrajectory_source_screening_selection"
        ),
        "bindingVersion": 1,
        "field": "topk_selection",
    }
    assert sources["evidence_schema"]["source"] == {
        "kind": "artifact",
        "bindingId": "bind:opentrajectory:evidence_schema_design:skill:evidence_schema_design",
        "bindingVersion": 1,
        "field": "evidence_schema",
    }


def test_evidence_extraction_binding_consumes_paper_packets_and_schema() -> None:
    row = _binding("opentrajectory:evidence_extraction", "evidence_node_extraction")
    sources = _source_map(row)
    assert sources["paper_packets"]["source"]["bindingId"] == (
        "bind:opentrajectory:progressive_reading:skill:deepxiv_progressive_reading"
    )
    assert sources["paper_packets"]["source"]["field"] == "paper_packets"
    assert sources["evidence_schema"]["source"]["bindingId"] == (
        "bind:opentrajectory:evidence_schema_design:skill:evidence_schema_design"
    )
    assert sources["evidence_schema"]["source"]["field"] == "evidence_schema"
    assert _runtime_map(row)["task_id"]["source"] == {"kind": "executionIdentity", "field": "task_id"}



def test_ot_binding_rows_are_v2_only_and_declarative() -> None:
    rows = [
        row
        for row in load_skill_binding_source_rows(BINDINGS_PATH)
        if row["definitionId"].startswith("opentrajectory:")
    ]
    assert rows

    forbidden = {"input_handoff", "field_mappings", "context_fields", "normalizers", "mapper", "shim", "fallback"}
    for row in rows:
        if "inputSources" in row:
            assert row["inputSources"]["contractVersion"] == 2
            for field in row["inputSources"]["fields"]:
                assert field["sourcePresence"] in {"required", "optional"}
                assert field["source"]["kind"] in {"artifact", "scientificState"}
        if "runtimeInputs" in row:
            assert row["runtimeInputs"]["contractVersion"] == 2
            for field in row["runtimeInputs"]["fields"]:
                assert field["source"]["kind"] in {"executionIdentity", "invocationContext", "literal"}
        assert forbidden.isdisjoint(row)
