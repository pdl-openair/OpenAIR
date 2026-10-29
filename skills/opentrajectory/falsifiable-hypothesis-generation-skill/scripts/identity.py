from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


DEFAULT_MAX_HYPOTHESES = 8


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def build_hypothesis_id(hypothesis: dict[str, Any]) -> str:
    identity_payload = {
        "source_research_lead_ids": sorted(hypothesis["source_research_lead_ids"]),
        "source_finding_ids": sorted(hypothesis["source_finding_ids"]),
        "statement": hypothesis["statement"],
        "independent_variable": hypothesis["independent_variable"],
        "dependent_variable": hypothesis["dependent_variable"],
        "expected_relation": hypothesis["expected_relation"],
        "boundary_conditions": sorted(hypothesis["boundary_conditions"]),
        "falsifiable_prediction": hypothesis["falsifiable_prediction"],
        "falsification_condition": hypothesis["falsification_condition"],
        "provenance_evidence_ids": sorted(hypothesis["provenance_evidence_ids"]),
        "provenance_mechanism_edge_ids": sorted(hypothesis["provenance_mechanism_edge_ids"]),
    }
    return "hy:" + _sha256(identity_payload)[:20]


def _strip_runtime_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_runtime_metadata(item)
            for key, item in value.items()
            if not key.startswith("local_")
        }
    if isinstance(value, list):
        return [_strip_runtime_metadata(item) for item in value]
    return copy.deepcopy(value)


def _sort_records(rows: list[dict[str, Any]], id_field: str) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: row[id_field])


def _sorted_fields(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    canonical = _strip_runtime_metadata(row)
    for field in fields:
        if field in canonical:
            canonical[field] = sorted(canonical[field])
    return canonical


def _canonical_research_context(context: dict[str, Any]) -> dict[str, Any]:
    canonical = _strip_runtime_metadata(context)
    canonical["target_metrics"] = sorted(canonical.get("target_metrics", []))
    canonical["boundary_conditions"] = sorted(canonical.get("boundary_conditions", []))
    if canonical.get("output_intent") is None:
        canonical.pop("output_intent", None)
    return canonical


def _canonical_mechanism_packet(packet: dict[str, Any]) -> dict[str, Any]:
    edges = [
        _sorted_fields(row, ("evidence_ids",))
        for row in packet["mechanism_edges"]
    ]
    chains = [_strip_runtime_metadata(row) for row in packet["mechanism_chains"]]
    semantic = {
        "mechanism_edges": _sort_records(edges, "edge_id"),
        "mechanism_chains": _sort_records(chains, "chain_id"),
    }
    for field in ("conflicts", "gaps", "synthesis_warnings"):
        if field in packet:
            semantic[field] = _strip_runtime_metadata(packet[field])
    return semantic


def _canonical_findings_packet(packet: dict[str, Any]) -> dict[str, Any]:
    findings = [
        _sorted_fields(
            row,
            (
                "provenance_evidence_ids",
                "basis_mechanism_edge_ids",
                "gap_mechanism_edge_ids",
                "uncertainties",
            ),
        )
        for row in packet["findings"]
    ]
    semantic = {"findings": _sort_records(findings, "finding_id")}
    if "generation_warnings" in packet:
        semantic["generation_warnings"] = sorted(packet["generation_warnings"])
    return semantic


def _canonical_lead_packet(packet: dict[str, Any]) -> dict[str, Any]:
    leads = [
        _sorted_fields(
            row,
            (
                "source_finding_ids",
                "mechanism_basis_edge_ids",
                "gap_edge_ids",
                "provenance_evidence_ids",
                "boundary_conditions",
                "uncertainties",
            ),
        )
        for row in packet["research_leads"]
    ]
    semantic = {"research_leads": _sort_records(leads, "research_lead_id")}
    if "generation_warnings" in packet:
        semantic["generation_warnings"] = sorted(packet["generation_warnings"])
    return semantic


def _canonical_generation_config(input_payload: dict[str, Any]) -> dict[str, Any]:
    config = input_payload.get("generation_config") or {}
    return {
        "max_hypotheses": config.get("max_hypotheses", DEFAULT_MAX_HYPOTHESES),
        "focus_questions": sorted(config.get("focus_questions", [])),
    }


def build_input_fingerprint(input_payload: dict[str, Any]) -> str:
    semantic_input = {
        "research_context": _canonical_research_context(input_payload["research_context"]),
        "findings_packet": _canonical_findings_packet(input_payload["findings_packet"]),
        "research_lead_packet": _canonical_lead_packet(input_payload["research_lead_packet"]),
        "generation_config": _canonical_generation_config(input_payload),
    }
    mechanism_packet = input_payload.get("mechanism_packet")
    if mechanism_packet is not None:
        semantic_input["mechanism_packet"] = _canonical_mechanism_packet(mechanism_packet)
    return _sha256(semantic_input)
