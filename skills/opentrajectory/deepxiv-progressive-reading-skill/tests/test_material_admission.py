from __future__ import annotations

import gzip
import importlib.util
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]


def load(name: str):
    path = SKILL / "adapter" / "material_admission.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


admission = load("material_admission_tests")


def test_versioned_identity_requires_provider_observed_same_version():
    requested = admission.requested_identity({"candidate_id": "c", "arxiv_id": "2401.12345v2", "title": "Paper"})
    resolved, basis = admission.verify_identity(requested, {"arxiv_id": "2401.12345v2"}, observed_title="Paper")
    assert resolved and resolved["arxiv_version"] == "v2" and basis == "arxiv_match"
    resolved, basis = admission.verify_identity(requested, {"arxiv_id": "2401.12345v1"}, observed_title="Paper")
    assert resolved is None and basis == "arxiv_version_conflict"
    resolved, basis = admission.verify_identity(requested, {"arxiv_id": "2401.12345"}, observed_title="Paper")
    assert resolved is None and basis == "arxiv_version_unverified"


def test_matching_arxiv_work_identity_is_not_overridden_by_title_variant():
    requested = admission.requested_identity({
        "candidate_id": "arxiv:2503.17170",
        "arxiv_id": "2503.17170",
        "title": (
            "Wafer scale reactive sputter deposition of ferroelectric "
            "Al0.6Sc0.4N from 300 mm AlSc Targets"
        ),
    })
    resolved, basis = admission.verify_identity(
        requested,
        {"arxiv_id": "2503.17170"},
        observed_title=(
            "Wafer scale reactive sputtering of highly oriented and ferroelectric "
            "Al$_{0.6}$Sc$_{0.4}$N from 300 mm AlSc Targets"
        ),
    )
    assert resolved is not None
    assert resolved["arxiv_work_id"] == "2503.17170"
    assert basis == "arxiv_match"


def test_provider_identity_missing_is_not_verified_by_title_alone():
    requested = admission.requested_identity({"candidate_id": "c", "doi": "10.1234/x", "title": "Paper"})
    resolved, basis = admission.verify_identity(requested, {}, observed_title="Paper")
    assert resolved is None and basis == "identity_unverified"


def test_supplementary_material_type_is_hard_rejected_before_body_admission():
    candidate = {"candidate_id": "c", "doi": "10.1234/x", "title": "Paper"}
    material, rejected = admission.admit_material(candidate, {
        "provider": "publisher_pdf", "source_locator": "https://example.org/supp.pdf",
        "provider_observed_title": "Paper", "provider_observed_identity": {"doi": "10.1234/x"},
        "material_type": "supplementary_material",
        "sections": [{"section_name": "fulltext", "content": "data " * 1000, "material_type": "supplementary_material"}],
    })
    assert material is None
    assert rejected["reason"] == "material_type_not_admissible:supplementary_material"


def test_article_fulltext_with_matching_provider_identity_is_admitted():
    candidate = {"candidate_id": "c", "doi": "10.1234/x", "title": "Paper"}
    material, rejected = admission.admit_material(candidate, {
        "provider": "publisher_html", "source_locator": "https://example.org/article",
        "provider_observed_title": "Paper", "provider_observed_identity": {"doi": "10.1234/x"},
        "material_type": "article_fulltext",
        "sections": [{"section_name": "Results", "content": "Measured result under controlled conditions. " * 20, "material_type": "article_fulltext"}],
    })
    assert rejected is None
    assert material["material_status"] == "MATERIAL_READY"
    assert material["resolved_identity"]["doi"] == "10.1234/x"


def test_openalex_embedded_tei_uses_identity_from_provider_payload_not_candidate_backfill():
    tei = '''<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader><fileDesc><titleStmt><title>Paper</title></titleStmt><publicationStmt><idno type="DOI">10.1234/x</idno></publicationStmt></fileDesc></teiHeader><text><body><div><head>Results</head><p>{}</p></div></body></text></TEI>'''.format("result " * 80)
    candidate = {"candidate_id": "c", "doi": "10.1234/x", "title": "Paper", "content_provider": "openalex", "source_locator": "https://openalex.example/work", "grobid_xml": gzip.compress(tei.encode())}
    acquired, error = admission.acquire_embedded_material(candidate)
    assert error is None
    assert acquired["provider_observed_identity"]["doi"] == "10.1234/x"
    material, rejected = admission.admit_material(candidate, acquired)
    assert rejected is None and material["resolved_identity"]["doi"] == "10.1234/x"


def test_nested_reference_identity_is_not_treated_as_current_document_identity():
    observed = admission.observed_identity_from_payload({
        "title": "Wrong paper",
        "citations": [{"doi": "10.1234/target", "arxiv_id": "2401.12345v2"}],
    })
    assert observed["doi"] == ""
    assert observed["arxiv_id"] == ""


def test_core_inline_fulltext_reference_doi_cannot_self_authenticate():
    candidate = {
        "candidate_id": "core-1",
        "doi": "10.1234/target",
        "title": "Target paper",
        "content_provider": "core",
        "source_locator": "https://core.example/document/1",
        "fullText": (
            "A different article with methods and results. " * 20
            + "References https://doi.org/10.1234/target. "
            + "Discussion continues. " * 20
        ),
    }
    acquired, error = admission.acquire_embedded_material(candidate)
    assert error is None
    assert acquired["provider_observed_identity"]["doi"] == ""
    material, rejected = admission.admit_material(candidate, acquired)
    assert material is None
    assert rejected["reason"] == "same_work_identity_unverified"
    assert rejected["identity_basis"] == "identity_unverified"


def test_reference_section_cannot_be_admitted_as_article_body():
    candidate = {"candidate_id": "c", "doi": "10.1234/x", "title": "Paper"}
    content = "References\n" + "[1] Smith J. Example study. Journal 2024;1:1-10. " * 80
    material, rejected = admission.admit_material(candidate, {
        "provider": "publisher_pdf",
        "source_locator": "https://example.org/article.pdf",
        "provider_observed_title": "Paper",
        "provider_observed_identity": {"doi": "10.1234/x"},
        "material_type": "article_fulltext",
        "sections": [{"section_name": "References", "content": content, "material_type": "article_fulltext"}],
    })
    assert material is None
    assert rejected["reason"] == "substantive_scientific_text_missing"


def test_fulltext_payload_whose_front_matter_is_bibliography_is_not_ready():
    candidate = {"candidate_id": "c", "doi": "10.1234/x", "title": "Paper"}
    content = "Bibliography\n" + "Doe A, Roe B. Materials study. doi:10.1000/example. " * 100
    material, rejected = admission.admit_material(candidate, {
        "provider": "publisher_pdf",
        "source_locator": "https://example.org/article.pdf",
        "provider_observed_title": "Paper",
        "provider_observed_identity": {"doi": "10.1234/x"},
        "material_type": "article_fulltext",
        "sections": [{"section_name": "fulltext", "content": content, "material_type": "article_fulltext"}],
    })
    assert material is None
    assert rejected["reason"] == "substantive_scientific_text_missing"
