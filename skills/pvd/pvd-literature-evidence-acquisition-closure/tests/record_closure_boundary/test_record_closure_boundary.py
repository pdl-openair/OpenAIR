from __future__ import annotations

import base64
import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "record_boundary_closure", ROOT / "adapter/literature_evidence_closure.py"
)
assert spec and spec.loader
closure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(closure)
finalizer = closure._load_record_finalizer()
close = closure._child_callable("pvd", ROOT.name, "record_closure", "close_research_records")
spec_rc = importlib.util.spec_from_file_location(
    "record_boundary_record_closure", ROOT / "adapter/record_closure.py"
)
assert spec_rc and spec_rc.loader
record_closure_module = importlib.util.module_from_spec(spec_rc)
spec_rc.loader.exec_module(record_closure_module)
EXAMPLES = ROOT.parent / "pvd-research-record-structuring/examples"


def request():
    return json.loads((EXAMPLES / "pvd_two_sample.request.json").read_text(encoding="utf-8"))


def _child_result(content, quarantined=False):
    return {
        "state": "succeeded",
        "artifactPayload": {
            "contentBase64": base64.b64encode(json.dumps(content).encode()).decode(),
            "presentation": {
                "metadata": {
                    "resultProtocol": {
                        "status": "degraded",
                        "quarantined_items": [{"path": "$", "reason": "finalization_contract_gap"}]
                        if quarantined
                        else [],
                    }
                }
            },
        },
    }


@pytest.mark.parametrize(
    "output", [{"record_groups": []}]
)
def test_unfinalized_or_empty_grouping_uses_existing_grounded_singletons(output):
    inp = request()
    warnings = []
    result = close(
        inp, llm_child=lambda *_args, **_kwargs: output, finalizer=finalizer, warnings=warnings
    )
    assert len(result["evidence_records"]) == len(inp["evidence_packet"]["evidence_items"])
    assert {ref for row in result["evidence_records"] for ref in row["provenance_refs"]} == {
        item["evidence_id"] for item in inp["evidence_packet"]["evidence_items"]
    }
    assert "research_record_grouping_unavailable:singleton_closure_used" in warnings


def test_root_quarantined_child_is_rejected_but_formal_degraded_material_is_accepted():
    with pytest.raises(RuntimeError, match="not_finalized"):
        closure._decode_artifact(_child_result({"record_groups": []}, quarantined=True))
    finalized = finalizer.materialize_evidence_singletons(request()).output
    assert closure._decode_artifact(_child_result(finalized)) == finalized


def test_formal_grouping_is_retained_without_scientific_coverage_validation():
    inp = request()
    model = json.loads((EXAMPLES / "pvd_two_sample.model.json").read_text(encoding="utf-8"))
    formal = finalizer.finalize(inp, model).output
    assert formal
    warnings = []
    result = close(
        inp, llm_child=lambda *_args, **_kwargs: formal, finalizer=finalizer, warnings=warnings
    )
    assert result == formal
    assert warnings == []
    partial = deepcopy(formal)
    partial["evidence_records"] = partial["evidence_records"][:1]
    recovered = close(
        inp, llm_child=lambda *_args, **_kwargs: partial, finalizer=finalizer, warnings=warnings
    )
    assert recovered == partial
    assert warnings == []


def test_source_content_is_not_rejected_during_singleton_materialization():
    inp = request()
    inp["evidence_packet"]["evidence_items"][0]["source_anchor_id"] = "nonexistent"
    result = close(
        inp, llm_child=lambda *_args, **_kwargs: {"record_groups": []},
        finalizer=finalizer, warnings=[],
    )
    assert result["evidence_records"]


def _multi_source_input(paper_count: int, evidence_per_paper: int) -> dict:
    """Build a valid multi-source record input from the single-source example."""
    base = request()
    packet = deepcopy(base["source_material"]["paper_packets"][0])
    item_template = deepcopy(base["evidence_packet"]["evidence_items"][0])
    packets, items = [], []
    for index in range(paper_count):
        candidate_id = f"source-{index:02d}"
        packet_copy = deepcopy(packet)
        packet_copy["candidate_id"] = candidate_id
        packet_copy["title"] = f"Paper {index}"
        packets.append(packet_copy)
        for item_index in range(evidence_per_paper):
            item = deepcopy(item_template)
            item["candidate_id"] = candidate_id
            item["evidence_id"] = f"ev:multi-{index:02d}-{item_index:02d}"
            if items:
                item["source_anchor_id"] = items[0]["source_anchor_id"]
            items.append(item)
    base["evidence_packet"]["evidence_items"] = items
    base["source_material"]["paper_packets"] = packets
    base.pop("evidence_index", None)
    base.pop("atomic_grounding_catalog", None)
    base.pop("source_index", None)
    return base


def test_multi_source_input_is_split_into_grouping_batches():
    inp = _multi_source_input(6, 4)  # 24 evidence > limit of 8 -> batches of 2 papers
    batches = record_closure_module._grouping_batches(inp)
    assert len(batches) > 1
    limit = record_closure_module.GROUPING_EVIDENCE_BATCH_LIMIT
    assert all(
        len(batch["evidence_packet"]["evidence_items"]) <= limit
        for batch in batches
    )
    # every source packet lands in exactly one batch and every item stays with its paper
    batch_sources = [
        str(row["candidate_id"])
        for batch in batches
        for row in batch["source_material"]["paper_packets"]
    ]
    all_sources = {row["candidate_id"] for row in inp["evidence_packet"]["evidence_items"]}
    assert sorted(batch_sources) == sorted(all_sources)
    for batch in batches:
        batch_paper_ids = {row["candidate_id"] for row in batch["source_material"]["paper_packets"]}
        for item in batch["evidence_packet"]["evidence_items"]:
            assert item["candidate_id"] in batch_paper_ids


def test_bounded_index_views_stay_consistent_with_their_batches():
    inp = _multi_source_input(6, 4)
    evidence_ids = {item["evidence_id"] for item in inp["evidence_packet"]["evidence_items"]}
    inp["evidence_index"] = {"evidence_items": [
        {"evidence_id": eid, "candidate_id": eid} for eid in sorted(evidence_ids)
    ]}
    inp["atomic_grounding_catalog"] = [
        {"grounded_ref": f"ground_{i:04d}", "text": f"fact {i}", "evidence_id": eid}
        for i, eid in enumerate(sorted(evidence_ids))
    ]
    inp["source_index"] = {"paper_packets": [
        {"candidate_id": row["candidate_id"], "title": row.get("title")}
        for row in inp["source_material"]["paper_packets"]
    ]}
    for batch in record_closure_module._grouping_batches(inp):
        batch_ids = {item["evidence_id"] for item in batch["evidence_packet"]["evidence_items"]}
        index_ids = {row["evidence_id"] for row in batch["evidence_index"]["evidence_items"]}
        assert index_ids == batch_ids
        assert {row["evidence_id"] for row in batch["atomic_grounding_catalog"]} <= batch_ids
        batch_sources = {row["candidate_id"] for row in batch["source_index"]["paper_packets"]}
        item_sources = {item["candidate_id"] for item in batch["evidence_packet"]["evidence_items"]}
        assert batch_sources == item_sources


def test_small_input_stays_single_call_with_canonical_adapter_name():
    inp = request()  # 6 evidence in one paper -> one batch
    seen_names = []

    def child(_skill_id, _params, *, adapter_name):
        seen_names.append(adapter_name)
        return finalizer.materialize_evidence_singletons(request()).output

    close(inp, llm_child=child, finalizer=finalizer, warnings=[])
    assert seen_names == ["pvd_t04_research_record_grouping"]


def test_multi_source_input_uses_batched_adapter_names_and_merges_outputs():
    inp = _multi_source_input(6, 4)
    seen_names = []

    def child(_skill_id, params, *, adapter_name):
        seen_names.append(adapter_name)
        return record_closure_module._accept_records(
            finalizer.materialize_evidence_singletons(params).output, params, finalizer
        )

    result = close(inp, llm_child=child, finalizer=finalizer, warnings=[])
    assert len(seen_names) > 1
    assert all(name.startswith("pvd_t04_research_record_grouping_batch_") for name in seen_names)
    # every evidence item survives in exactly one merged record
    all_refs = {ref for row in result["evidence_records"] for ref in row["provenance_refs"]}
    assert all_refs == {item["evidence_id"] for item in inp["evidence_packet"]["evidence_items"]}
    assert len(result["source_provenance"]) == 6


def test_hollow_but_schema_valid_grouping_falls_back_to_singletons():
    inp = request()
    # simulate the observed production failure mode: records with identity only
    hollow_records = []
    for item in inp["evidence_packet"]["evidence_items"]:
        formal = finalizer.finalize(inp, {"record_groups": [{
            "group_id": f"g-{item['evidence_id']}",
            "grouping_status": "ambiguous_singleton",
            "evidence_refs": [item["evidence_id"]],
            "grouping_basis_evidence_refs": [item["evidence_id"]],
            "record_role": "unresolved_record_identity",
        }]}).output
        hollow_records.extend(formal["evidence_records"])
    hollow_output = {"evidence_records": hollow_records, "source_provenance": []}
    assert record_closure_module._known_fact_count(hollow_output) == 0

    def child(*_args, **_kwargs):
        return deepcopy(hollow_output)

    warnings = []
    result = close(inp, llm_child=child, finalizer=finalizer, warnings=warnings)
    assert "research_record_grouping_uninformative:singleton_closure_used" in warnings
    # degraded singletons carry the claims and metrics the hollow groups dropped
    assert record_closure_module._known_fact_count(result) > 0


def test_one_batch_failure_degrades_only_that_batch():
    inp = _multi_source_input(6, 4)

    def child(_skill_id, params, *, adapter_name):
        first_batch = "batch_01" in adapter_name
        if first_batch:
            raise RuntimeError("child_skill_output_not_finalized")
        return record_closure_module._accept_records(
            finalizer.materialize_evidence_singletons(params).output, params, finalizer
        )

    warnings = []
    result = close(inp, llm_child=child, finalizer=finalizer, warnings=warnings)
    assert "research_record_grouping_unavailable:singleton_closure_used" in warnings
    assert {ref for row in result["evidence_records"] for ref in row["provenance_refs"]} == {
        item["evidence_id"] for item in inp["evidence_packet"]["evidence_items"]
    }
