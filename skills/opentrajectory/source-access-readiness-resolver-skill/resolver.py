"""
Source Access Readiness Resolver - #7

Determines access readiness for deduplicated candidates based on metadata only.
Does NOT download, fetch, or parse any content.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def resolve_access_status(
    candidate: Dict[str, Any],
    policy: Optional[Dict[str, Any]] = None
) -> Tuple[str, List[str], Optional[str], List[Dict[str, Any]], bool, Optional[Dict[str, Any]], List[str]]:
    """
    Resolve access status for a single candidate.

    Returns:
        (access_status, available_access_types, preferred_access_type,
         access_routes, needs_acquisition, acquisition_hint, evidence_fields)
    """
    policy = policy or {}
    prefer_structured = policy.get("prefer_structured_fulltext", True)
    allowed_types = set(policy.get("allowed_access_types", [
        "html", "xml", "tei", "pdf", "landing_page", "metadata_only"
    ]))

    external_ids = candidate.get("external_ids", {})
    pmcid = external_ids.get("pmcid")
    arxiv_id = external_ids.get("arxiv_id")
    doi = candidate.get("doi")
    pdf_url = candidate.get("pdf_url")
    landing_page_url = candidate.get("landing_page_url")
    source_url = candidate.get("source_url")

    available_types = []
    access_routes = []
    evidence_fields = []

    # Check structured fulltext indicators
    has_structured = False
    if pmcid:
        has_structured = True
        evidence_fields.append("pmcid")
        if "xml" in allowed_types:
            available_types.append("xml")
            access_routes.append({
                "access_type": "xml",
                "status": "candidate",
                "source": "pmcid",
                "confidence": 0.85,
                "notes": "PMCID indicates likely structured fulltext route. Not fetched in this skill."
            })
        if "html" in allowed_types:
            available_types.append("html")
            access_routes.append({
                "access_type": "html",
                "status": "candidate",
                "source": "pmcid",
                "confidence": 0.8,
                "notes": "PMCID may provide HTML fulltext."
            })

    # Check PDF indicators
    has_pdf = False
    if pdf_url:
        has_pdf = True
        evidence_fields.append("pdf_url")
        if "pdf" in allowed_types:
            available_types.append("pdf")
            access_routes.append({
                "access_type": "pdf",
                "status": "candidate",
                "url": pdf_url,
                "source": "pdf_url",
                "confidence": 0.9,
                "notes": "PDF URL provided in metadata."
            })
    elif arxiv_id:
        has_pdf = True
        evidence_fields.append("arxiv_id")
        if "pdf" in allowed_types:
            available_types.append("pdf")
            access_routes.append({
                "access_type": "pdf",
                "status": "candidate",
                "source": "arxiv_id",
                "confidence": 0.95,
                "notes": "arXiv ID indicates PDF availability."
            })

    # Check landing page indicators
    has_landing = False
    landing_url = landing_page_url or source_url
    if landing_url:
        has_landing = True
        if landing_page_url:
            evidence_fields.append("landing_page_url")
        if source_url:
            evidence_fields.append("source_url")
        if "landing_page" in allowed_types:
            available_types.append("landing_page")
            access_routes.append({
                "access_type": "landing_page",
                "status": "available",
                "url": landing_url,
                "source": "landing_page_url" if landing_page_url else "source_url",
                "confidence": 0.9
            })
    elif doi:
        has_landing = True
        evidence_fields.append("doi")
        if "landing_page" in allowed_types:
            available_types.append("landing_page")
            access_routes.append({
                "access_type": "landing_page",
                "status": "candidate",
                "source": "doi",
                "confidence": 0.85,
                "notes": "DOI can be resolved to landing page."
            })

    # Determine access status and preferred type
    if has_structured and prefer_structured:
        access_status = "structured_fulltext_ready"
        preferred_type = "xml" if "xml" in available_types else "html"
        needs_acquisition = False
        acquisition_hint = {
            "recommended_next_action": "use_structured_fulltext",
            "reason": "Structured fulltext route is likely available from PMCID."
        }
    elif has_pdf:
        access_status = "pdf_ready"
        preferred_type = "pdf"
        needs_acquisition = False
        acquisition_hint = {
            "recommended_next_action": "try_pdf_acquisition",
            "reason": "PDF route available."
        }
    elif has_landing:
        access_status = "landing_page_only"
        preferred_type = "landing_page"
        needs_acquisition = True
        acquisition_hint = {
            "recommended_next_action": "try_landing_page_resolution",
            "reason": "Only landing page available; fulltext may require scraping or publisher API."
        }
    elif candidate.get("title") or candidate.get("abstract"):
        access_status = "metadata_only"
        preferred_type = "metadata_only"
        needs_acquisition = True
        if "metadata_only" in allowed_types:
            available_types.append("metadata_only")
            access_routes.append({
                "access_type": "metadata_only",
                "status": "available",
                "source": "candidate_metadata",
                "confidence": 1.0,
                "notes": "Only title/abstract available."
            })
        acquisition_hint = {
            "recommended_next_action": "metadata_only_screening",
            "reason": "No fulltext or landing page route found."
        }
    else:
        access_status = "unknown"
        preferred_type = None
        needs_acquisition = True
        acquisition_hint = {
            "recommended_next_action": "manual_check",
            "reason": "Insufficient metadata to determine access route."
        }

    return (
        access_status,
        available_types,
        preferred_type,
        access_routes,
        needs_acquisition,
        acquisition_hint,
        evidence_fields
    )


def generate_warnings(
    candidate: Dict[str, Any],
    access_status: str,
    available_types: List[str],
    access_routes: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Generate warnings for a candidate based on its access status."""
    warnings = []
    candidate_id = candidate["candidate_id"]

    if not candidate.get("doi"):
        warnings.append({
            "candidate_id": candidate_id,
            "code": "MISSING_DOI",
            "message": "No DOI found; may limit access resolution options."
        })

    if access_status == "metadata_only":
        warnings.append({
            "candidate_id": candidate_id,
            "code": "METADATA_ONLY",
            "message": "Only metadata available; no fulltext or landing page route found."
        })
    elif access_status == "landing_page_only":
        warnings.append({
            "candidate_id": candidate_id,
            "code": "LANDING_PAGE_ONLY",
            "message": "Only landing page available; fulltext acquisition may be required."
        })
    elif access_status == "pdf_ready" and "xml" not in available_types and "html" not in available_types:
        warnings.append({
            "candidate_id": candidate_id,
            "code": "PDF_ONLY",
            "message": "Only PDF route available; structured fulltext not found."
        })

    # Check for low confidence routes
    for route in access_routes:
        if route["confidence"] < 0.7:
            warnings.append({
                "candidate_id": candidate_id,
                "code": "LOW_CONFIDENCE_ROUTE",
                "message": f"Low confidence ({route['confidence']}) for {route['access_type']} route."
            })

    # Add unverified access warning for all candidates (metadata-only resolution)
    if access_status not in ["unknown", "metadata_only"]:
        warnings.append({
            "candidate_id": candidate_id,
            "code": "UNVERIFIED_ACCESS",
            "message": "Access route was inferred from metadata only; no network fetch was performed."
        })

    return warnings


def determine_retry_policy(access_status: str) -> Dict[str, Any]:
    """Determine retry policy based on access status."""
    if access_status == "unknown":
        return {
            "retryable": True,
            "retry_reason": "missing_identifier",
            "recommended_retry_after_seconds": 3600
        }
    elif access_status in ["structured_fulltext_ready", "pdf_ready"]:
        return {
            "retryable": False,
            "retry_reason": "no_retry_needed",
            "recommended_retry_after_seconds": None
        }
    else:
        return {
            "retryable": True,
            "retry_reason": "publisher_route_unknown",
            "recommended_retry_after_seconds": 7200
        }


def resolve_candidates(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main entry point for source access readiness resolution.

    Args:
        input_data: Input conforming to input.schema.json

    Returns:
        Output conforming to output.schema.json
    """
    task_id = input_data["task_id"]
    skill_id = input_data["skill_id"]
    run_id = input_data.get("run_id")
    candidates = input_data["candidates"]
    policy = input_data.get("resolver_policy", {})

    output_candidates = []
    all_warnings = []
    all_errors = []

    # Summary counters
    summary = {
        "total_candidates": len(candidates),
        "structured_fulltext_available_count": 0,
        "pdf_available_count": 0,
        "landing_page_only_count": 0,
        "metadata_only_count": 0,
        "access_blocked_count": 0,
        "needs_acquisition_count": 0
    }

    timestamp = datetime.now(timezone.utc).isoformat()

    for candidate in candidates:
        try:
            # Validate required fields
            if not candidate.get("candidate_id") or not candidate.get("title"):
                all_errors.append({
                    "candidate_id": candidate.get("candidate_id", "unknown"),
                    "code": "MISSING_REQUIRED_FIELDS",
                    "message": "Candidate missing required fields (candidate_id or title).",
                    "retryable": False
                })
                continue

            # Resolve access status
            (
                access_status,
                available_types,
                preferred_type,
                access_routes,
                needs_acquisition,
                acquisition_hint,
                evidence_fields
            ) = resolve_access_status(candidate, policy)

            # Generate warnings
            warnings = generate_warnings(candidate, access_status, available_types, access_routes)

            # Determine retry policy
            retry_policy = determine_retry_policy(access_status)

            # Inherit upstream provenance and append this skill's record
            provenance = list(candidate.get("provenance", []))
            provenance.append({
                "stage": "source_access_readiness_resolution",
                "skill_id": skill_id,
                "timestamp": timestamp,
                "operation": "resolve_access_readiness",
                "input_candidate_id": candidate["candidate_id"],
                "decision": access_status,
                "evidence_fields": evidence_fields
            })

            # Build output candidate
            output_candidate = {
                "candidate_id": candidate["candidate_id"],
                "title": candidate["title"],
                "access_status": access_status,
                "available_access_types": available_types,
                "preferred_access_type": preferred_type,
                "access_routes": access_routes,
                "needs_acquisition": needs_acquisition,
                "provenance": provenance
            }

            # Copy optional passthrough fields needed by downstream contracts
            for field in [
                "dedupe_key",
                "canonical_source_id",
                "normalized_title",
                "doi",
                "arxiv_id",
                "external_ids",
                "abstract",
                "authors",
                "venue",
                "source_name",
                "year",
                "citation_count",
                "source_url",
                "landing_page_url",
                "pdf_url",
                "matched_query",
                "search_intent",
                "query_variant_type",
                "matched_source_count",
                "raw_metadata",
                "retrieval_warnings",
            ]:
                if field in candidate:
                    output_candidate[field] = candidate[field]

            # Add optional fields
            if acquisition_hint:
                output_candidate["acquisition_hint"] = acquisition_hint
            if warnings:
                output_candidate["warnings"] = warnings
            output_candidate["retry"] = retry_policy

            output_candidates.append(output_candidate)
            all_warnings.extend(warnings)

            # Update summary
            if access_status == "structured_fulltext_ready":
                summary["structured_fulltext_available_count"] += 1
            elif access_status == "pdf_ready":
                summary["pdf_available_count"] += 1
            elif access_status == "landing_page_only":
                summary["landing_page_only_count"] += 1
            elif access_status == "metadata_only":
                summary["metadata_only_count"] += 1
            elif access_status == "blocked":
                summary["access_blocked_count"] += 1

            if needs_acquisition:
                summary["needs_acquisition_count"] += 1

        except Exception as e:
            all_errors.append({
                "candidate_id": candidate.get("candidate_id", "unknown"),
                "code": "ACCESS_RESOLUTION_FAILED",
                "message": f"Failed to resolve access status: {str(e)}",
                "retryable": True
            })

    # Build output
    output = {
        "task_id": task_id,
        "skill_id": skill_id,
        "candidates": output_candidates,
        "summary": summary,
        "warnings": all_warnings,
        "errors": all_errors
    }

    if run_id:
        output["run_id"] = run_id

    return output


def main():
    """CLI entry point for testing."""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python resolver.py <input.json>")
        sys.exit(1)

    input_path = sys.argv[1]
    with open(input_path, "r", encoding="utf-8") as f:
        input_data = json.load(f)

    output = resolve_candidates(input_data)
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
