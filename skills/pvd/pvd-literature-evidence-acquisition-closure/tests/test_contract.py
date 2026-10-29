from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "adapter" / "literature_evidence_closure.py"
spec = importlib.util.spec_from_file_location("pvd_t04_closure_test", MODULE_PATH)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def base_payload(candidate_count: int = 10):
    return {
        "task_id": "task-t04",
        "user_query": "PVD AlScN process optimization",
        "retrieval_design": {
            "contract": "pvd-evidence-retrieval-design/v1",
            "mode": "core",
            "query_variants": [{"query_id": "query_001", "query": "AlScN PVD sputtering", "priority": 1}],
            "design_rationale": "cover process evidence",
        },
        "evidence_schema": {
            "contract": "pvd-evidence-schema/v1",
            "evidence_targets": [
                {"target_id": "target_001", "target_type": "experimental_condition", "scientific_role": "condition", "description": "power evidence", "requiredness": "required", "completion_criteria": "direct evidence", "covered_parameter_keys": ["power"], "comparison_dimensions": [], "provenance_requirements": ["source"]},
                {"target_id": "target_002", "target_type": "experimental_condition", "scientific_role": "condition", "description": "temperature evidence", "requiredness": "required", "completion_criteria": "direct evidence", "covered_parameter_keys": ["substrate_temperature"], "comparison_dimensions": [], "provenance_requirements": ["source"]},
            ],
            "gap_taxonomy": ["missing_condition"],
            "uncertainty_rules": ["preserve uncertainty"],
        },
        "coverage_target": {"formal_parameter_keys": ["power"], "pending_parameter_keys": ["substrate_temperature"], "all_parameter_keys": ["power", "substrate_temperature"]},
        "pending_parameter_evidence_identities": ["substrate_temperature"],
        "candidate_count": candidate_count,
    }


def source(payload):
    n = payload.pop("candidate_count", None) or 10
    candidates = [{"candidate_id": f"c{i:02d}", "title": f"Paper {i}", "provenance": [{"source": "openalex"}]} for i in range(1, n + 1)]
    return {"candidates": candidates, "retrieval_report": {"source_counts": {"openalex": n}, "query_counts": {"query_001": n}, "failed_sources": [], "total_candidates": n, "partial_success": False, "fallback_used": False, "risk_flags": []}}


def reader(payload):
    rows = payload["topk_selection"]["selected_for_p03"]
    packets = []
    for row in rows:
        cid = row["candidate_id"]
        content = (f"Methods for {cid}: AlScN sputtering power and gas-flow conditions. " * 40).strip()
        content_hash = mod.hashlib.sha256(content.encode("utf-8")).hexdigest()
        packets.append({
            "candidate_id": cid,
            "title": row["title"],
            "arxiv_id": "",
            "doi": "",
            "sections": [{
                "section_name": "Methods",
                "content": content,
                "content_hash": content_hash,
                "source_type": "test",
                "provider": "test",
                "source_locator": f"test://{cid}",
            }],
            "source_anchors": [{
                "anchor_id": f"anchor:{cid}:methods",
                "candidate_id": cid,
                "arxiv_id": "",
                "section_name": "Methods",
                "content_hash": content_hash,
                "source_type": "test",
                "provider": "test",
                "source_locator": f"test://{cid}",
            }],
        })
    return {
        "status": "success", "paper_packets": packets, "material_attempts": [],
        "summary": {"input_candidate_count": len(rows), "attempted_candidate_count": len(rows), "truncated_candidate_count": 0, "paper_packet_count": len(rows), "ready_packet_count": len(rows), "abstract_only_packet_count": 0, "failed_candidate_count": 0, "material_attempt_count": 0, "section_count": 0, "source_anchor_count": 0, "partial_success": False, "degraded": False},
        "audit": {"skill_id": "deepxiv_progressive_reading", "reader_mode": "deepxiv_sdk_reader", "raw_default_enabled": False, "raw_call_count": 0, "max_papers": len(rows), "max_sections_per_paper": 3, "network_timeout_seconds": 20.0, "max_provider_retries": 1, "max_total_chars": 300000, "output_content_chars": 0, "section_priority": ["method", "results", "discussion"], "input_fingerprint": "a" * 64},
    }


def evidence(targets):
    return {
        "evidence_items": [
            {"evidence_id": f"ev:{tid}", "candidate_id": "c01", "evidence_type": "experimental_condition", "matched_target_ids": [tid], "target_match_status": "matched"}
            for tid in targets
        ],
        "summary": {"input_paper_packet_count": 1, "input_source_anchor_count": 1, "dropped_invalid_count": 0, "dropped_duplicate_count": 0},
        "extraction_warnings": [],
    }


def record_output(parameters):
    # Protocol-minimal grouping fixture: keep every sibling Evidence, and carry
    # the cited condition so the record is informative rather than identity-only.
    return {
        "evidence_records": [
            {"record_id": f"record:{row['evidence_id']}", "source_id": row["candidate_id"],
             "provenance_refs": [row["evidence_id"]],
             "process": {"power": {"fact_status": "known", "value": "500", "provenance_refs": [row["evidence_id"]]}}}
            if (row.get("conditions") or [{"name": "power", "value_text": "500"}])[0].get("name") == "power"
            else {"record_id": f"record:{row['evidence_id']}", "source_id": row["candidate_id"],
                  "provenance_refs": [row["evidence_id"]]}
            for row in parameters["evidence_packet"]["evidence_items"]
        ],
        "source_provenance": [{"source_id": "c01", "source_type": "other"}],
    }


def screening_result(parameters):
    rows = parameters["candidates"]
    return {"topk_selection": {"selected_for_p03": [{**r, "selection_decision": "priority", "selection_reason": "relevant", "priority_rank": i + 1, "evidence_roles": [], "relevant_target_ids": []} for i, r in enumerate(rows)], "degraded": False}, "summary": {"selected_count": len(rows), "unreviewed_count": 0}}


def test_required_coverage_stops_after_initial_eight():
    calls = {"reader": [], "evidence": 0}
    def wrapped_reader(payload):
        calls["reader"].append(len(payload["topk_selection"]["selected_for_p03"]))
        return reader(payload)
    def llm(skill_id, parameters, **_):
        if skill_id == "opentrajectory_source_screening_selection": return screening_result(parameters)
        if skill_id == "pvd_evidence_node_extraction":
            calls["evidence"] += 1
            return evidence(["target_001", "target_002"])
        if skill_id == "pvd_research_record_structuring": return record_output(parameters)
        raise AssertionError(skill_id)
    payload = base_payload(); payload.pop("candidate_count")
    result = mod.run_literature_evidence_closure(payload, source_acquisition=source, progressive_reading=wrapped_reader, llm_child=llm)
    assert calls["reader"] == [8]
    assert calls["evidence"] == 1
    assert result["coverage"]["decision_coverage_sufficient"] is True
    assert result["coverage"]["early_stop_reason"] == "required_evidence_coverage_satisfied"
    assert result["conditional_evidence_linkage"] == [{"parameter_key": "substrate_temperature", "target_ids": ["target_002"], "evidence_refs": ["ev:target_002"]}]


def test_missing_required_target_admits_only_four_paper_expansion():
    calls = {"reader": [], "evidence": 0}
    def wrapped_reader(payload):
        calls["reader"].append(len(payload["topk_selection"]["selected_for_p03"]))
        return reader(payload)
    def llm(skill_id, parameters, **_):
        if skill_id == "opentrajectory_source_screening_selection": return screening_result(parameters)
        if skill_id == "pvd_evidence_node_extraction":
            calls["evidence"] += 1
            return evidence(["target_001"] if calls["evidence"] == 1 else ["target_002"])
        if skill_id == "pvd_research_record_structuring": return record_output(parameters)
        raise AssertionError(skill_id)
    payload = base_payload(); payload.pop("candidate_count")
    result = mod.run_literature_evidence_closure(payload, source_acquisition=source, progressive_reading=wrapped_reader, llm_child=llm)
    assert calls["reader"] == [8, 2]
    assert result["coverage"]["decision_coverage_sufficient"] is True
    assert result["coverage"]["waves_completed"] == 2
    assert result["coverage"]["early_stop_reason"] == "required_evidence_coverage_satisfied_after_expansion"


def test_screening_failure_preserves_real_retrieval_order():
    def llm(skill_id, parameters, **_):
        if skill_id == "opentrajectory_source_screening_selection": raise RuntimeError("provider unavailable")
        if skill_id == "pvd_evidence_node_extraction": return evidence(["target_001", "target_002"])
        if skill_id == "pvd_research_record_structuring": return record_output(parameters)
        raise AssertionError(skill_id)
    payload = base_payload(); payload.pop("candidate_count")
    result = mod.run_literature_evidence_closure(payload, source_acquisition=source, progressive_reading=reader, llm_child=llm)
    assert result["selection"]["mode"] == "retrieval_order_fallback"
    assert result["selection"]["selected_candidate_ids"][:3] == ["c01", "c02", "c03"]
    assert "scientific_screening_unavailable:retrieval_order_preserved" in result["warnings"]


def test_grouping_failure_uses_deterministic_singletons():
    def llm(skill_id, parameters, **_):
        if skill_id == "opentrajectory_source_screening_selection": return screening_result(parameters)
        if skill_id == "pvd_evidence_node_extraction": return evidence(["target_001", "target_002"])
        if skill_id == "pvd_research_record_structuring": raise RuntimeError("grouping failed")
        raise AssertionError(skill_id)
    def singleton(parameters): return SimpleNamespace(output=record_output(parameters))
    payload = base_payload(); payload.pop("candidate_count")
    result = mod.run_literature_evidence_closure(payload, source_acquisition=source, progressive_reading=reader, llm_child=llm, singleton_materializer=singleton)
    assert {ref for row in result["research_records"] for ref in row["provenance_refs"]} == {"ev:target_001", "ev:target_002"}
    assert "research_record_grouping_unavailable:singleton_closure_used" in result["warnings"]




def test_screening_fills_remaining_reading_capacity_from_real_retrieval_order():
    candidates=[{'candidate_id':f'c{i:02d}','title':f'Paper {i}','provenance':[{'source':'openalex'}]} for i in range(1,15)]
    screening={
        'topk_selection':{
            'selected_for_p03':[{**candidates[2], 'selection_decision':'priority'}],
            'excluded_examples':[{'candidate_id':'c02'}],
            'degraded':False,
        },
        'summary':{'selected_count':1},
    }
    selected, meta=mod._screened_selection(screening,candidates)
    ids=[row['candidate_id'] for row in selected]
    assert ids[0]=='c03'
    assert 'c02' not in ids
    assert len(ids)==12
    assert meta['retrieval_fill_used'] is True


def test_all_screened_excluded_still_preserves_fulltext_fallback():
    candidates=[{'candidate_id':'c01','title':'P1','provenance':[{'source':'openalex'}]}]
    screening={
        'topk_selection':{
            'selected_for_p03':[],
            'excluded_examples':[{'candidate_id':'c01'}],
            'degraded':False,
        },
        'summary':{},
    }
    selected, meta=mod._screened_selection(screening,candidates)
    assert [row['candidate_id'] for row in selected]==['c01']
    assert meta['mode']=='retrieval_order_fallback'


def test_empty_selected_rows_use_nonexcluded_retrieval_candidates_before_full_fallback():
    candidates=[{"candidate_id":"a"},{"candidate_id":"b"},{"candidate_id":"c"}]
    screening={
        "topk_selection":{
            "selected_for_p03":[],
            "excluded_examples":[{"candidate_id":"a"}],
            "degraded":False,
        },
        "summary":{},
    }
    selected, meta=mod._screened_selection(screening,candidates)
    assert [row["candidate_id"] for row in selected] == ["b","c"]
    assert meta["mode"] == "scientific_screening_with_retrieval_fill"


def test_coverage_usable_count_excludes_not_comparable_evidence():
    schema={
        "evidence_targets":[{
            "target_id":"t1",
            "requiredness":"required",
            "covered_parameter_keys":["power"],
        }]
    }
    packet={"evidence_items":[
        {"evidence_id":"e1","target_match_status":"not_comparable","matched_target_ids":["t1"]},
        {"evidence_id":"e2","target_match_status":"matched","matched_target_ids":["t1"]},
    ]}
    coverage=mod._coverage(schema,packet)
    assert coverage["usable_evidence_count"] == 1
    assert coverage["decision_coverage_sufficient"] is True


def test_model_paper_projection_matches_407b_budget_and_preserves_anchor_identity():
    content_a = "A" * 9000
    content_b = "B" * 9000
    hash_a = mod.hashlib.sha256(content_a.encode("utf-8")).hexdigest()
    hash_b = mod.hashlib.sha256(content_b.encode("utf-8")).hexdigest()
    packet = {
        "candidate_id": "paper-1",
        "title": "Long paper",
        "doi": "10.test/1",
        "arxiv_id": "",
        "sections": [
            {"section_name": "Methods", "content": content_a, "content_hash": hash_a},
            {"section_name": "Results", "content": content_b, "content_hash": hash_b},
        ],
        "source_anchors": [
            {"anchor_id": "a1", "candidate_id": "paper-1", "section_name": "Methods", "content_hash": hash_a},
            {"anchor_id": "a2", "candidate_id": "paper-1", "section_name": "Results", "content_hash": hash_b},
        ],
        "extra_full_packet_field": "finalizer-only",
    }
    projected = mod._model_paper_packet(packet)
    assert projected["candidate_id"] == "paper-1"
    assert [row["source_anchor_id"] for row in projected["anchored_sections"]] == ["a1", "a2"]
    assert sum(len(row["content"]) for row in projected["anchored_sections"]) <= 8000
    assert "sections" not in projected
    assert "source_anchors" not in projected
    assert "extra_full_packet_field" not in projected

    canonical = mod._evidence_skill_packet(packet)
    assert canonical["sections"] == packet["sections"]
    assert canonical["source_anchors"] == packet["source_anchors"]
    assert canonical["extra_full_packet_field"] == "finalizer-only"
    assert canonical["anchored_sections"] == projected["anchored_sections"]


def test_evidence_child_keeps_full_finalizer_packets_and_attaches_bounded_provider_view():
    captured = {}
    def llm(skill_id, parameters, **_):
        if skill_id == "opentrajectory_source_screening_selection":
            return screening_result(parameters)
        if skill_id == "pvd_evidence_node_extraction":
            captured["packets"] = parameters["paper_packets"]
            return evidence(["target_001", "target_002"])
        if skill_id == "pvd_research_record_structuring":
            return record_output(parameters)
        raise AssertionError(skill_id)
    payload = base_payload(candidate_count=1)
    payload.pop("candidate_count")
    result = mod.run_literature_evidence_closure(
        payload, source_acquisition=lambda _p: source({"candidate_count": 1}),
        progressive_reading=reader, llm_child=llm
    )
    packets = captured["packets"]
    assert len(packets) == 1
    assert packets[0]["sections"]
    assert packets[0]["source_anchors"]
    assert packets[0]["anchored_sections"]
    assert sum(len(row["content"]) for row in packets[0]["anchored_sections"]) <= 8000
    assert result["coverage"]["decision_coverage_sufficient"] is True


def test_child_skill_failure_preserves_classification_in_warning():
    def llm(skill_id, parameters, **_):
        if skill_id == "opentrajectory_source_screening_selection":
            return screening_result(parameters)
        if skill_id == "pvd_evidence_node_extraction":
            raise mod.ChildSkillFailure({
                "state": "failed",
                "failureClassification": "provider_response_contract_invalid",
                "summary": "No usable Evidence could be extracted from the available papers.",
            })
        raise AssertionError(skill_id)
    payload = base_payload(candidate_count=1)
    payload.pop("candidate_count")
    result = mod.run_literature_evidence_closure(
        payload, source_acquisition=lambda _p: source({"candidate_count": 1}),
        progressive_reading=reader, llm_child=llm
    )
    assert any(
        "classification=provider_response_contract_invalid" in warning
        for warning in result["warnings"]
    )
    assert result["coverage"]["decision_coverage_sufficient"] is False


def test_record_grouping_receives_407b_compact_views_alongside_canonical_finalizer_inputs():
    captured = {}
    def llm(skill_id, parameters, **_):
        if skill_id == "opentrajectory_source_screening_selection":
            return screening_result(parameters)
        if skill_id == "pvd_evidence_node_extraction":
            return {
                "evidence_items": [{
                    "evidence_id": "ev:1",
                    "candidate_id": "c01",
                    "section_name": "Methods",
                    "evidence_type": "experimental_condition",
                    "assertion_mode": "direct_observation",
                    "claim": "Power was reported.",
                    "evidence_quote": "AlScN sputtering power and gas-flow conditions.",
                    "conditions": [{"name": "power", "value_text": "500", "unit": "W"}],
                    "metric": None,
                    "mechanism": None,
                    "comparison": None,
                    "qualifiers": [],
                    "matched_target_ids": ["target_001", "target_002"],
                    "target_match_status": "matched",
                }],
                "summary": {},
                "extraction_warnings": [],
            }
        if skill_id == "pvd_research_record_structuring":
            captured.update(parameters)
            return record_output(parameters)
        raise AssertionError(skill_id)
    payload = base_payload(candidate_count=1)
    payload.pop("candidate_count")
    mod.run_literature_evidence_closure(
        payload,
        source_acquisition=lambda _p: source({"candidate_count": 1}),
        progressive_reading=reader,
        llm_child=llm,
    )
    assert "evidence_packet" in captured and "source_material" in captured
    assert captured["evidence_index"]["evidence_items"] == [{
        "evidence_id": "ev:1",
        "candidate_id": "c01",
        "evidence_type": "experimental_condition",
        "section_name": "Methods",
        "matched_target_ids": ["target_001", "target_002"],
    }]
    assert captured["source_index"]["paper_packets"][0]["candidate_id"] == "c01"
    catalog = captured["atomic_grounding_catalog"]
    assert catalog
    assert all(set(row) == {"grounded_ref", "text", "evidence_id"} for row in catalog)
    assert {row["evidence_id"] for row in catalog} == {"ev:1"}
