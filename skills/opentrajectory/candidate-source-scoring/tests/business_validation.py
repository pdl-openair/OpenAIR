"""Representative business regression for deterministic candidate scoring."""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapter.scoring_adapter import score_candidates


PROVENANCE = [
    {
        "stage": "source_screening_selection",
        "skill_id": "title-abstract-screening-skill",
        "timestamp": "2026-08-29T12:00:00Z",
    }
]


def candidate(
    candidate_id: str,
    title: str,
    abstract: str | None,
    *,
    decision: str = "include",
    access_status: str = "pdf_ready",
    signals: list[str] | None = None,
    citation_count: int = 0,
) -> dict:
    return {
        "candidate_id": candidate_id,
        "dedupe_key": f"key:{candidate_id}",
        "title": title,
        "abstract": abstract,
        "screening_decision": decision,
        "screening_reason": "Representative screening decision.",
        "relevance_signals": signals or [],
        "access_status": access_status,
        "citation_count": citation_count,
        "provenance": PROVENANCE,
    }


def score(rows: list[dict], context: dict) -> dict:
    return score_candidates({"screened_candidates": rows, "research_context": context})


def by_id(result: dict, candidate_id: str) -> dict:
    for row in result["candidate_scores"]:
        if row["candidate_id"] == candidate_id:
            return row
    raise AssertionError(f"missing candidate score: {candidate_id}")


def materials_case() -> None:
    context = {
        "research_question": "Which AlN sputtering conditions are associated with higher d33?",
        "research_objects": ["AlN", "Mo electrode"],
        "conditions": ["nitrogen flow", "substrate temperature"],
        "results_or_metrics": ["d33", "piezoelectric response"],
        "mechanisms": ["reactive sputtering"],
        "comparison_dimensions": ["nitrogen flow", "substrate temperature"],
        "critical_evidence_requirements": ["AlN", "d33"],
        "negative_signals": ["review only"],
        "off_domain_signals": ["lithium-ion", "graphite plating"],
    }
    rows = [
        candidate(
            "mat-core",
            "Reactive sputtering of AlN on Mo electrodes",
            "Nitrogen flow and substrate temperature were varied; d33 and piezoelectric response were measured.",
            signals=["AlN", "d33", "nitrogen flow"],
            citation_count=2,
        ),
        candidate(
            "mat-off",
            "Fast-charging lithium-ion battery graphite plating",
            "Low-temperature graphite plating and capacity retention in lithium-ion battery cells.",
            decision="maybe",
            access_status="metadata_only",
            signals=["temperature"],
            citation_count=1200,
        ),
    ]
    result = score(rows, context)
    relevant = by_id(result, "mat-core")
    off_domain = by_id(result, "mat-off")
    assert relevant["final_score"] >= off_domain["final_score"] + 0.35
    assert off_domain["score_breakdown"]["off_domain_penalty"] >= 0.6
    assert off_domain["citation_count"] == 1200


def ai_case() -> None:
    context = {
        "research_question": "Does retrieval augmentation improve factual QA for scientific assistants?",
        "research_objects": ["retrieval augmented generation", "scientific assistant"],
        "conditions": ["factual question answering"],
        "results_or_metrics": ["factual accuracy", "citation precision"],
        "mechanisms": ["retrieval grounding"],
        "comparison_dimensions": ["with retrieval", "without retrieval"],
        "critical_evidence_requirements": ["factual accuracy", "retrieval"],
        "negative_signals": ["image classification"],
        "off_domain_signals": ["wireless sensor", "crop yield"],
    }
    rows = [
        candidate(
            "ai-core",
            "Retrieval augmented generation for factual scientific question answering",
            "A scientific assistant with retrieval grounding improved factual accuracy and citation precision versus without retrieval.",
            signals=["retrieval", "factual accuracy", "citation precision"],
        ),
        candidate(
            "ai-metric-only",
            "Accuracy benchmarking for image classification",
            "Image classification accuracy is compared across convolutional models.",
            signals=["accuracy"],
        ),
    ]
    result = score(rows, context)
    assert by_id(result, "ai-core")["final_score"] > by_id(result, "ai-metric-only")["final_score"]
    assert by_id(result, "ai-metric-only")["negative_hits"] == ["image classification"]


def biomed_case() -> None:
    context = {
        "research_question": "How does EGFR mutation status affect osimertinib response in NSCLC?",
        "research_objects": ["EGFR", "NSCLC", "osimertinib"],
        "conditions": ["EGFR mutation"],
        "results_or_metrics": ["progression-free survival", "response rate"],
        "mechanisms": ["EGFR inhibition"],
        "comparison_dimensions": ["mutation status"],
        "critical_evidence_requirements": ["EGFR", "osimertinib", "response rate"],
        "negative_signals": ["pediatric leukemia"],
        "off_domain_signals": ["plant growth", "battery"],
    }
    full = candidate(
        "bio-full",
        "Osimertinib response by EGFR mutation status in NSCLC",
        "EGFR-mutant NSCLC treated with osimertinib showed response rate and progression-free survival differences by mutation status.",
        signals=["EGFR", "osimertinib", "response rate"],
        access_status="structured_fulltext_ready",
    )
    missing_abstract = candidate(
        "bio-missing-abstract",
        "Osimertinib in EGFR-mutant NSCLC",
        None,
        signals=["EGFR", "osimertinib"],
        access_status="metadata_only",
    )
    blocked = candidate(
        "bio-blocked",
        "Osimertinib response rate in EGFR-mutant NSCLC",
        "EGFR-mutant NSCLC and osimertinib response rate.",
        signals=["EGFR", "osimertinib", "response rate"],
        access_status="blocked",
    )
    result = score([full, missing_abstract, blocked], context)
    full_score = by_id(result, "bio-full")
    missing_score = by_id(result, "bio-missing-abstract")
    blocked_score = by_id(result, "bio-blocked")
    assert "missing_abstract" in missing_score["degradation_reasons"]
    assert "critical_evidence_requirements_incomplete" in missing_score["degradation_reasons"]
    assert blocked_score["score_breakdown"]["source_accessibility"] < full_score["score_breakdown"]["source_accessibility"]
    assert any(reason.startswith("access_degraded:blocked") for reason in blocked_score["degradation_reasons"])
    assert result["scoring_report"]["missing_abstract_count"] == 1
    assert result["scoring_report"]["access_degraded_count"] == 2


def citation_invariance_case() -> None:
    context = {
        "research_question": "Does catalyst A improve conversion?",
        "research_objects": ["catalyst A"],
        "conditions": ["reaction temperature"],
        "results_or_metrics": ["conversion"],
        "mechanisms": [],
        "comparison_dimensions": ["catalyst loading"],
        "critical_evidence_requirements": ["catalyst A", "conversion"],
        "negative_signals": [],
        "off_domain_signals": [],
    }
    low_cited = candidate(
        "cite-low",
        "Catalyst A conversion at controlled reaction temperature",
        "Catalyst A conversion was measured across catalyst loading.",
        signals=["catalyst A", "conversion"],
        citation_count=1,
    )
    highly_cited = dict(low_cited)
    highly_cited["candidate_id"] = "cite-high"
    highly_cited["dedupe_key"] = "key:cite-high"
    highly_cited["citation_count"] = 10000
    result = score([low_cited, highly_cited], context)
    assert by_id(result, "cite-low")["final_score"] == by_id(result, "cite-high")["final_score"]


def exclusion_and_identity_case() -> None:
    context = {
        "research_question": "Representative question",
        "research_objects": [],
        "conditions": [],
        "results_or_metrics": [],
        "mechanisms": [],
        "comparison_dimensions": [],
        "critical_evidence_requirements": [],
        "negative_signals": [],
        "off_domain_signals": [],
    }
    excluded = candidate("excluded", "Excluded candidate", "Not relevant", decision="exclude")
    missing_identity = candidate("missing-id", "Missing identity", "Relevant abstract")
    del missing_identity["dedupe_key"]
    result = score([excluded, missing_identity], context)
    assert result["candidate_scores"] == []
    assert result["scoring_report"]["screening_excluded_count"] == 1
    assert result["scoring_report"]["unscored_identity_count"] == 1
    assert result["scoring_report"]["unscored_candidates"] == [
        {"candidate_id": "missing-id", "reason": "missing_dedupe_key"}
    ]



def context_missing_vs_explicit_empty_case() -> None:
    row = candidate(
        "context-check",
        "Catalyst A conversion study",
        "Catalyst A conversion was measured.",
        signals=["catalyst A", "conversion"],
    )
    incomplete = {
        "research_question": "Does catalyst A improve conversion?",
        "research_objects": ["catalyst A"],
    }
    incomplete_result = score([row], incomplete)
    incomplete_row = by_id(incomplete_result, "context-check")
    assert incomplete_result["scoring_report"]["facets_incomplete"] is True
    assert "conditions" in incomplete_result["scoring_report"]["missing_facets"]
    assert "research_context_incomplete" in incomplete_row["degradation_reasons"]

    explicit_empty = {
        "research_question": "Does catalyst A improve conversion?",
        "research_objects": ["catalyst A"],
        "conditions": [],
        "results_or_metrics": [],
        "mechanisms": [],
        "comparison_dimensions": [],
        "critical_evidence_requirements": [],
        "negative_signals": [],
        "off_domain_signals": [],
    }
    explicit_result = score([row], explicit_empty)
    explicit_row = by_id(explicit_result, "context-check")
    assert explicit_result["scoring_report"]["facets_incomplete"] is False
    assert "research_context_incomplete" not in explicit_row["degradation_reasons"]


def off_domain_soft_penalty_case() -> None:
    context = {
        "research_question": "Which AlN conditions affect d33?",
        "research_objects": ["AlN"],
        "conditions": ["temperature"],
        "results_or_metrics": ["d33"],
        "mechanisms": [],
        "comparison_dimensions": [],
        "critical_evidence_requirements": ["AlN", "d33"],
        "negative_signals": [],
        "off_domain_signals": ["battery", "graphite plating"],
    }
    one_hit = candidate(
        "one-off-domain-hit",
        "AlN d33 study with a battery-related comparison note",
        "AlN d33 was measured versus temperature.",
        signals=["AlN", "d33", "temperature"],
    )
    result = score([one_hit], context)
    row = by_id(result, "one-off-domain-hit")
    assert row["score_breakdown"]["off_domain_penalty"] == 0.4
    assert result["scoring_report"]["off_domain_candidate_count"] == 0
    assert row["screening_decision"] == "include"


def critical_requirement_degraded_case() -> None:
    context = {
        "research_question": "Which AlN conditions affect d33 and stress?",
        "research_objects": ["AlN"],
        "conditions": ["temperature"],
        "results_or_metrics": ["d33", "stress"],
        "mechanisms": [],
        "comparison_dimensions": [],
        "critical_evidence_requirements": ["d33", "stress"],
        "negative_signals": [],
        "off_domain_signals": [],
    }
    partial = candidate(
        "critical-partial",
        "AlN d33 response versus temperature",
        "The study reports AlN d33 versus temperature but does not report the required mechanical outcome.",
        signals=["AlN", "d33", "temperature"],
        access_status="pdf_ready",
    )
    result = score([partial], context)
    row = by_id(result, "critical-partial")
    assert "critical_evidence_requirements_incomplete" in row["degradation_reasons"]
    assert row["score_breakdown"]["slot_coverage_bonus"] == 0.075
    assert result["scoring_report"]["critical_coverage_degraded_count"] == 1

def main() -> int:
    materials_case()
    ai_case()
    biomed_case()
    citation_invariance_case()
    exclusion_and_identity_case()
    context_missing_vs_explicit_empty_case()
    off_domain_soft_penalty_case()
    critical_requirement_degraded_case()
    print("BUSINESS_VALIDATION = PASS")
    print("DOMAINS = materials, computing/AI, biomedicine")
    print("DEGRADED = missing_abstract, access_degraded, critical_evidence_incomplete")
    print("CITATION_DOMINANCE = ABSENT")
    print("SCREENING_TIERING_BOUNDARIES = PRESERVED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
