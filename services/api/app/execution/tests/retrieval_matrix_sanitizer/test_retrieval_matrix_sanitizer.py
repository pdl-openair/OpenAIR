"""Compatibility tests for the retired Retrieval semantic sanitizer."""
from __future__ import annotations

from copy import deepcopy

import pytest

from app.execution.retrieval_matrix_sanitizer import sanitize_retrieval_matrix_output


@pytest.mark.parametrize(
    "input_payload",
    [
        {},
        {"user_query": "任意中文术语?"},
        {"user_query": '"risk or benefit" AND "decision making"'},
        {"interpreted_constraints": {"legacy": ["arbitrary", {"nested": True}]}},
    ],
)
def test_retired_sanitizer_is_content_independent_passthrough(input_payload: dict) -> None:
    output = {
        "search_matrix": [
            {
                "search_intent": "noise_exclusion",
                "query_terms": ["keep exactly"],
                "exclude_terms": ["do not reinterpret"],
            }
        ],
        "expanded_query_variants": [
            {
                "query": '"中文科研代理?" OR "[EMIM][TFSI]"',
                "variant_type": "exclusion",
            }
        ],
        "legacy": {"arbitrary": ["string", {"object": True}]},
    }
    original = deepcopy(output)

    sanitized, warnings = sanitize_retrieval_matrix_output(output, input_payload)

    assert sanitized == original
    assert sanitized is not output
    assert sanitized["search_matrix"] is not output["search_matrix"]
    assert warnings == ["legacy_semantic_sanitizer_retired"]


def test_retired_sanitizer_never_mutates_the_caller_payload() -> None:
    output = {"expanded_query_variants": [{"query": "random string"}]}

    sanitized, _warnings = sanitize_retrieval_matrix_output(output, {"user_query": "ignored"})
    sanitized["expanded_query_variants"][0]["query"] = "changed copy"

    assert output == {"expanded_query_variants": [{"query": "random string"}]}
