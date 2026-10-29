from __future__ import annotations

import json

import pytest

from app.execution.durable_skill_runtime import _parse_structured_output


def test_extracts_one_lossless_object_from_wrapper() -> None:
    expected = {"scope": "GraphRAG", "criteria": [{"criterion": "peer reviewed"}]}
    raw = (
        "Here is the requested JSON:\n"
        f"```json\n{json.dumps(expected)}\n```\n"
        "No additional data was added."
    )

    assert _parse_structured_output(raw) == expected


def test_rejects_ambiguous_multiple_objects() -> None:
    with pytest.raises(json.JSONDecodeError):
        _parse_structured_output('first {"a":1} second {"b":2}')


def test_rejects_truncated_object_without_repair() -> None:
    with pytest.raises(json.JSONDecodeError):
        _parse_structured_output('{"scope":"GraphRAG","criteria":[')
