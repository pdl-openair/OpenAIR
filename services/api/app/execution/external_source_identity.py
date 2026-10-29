"""Validate external source identities only from frozen Skill declarations."""
from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Any, Mapping

_TYPES = {
    "arxiv_identifier",
    "doi",
    "url_locator",
    "provider_observed_source_identity",
}
_ARXIV = re.compile(
    r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", re.I
)
_DOI = re.compile(r"10\.\d{4,9}/\S+", re.I)
_URL = re.compile(r"https?://\S+", re.I)
_PLACEHOLDERS = {
    "",
    "unknown",
    "unknown source",
    "placeholder",
    "example",
    "tbd",
    "todo",
    "n/a",
    "na",
    "none",
    "null",
}


def _parts(value: Any, *, wildcard: bool) -> tuple[str, ...]:
    text = str(value or "")
    parts = tuple(
        part.replace("~1", "/").replace("~0", "~")
        for part in text[1:].split("/")
    )
    indexes = [index for index, part in enumerate(parts) if part == "*"]
    if (
        not text.startswith("/") or text == "/" or any(not part for part in parts)
        or (wildcard and indexes != [len(parts) - 1])
        or (not wildcard and indexes)
    ):
        raise ValueError("external_source_path_invalid")
    return parts


def _schema_at(
    schema: Mapping[str, Any], parts: tuple[str, ...]
) -> Mapping[str, Any]:
    current: Any = schema
    for part in parts:
        while isinstance(current, Mapping) and isinstance(current.get("$ref"), str):
            ref = current["$ref"]
            if not ref.startswith("#/"):
                raise ValueError("external_source_schema_ref_invalid")
            current = schema
            for key in ref[2:].split("/"):
                key = key.replace("~1", "/").replace("~0", "~")
                if not isinstance(current, Mapping) or key not in current:
                    raise ValueError("external_source_schema_ref_invalid")
                current = current[key]
        current = (
            current.get("items")
            if part == "*" and isinstance(current, Mapping)
            else (current.get("properties") or {}).get(part)
            if isinstance(current, Mapping)
            else None
        )
        if not isinstance(current, Mapping):
            raise ValueError("external_source_path_not_in_output_schema")
    while isinstance(current.get("$ref"), str):
        ref = current["$ref"]
        if not ref.startswith("#/"):
            raise ValueError("external_source_schema_ref_invalid")
        current = schema
        for key in ref[2:].split("/"):
            key = key.replace("~1", "/").replace("~0", "~")
            if not isinstance(current, Mapping) or key not in current:
                raise ValueError("external_source_schema_ref_invalid")
            current = current[key]
    return current


def validate_external_source_records_declaration(
    value: Any, *, output_schema: Mapping[str, Any]
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError("external_source_records_invalid")
    normalized: list[dict[str, Any]] = []
    seen_records: set[str] = set()
    for row in value:
        if not isinstance(row, Mapping) or set(row) != {
            "record_path", "identity_fields"
        }:
            raise ValueError("external_source_record_declaration_invalid")
        record_path = str(row["record_path"])
        if record_path in seen_records:
            raise ValueError("external_source_record_path_duplicate")
        record_parts = _parts(record_path, wildcard=True)
        _schema_at(output_schema, record_parts)
        fields = row["identity_fields"]
        if not isinstance(fields, list) or not fields:
            raise ValueError("external_source_identity_fields_invalid")
        normalized_fields = []
        seen_fields: set[str] = set()
        for field in fields:
            if not isinstance(field, Mapping) or set(field) != {
                "field_path", "identity_type"
            }:
                raise ValueError("external_source_identity_field_invalid")
            path = str(field["field_path"])
            kind = str(field["identity_type"])
            if path in seen_fields or kind not in _TYPES:
                raise ValueError("external_source_identity_field_invalid")
            _schema_at(
                output_schema,
                record_parts + _parts(path, wildcard=False),
            )
            normalized_fields.append({"field_path": path, "identity_type": kind})
            seen_fields.add(path)
        normalized.append(
            {
                "record_path": record_path,
                "identity_fields": normalized_fields,
            }
        )
        seen_records.add(record_path)
    return normalized


def _at(document: Any, parts: tuple[str, ...]) -> tuple[bool, Any]:
    current = document
    for part in parts:
        if not isinstance(current, Mapping) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _valid(value: Any, kind: str) -> bool:
    if not isinstance(value, str) or value.strip().casefold() in _PLACEHOLDERS:
        return False
    value = value.strip()
    pattern = {
        "arxiv_identifier": _ARXIV,
        "doi": _DOI,
        "url_locator": _URL,
    }.get(kind)
    return bool(value) if pattern is None else pattern.fullmatch(value) is not None


def validate_result_external_sources(
    content: str, declaration: Any, *, result_protocol: Mapping[str, Any] | None = None
) -> tuple[str, dict[str, Any]]:
    """Return accepted Artifact content and facts without altering Result content."""
    if declaration is None:
        return content, {
            "status": "not_declared",
            "acceptedCount": 0,
            "rejectedCount": 0,
            "records": [],
        }
    if not isinstance(declaration, list):
        raise ValueError("frozen_external_source_records_invalid")
    from app.models.result_protocol import result_structure_quarantined

    if result_structure_quarantined(result_protocol):
        return content, {
            "status": "not_validated",
            "reasonCode": "result_structure_quarantined",
            "acceptedCount": 0,
            "rejectedCount": 0,
            "records": [],
        }
    document = json.loads(content)
    if not isinstance(document, dict):
        raise ValueError("external_source_result_not_object")
    accepted_document = deepcopy(document)
    facts: list[dict[str, Any]] = []
    accepted_count = rejected_count = 0
    for item in declaration:
        record_path = str(item["record_path"])
        parts = _parts(record_path, wildcard=True)
        source_ok, records = _at(document, parts[:-1])
        target_ok, accepted_records = _at(accepted_document, parts[:-1])
        if (
            not source_ok
            or not target_ok
            or not isinstance(records, list)
            or not isinstance(accepted_records, list)
        ):
            raise ValueError("external_source_record_path_missing")
        accepted_rows: list[Any] = []
        for index, record in enumerate(records):
            reasons: list[dict[str, str]] = []
            if not isinstance(record, Mapping):
                reasons.append(
                    {
                        "fieldPath": "",
                        "identityType": "",
                        "reasonCode": "source_record_not_object",
                    }
                )
            else:
                for field in item["identity_fields"]:
                    path = str(field["field_path"])
                    kind = str(field["identity_type"])
                    present, value = _at(record, _parts(path, wildcard=False))
                    if not present or not _valid(value, kind):
                        reasons.append(
                            {
                                "fieldPath": path,
                                "identityType": kind,
                                "reasonCode": (
                                    "source_identity_missing"
                                    if not present
                                    else "source_identity_invalid"
                                ),
                            }
                        )
            pointer = record_path[:-1] + str(index)
            if reasons:
                rejected_count += 1
                facts.append(
                    {
                        "recordPath": pointer,
                        "status": "rejected",
                        "reasons": reasons,
                    }
                )
            else:
                accepted_count += 1
                accepted_rows.append(deepcopy(record))
                facts.append(
                    {
                        "recordPath": pointer,
                        "status": "accepted",
                        "reasons": [],
                    }
                )
        accepted_records[:] = accepted_rows
    acceptance = {
        "status": "validated",
        "acceptedCount": accepted_count,
        "rejectedCount": rejected_count,
        "records": facts,
    }
    if rejected_count == 0:
        return content, acceptance
    return json.dumps(
        accepted_document, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ), acceptance


__all__ = [
    "validate_external_source_records_declaration",
    "validate_result_external_sources",
]
