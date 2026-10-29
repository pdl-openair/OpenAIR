"""Minimal deterministic adapter for title-abstract-screening-skill (metadata-only)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


_READY_ACCESS = {"structured_fulltext_ready", "pdf_ready", "landing_page_only"}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _decide(candidate: dict[str, Any]) -> tuple[str, str, list[str], list[dict[str, str]]]:
    title = _safe_str(candidate.get("title")).strip()
    abstract = _safe_str(candidate.get("abstract")).strip()
    access_status = _safe_str(candidate.get("access_status"))
    needs_acquisition = bool(candidate.get("needs_acquisition", False))

    signals: list[str] = []
    warnings: list[dict[str, str]] = []

    if title:
        signals.append("has_title")
    if abstract:
        signals.append("has_abstract")
    else:
        signals.append("missing_abstract")
        warnings.append(
            {
                "code": "MISSING_ABSTRACT",
                "message": "Candidate has no abstract; decision confidence is reduced."
            }
        )

    if access_status in _READY_ACCESS:
        signals.append("access_ready")
    elif access_status == "metadata_only":
        signals.append("metadata_only_access")
        warnings.append(
            {
                "code": "METADATA_ONLY_DECISION",
                "message": "Decision made in metadata-only mode without network/fulltext checks."
            }
        )
    elif access_status == "blocked":
        signals.append("blocked_access")
        warnings.append(
            {"code": "BLOCKED_ACCESS", "message": "Candidate access is blocked in upstream resolver."}
        )
    else:
        signals.append("unknown_access")
        warnings.append(
            {"code": "UNKNOWN_ACCESS", "message": "Candidate access status is unknown."}
        )

    if title and abstract and access_status in _READY_ACCESS and not needs_acquisition:
        return "include", "Has title+abstract and access is ready without acquisition.", signals, warnings
    if title and access_status in _READY_ACCESS:
        return "maybe", "Title is present and access is available, but abstract or readiness is incomplete.", signals, warnings
    return "exclude", "Insufficient metadata signal for minimal screening.", signals, warnings


def screen_title_abstract_candidates(input_data: dict[str, Any]) -> dict[str, Any]:
    """Run minimal deterministic metadata-only title/abstract screening."""
    task_id = _safe_str(input_data.get("task_id"))
    run_id = _safe_str(input_data.get("run_id"))
    candidates = input_data.get("candidates", [])

    screened_candidates: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    errors: list[dict[str, Any]] = []

    include_count = 0
    maybe_count = 0
    exclude_count = 0

    for raw in candidates:
        candidate_id = _safe_str(raw.get("candidate_id"))
        title = _safe_str(raw.get("title"))
        access_status = _safe_str(raw.get("access_status"))

        if not candidate_id or not title or not access_status:
            errors.append(
                {
                    "candidate_id": candidate_id or "unknown",
                    "code": "MISSING_REQUIRED_FIELDS",
                    "message": "candidate_id/title/access_status is required.",
                    "retryable": False,
                }
            )
            continue

        decision, reason, signals, candidate_warnings = _decide(raw)

        if decision == "include":
            include_count += 1
        elif decision == "maybe":
            maybe_count += 1
        else:
            exclude_count += 1

        for w in candidate_warnings:
            warnings.append({"candidate_id": candidate_id, **w})

        provenance = list(raw.get("provenance", []))
        provenance.append(
            {
                "stage": "title_abstract_screening",
                "skill_id": "title-abstract-screening-skill",
                "timestamp": _iso_now(),
                "operation": "deterministic_metadata_screening",
                "decision": decision,
            }
        )

        screened_candidate = dict(raw)
        screened_candidate.update(
            {
                "screening_decision": decision,
                "screening_reason": reason,
                "relevance_signals": signals,
                "access_status": access_status,
                "provenance": provenance,
            }
        )
        screened_candidates.append(screened_candidate)

    return {
        "task_id": task_id,
        "skill_id": "title-abstract-screening-skill",
        "run_id": run_id,
        "screened_candidates": screened_candidates,
        "summary": {
            "total_candidates": len(candidates),
            "include_count": include_count,
            "maybe_count": maybe_count,
            "exclude_count": exclude_count,
        },
        "warnings": warnings,
        "errors": errors,
    }
