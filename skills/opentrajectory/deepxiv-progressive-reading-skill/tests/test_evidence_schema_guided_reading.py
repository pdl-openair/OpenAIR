from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import ValidationError, validate

SKILL = Path(__file__).resolve().parents[1]


def load_adapter():
    path = SKILL / "adapter" / "progressive_reading_adapter.py"
    spec = importlib.util.spec_from_file_location("evidence_guided_progressive", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


adapter = load_adapter()
INPUT_SCHEMA = json.loads((SKILL / "input.schema.json").read_text(encoding="utf-8"))
OUTPUT_SCHEMA = json.loads((SKILL / "output.schema.json").read_text(encoding="utf-8"))


def target(
    target_id: str,
    description: str,
    *,
    target_type: str = "other",
    scientific_role: str = "other",
    requiredness: str = "required",
    conditions: tuple[str, ...] = (),
    comparisons: tuple[str, ...] = (),
):
    return {
        "target_id": target_id,
        "target_type": target_type,
        "scientific_role": scientific_role,
        "description": description,
        "requiredness": requiredness,
        "completion_criteria": "The explicit target information is located in source content with a source anchor.",
        "allowed_evidence_forms": ["text", "table"],
        "condition_dimensions": list(conditions),
        "comparison_requirements": list(comparisons),
        "provenance_requirements": ["source anchor"],
    }


def evidence_schema(*targets, schema_id="guided-reading"):
    return {
        "schema_id": schema_id,
        "schema_version": "0.1.0",
        "task_context": {
            "research_question": "Which source content is needed for the current research judgment?",
            "task_scope": "progressive reading test",
            "domain_hints": [],
            "design_assumptions": [],
        },
        "evidence_targets": list(targets),
        "gap_taxonomy": ["missing_condition", "missing_metric", "missing_baseline"],
        "uncertainty_rules": [{
            "rule_id": "u1",
            "trigger": "Target content is not located in read material",
            "handling": "Keep the target unresolved",
        }],
    }


def request(evidence, *, max_sections=3, candidate=None, config=None):
    if candidate is None:
        candidate = {
            "candidate_id": "paper-1",
            "arxiv_id": "2401.12345v2",
            "title": "Target paper",
        }
    reading_config = {
        "max_papers": 1,
        "max_sections_per_paper": max_sections,
        "allow_raw_fallback": False,
    }
    if config:
        reading_config.update(config)
    return {
        "topk_selection": {"selected_for_p03": [candidate]},
        "evidence_schema": evidence,
        "reading_config": reading_config,
    }


class StructuredReader:
    def __init__(self, sections: dict[str, str], *, observed="2401.12345v2", raw_text=""):
        self.sections = sections
        self.observed = observed
        self.raw_text = raw_text
        self.calls: list[tuple[str, str]] = []

    def brief(self, arxiv_id):
        self.calls.append(("brief", arxiv_id))
        return {"title": "Target paper", "arxiv_id": self.observed}

    def head(self, arxiv_id):
        self.calls.append(("head", arxiv_id))
        return {
            "title": "Target paper",
            "arxiv_id": self.observed,
            "sections": {name: {} for name in self.sections},
        }

    def section(self, arxiv_id, name):
        self.calls.append(("section", name))
        return {
            "section_name": name,
            "arxiv_id": self.observed,
            "content": self.sections[name],
        }

    def raw(self, arxiv_id):
        self.calls.append(("raw", arxiv_id))
        return {"arxiv_id": self.observed, "content": self.raw_text}


def target_result(output, target_id, packet_index=0):
    rows = output["paper_packets"][packet_index]["diagnostics"]["target_reading"]
    return next(row for row in rows if row["target_id"] == target_id)


def scientific_text(sentence: str) -> str:
    return (sentence + " ") * 80


def test_public_contract_requires_direct_evidence_schema_and_rejects_outer_alias():
    candidate = {"candidate_id": "paper-1", "arxiv_id": "2401.12345v2", "title": "Target paper"}
    valid = request(evidence_schema(target("target_001", "target information")), candidate=candidate)
    validate(valid, INPUT_SCHEMA)

    missing = {"topk_selection": valid["topk_selection"]}
    with pytest.raises(ValidationError):
        validate(missing, INPUT_SCHEMA)
    with pytest.raises(ValueError, match="evidence_schema"):
        adapter.progressive_read_candidates(missing, reader=StructuredReader({"Results": scientific_text("target information")}))

    outer_alias = {
        "topk_selection": valid["topk_selection"],
        "evidence_schema_design": {"status": "ready", "evidence_schema": valid["evidence_schema"]},
    }
    with pytest.raises(ValidationError):
        validate(outer_alias, INPUT_SCHEMA)
    with pytest.raises(ValueError, match="evidence_schema"):
        adapter.progressive_read_candidates(outer_alias, reader=StructuredReader({"Results": scientific_text("target information")}))


def test_original_failure_root_is_closed_mechanism_target_drives_first_deep_read():
    evidence = evidence_schema(target(
        "target_001",
        "interfacial charge transfer",
        target_type="mechanism_evidence",
        scientific_role="mechanism",
    ))
    reader = StructuredReader({
        "Methods": scientific_text("Sample preparation and measurement protocol."),
        "Results": scientific_text("Performance results are reported."),
        "Mechanism Analysis": scientific_text("The interfacial charge transfer pathway is discussed from the measured observations."),
    })

    output = adapter.progressive_read_candidates(request(evidence, max_sections=1), reader=reader)
    validate(output, OUTPUT_SCHEMA)

    section_calls = [call[1] for call in reader.calls if call[0] == "section"]
    assert section_calls == ["Mechanism Analysis"]
    assert output["status"] == "success"
    packet = output["paper_packets"][0]
    assert [section["section_name"] for section in packet["sections"]] == ["Mechanism Analysis"]
    result = target_result(output, "target_001")
    assert result["status"] == "located_in_read_content"
    assert result["matched_cues"] == ["interfacial charge transfer"]
    assert result["missing_cues"] == []
    assert result["source_anchor_ids"] == [packet["source_anchors"][0]["anchor_id"]]


def test_required_target_not_found_stays_unresolved_and_degrades_without_fake_provider_failure():
    evidence = evidence_schema(target(
        "target_001",
        "critical current density",
        target_type="performance_metric",
        scientific_role="metric",
    ))
    reader = StructuredReader({
        "Results": scientific_text("Voltage response and resistance are reported."),
        "Discussion": scientific_text("The authors discuss the observed trends."),
    })

    output = adapter.progressive_read_candidates(request(evidence, max_sections=2), reader=reader)
    validate(output, OUTPUT_SCHEMA)

    assert output["status"] == "degraded"
    assert output["paper_packets"][0]["material_status"] == "MATERIAL_READY"
    assert output["material_attempts"] == []
    result = target_result(output, "target_001")
    assert result == {
        "target_id": "target_001",
        "target_type": "performance_metric",
        "scientific_role": "metric",
        "requiredness": "required",
        "status": "unresolved",
        "matched_cues": [],
        "missing_cues": ["critical current density"],
        "source_anchor_ids": [],
    }
    assert not any(call[0] == "raw" for call in reader.calls)


def test_multiple_targets_drive_progressive_methods_then_characterization_and_stop_when_located():
    evidence = evidence_schema(
        target(
            "target_001",
            "annealing temperature",
            target_type="experimental_condition",
            scientific_role="condition",
        ),
        target(
            "target_002",
            "grain size",
            target_type="characterization_observation",
            scientific_role="characterization",
        ),
    )
    reader = StructuredReader({
        "Experimental Setup": scientific_text("The annealing temperature was controlled for every specimen."),
        "Characterization Results": scientific_text("The grain size was measured from microscopy images."),
        "Discussion": scientific_text("Additional interpretation follows."),
    })

    output = adapter.progressive_read_candidates(request(evidence, max_sections=3), reader=reader)
    validate(output, OUTPUT_SCHEMA)

    section_calls = [call[1] for call in reader.calls if call[0] == "section"]
    assert section_calls == ["Experimental Setup", "Characterization Results"]
    assert output["status"] == "success"
    assert target_result(output, "target_001")["status"] == "located_in_read_content"
    assert target_result(output, "target_002")["status"] == "located_in_read_content"


def test_comparison_dimension_must_also_be_located_before_target_is_resolved():
    evidence = evidence_schema(target(
        "target_001",
        "overall survival",
        target_type="performance_metric",
        scientific_role="metric",
        conditions=("PD-L1 status",),
        comparisons=("standard therapy",),
    ))
    reader = StructuredReader({
        "Methods": scientific_text("Patients were stratified by PD-L1 status before analysis."),
        "Comparative Results": scientific_text("Overall survival was compared against standard therapy in the prespecified groups."),
        "Discussion": scientific_text("Clinical interpretation is discussed."),
    })

    output = adapter.progressive_read_candidates(request(evidence, max_sections=3), reader=reader)
    validate(output, OUTPUT_SCHEMA)

    section_calls = [call[1] for call in reader.calls if call[0] == "section"]
    assert section_calls == ["Methods", "Comparative Results"]
    result = target_result(output, "target_001")
    assert result["status"] == "located_in_read_content"
    assert result["matched_cues"] == ["overall survival", "PD-L1 status", "standard therapy"]
    assert result["missing_cues"] == []
    assert len(result["source_anchor_ids"]) == 2


@pytest.mark.parametrize(
    "description,target_type,role,section_name,content",
    [
        (
            "redshift distribution",
            "attribute",
            "metric",
            "Results",
            "The redshift distribution is reported for the observed galaxy sample.",
        ),
        (
            "species richness",
            "performance_metric",
            "metric",
            "Results",
            "Species richness is reported for each survey plot.",
        ),
        (
            "coercive field",
            "characterization_observation",
            "characterization",
            "Characterization",
            "The coercive field is measured from the hysteresis loop.",
        ),
    ],
)
def test_cross_domain_evidence_schemas_use_same_general_mechanism(description, target_type, role, section_name, content):
    evidence = evidence_schema(target(
        "target_001",
        description,
        target_type=target_type,
        scientific_role=role,
    ))
    reader = StructuredReader({
        "Methods": scientific_text("Standard study procedures are described."),
        section_name: scientific_text(content),
        "Discussion": scientific_text("Interpretation follows."),
    })

    output = adapter.progressive_read_candidates(request(evidence, max_sections=2), reader=reader)
    validate(output, OUTPUT_SCHEMA)
    assert target_result(output, "target_001")["status"] == "located_in_read_content"
    assert output["status"] == "success"


def test_required_target_does_not_enable_raw_fallback_by_itself():
    evidence = evidence_schema(target(
        "target_001",
        "hidden target phrase",
        target_type="performance_metric",
        scientific_role="metric",
    ))
    reader = StructuredReader(
        {"Results": scientific_text("Readable results without the requested phrase.")},
        raw_text=scientific_text("hidden target phrase"),
    )

    output = adapter.progressive_read_candidates(request(evidence, max_sections=1), reader=reader)
    validate(output, OUTPUT_SCHEMA)
    assert output["status"] == "degraded"
    assert target_result(output, "target_001")["status"] == "unresolved"
    assert not any(call[0] == "raw" for call in reader.calls)
    assert output["audit"]["raw_call_count"] == 0


def test_input_fingerprint_includes_evidence_schema_not_only_selected_sources():
    candidate = {"candidate_id": "paper-1", "arxiv_id": "2401.12345v2", "title": "Target paper"}
    reader1 = StructuredReader({"Results": scientific_text("metric alpha is reported.")})
    reader2 = StructuredReader({"Results": scientific_text("metric alpha is reported.")})
    first = adapter.progressive_read_candidates(
        request(evidence_schema(target("target_001", "metric alpha"), schema_id="schema-a"), candidate=candidate),
        reader=reader1,
    )
    second = adapter.progressive_read_candidates(
        request(evidence_schema(target("target_001", "metric beta"), schema_id="schema-b"), candidate=candidate),
        reader=reader2,
    )
    assert first["audit"]["input_fingerprint"] != second["audit"]["input_fingerprint"]


def test_non_arxiv_fulltext_keeps_original_content_and_reports_target_location_without_reshape():
    evidence = evidence_schema(target(
        "target_001",
        "hazard ratio",
        target_type="performance_metric",
        scientific_role="metric",
    ))
    candidate = {"candidate_id": "doi-paper", "doi": "10.1234/example", "title": "Clinical paper"}
    original = scientific_text("The hazard ratio is reported with its confidence interval.")

    class FulltextReader:
        last_diagnostics = []

        def read(self, _candidate):
            return SimpleNamespace(
                text=original,
                source_url="https://publisher.example/article",
                reading_mode="html_fulltext",
                acquisition_provider="publisher_html",
                material_type="article_fulltext",
                parser_used="beautifulsoup4",
                provider_observed_title="Clinical paper",
                provider_observed_identity={"doi": "10.1234/example"},
                diagnostics={"steps": [{"step": "publisher_html", "status": "success"}]},
                tables=(),
            )

    output = adapter.progressive_read_candidates(
        request(evidence, candidate=candidate, config={"enable_non_arxiv_fulltext": True}),
        fulltext_reader=FulltextReader(),
    )
    validate(output, OUTPUT_SCHEMA)
    packet = output["paper_packets"][0]
    assert packet["sections"][0]["content"] == original.strip()
    assert packet["provider"] == "publisher_html"
    assert packet["source_locator"] == "https://publisher.example/article"
    result = target_result(output, "target_001")
    assert result["status"] == "located_in_read_content"
    assert result["source_anchor_ids"] == [packet["source_anchors"][0]["anchor_id"]]
