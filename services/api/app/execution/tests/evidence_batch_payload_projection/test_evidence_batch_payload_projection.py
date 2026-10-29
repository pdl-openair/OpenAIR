from __future__ import annotations

import ast
from copy import deepcopy
import json
from pathlib import Path

from app.execution import durable_skill_runtime


VISIBLE_PACKET_FIELDS = {
    "candidate_id",
    "title",
    "doi",
    "arxiv_id",
    "anchored_sections",
}


def _canonical_packet(candidate_id: str) -> dict:
    return {
        "candidate_id": candidate_id,
        "title": f"Paper {candidate_id}",
        "doi": f"10.test/{candidate_id}",
        "arxiv_id": "",
        "anchored_sections": [{
            "source_anchor_id": f"anchor-{candidate_id}",
            "section_name": "Methods",
            "content": "Provider-visible bounded source.",
        }],
        "sections": [{"section_name": "Methods", "content": "complete canonical source"}],
        "source_anchors": [{"anchor_id": f"anchor-{candidate_id}"}],
        "finalizer_only": "must-stay-canonical",
    }


def _provider_packet(packet: dict) -> dict:
    return {field: deepcopy(packet[field]) for field in VISIBLE_PACKET_FIELDS}


def test_batch_caller_selects_packet_from_provider_projection() -> None:
    source = Path(durable_skill_runtime.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_execute_evidence_batch_requests"
    )
    payload_call = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_payload_for_evidence_paper"
    )
    packet_keyword = next(
        keyword for keyword in payload_call.keywords if keyword.arg == "packet"
    )
    assert ast.unparse(packet_keyword.value) == "dict(provider_packets[index])"
