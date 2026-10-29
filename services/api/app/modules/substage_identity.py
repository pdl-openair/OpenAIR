"""Canonical Substage identity and one-way historical aliases."""

from __future__ import annotations


HISTORICAL_SUBSTAGE_ALIASES = {
    "literature_search": "source_acquisition",
}


def canonical_substage_key(value: object) -> str:
    key = str(value or "").strip()
    return HISTORICAL_SUBSTAGE_ALIASES.get(key, key)
