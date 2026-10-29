"""Pure Research Start intent normalization and fingerprinting contracts."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from typing import Any

from app.modules.research_start_errors import InvalidIntentError, ResearchStartError


__all__ = ("InvalidIntentError", "ResearchStartError", "canonical_fingerprint")


FINGERPRINT_VERSION = "research-start-intent-v1"
DURABLE_FINGERPRINT_VERSION = "research-start-request-v2"
INTENT_KEYS = (
    "input",
    "sourcePage",
    "selectedHint",
    "presetQuery",
    "requestedWorkflow",
    "sessionId",
    "executionMode",
    "createClarificationDraft",
    "planningSources",
)
INTENT_DEFAULTS: dict[str, Any] = {
    "sourcePage": "home",
    "selectedHint": None,
    "presetQuery": None,
    "requestedWorkflow": None,
    "sessionId": None,
    "executionMode": "stage_confirmation",
    "createClarificationDraft": False,
    "planningSources": [],
}
SOURCE_PAGES = frozenset({"home", "board", "starter_plaza", "sample"})


def _normalize_text(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _canonical_planning_sources(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise InvalidIntentError()
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != {"kind", "id", "version"}:
            raise InvalidIntentError()
        kind = raw.get("kind")
        source_id = raw.get("id")
        version = raw.get("version")
        if kind != "research_package" or not isinstance(source_id, str) or not source_id.strip():
            raise InvalidIntentError()
        if not isinstance(version, str) or not version.strip():
            raise InvalidIntentError()
        row = {
            "kind": kind,
            "id": _normalize_text(source_id),
            "version": _normalize_text(version),
        }
        identity = (row["kind"], row["id"], row["version"])
        if identity in seen:
            raise InvalidIntentError()
        seen.add(identity)
        normalized.append(row)
    return normalized


def _canonical_intent(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise InvalidIntentError()
    keys = set(value)
    if "input" not in keys or keys - set(INTENT_KEYS):
        raise InvalidIntentError()
    normalized = {**INTENT_DEFAULTS, **dict(value)}
    if not isinstance(normalized["input"], str) or not normalized["input"]:
        raise InvalidIntentError()
    if normalized["sourcePage"] not in SOURCE_PAGES:
        raise InvalidIntentError()
    for key in ("selectedHint", "presetQuery"):
        if normalized[key] is not None and not isinstance(normalized[key], str):
            raise InvalidIntentError()
    if normalized["requestedWorkflow"] not in {
        None,
        "opentrajectory_research_workflow",
        "materials_candidate_discovery_workflow",
        "materials_full_discovery_workflow",
        "pvd_process_optimization_workflow",
        "cs_paper_reproduction_workflow",
    }:
        raise InvalidIntentError()
    session_id = normalized["sessionId"]
    if session_id is not None and (
        not isinstance(session_id, str) or re.fullmatch(r"intent_[0-9a-f]{12}", session_id) is None
    ):
        raise InvalidIntentError()
    if normalized["executionMode"] not in {
        "automatic_research",
        "stage_confirmation",
        "manual_step",
    }:
        raise InvalidIntentError()
    if type(normalized["createClarificationDraft"]) is not bool:
        raise InvalidIntentError()
    normalized["planningSources"] = _canonical_planning_sources(normalized["planningSources"])
    for key in ("input", "sourcePage", "selectedHint", "presetQuery", "sessionId"):
        if isinstance(normalized[key], str):
            normalized[key] = _normalize_text(normalized[key])
    canonical = {key: normalized[key] for key in INTENT_KEYS if key != "planningSources"}
    # Preserve the pre-R3 fingerprint for requests that do not use prior research
    # sources, while making exact source identity part of new source-aware requests.
    if normalized["planningSources"]:
        canonical["planningSources"] = normalized["planningSources"]
    return canonical


def _fingerprint_normalized(intent: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        {"version": FINGERPRINT_VERSION, "intent": intent},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def canonical_fingerprint(value: Mapping[str, Any]) -> str:
    """Hash the versioned, exact Research Start intent contract."""

    return _fingerprint_normalized(_canonical_intent(value))


def _durable_fingerprint(intent: Mapping[str, Any], created_by: str) -> str:
    encoded = json.dumps(
        {"version": DURABLE_FINGERPRINT_VERSION, "intent": intent, "createdBy": created_by},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _request_fingerprint_matches(
    request: Mapping[str, Any], intent: Mapping[str, Any], created_by: str
) -> bool:
    payload = request.get("request_payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return False
    if not isinstance(payload, Mapping):
        return False
    persisted = dict(payload)
    marker_present = "_createdBy" in persisted
    persisted_actor = persisted.pop("_createdBy", None)
    execution_mode_present = "executionMode" in persisted
    try:
        persisted_intent = _canonical_intent(persisted)
    except InvalidIntentError:
        return False
    if persisted_intent != dict(intent):
        return False

    fingerprint_intent = dict(intent)
    if (
        not execution_mode_present
        and fingerprint_intent.get("executionMode") == "stage_confirmation"
    ):
        # Rows reserved before executionMode joined the durable contract did
        # not hash that default field. Preserve exact replay for those rows only.
        fingerprint_intent.pop("executionMode", None)
    if not marker_present:
        return created_by == request.get("owner_id") and request.get(
            "fingerprint"
        ) == _fingerprint_normalized(fingerprint_intent)
    return persisted_actor == created_by and request.get("fingerprint") == _durable_fingerprint(
        fingerprint_intent, created_by
    )
