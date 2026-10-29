"""Stable per-paper work identity within a frozen batch execution scope."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def evidence_paper_operation_identity(
    skill_id: str,
    parameters: Mapping[str, Any],
    packet: Mapping[str, Any],
    provider_evidence: Mapping[str, Any],
) -> str:
    """Distinguish new paper work without changing identity on bounded retry.

    The parent is enforced by the attempt repository. Child adapter identity
    supplies the frozen batch scope, and canonical scientific inputs identify
    the paper work independently of its position or the retry prompt.
    """
    identity = {
        "skillId": skill_id,
        "batchScope": provider_evidence.get("adapterName") or "",
        "scientificContext": {
            key: value for key, value in parameters.items() if key != "paper_packets"
        },
        "paper": dict(packet),
    }
    encoded = json.dumps(identity, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return f"evidence_batch:{skill_id}:paper:{digest}"
