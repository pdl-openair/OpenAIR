"""Code-owned Result Protocol shared across runtime, dispatch and Artifact views.

The protocol describes the quality of one materialized result.  It deliberately
separates usable-but-degraded content from review requirements, external I/O
uncertainty and final scientific completeness.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

RESULT_PROTOCOL_VERSION = "result-protocol.v1"
RESULT_PROTOCOL_STATUSES = frozenset({"ok", "degraded", "fatal", "no_usable_result"})
USABLE_RESULT_PROTOCOL_STATUSES = frozenset({"ok", "degraded"})
_MAX_ITEMS = 256
_MAX_WARNINGS = 128
_MAX_TEXT = 512


def _bounded_text(value: Any, *, limit: int = _MAX_TEXT) -> str:
    return str(value or "").strip()[:limit]


def _dedupe_strings(values: Iterable[Any], *, limit: int = _MAX_WARNINGS) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _bounded_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
        if len(output) >= limit:
            break
    return output


def _normalize_item_list(value: Any, *, kind: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"result protocol {kind} must be a list")
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value[:_MAX_ITEMS]:
        if not isinstance(raw, Mapping):
            raise ValueError(f"result protocol {kind} items must be objects")
        item = deepcopy(dict(raw))
        # Stable, bounded keys used by current Guard/finalizers. Extra scalar
        # fields are preserved for forward-compatible diagnostics.
        if kind == "accepted_items":
            path = _bounded_text(item.get("path"))
            count = item.get("count")
            if not path or isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError("accepted result items require path and nonnegative count")
            item = {"path": path, "count": count}
        elif kind == "quarantined_items":
            path = _bounded_text(item.get("path"))
            reason = _bounded_text(item.get("reason"))
            if not path or not reason:
                raise ValueError("quarantined result items require path and reason")
            item = {"path": path, "reason": reason}
        elif kind == "repaired_items":
            path = _bounded_text(item.get("path"))
            action = _bounded_text(item.get("action"))
            if not path or not action:
                raise ValueError("repaired result items require path and action")
            item = {"path": path, "action": action}
        identity = repr(sorted(item.items(), key=lambda row: str(row[0])))
        if identity in seen:
            continue
        seen.add(identity)
        output.append(item)
    return output


def _normalize_coverage(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("result protocol coverage must be an object")
    output: dict[str, Any] = {}
    for key, raw in list(value.items())[:_MAX_ITEMS]:
        name = _bounded_text(key, limit=128)
        if not name:
            continue
        if raw is None or isinstance(raw, (str, int, float, bool)):
            output[name] = deepcopy(raw)
        elif isinstance(raw, list):
            output[name] = deepcopy(raw[:_MAX_ITEMS])
        elif isinstance(raw, Mapping):
            output[name] = deepcopy(dict(raw))
        else:
            raise ValueError("result protocol coverage contains an unsupported value")
    return output


def normalize_result_protocol(
    value: Mapping[str, Any] | None,
    *,
    warnings: Iterable[Any] = (),
    default_status: str = "ok",
) -> dict[str, Any]:
    """Return one canonical ResultProtocolV1 mapping.

    The protocol is an execution/result-quality fact.  It does not decide
    reviewer responsibility, Artifact usability, external execution outcome or
    final scientific completeness.
    """

    raw = dict(value or {})
    version = _bounded_text(raw.get("version") or RESULT_PROTOCOL_VERSION, limit=64)
    if version != RESULT_PROTOCOL_VERSION:
        raise ValueError("result protocol version is unsupported")
    status = _bounded_text(raw.get("status") or default_status, limit=64)
    if status not in RESULT_PROTOCOL_STATUSES:
        raise ValueError("result protocol status is invalid")
    accepted = _normalize_item_list(raw.get("accepted_items"), kind="accepted_items")
    quarantined = _normalize_item_list(raw.get("quarantined_items"), kind="quarantined_items")
    repaired = _normalize_item_list(raw.get("repaired_items"), kind="repaired_items")
    raw_warnings = raw.get("warnings") or []
    if not isinstance(raw_warnings, (list, tuple)):
        raise ValueError("result protocol warnings must be a list")
    supplied_warnings = [warnings] if isinstance(warnings, str) else list(warnings)
    normalized_warnings = _dedupe_strings([*raw_warnings, *supplied_warnings])
    coverage = _normalize_coverage(raw.get("coverage"))

    # A usable result with any isolated/repaired issue is necessarily degraded.
    if status == "ok" and (quarantined or repaired or normalized_warnings):
        status = "degraded"

    return {
        "version": RESULT_PROTOCOL_VERSION,
        "status": status,
        "accepted_items": accepted,
        "quarantined_items": quarantined,
        "repaired_items": repaired,
        "warnings": normalized_warnings,
        "coverage": coverage,
    }


def merge_result_protocols(
    *values: Mapping[str, Any] | None,
    warnings: Iterable[Any] = (),
) -> dict[str, Any]:
    """Merge code-owned protocol facts without changing scientific content."""

    status = "ok"
    accepted: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    repaired: list[dict[str, Any]] = []
    all_warnings: list[str] = []
    coverage: dict[str, Any] = {}
    for value in values:
        if value is None:
            continue
        normalized = normalize_result_protocol(value)
        candidate_status = normalized["status"]
        if candidate_status == "fatal":
            status = "fatal"
        elif candidate_status == "no_usable_result" and status != "fatal":
            status = "no_usable_result"
        elif candidate_status == "degraded" and status == "ok":
            status = "degraded"
        accepted.extend(normalized["accepted_items"])
        quarantined.extend(normalized["quarantined_items"])
        repaired.extend(normalized["repaired_items"])
        all_warnings.extend(normalized["warnings"])
        coverage.update(normalized["coverage"])
    return normalize_result_protocol(
        {
            "status": status,
            "accepted_items": accepted,
            "quarantined_items": quarantined,
            "repaired_items": repaired,
            "warnings": all_warnings,
            "coverage": coverage,
        },
        warnings=warnings,
    )


def result_protocol_usable(value: Mapping[str, Any] | None) -> bool:
    return normalize_result_protocol(value)["status"] in USABLE_RESULT_PROTOCOL_STATUSES


def artifact_quality_for_protocol(value: Mapping[str, Any] | None) -> str:
    return (
        "standard"
        if normalize_result_protocol(value)["status"] == "ok"
        else "degraded"
    )


def result_structure_quarantined(value: Mapping[str, Any] | None) -> bool:
    """Read code-owned whole-result isolation without rejecting partial results."""
    return any(
        row["path"] == "$"
        for row in normalize_result_protocol(value)["quarantined_items"]
    )
