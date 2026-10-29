"""Retired semantic sanitizer compatibility shim.

The OpenAIR product path no longer lets deterministic code reinterpret research
semantics, rewrite queries from keywords, or silently invent comparison
coverage.  The registered Retrieval Strategy Skill and its structural finalizer
own the production behavior.  Legacy Skill Lab callers receive an unchanged
copy plus one migration warning.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


def sanitize_retrieval_matrix_output(
    output_dict: dict[str, Any],
    input_payload: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    del input_payload
    return deepcopy(output_dict), ["legacy_semantic_sanitizer_retired"]
