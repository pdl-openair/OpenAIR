from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys

SKILL = Path(__file__).resolve().parents[1]


def finalizer_module():
    path = SKILL / "scripts" / "finalize.py"
    spec = importlib.util.spec_from_file_location("research_record_regression_finalize", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def packet(candidate_id: str, title: str, arxiv_id: str, doi: str, sections: list[tuple[str, str]]):
    identity = {
        "candidate_id": candidate_id,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "arxiv_work_id": arxiv_id,
        "arxiv_version": "",
        "pmcid": "",
        "openalex_id": f"OA-{candidate_id}",
        "title": title,
    }
    section_rows = []
    anchors = []
    for index, (name, content) in enumerate(sections, 1):
        content_hash = sha256(content.encode("utf-8")).hexdigest()
        locator = f"https://example.org/{candidate_id}#{name.lower().replace(' ', '-')}"
        section_rows.append(
            {
                "section_name": name,
                "content": content,
                "content_hash": content_hash,
                "source_type": "fulltext",
                "provider": "test-provider",
                "source_locator": locator,
                "paper_identity": identity,
                "material_type": "article_fulltext",
                "material_status": "MATERIAL_READY",
            }
        )
        anchors.append(
            {
                "anchor_id": f"{candidate_id}_a{index}",
                "candidate_id": candidate_id,
                "arxiv_id": arxiv_id,
                "section_name": name,
                "content_hash": content_hash,
                "source_type": "fulltext",
                "provider": "test-provider",
                "source_locator": locator,
            }
        )
    observed = {
        "doi": doi,
        "arxiv_id": arxiv_id,
        "arxiv_work_id": arxiv_id,
        "arxiv_version": "",
        "pmcid": "",
        "openalex_id": f"OA-{candidate_id}",
    }
    return {
        "candidate_id": candidate_id,
        "arxiv_id": arxiv_id,
        "doi": doi,
        "title": title,
        "read_depth": "pdf_fulltext",
        "reading_mode": "section_priority",
        "brief": {},
        "head": {},
        "sections": section_rows,
        "source_anchors": anchors,
        "material_status": "MATERIAL_READY",
        "material_type": "article_fulltext",
        "requested_identity": identity,
        "provider_observed_identity": observed,
        "resolved_identity": identity,
        "paper_identity": identity,
        "provider": "test-provider",
        "source_locator": f"https://example.org/{candidate_id}",
        "provider_observed_title": title,
        "provenance": [{"source": "test-provider", "id": candidate_id}],
        "tables": [],
        "diagnostics": {},
    }


def target(target_id: str, target_type: str, role: str, description: str):
    return {
        "target_id": target_id,
        "target_type": target_type,
        "scientific_role": role,
        "description": description,
        "requiredness": "required",
        "completion_criteria": "source-local record exists",
        "allowed_evidence_forms": [description],
        "condition_dimensions": [],
        "comparison_requirements": [],
        "provenance_requirements": ["source_anchor"],
    }


def record(
    anchor: str,
    record_type: str,
    research_object: str,
    conditions: list[dict],
    statement: str,
    quote: str,
    identity: str,
    assertion: str,
    targets: list[str],
    *,
    values=None,
    mechanism=None,
    comparison=None,
    qualifiers=None,
    uncertainty=None,
    unknown_fields=None,
):
    return {
        "source_anchor_id": anchor,
        "record_type": record_type,
        "research_object": research_object,
        "conditions": conditions,
        "result": {
            "statement": statement,
            "values": values or [],
            "mechanism": mechanism,
            "comparison": comparison,
        },
        "information_identity": identity,
        "assertion_mode": assertion,
        "source_quote": quote,
        "qualifiers": qualifiers or [],
        "uncertainty": uncertainty,
        "unknown_fields": unknown_fields or [],
        "relevant_target_ids": targets,
    }


def main() -> None:
    f = finalizer_module()

    source1 = packet(
        "src1",
        "Representative mixed scientific records",
        "2608.00001",
        "10.1000/src1",
        [
            ("Transport", "At 300 K, Membrane M1 reached proton conductivity of 0.12 S cm−1."),
            ("Cycling", "At 1 C, Cell A retained 92% capacity after 500 cycles, whereas Cell B retained 81%."),
            ("Mechanism", "The authors suggest that oxygen vacancies may enhance adsorption, which could accelerate the reaction."),
            ("Characterization", "XRD of Film F showed a (002) peak at 36.2°."),
            ("Computation", "DFT calculations for Surface S gave an adsorption energy of -1.35 eV."),
            ("Limitation", "The study did not report humidity for Membrane M1, limiting comparison across measurements."),
            ("Association", "Higher porosity was associated with increased uptake in Sample P."),
            ("Causal", "The report states that catalyst poisoning causes a decrease in reaction rate."),
        ],
    )
    source2 = packet(
        "src2",
        "Independent transport measurement",
        "2608.00002",
        "10.1000/src2",
        [("Transport", "At 350 K, Membrane M1 reached proton conductivity of 0.09 S cm−1.")],
    )
    targets = [
        target("target_001", "performance_metric", "metric", "quantitative_result"),
        target("target_002", "experimental_condition", "condition", "experimental_condition"),
        target("target_003", "comparison_baseline", "comparability", "comparison"),
        target("target_004", "mechanism_evidence", "mechanism", "mechanism_statement"),
        target("target_005", "characterization_observation", "characterization", "characterization_observation"),
        target("target_006", "theoretical_calculation", "theoretical_explanation", "computation_result"),
        target("target_007", "limitation", "limitation", "limitation"),
    ]
    request = {
        "task_id": "research-record-regression",
        "paper_packets": [source1, source2],
        "evidence_schema": {
            "schema_id": "schema_rr_regression",
            "schema_version": "0.1.0",
            "task_context": {
                "research_question": "Extract source-local research records without forming downstream judgments.",
                "task_scope": "cross-domain regression",
                "domain_hints": ["electrochemistry", "catalysis", "characterization"],
                "design_assumptions": [],
            },
            "evidence_targets": targets,
            "gap_taxonomy": ["missing_condition", "missing_metric", "missing_mechanism", "missing_baseline"],
            "uncertainty_rules": [],
        },
    }

    drafts = [
        record(
            "src1_a1", "quantitative_result", "Membrane M1",
            [{"name": "temperature", "status": "reported", "value_text": "300", "unit": "K"}],
            "Membrane M1 reached proton conductivity of 0.12 S cm−1 at 300 K.",
            "At 300 K, Membrane M1 reached proton conductivity of 0.12 S cm−1.",
            "direct_source_fact", "direct_observation", ["target_001"],
            values=[{"name": "proton conductivity", "value_text": "0.12", "unit": "S cm−1"}],
        ),
        record(
            "src1_a1", "experimental_condition", "Membrane M1",
            [{"name": "temperature", "status": "reported", "value_text": "300", "unit": "K"}],
            "Membrane M1 was reported at 300 K.",
            "At 300 K, Membrane M1 reached proton conductivity of 0.12 S cm−1.",
            "direct_source_fact", "direct_observation", ["target_002"],
        ),
        record(
            "src1_a2", "comparison", "Cell A and Cell B",
            [{"name": "rate", "status": "reported", "value_text": "1", "unit": "C"}, {"name": "cycle_count", "status": "reported", "value_text": "500", "unit": "cycles"}],
            "At 1 C after 500 cycles, Cell A retained 92% and Cell B retained 81%.",
            "At 1 C, Cell A retained 92% capacity after 500 cycles, whereas Cell B retained 81%.",
            "direct_source_fact", "direct_observation", ["target_003"],
            values=[
                {"name": "Cell A capacity retention", "value_text": "92", "unit": "%"},
                {"name": "Cell B capacity retention", "value_text": "81", "unit": "%"},
            ],
            comparison={"subject": "Cell A", "baseline": "Cell B", "dimension": "capacity retention", "relation": "higher", "condition_text": ["1 C", "500 cycles"]},
        ),
        record(
            "src1_a3", "mechanism_statement", "oxygen vacancies", [],
            "The authors suggest that oxygen vacancies may enhance adsorption and could accelerate the reaction.",
            "The authors suggest that oxygen vacancies may enhance adsorption, which could accelerate the reaction.",
            "author_interpretation", "author_interpretation", ["target_004"],
            mechanism={"cause": "oxygen vacancies may enhance adsorption", "effect": "accelerate the reaction"},
            qualifiers=["authors suggest", "may", "could"],
            uncertainty="Tentative author interpretation.",
        ),
        record(
            "src1_a4", "characterization_observation", "Film F", [],
            "XRD of Film F showed a (002) peak at 36.2°.",
            "XRD of Film F showed a (002) peak at 36.2°.",
            "direct_source_fact", "direct_observation", ["target_005"],
            values=[{"name": "XRD peak position", "value_text": "36.2", "unit": "°"}],
        ),
        record(
            "src1_a5", "computation_result", "Surface S", [],
            "DFT calculations for Surface S gave an adsorption energy of -1.35 eV.",
            "DFT calculations for Surface S gave an adsorption energy of -1.35 eV.",
            "direct_source_fact", "direct_observation", ["target_006"],
            values=[{"name": "adsorption energy", "value_text": "-1.35", "unit": "eV"}],
        ),
        record(
            "src1_a6", "limitation", "Membrane M1",
            [{"name": "humidity", "status": "unknown", "value_text": None, "unit": None}],
            "The study did not report humidity for Membrane M1, limiting comparison across measurements.",
            "The study did not report humidity for Membrane M1, limiting comparison across measurements.",
            "other_source_information", "source_statement", ["target_007"],
            unknown_fields=["humidity value"],
        ),
        record(
            "src1_a7", "qualitative_result", "Sample P", [],
            "Higher porosity was associated with increased uptake in Sample P.",
            "Higher porosity was associated with increased uptake in Sample P.",
            "other_source_information", "association", [],
        ),
        record(
            "src1_a8", "mechanism_statement", "catalyst poisoning", [],
            "The report states that catalyst poisoning causes a decrease in reaction rate.",
            "The report states that catalyst poisoning causes a decrease in reaction rate.",
            "other_source_information", "causal_claim", [],
            mechanism={"cause": "catalyst poisoning", "effect": "decrease in reaction rate"},
        ),
        record(
            "src2_a1", "quantitative_result", "Membrane M1",
            [{"name": "temperature", "status": "reported", "value_text": "350", "unit": "K"}],
            "Membrane M1 reached proton conductivity of 0.09 S cm−1 at 350 K.",
            "At 350 K, Membrane M1 reached proton conductivity of 0.09 S cm−1.",
            "direct_source_fact", "direct_observation", [],
            values=[{"name": "proton conductivity", "value_text": "0.09", "unit": "S cm−1"}],
        ),
    ]

    result = f.finalize(request, {"research_records": drafts})
    assert result.status == "succeeded", result
    assert result.output is not None
    rows = result.output["research_records"]
    assert len(rows) == 10
    assert result.output["summary"]["uncovered_required_target_ids"] == []

    types = {row["record_type"] for row in rows}
    for expected in {
        "quantitative_result", "experimental_condition", "comparison", "mechanism_statement",
        "characterization_observation", "computation_result", "limitation", "qualitative_result",
    }:
        assert expected in types

    tentative = next(row for row in rows if row["source_anchor_id"] == "src1_a3")
    assert tentative["information_identity"] == "author_interpretation"
    assert tentative["assertion_mode"] == "author_interpretation"
    causal = next(row for row in rows if row["source_anchor_id"] == "src1_a8")
    assert causal["information_identity"] == "other_source_information"
    assert causal["assertion_mode"] == "causal_claim"
    association = next(row for row in rows if row["source_anchor_id"] == "src1_a7")
    assert association["assertion_mode"] == "association"
    assert association["record_type"] != "mechanism_statement"
    comparison_row = next(row for row in rows if row["source_anchor_id"] == "src1_a2")
    assert comparison_row["research_object"] == "Cell A and Cell B"
    assert [value["value_text"] for value in comparison_row["result"]["values"]] == ["92", "81"]

    limitation = next(row for row in rows if row["source_anchor_id"] == "src1_a6")
    assert limitation["unknown_fields"] == ["humidity value"]
    assert limitation["conditions"][0]["name"] == "humidity"
    assert limitation["conditions"][0]["status"] == "unknown"
    assert limitation["conditions"][0]["value_text"] is None
    assert limitation["condition_status"] == "partially_unknown"
    assert all(
        row["condition_status"] != "partially_unknown"
        for row in rows
        if not any(condition["status"] == "unknown" for condition in row["conditions"])
    )

    # One source may yield many records; every record remains source-local.
    assert sum(row["source"]["candidate_id"] == "src1" for row in rows) == 9
    assert sum(row["source"]["candidate_id"] == "src2" for row in rows) == 1
    assert all(row["source"]["anchor_id"] == row["source_anchor_id"] for row in rows)
    assert all(row["source_quote"] in next(
        section["content"]
        for packet_row in request["paper_packets"]
        if packet_row["candidate_id"] == row["source"]["candidate_id"]
        for section in packet_row["sections"]
        if section["section_name"] == row["source"]["section_name"]
    ) for row in rows)

    serialized = json.dumps(result.output, ensure_ascii=False)
    assert "evidence_items" not in serialized
    assert "evidence_id" not in serialized
    assert "scientific_judgment" not in serialized

    # EvidenceSchema target absence does not create filler records.
    empty = f.finalize(request, {"research_records": []})
    assert empty.status == "succeeded"
    assert empty.output["summary"]["research_record_count"] == 0
    assert empty.output["summary"]["uncovered_required_target_ids"] == [f"target_{i:03d}" for i in range(1, 8)]

    # Abstract-only confirmed source material is retained as degraded, never silently upgraded.
    degraded_request = deepcopy(request)
    degraded_packet = degraded_request["paper_packets"][1]
    degraded_packet["material_status"] = "ABSTRACT_ONLY"
    degraded_packet["material_type"] = "abstract_only"
    degraded_packet["read_depth"] = "abstract_only"
    degraded_packet["sections"][0]["material_status"] = "ABSTRACT_ONLY"
    degraded_packet["sections"][0]["material_type"] = "abstract_only"
    degraded_packet["sections"][0]["source_type"] = "abstract"
    degraded_packet["source_anchors"][0]["source_type"] = "abstract"
    degraded_draft = {"research_records": [deepcopy(drafts[-1])]}
    degraded_draft["research_records"][0]["relevant_target_ids"] = []
    degraded_request["evidence_schema"]["evidence_targets"] = [
        {**target("target_001", "performance_metric", "metric", "quantitative_result"), "requiredness": "optional"}
    ]
    degraded = f.finalize(degraded_request, degraded_draft)
    assert degraded.status == "degraded", degraded
    assert degraded.output["summary"]["degraded"] is True

    print("RESEARCH_RECORD_SPECIALIZED_REGRESSION=PASS")
    print("QUANTITATIVE_RESULT=PASS")
    print("EXPERIMENTAL_CONDITION=PASS")
    print("COMPARISON=PASS")
    print("MECHANISM_IDENTITY_BOUNDARY=PASS")
    print("CHARACTERIZATION_OBSERVATION=PASS")
    print("COMPUTATION_RESULT=PASS")
    print("LIMITATION_AND_UNKNOWN=PASS")
    print("EXACT_QUOTE_AND_SOURCE_GROUNDING=PASS")
    print("ONE_SOURCE_MULTIPLE_RECORDS=PASS")
    print("MULTI_SOURCE_NO_SYNTHESIZED_IDENTITY=PASS")
    print("NO_DOWNSTREAM_EVIDENCE_IDENTITY=PASS")
    print("NO_FILLER_RECORDS=PASS")
    print("DEGRADED_SOURCE_SEMANTICS=PASS")


if __name__ == "__main__":
    main()
