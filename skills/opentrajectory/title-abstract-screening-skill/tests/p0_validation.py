"""Pytest P0 validation for title-abstract-screening-skill."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_schema_files_exist() -> None:
    assert (ROOT / "input.schema.json").is_file()
    assert (ROOT / "output.schema.json").is_file()
    assert (ROOT / "examples" / "request.json").is_file()
    assert (ROOT / "examples" / "response.json").is_file()


def test_request_conforms_to_input_schema() -> None:
    input_schema = _load_json(ROOT / "input.schema.json")
    request = _load_json(ROOT / "examples" / "request.json")
    Draft202012Validator(input_schema).validate(request)


def test_response_conforms_to_output_schema() -> None:
    output_schema = _load_json(ROOT / "output.schema.json")
    response = _load_json(ROOT / "examples" / "response.json")
    Draft202012Validator(output_schema).validate(response)


def test_adapter_importable() -> None:
    from adapter.title_abstract_screening_adapter import screen_title_abstract_candidates

    assert callable(screen_title_abstract_candidates)


def test_adapter_output_conforms_to_output_schema() -> None:
    from adapter.title_abstract_screening_adapter import screen_title_abstract_candidates

    request = _load_json(ROOT / "examples" / "request.json")
    output_schema = _load_json(ROOT / "output.schema.json")

    output = screen_title_abstract_candidates(request)
    Draft202012Validator(output_schema).validate(output)
