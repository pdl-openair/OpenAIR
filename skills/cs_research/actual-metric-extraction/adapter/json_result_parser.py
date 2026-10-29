"""Strict JSON Pointer metric extraction."""

from __future__ import annotations

import json
from typing import Any

from .metric_values import validate_json_value
from .parser_errors import ParseFailure


def _decode_pointer_token(token: str) -> str:
    output = ""
    index = 0
    while index < len(token):
        char = token[index]
        if char != "~":
            output += char
            index += 1
            continue
        if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
            raise ParseFailure("metric_selector_invalid")
        output += "~" if token[index + 1] == "0" else "/"
        index += 2
    return output


def _json_pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ParseFailure("metric_selector_invalid")
    current = document
    for raw_token in pointer[1:].split("/"):
        token = _decode_pointer_token(raw_token)
        if isinstance(current, dict):
            if token not in current:
                raise ParseFailure("metric_not_found")
            current = current[token]
            continue
        if isinstance(current, list):
            if token == "-" or not token.isdigit():
                raise ParseFailure("metric_not_found")
            index = int(token)
            if index >= len(current):
                raise ParseFailure("metric_not_found")
            current = current[index]
            continue
        raise ParseFailure("metric_not_found")
    return current


def extract_json_metrics(text: str, metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        raise ParseFailure("source_format_invalid")

    results: list[dict[str, Any]] = []
    for metric in metrics:
        pointer = metric["selector"]["json_pointer"]
        try:
            raw = _json_pointer(document, pointer)
        except ParseFailure as error:
            results.append({"metric": metric, "failure_code": error.code})
            continue
        value, type_error = validate_json_value(raw, metric["value_type"])
        if type_error:
            results.append({"metric": metric, "failure_code": type_error})
            continue
        results.append({
            "metric": metric,
            "actual_value": value,
            "source_trace": {"json_pointer": pointer},
        })
    return results
