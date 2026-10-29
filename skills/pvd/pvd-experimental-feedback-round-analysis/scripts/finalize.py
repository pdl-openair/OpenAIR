from __future__ import annotations

from typing import Any


class FinalizationError(ValueError):
    pass


def validate_final_result(formal_input: dict[str, Any], model_input: dict[str, Any], result: dict[str, Any]) -> None:
    """Require the output identity key without checking scientific content."""
    if "experiment_ref" not in result:
        raise FinalizationError("output experiment_ref key is required")
