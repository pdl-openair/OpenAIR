"""Preserve metric and comparison records without scientific content rejection."""
from __future__ import annotations
from copy import deepcopy
from typing import Any


def validate_actual_metrics(raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    if raw is None:
        return None, None
    return (deepcopy(raw), None) if isinstance(raw, dict) else (None, "actual_metrics_structure_invalid")


def validate_reference_comparison(raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    if raw is None:
        return None, None
    return (deepcopy(raw), None) if isinstance(raw, dict) else (None, "reference_comparison_structure_invalid")
