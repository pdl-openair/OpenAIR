"""
Test cases for source-access-readiness-resolver-skill
"""

import json
from resolver import resolve_candidates


def test_structured_fulltext_ready():
    """Test candidate with PMCID (structured fulltext)"""
    input_data = {
        "task_id": "test_001",
        "skill_id": "source-access-readiness-resolver-skill",
        "candidates": [{
            "candidate_id": "cand_001",
            "title": "Test Paper",
            "doi": "10.1000/test",
            "external_ids": {"pmcid": "PMC123456"},
            "provenance": []
        }]
    }

    output = resolve_candidates(input_data)
    candidate = output["candidates"][0]

    assert candidate["access_status"] == "structured_fulltext_ready"
    assert "xml" in candidate["available_access_types"]
    assert candidate["preferred_access_type"] == "xml"
    assert candidate["needs_acquisition"] == False
    assert output["summary"]["structured_fulltext_available_count"] == 1
    print("[PASS] test_structured_fulltext_ready")


def test_pdf_ready():
    """Test candidate with PDF URL"""
    input_data = {
        "task_id": "test_002",
        "skill_id": "source-access-readiness-resolver-skill",
        "candidates": [{
            "candidate_id": "cand_002",
            "title": "Test Paper",
            "pdf_url": "https://example.com/paper.pdf",
            "provenance": []
        }]
    }

    output = resolve_candidates(input_data)
    candidate = output["candidates"][0]

    assert candidate["access_status"] == "pdf_ready"
    assert "pdf" in candidate["available_access_types"]
    assert candidate["preferred_access_type"] == "pdf"
    assert candidate["needs_acquisition"] == False
    assert output["summary"]["pdf_available_count"] == 1
    print("[PASS] test_pdf_ready")


def test_arxiv_pdf():
    """Test candidate with arXiv ID"""
    input_data = {
        "task_id": "test_003",
        "skill_id": "source-access-readiness-resolver-skill",
        "candidates": [{
            "candidate_id": "cand_003",
            "title": "Test Paper",
            "external_ids": {"arxiv_id": "2401.12345"},
            "provenance": []
        }]
    }

    output = resolve_candidates(input_data)
    candidate = output["candidates"][0]

    assert candidate["access_status"] == "pdf_ready"
    assert "pdf" in candidate["available_access_types"]
    assert any(r["source"] == "arxiv_id" for r in candidate["access_routes"])
    print("[PASS] test_arxiv_pdf")


def test_landing_page_only():
    """Test candidate with only landing page"""
    input_data = {
        "task_id": "test_004",
        "skill_id": "source-access-readiness-resolver-skill",
        "candidates": [{
            "candidate_id": "cand_004",
            "title": "Test Paper",
            "doi": "10.1000/test",
            "landing_page_url": "https://example.com/article",
            "provenance": []
        }]
    }

    output = resolve_candidates(input_data)
    candidate = output["candidates"][0]

    assert candidate["access_status"] == "landing_page_only"
    assert "landing_page" in candidate["available_access_types"]
    assert candidate["needs_acquisition"] == True
    assert output["summary"]["landing_page_only_count"] == 1
    assert any(w["code"] == "LANDING_PAGE_ONLY" for w in candidate["warnings"])
    print("[PASS] test_landing_page_only")


def test_metadata_only():
    """Test candidate with only metadata"""
    input_data = {
        "task_id": "test_005",
        "skill_id": "source-access-readiness-resolver-skill",
        "candidates": [{
            "candidate_id": "cand_005",
            "title": "Test Paper",
            "abstract": "This is a test abstract",
            "provenance": []
        }]
    }

    output = resolve_candidates(input_data)
    candidate = output["candidates"][0]

    assert candidate["access_status"] == "metadata_only"
    assert candidate["preferred_access_type"] == "metadata_only"
    assert candidate["needs_acquisition"] == True
    assert output["summary"]["metadata_only_count"] == 1
    assert any(w["code"] == "METADATA_ONLY" for w in candidate["warnings"])
    print("[PASS] test_metadata_only")


def test_prefer_structured_over_pdf():
    """Test that structured fulltext is preferred over PDF"""
    input_data = {
        "task_id": "test_006",
        "skill_id": "source-access-readiness-resolver-skill",
        "candidates": [{
            "candidate_id": "cand_006",
            "title": "Test Paper",
            "pdf_url": "https://example.com/paper.pdf",
            "external_ids": {"pmcid": "PMC123456"},
            "provenance": []
        }]
    }

    output = resolve_candidates(input_data)
    candidate = output["candidates"][0]

    assert candidate["access_status"] == "structured_fulltext_ready"
    assert candidate["preferred_access_type"] == "xml"
    assert "pdf" in candidate["available_access_types"]
    assert "xml" in candidate["available_access_types"]
    print("[PASS] test_prefer_structured_over_pdf")


def test_provenance_inheritance():
    """Test that upstream provenance is preserved and new record is appended"""
    input_data = {
        "task_id": "test_007",
        "skill_id": "source-access-readiness-resolver-skill",
        "candidates": [{
            "candidate_id": "cand_007",
            "title": "Test Paper",
            "pdf_url": "https://example.com/paper.pdf",
            "provenance": [
                {
                    "stage": "candidate_dedup_and_provenance",
                    "skill_id": "candidate-dedup-and-provenance",
                    "timestamp": "2026-05-27T00:00:00Z"
                }
            ]
        }]
    }

    output = resolve_candidates(input_data)
    candidate = output["candidates"][0]

    assert len(candidate["provenance"]) == 2
    assert candidate["provenance"][0]["skill_id"] == "candidate-dedup-and-provenance"
    assert candidate["provenance"][1]["skill_id"] == "source-access-readiness-resolver-skill"
    assert candidate["provenance"][1]["stage"] == "source_access_readiness_resolution"
    assert candidate["provenance"][1]["decision"] == "pdf_ready"
    assert "evidence_fields" in candidate["provenance"][1]
    print("[PASS] test_provenance_inheritance")


def test_missing_doi_warning():
    """Test that missing DOI generates warning"""
    input_data = {
        "task_id": "test_008",
        "skill_id": "source-access-readiness-resolver-skill",
        "candidates": [{
            "candidate_id": "cand_008",
            "title": "Test Paper",
            "pdf_url": "https://example.com/paper.pdf",
            "provenance": []
        }]
    }

    output = resolve_candidates(input_data)
    candidate = output["candidates"][0]

    assert any(w["code"] == "MISSING_DOI" for w in candidate["warnings"])
    print("[PASS] test_missing_doi_warning")


def test_multiple_candidates():
    """Test processing multiple candidates with different statuses"""
    input_data = {
        "task_id": "test_009",
        "skill_id": "source-access-readiness-resolver-skill",
        "candidates": [
            {
                "candidate_id": "cand_009_a",
                "title": "Paper A",
                "external_ids": {"pmcid": "PMC111"},
                "provenance": []
            },
            {
                "candidate_id": "cand_009_b",
                "title": "Paper B",
                "pdf_url": "https://example.com/b.pdf",
                "provenance": []
            },
            {
                "candidate_id": "cand_009_c",
                "title": "Paper C",
                "landing_page_url": "https://example.com/c",
                "provenance": []
            },
            {
                "candidate_id": "cand_009_d",
                "title": "Paper D",
                "abstract": "Only metadata",
                "provenance": []
            }
        ]
    }

    output = resolve_candidates(input_data)

    assert output["summary"]["total_candidates"] == 4
    assert output["summary"]["structured_fulltext_available_count"] == 1
    assert output["summary"]["pdf_available_count"] == 1
    assert output["summary"]["landing_page_only_count"] == 1
    assert output["summary"]["metadata_only_count"] == 1
    assert output["summary"]["needs_acquisition_count"] == 2
    print("[PASS] test_multiple_candidates")


def test_policy_prefer_structured():
    """Test resolver_policy prefer_structured_fulltext"""
    input_data = {
        "task_id": "test_010",
        "skill_id": "source-access-readiness-resolver-skill",
        "resolver_policy": {
            "prefer_structured_fulltext": True
        },
        "candidates": [{
            "candidate_id": "cand_010",
            "title": "Test Paper",
            "pdf_url": "https://example.com/paper.pdf",
            "external_ids": {"pmcid": "PMC123"},
            "provenance": []
        }]
    }

    output = resolve_candidates(input_data)
    candidate = output["candidates"][0]

    assert candidate["access_status"] == "structured_fulltext_ready"
    assert candidate["preferred_access_type"] == "xml"
    print("[PASS] test_policy_prefer_structured")


if __name__ == "__main__":
    test_structured_fulltext_ready()
    test_pdf_ready()
    test_arxiv_pdf()
    test_landing_page_only()
    test_metadata_only()
    test_prefer_structured_over_pdf()
    test_provenance_inheritance()
    test_missing_doi_warning()
    test_multiple_candidates()
    test_policy_prefer_structured()
    print("\n[SUCCESS] All tests passed")
