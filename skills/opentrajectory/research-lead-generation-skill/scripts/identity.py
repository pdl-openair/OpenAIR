from __future__ import annotations

import hashlib
import json
from typing import Any


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _canonical_refs(values: list[str]) -> list[str]:
    return sorted(values)


def build_research_lead_id(lead: dict[str, Any]) -> str:
    identity_payload = {
        "lead_kind": lead["lead_kind"],
        "title": lead["title"],
        "research_question": lead["research_question"],
        "investigation_target": lead["investigation_target"],
        "source_finding_ids": _canonical_refs(lead["source_finding_ids"]),
        "mechanism_basis_edge_ids": _canonical_refs(lead["mechanism_basis_edge_ids"]),
        "gap_edge_ids": _canonical_refs(lead["gap_edge_ids"]),
        "provenance_evidence_ids": _canonical_refs(lead["provenance_evidence_ids"]),
        "boundary_conditions": lead["boundary_conditions"],
    }
    return "rl:" + _sha256(identity_payload)[:20]


def build_input_fingerprint(input_payload: dict[str, Any]) -> str:
    return _sha256(input_payload)
