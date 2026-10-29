"""Deterministic adapter entry for the gap-to-action product module."""
from __future__ import annotations

from typing import Any

from .pipeline import AuthenticGapNormalizer, run_gap_to_action_pipeline


def run_gap_to_action_adapter(
    input_payload: dict[str, Any],
    *,
    normalizer: AuthenticGapNormalizer | None = None,
) -> dict[str, Any]:
    """Run gap-to-action planning from a validated skill-like input payload.

    Track B intentionally does not register this adapter with Workbench's
    ``execute_skill`` path. Track C owns that wiring. Authentic package
    normalization is injected explicitly; Track F owns its implementation.
    """

    if not isinstance(input_payload, dict):
        raise TypeError("input_payload must be an object")
    package = input_payload.get("research_lead_package")
    if not isinstance(package, dict) or not package:
        raise ValueError("research_lead_package must be a non-empty object")
    return run_gap_to_action_pipeline(package, normalizer=normalizer)


# Conventional short alias for a future python-adapter registry entry.
run_adapter = run_gap_to_action_adapter


__all__ = ["run_adapter", "run_gap_to_action_adapter"]
