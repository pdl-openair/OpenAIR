from __future__ import annotations

import hashlib
import json
from typing import Any


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def build_finding_id(finding: dict[str, Any]) -> str:
    identity_payload = {
        "statement": finding["statement"],
        "epistemic_status": finding["epistemic_status"],
        "provenance_evidence_ids": sorted(finding["provenance_evidence_ids"]),
        "basis_mechanism_edge_ids": sorted(finding["basis_mechanism_edge_ids"]),
        "gap_mechanism_edge_ids": sorted(finding["gap_mechanism_edge_ids"]),
        "scope_boundary": finding["scope_boundary"],
    }
    return "fd:" + _sha256(identity_payload)[:20]


def build_input_fingerprint(input_payload: dict[str, Any]) -> str:
    return _sha256(input_payload)
