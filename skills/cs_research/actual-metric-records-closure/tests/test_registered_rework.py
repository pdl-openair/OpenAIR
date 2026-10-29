from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from app.execution.durable_skill_runtime import (
    _read_skill_contract,
    resolve_registered_durable_skill,
)
from app.execution.provider_visible_skill_input import project_provider_parameters
from app.execution.skill_finalization_runtime import finalize_and_validate_registered_output

ROOT = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize(
    "name", ["actual-metric-records-closure", "reproduction-execution-record-closure"]
)
@pytest.mark.parametrize(
    "draft",
    [
        {},
        {"unknowns": ["valid scientific limit", "", 0, False, {"text": "preserve me"}]},
        {"unknowns": None},
        {"unknowns": "unstructured limit"},
        {"unknowns": ["long " * 500] * 65},
    ],
)
def test_registered_source_restoration_is_not_gated_by_local_unknown_errors(name, draft):
    directory = ROOT / "skills/cs_research" / name
    spec = importlib.util.spec_from_file_location(
        "rework_" + name, directory / "tests/test_finalization.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    metadata = yaml.safe_load((directory / "skill.yaml").read_text(encoding="utf-8"))
    skill = resolve_registered_durable_skill(metadata["skill_id"])
    assert skill and skill["durableExecutable"]
    source = module._source() if name.startswith("actual") else module._raw()
    payload = module._input(source)
    before = deepcopy(payload)
    contract = skill["sourceContract"]
    production_metadata, instructions = _read_skill_contract(skill)
    assert production_metadata["llm"] == metadata["llm"]
    assert "unknowns" in instructions
    projected = project_provider_parameters(production_metadata, payload)
    assert "raw_execution_record" not in projected
    assert "raw_actual_metrics" not in projected
    output = finalize_and_validate_registered_output(
        repo_root=ROOT,
        skill_path=skill["skillPath"],
        frozen_contract=contract["frozenFinalization"],
        input_payload=payload,
        draft_payload=draft,
        final_schema=json.loads((directory / "output.schema.json").read_text()),
    )
    assert payload == before
    assert (
        output.output["metrics"] == source["metrics"]
        if name.startswith("actual")
        else output.output["execution"] == source
    )
    values = draft.get("unknowns", [])
    assert output.status == "succeeded"
    assert not output.errors
    assert output.output["unknowns"] == (values if isinstance(values, list) else [values])
