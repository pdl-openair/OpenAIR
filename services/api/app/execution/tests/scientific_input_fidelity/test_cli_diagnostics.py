from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from app.execution import llm_structured_runner as runner


@pytest.mark.parametrize("judgment", ["unsupported", " DEFER "])
def test_real_cli_runner_preserves_input_and_reports_schema_truth(monkeypatch, tmp_path, judgment):
    repository = tmp_path / "repo"
    directory = repository / "skills" / "input-fidelity"
    directory.mkdir(parents=True)
    (repository / "schemas" / "pvd").mkdir(parents=True)
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"judgment": {"enum": ["release", "defer"]}},
    }
    output_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["value"],
        "properties": {"value": {"type": "number"}},
    }
    (directory / "input.schema.json").write_text(json.dumps(input_schema), encoding="utf-8")
    (directory / "output.schema.json").write_text(json.dumps(output_schema), encoding="utf-8")
    (directory / "SKILL.md").write_text(
        "Return a JSON object with numeric field value.", encoding="utf-8"
    )
    (directory / "skill.yaml").write_text("output_file: output.json\n", encoding="utf-8")
    calls = []

    class Client:
        def __init__(self, **_kwargs):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, _url, *, json, **_kwargs):
            calls.append(deepcopy(json))
            body = {"choices": [{"message": {"content": '{"value":0}'}, "finish_reason": "stop"}]}
            return SimpleNamespace(
                status_code=200, json=lambda: body, raise_for_status=lambda: None
            )

    monkeypatch.setattr(runner.httpx, "Client", Client)
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "offline-only")
    monkeypatch.setenv("LLM_PROVIDER_BASE_URL", "https://offline.invalid")
    original = {"judgment": judgment}
    output, _elapsed, diagnostics = runner.run_skill_with_llm(
        directory,
        original,
        tmp_path / "workspace",
        "offline-case",
    )
    assert original == {"judgment": judgment}
    assert output == {"value": 0}
    assert len(calls) == 1
    assert diagnostics["input_schema_passed"] is True
    assert not diagnostics["input_contract_warnings"]
    assert json.dumps(original, ensure_ascii=False, indent=2) in calls[0]["messages"][1]["content"]


def test_cli_valid_output_union_is_not_cleared_by_aliasing() -> None:
    contract = {"anyOf": [{"type": "object", "required": ["finding"]}, {"type": "null"}]}
    value = {"finding": "existing valid result"}
    assert runner._validate_schema(value, contract, "Output") == value
    assert value == {"finding": "existing valid result"}
