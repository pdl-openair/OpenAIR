"""Gap-record generation for research-lead packages."""
from __future__ import annotations

from typing import Any

from .schema import REPRODUCIBILITY_KEYS


def build_gap_record(gap_type: str, severity: str, message: str, fields: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": gap_type,
        "severity": severity,
        "message": message,
        "fields": fields or [],
    }


def derive_gap_records(package: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive honesty gaps without promoting artifacts into evidence."""

    proposal = package.get("proposal") or {}
    source_mapping = package.get("source_mapping") or {}
    gaps: list[dict[str, Any]] = list(proposal.get("gap_records") or [])

    evidence_refs = proposal.get("evidence_refs") or []
    if not evidence_refs:
        gaps.append(
            build_gap_record(
                "missing_evidence_refs",
                "high",
                "No traceable evidence references were found; proposal artifacts remain artifacts only.",
                ["proposal.evidence_refs"],
            )
        )

    has_measured_result = bool(proposal.get("observed_results") or proposal.get("measured_results") or proposal.get("measured_metrics"))
    if not has_measured_result:
        gaps.append(
            build_gap_record(
                "missing_measured_results",
                "high",
                "No observed or measured result record is present.",
                ["proposal.observed_results", "proposal.measured_results", "proposal.measured_metrics"],
            )
        )

    repro = proposal.get("reproducibility_protocol") or {}
    missing_repro = [key for key in REPRODUCIBILITY_KEYS if not str(repro.get(key, "")).strip()]
    if missing_repro:
        gaps.append(
            build_gap_record(
                "missing_reproducibility_protocol",
                "high",
                "Reproducibility metadata is incomplete.",
                [f"proposal.reproducibility_protocol.{key}" for key in missing_repro],
            )
        )

    if source_mapping.get("mapping_confidence") == "low":
        gaps.append(
            build_gap_record(
                "source_mapping_low_confidence",
                "medium",
                "Source mapping confidence is low.",
                ["source_mapping.mapping_confidence"],
            )
        )

    unmapped = source_mapping.get("unmapped_source_fields") or []
    if unmapped:
        gaps.append(
            build_gap_record(
                "unmapped_source_fields",
                "medium",
                "Some source fields were not mapped into the package contract.",
                [str(item) for item in unmapped],
            )
        )

    if package.get("evaluation_mode") == "offline_proposal":
        gaps.append(
            build_gap_record(
                "offline_proposal_traceability_limited",
                "medium",
                "Offline proposal mode does not claim traceable or reproduced results.",
                ["evaluation_mode", "proposal.claimed_result_status"],
            )
        )

    return _dedupe_gaps(gaps)


def _dedupe_gaps(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for gap in gaps:
        key = (str(gap.get("type", "")), str(gap.get("message", "")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(gap)
    return deduped
