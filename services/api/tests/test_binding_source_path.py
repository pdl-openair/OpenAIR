from __future__ import annotations

import pytest

from app.modules.binding_source_path import (
    BindingSourcePathError,
    resolve_binding_source_value,
)


def test_root_and_top_level_paths_return_the_declared_values() -> None:
    document = {"name": "OpenAIR", "nested": {"value": 7}}

    assert resolve_binding_source_value(document, "$") == document
    assert resolve_binding_source_value(document, "name") == "OpenAIR"


def test_dotted_path_traverses_objects_but_literal_top_level_key_wins() -> None:
    document = {
        "structured_context": {"parameter_admission_judgments": ["nested"]},
        "structured_context.parameter_admission_judgments": ["literal"],
    }

    assert resolve_binding_source_value(
        document, "structured_context.parameter_admission_judgments"
    ) == ["literal"]
    assert resolve_binding_source_value(
        {"structured_context": {"parameter_admission_judgments": ["nested"]}},
        "structured_context.parameter_admission_judgments",
    ) == ["nested"]


@pytest.mark.parametrize(
    ("document", "path", "code"),
    [
        ({"nested": {}}, "nested.value", "binding_source_path_missing"),
        ({"nested": "not-an-object"}, "nested.value", "binding_source_path_non_object"),
        ({"nested": {}}, "nested..value", "binding_source_path_invalid"),
    ],
)
def test_invalid_or_unreadable_paths_have_stable_diagnostics(
    document: dict,
    path: str,
    code: str,
) -> None:
    with pytest.raises(BindingSourcePathError) as caught:
        resolve_binding_source_value(document, path)

    assert caught.value.code == code
    assert caught.value.source_path == path
