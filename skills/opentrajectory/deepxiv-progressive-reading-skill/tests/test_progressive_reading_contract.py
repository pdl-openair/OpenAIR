from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import ValidationError, validate

SKILL = Path(__file__).resolve().parents[1]


def load(name: str, rel: str):
    path = SKILL / rel
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


adapter = load("contract_progressive", "adapter/progressive_reading_adapter.py")
INPUT_SCHEMA = json.loads((SKILL / "input.schema.json").read_text())
OUTPUT_SCHEMA = json.loads((SKILL / "output.schema.json").read_text())


def neutral_evidence_schema():
    return {
        "schema_id": "test-neutral",
        "schema_version": "0.1.0",
        "task_context": {
            "research_question": "What does the selected source report?",
            "task_scope": "contract regression",
            "domain_hints": [],
            "design_assumptions": [],
        },
        "evidence_targets": [{
            "target_id": "target_001",
            "target_type": "other",
            "scientific_role": "other",
            "description": "target information",
            "requiredness": "optional",
            "completion_criteria": "Locate explicit source content when present.",
            "allowed_evidence_forms": ["text"],
            "condition_dimensions": [],
            "comparison_requirements": [],
            "provenance_requirements": ["source anchor"],
        }],
        "gap_taxonomy": [],
        "uncertainty_rules": [],
    }


def payload(*candidates, config=None, evidence=None):
    out = {
        "topk_selection": {"selected_for_p03": list(candidates)},
        "evidence_schema": evidence or neutral_evidence_schema(),
    }
    if config is not None:
        out["reading_config"] = config
    return out


def test_real_doi_pmcid_candidate_accepts_nullable_identity_without_fabrication():
    candidate = {
        "candidate_id": "semantic_scholar_0_6",
        "title": "A monofluoride ether-based electrolyte solution for fast-charging high-voltage lithium metal batteries",
        "arxiv_id": None,
        "doi": "10.1038/s41467-023-36793-6",
        "pmcid": "9968335",
        "openalex_id": None,
        "external_ids": {
            "doi": "10.1038/s41467-023-36793-6",
            "arxiv_id": None,
            "pmcid": "9968335",
            "openalex_id": None,
        },
    }

    validate(payload(candidate), INPUT_SCHEMA)

    requested = adapter._admission().requested_identity(candidate)
    assert requested["doi"] == "10.1038/s41467-023-36793-6"
    assert requested["pmcid"] == "9968335"
    assert requested["arxiv_id"] == ""
    assert requested["openalex_id"] == ""
    assert adapter.extract_arxiv_id(candidate) is None

    direct_doi_with_null_external_ids = {
        "candidate_id": "doi-only",
        "title": "DOI-only source",
        "arxiv_id": None,
        "doi": "10.1038/s41467-023-36793-6",
        "external_ids": None,
    }
    validate(payload(direct_doi_with_null_external_ids), INPUT_SCHEMA)

    invalid_empty_identifier = dict(direct_doi_with_null_external_ids)
    invalid_empty_identifier["doi"] = ""
    with pytest.raises(ValidationError):
        validate(payload(invalid_empty_identifier), INPUT_SCHEMA)


class DeepXivReader:
    def __init__(self, observed_by_requested=None, section_factory=None, fail_ids=None):
        self.observed_by_requested = observed_by_requested or {}
        self.section_factory = section_factory
        self.fail_ids = set(fail_ids or [])
        self.calls = []

    def observed(self, requested):
        return self.observed_by_requested.get(requested, requested)

    def brief(self, arxiv_id):
        self.calls.append(("brief", arxiv_id))
        if arxiv_id in self.fail_ids:
            raise TimeoutError("provider timeout")
        observed = self.observed(arxiv_id)
        return {"title": f"Paper {observed}", "arxiv_id": observed}

    def head(self, arxiv_id):
        self.calls.append(("head", arxiv_id))
        observed = self.observed(arxiv_id)
        return {"title": f"Paper {observed}", "arxiv_id": observed, "sections": {"Results": {}}}

    def section(self, arxiv_id, name):
        self.calls.append(("section", arxiv_id, name))
        observed = self.observed(arxiv_id)
        if self.section_factory:
            return self.section_factory(arxiv_id, observed, name)
        return {"section_name": name, "arxiv_id": observed, "content": "Measured results and controlled conditions. " * 40}

    def raw(self, arxiv_id):
        self.calls.append(("raw", arxiv_id))
        return {"arxiv_id": self.observed(arxiv_id), "content": "Raw article body with methods and results. " * 40}


def test_A_production_chain_rejects_requested_v2_when_provider_observed_v1():
    requested = "2401.12345v2"
    reader = DeepXivReader({requested: "2401.12345v1"})
    out = adapter.progressive_read_candidates(
        payload({"candidate_id": "c", "arxiv_id": requested, "title": "Paper 2401.12345v1"}),
        reader=reader,
    )
    validate(out, OUTPUT_SCHEMA)
    assert out["status"] == "failed"
    assert out["paper_packets"] == []
    failure = out["material_attempts"][0]
    assert failure["reason"] == "same_work_identity_conflict"
    assert failure["diagnostics"]["identity_basis"] == "arxiv_version_conflict"
    assert failure["diagnostics"]["requested_identity"]["arxiv_version"] == "v2"
    assert failure["diagnostics"]["provider_observed_identity"]["arxiv_version"] == "v1"


def test_A_provider_missing_version_does_not_inherit_requested_version():
    requested = "2401.12345v2"
    reader = DeepXivReader({requested: "2401.12345"})
    out = adapter.progressive_read_candidates(
        payload({"candidate_id": "c", "arxiv_id": requested, "title": "Paper 2401.12345"}),
        reader=reader,
    )
    assert out["status"] == "failed"
    failure = out["material_attempts"][0]
    assert failure["diagnostics"]["identity_basis"] == "arxiv_version_unverified"
    assert failure["diagnostics"]["provider_observed_identity"]["arxiv_version"] == ""


def test_A_provider_responses_with_conflicting_versions_fail_before_admission():
    class InconsistentReader(DeepXivReader):
        def brief(self, arxiv_id):
            return {"title": f"Paper {arxiv_id}", "arxiv_id": "2401.12345v2"}

        def head(self, arxiv_id):
            return {"title": f"Paper {arxiv_id}", "arxiv_id": "2401.12345v2", "sections": {"Results": {}}}

        def section(self, arxiv_id, name):
            return {"section_name": name, "arxiv_id": "2401.12345v1", "content": "Measured results. " * 80}

    out = adapter.progressive_read_candidates(
        payload({"candidate_id": "c", "arxiv_id": "2401.12345v2", "title": "Paper 2401.12345v2"}),
        reader=InconsistentReader(),
    )
    assert out["status"] == "failed"
    assert out["paper_packets"] == []
    assert out["material_attempts"][0]["reason"] == "provider_identity_inconsistent"
    assert out["material_attempts"][0]["diagnostics"]["identity_basis"] == "arxiv_version_conflict"


def test_B_production_chain_rejects_provider_supplement_only_material():
    def supplement(_requested, observed, _name):
        return {
            "section_name": "Supplementary Information",
            "arxiv_id": observed,
            "material_type": "supplementary_material",
            "content": "Supplementary Information\n" + ("supporting dataset " * 100),
        }

    reader = DeepXivReader(section_factory=supplement)
    out = adapter.progressive_read_candidates(
        payload({"candidate_id": "c", "arxiv_id": "2401.12345v2", "title": "Paper 2401.12345v2"}),
        reader=reader,
    )
    validate(out, OUTPUT_SCHEMA)
    assert out["status"] == "failed"
    assert out["paper_packets"] == []
    assert out["material_attempts"][0]["reason"] == "material_type_not_admissible:supplementary_material"
    assert out["material_attempts"][0]["diagnostics"]["material_type"] == "supplementary_material"


def test_C_normal_article_fulltext_closes_identity_provenance_and_anchor_loop():
    requested = "2401.12345v2"
    reader = DeepXivReader()
    request = payload({"candidate_id": "c", "arxiv_id": requested, "title": f"Paper {requested}"})
    first = adapter.progressive_read_candidates(request, reader=reader)
    second = adapter.progressive_read_candidates(request, reader=DeepXivReader())
    validate(first, OUTPUT_SCHEMA)
    assert first["status"] == "success"
    packet = first["paper_packets"][0]
    assert packet["material_status"] == "MATERIAL_READY"
    assert packet["material_type"] == "article_fulltext"
    assert packet["requested_identity"]["arxiv_id"] == requested
    assert packet["provider_observed_identity"]["arxiv_id"] == requested
    assert packet["resolved_identity"]["arxiv_id"] == requested
    assert packet["source_locator"] == f"deepxiv://arxiv/{requested}"
    assert packet["source_anchors"][0]["source_locator"] == packet["source_locator"]
    assert packet["source_anchors"][0]["content_hash"] == packet["sections"][0]["content_hash"]
    assert second["paper_packets"][0]["source_anchors"][0] == packet["source_anchors"][0]


def test_D_all_material_failures_are_failed_with_diagnostics_not_fake_success():
    reader = DeepXivReader(
        {"2401.12345v2": "2401.12345v1"},
        fail_ids={"2402.11111v1"},
    )
    out = adapter.progressive_read_candidates(
        payload(
            {"candidate_id": "version-mismatch", "arxiv_id": "2401.12345v2", "title": "Paper 2401.12345v1"},
            {"candidate_id": "timeout", "arxiv_id": "2402.11111v1", "title": "Paper 2402.11111v1"},
        ),
        reader=reader,
    )
    validate(out, OUTPUT_SCHEMA)
    assert out["status"] == "failed"
    assert out["paper_packets"] == []
    assert out["summary"]["ready_packet_count"] == 0
    assert out["summary"]["failed_candidate_count"] == 2
    assert {row["candidate_id"] for row in out["material_attempts"]} == {"version-mismatch", "timeout"}
    assert all(row["reason"] for row in out["material_attempts"])


def test_E_canonical_input_only_schema_and_production_entry_reject_legacy_inputs():
    canonical = payload({"candidate_id": "c", "arxiv_id": "2401.12345", "title": "Paper 2401.12345"})
    validate(canonical, INPUT_SCHEMA)
    for legacy in (
        {"candidates": canonical["topk_selection"]["selected_for_p03"]},
        {"selected_candidates": canonical["topk_selection"]["selected_for_p03"]},
    ):
        with pytest.raises(ValidationError):
            validate(legacy, INPUT_SCHEMA)
        with pytest.raises(ValueError, match="topk_selection"):
            adapter.progressive_read_candidates(legacy, reader=DeepXivReader())


def test_F_max_papers_and_total_chars_apply_to_real_output_and_summary_truth():
    content = "R" * 6000
    reader = DeepXivReader(section_factory=lambda _r, observed, name: {"section_name": name, "arxiv_id": observed, "content": content})
    candidates = [
        {"candidate_id": f"c{i}", "arxiv_id": f"2401.1234{i}", "title": f"Paper 2401.1234{i}"}
        for i in range(3)
    ]
    out = adapter.progressive_read_candidates(
        payload(*candidates, config={"max_papers": 2, "max_sections_per_paper": 1, "max_total_chars": 10000}),
        reader=reader,
    )
    validate(out, OUTPUT_SCHEMA)
    assert out["summary"]["input_candidate_count"] == 3
    assert out["summary"]["attempted_candidate_count"] == 2
    assert out["summary"]["truncated_candidate_count"] == 1
    assert out["audit"]["output_content_chars"] <= 10000
    assert sum(len(s["content"]) for p in out["paper_packets"] for s in p["sections"]) <= 10000
    assert any(row["reason"] == "payload_budget_exhausted" for row in out["material_attempts"])
    assert out["status"] == "degraded"


def test_F_timeout_and_retry_config_reach_real_deepxiv_reader_creation(monkeypatch):
    captured = {}

    class CapturingReader(DeepXivReader):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            super().__init__()

    monkeypatch.setitem(sys.modules, "deepxiv_sdk", SimpleNamespace(Reader=CapturingReader))
    monkeypatch.delenv("OT_DEEPXIV_TOKEN", raising=False)
    out = adapter.progressive_read_candidates(
        payload(
            {"candidate_id": "c", "arxiv_id": "2401.12345", "title": "Paper 2401.12345"},
            config={"network_timeout_seconds": 7.5, "max_provider_retries": 2},
        )
    )
    validate(out, OUTPUT_SCHEMA)
    assert captured == {"timeout": 7.5, "max_retries": 2}
    assert out["status"] == "success"


def test_abstract_only_is_explicit_degraded_not_verified_fulltext():
    candidate = {
        "candidate_id": "c", "doi": "10.1234/no-fulltext", "title": "Paper",
        "abstract": "Abstract-only material. " * 40,
        "landing_page_url": "https://example.org/paper",
    }
    out = adapter.progressive_read_candidates(payload(candidate), reader=DeepXivReader())
    validate(out, OUTPUT_SCHEMA)
    packet = out["paper_packets"][0]
    assert out["status"] == "degraded"
    assert packet["material_status"] == "ABSTRACT_ONLY"
    assert packet["material_type"] == "abstract_only"
    assert packet["provider_observed_identity"] is None
    assert packet["resolved_identity"] is None
    assert packet["paper_identity"] is None


def test_raw_fallback_remains_explicit_opt_in():
    class EmptySectionReader(DeepXivReader):
        def section(self, arxiv_id, name):
            self.calls.append(("section", arxiv_id, name))
            return {"section_name": name, "arxiv_id": arxiv_id, "content": ""}

    candidate = {"candidate_id": "c", "arxiv_id": "2401.12345", "title": "Paper 2401.12345"}
    off_reader = EmptySectionReader()
    off = adapter.progressive_read_candidates(payload(candidate), reader=off_reader)
    assert not any(call[0] == "raw" for call in off_reader.calls)
    assert off["paper_packets"] == []

    on_reader = EmptySectionReader()
    on = adapter.progressive_read_candidates(payload(candidate, config={"allow_raw_fallback": True}), reader=on_reader)
    assert any(call[0] == "raw" for call in on_reader.calls)
    assert on["paper_packets"][0]["read_depth"] == "raw_fallback"


class FakeFulltextReader:
    def __init__(self, *, observed_doi, material_type="article_fulltext"):
        self.observed_doi = observed_doi
        self.material_type = material_type
        self.last_diagnostics = []

    def read(self, _candidate):
        text = "Methods and results for non-arXiv article. " * 40
        return SimpleNamespace(
            text=text,
            source_url="https://provider.example/article",
            reading_mode="html_fulltext",
            acquisition_provider="publisher_html",
            material_type=self.material_type,
            parser_used="beautifulsoup4",
            provider_observed_title="Non Arxiv Paper",
            provider_observed_identity={"doi": self.observed_doi},
            diagnostics={"steps": [{"step": "publisher_html", "status": "success"}]},
            tables=(),
        )


def test_B_non_arxiv_supplement_type_survives_adapter_and_is_not_ready():
    candidate = {"candidate_id": "c", "doi": "10.1234/requested", "title": "Non Arxiv Paper"}
    out = adapter.progressive_read_candidates(
        payload(candidate, config={"enable_non_arxiv_fulltext": True}),
        fulltext_reader=FakeFulltextReader(
            observed_doi="10.1234/requested",
            material_type="supplementary_material",
        ),
    )
    assert out["paper_packets"] == []
    assert out["status"] == "failed"
    failure = out["material_attempts"][0]
    assert failure["reason"] == "material_type_not_admissible:supplementary_material"
    assert failure["diagnostics"]["material_type"] == "supplementary_material"


def test_versioned_arxiv_request_cannot_be_verified_by_doi_alone():
    candidate = {
        "candidate_id": "c", "arxiv_id": "2401.12345v2",
        "doi": "10.1234/requested", "title": "Non Arxiv Paper",
    }
    out = adapter.progressive_read_candidates(
        payload(candidate, config={"enable_non_arxiv_fulltext": True}),
        reader=DeepXivReader(fail_ids={"2401.12345v2"}),
        fulltext_reader=FakeFulltextReader(observed_doi="10.1234/requested"),
    )
    assert out["paper_packets"] == []
    assert out["status"] == "failed"
    assert any(
        row["diagnostics"].get("identity_basis") == "arxiv_version_unverified"
        for row in out["material_attempts"]
    )


def test_non_arxiv_provider_identity_is_not_backfilled_from_requested_doi():
    candidate = {"candidate_id": "c", "doi": "10.1234/requested", "title": "Non Arxiv Paper"}
    mismatch = adapter.progressive_read_candidates(
        payload(candidate, config={"enable_non_arxiv_fulltext": True}),
        fulltext_reader=FakeFulltextReader(observed_doi="10.9999/other"),
    )
    assert mismatch["paper_packets"] == []
    assert mismatch["status"] == "failed"
    assert mismatch["material_attempts"][0]["diagnostics"]["provider_observed_identity"]["doi"] == "10.9999/other"

    matched = adapter.progressive_read_candidates(
        payload(candidate, config={"enable_non_arxiv_fulltext": True}),
        fulltext_reader=FakeFulltextReader(observed_doi="10.1234/requested"),
    )
    validate(matched, OUTPUT_SCHEMA)
    assert matched["paper_packets"][0]["material_status"] == "MATERIAL_READY"
    assert matched["paper_packets"][0]["provider"] == "publisher_html"
    assert matched["paper_packets"][0]["source_locator"] == "https://provider.example/article"


def test_non_arxiv_direct_pdf_reference_doi_cannot_authenticate_the_pdf():
    """A DOI cited in PDF body is not evidence of the PDF's own identity."""
    fulltext = adapter._fulltext_module()
    target_doi = "10.1234/target"
    pdf_url = "https://publisher.example/wrong-paper.pdf"

    class PdfHttpClient:
        def get(self, url, *, timeout=30.0, follow_redirects=True, headers=None, max_bytes=None):
            return fulltext.HttpResponse(
                status_code=200,
                text="",
                content=b"%PDF-1.4 fake",
                headers={"content-type": "application/pdf"},
                url=url,
            )

    class PdfParser:
        def extract_text(self, pdf_bytes, *, max_pages=20):
            return (
                "A completely different paper with methods and results. " * 20
                + f" References include https://doi.org/{target_doi}. "
                + "Additional discussion. " * 20
            )

    reader = fulltext.FulltextReader(
        http_client=PdfHttpClient(),
        pdf_parser=PdfParser(),
        unpaywall_email=None,
    )
    candidate = {
        "candidate_id": "wrong-pdf",
        "doi": target_doi,
        "title": "Target Paper",
        "pdf_url": pdf_url,
    }
    out = adapter.progressive_read_candidates(
        payload(candidate, config={"enable_non_arxiv_fulltext": True}),
        fulltext_reader=reader,
    )

    validate(out, OUTPUT_SCHEMA)
    assert out["status"] == "failed"
    assert out["paper_packets"] == []
    attempt = out["material_attempts"][0]
    assert attempt["reason"] == "same_work_identity_unverified"
    assert attempt["diagnostics"]["identity_basis"] == "identity_unverified"
    assert attempt["diagnostics"]["provider_observed_identity"]["doi"] == ""


def test_payload_budget_rebuilds_anchors_from_surviving_sections_not_section_names():
    """Truncation cannot leave an anchor for a section that was removed."""
    class DuplicateProviderSectionNames(DeepXivReader):
        def head(self, arxiv_id):
            observed = self.observed(arxiv_id)
            return {
                "title": f"Paper {observed}",
                "arxiv_id": observed,
                "sections": {"Results": {}, "Methods": {}},
            }

        def section(self, arxiv_id, name):
            observed = self.observed(arxiv_id)
            marker = "R" if name == "Results" else "M"
            return {
                "section_name": "Same Section",
                "arxiv_id": observed,
                "content": marker * 7000,
            }

    requested = "2401.12345v2"
    out = adapter.progressive_read_candidates(
        payload(
            {"candidate_id": "c", "arxiv_id": requested, "title": f"Paper {requested}"},
            config={"max_sections_per_paper": 2, "max_total_chars": 10000},
        ),
        reader=DuplicateProviderSectionNames(),
    )

    validate(out, OUTPUT_SCHEMA)
    packet = out["paper_packets"][0]
    assert len(packet["sections"]) == 1
    assert len(packet["source_anchors"]) == 1
    assert packet["source_anchors"][0]["content_hash"] == packet["sections"][0]["content_hash"]


def test_default_section_budget_preserves_methods_results_discussion_diversity():
    class StructuredReader(DeepXivReader):
        def head(self, arxiv_id):
            return {
                "title": f"Paper {arxiv_id}",
                "arxiv_id": arxiv_id,
                "sections": {
                    "Materials and Methods": {},
                    "Method Validation": {},
                    "Experimental Setup": {},
                    "Results": {},
                    "Discussion": {},
                    "Conclusion": {},
                },
            }

    requested = "2401.12345v2"
    reader = StructuredReader()
    out = adapter.progressive_read_candidates(
        payload({"candidate_id": "c", "arxiv_id": requested, "title": f"Paper {requested}"}),
        reader=reader,
    )
    validate(out, OUTPUT_SCHEMA)
    assert out["status"] == "success"
    names = [section["section_name"] for section in out["paper_packets"][0]["sections"]]
    assert names == ["Materials and Methods", "Results", "Discussion"]


def test_partial_section_read_failure_is_reported_as_degraded_not_full_success():
    class PartialReader(DeepXivReader):
        def head(self, arxiv_id):
            return {
                "title": f"Paper {arxiv_id}",
                "arxiv_id": arxiv_id,
                "sections": {"Methods": {}, "Results": {}, "Discussion": {}},
            }

        def section(self, arxiv_id, name):
            if name == "Results":
                raise TimeoutError("results section unavailable")
            return {
                "section_name": name,
                "arxiv_id": arxiv_id,
                "content": f"{name} scientific text with controlled measurements. " * 60,
            }

    requested = "2401.12345v2"
    out = adapter.progressive_read_candidates(
        payload({"candidate_id": "c", "arxiv_id": requested, "title": f"Paper {requested}"}),
        reader=PartialReader(),
    )
    validate(out, OUTPUT_SCHEMA)
    assert out["paper_packets"][0]["material_status"] == "MATERIAL_READY"
    assert out["status"] == "degraded"
    assert any(row["reason"] == "partial_section_acquisition" for row in out["material_attempts"])
