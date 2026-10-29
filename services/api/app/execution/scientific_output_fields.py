"""Describe the actual model-owned fields without sending a Formal Schema table."""

from collections.abc import Mapping
from typing import Any


def _field_description(node, path, required):
    row: dict[str, Any] = {"field": path, "required": required}
    for key, label in (
        ("type", "valueKind"),
        ("enum", "allowedValues"),
        ("const", "fixedValue"),
        ("description", "meaning"),
        ("minItems", "minimumItems"),
        ("maxItems", "maximumItems"),
        ("minLength", "minimumLength"),
        ("maxLength", "maximumLength"),
        ("minimum", "minimumValue"),
        ("maximum", "maximumValue"),
    ):
        if key in node:
            row[label] = node[key]
    if node.get("additionalProperties") is False:
        row["useDeclaredFieldNames"] = True
    return row


def scientific_output_fields(schema: Mapping[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []

    def visit(
        node: Mapping[str, Any], path: str, required: bool, refs: frozenset[str] = frozenset()
    ) -> None:
        reference = node.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/"):
            if reference in refs:
                return
            target: Any = schema
            for part in reference[2:].split("/"):
                target = target.get(part.replace("~1", "/").replace("~0", "~"), {})
            if isinstance(target, Mapping):
                visit(target, path, required, refs | {reference})
            return
        if path:
            fields.append(_field_description(node, path, required))
        properties = node.get("properties", {})
        if isinstance(properties, Mapping):
            for name, child in properties.items():
                if isinstance(child, Mapping):
                    visit(
                        child,
                        f"{path}.{name}" if path else name,
                        name in node.get("required", []),
                        refs,
                    )
        items = node.get("items")
        if isinstance(items, Mapping):
            visit(items, f"{path}[]", True, refs)
        for keyword in ("anyOf", "oneOf", "allOf"):
            for branch in node.get(keyword, []):
                if isinstance(branch, Mapping):
                    visit(branch, path, required, refs)

    visit(schema, "", True)
    return fields
