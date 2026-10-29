from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = ROOT / "skills/materials/crystal-design-structure-crossover-py"
ADAPTER_PATH = SKILL_ROOT / "adapter/structure_crossover_adapter.py"


def _adapter_module():
    spec = importlib.util.spec_from_file_location("structure_crossover_adapter_contract_test", ADAPTER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _candidate(index: int, cif_text: str | None = None) -> dict:
    return {
        "material_id": f"mp-{index}",
        "formula_pretty": f"A{index}B",
        "cif_text": cif_text or f"data_mp_{index}\n_cell_length_a 4.2\n",
    }


class _Completed:
    returncode = 0
    stdout = ""
    stderr = ""


def test_canonical_schema_and_adapter_deliver_cif_content_to_skill_owned_seed_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    schema = json.loads((SKILL_ROOT / "input.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.validators.validator_for(schema)(schema)
    cif_text = "# submitted material\ndata_boundary_marker\n_cell_length_a 4.2\n"
    payload = {
        "goal": "canonical candidate replay",
        "candidates": [{**_candidate(1, cif_text), "provenance": {"source": "user"}}],
        "task_instruction": {"skip_runplan_llm": True, "run_dir": str(tmp_path / "run")},
    }
    validator.validate(payload)
    module = _adapter_module()
    observed_seed_texts: list[str] = []

    def fake_run(command, **_kwargs):
        if "init" in command:
            seed_paths = command[command.index("--seed-cifs") + 1].split(",")
            observed_seed_texts.extend(Path(path).read_text(encoding="utf-8") for path in seed_paths)
        return _Completed()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    result = module.run_structure_crossover_mvp(payload)

    assert result["state"] == "succeeded"
    assert observed_seed_texts == [cif_text]


def test_canonical_input_rejects_host_file_path_without_reading_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    host_file = tmp_path / "host-material.cif"
    host_marker = "data_host_secret_marker\n_cell_length_a 99\n"
    host_file.write_text(host_marker, encoding="utf-8")
    module = _adapter_module()
    subprocess_calls: list[list[str]] = []
    monkeypatch.setattr(
        module.subprocess, "run", lambda command, **_kwargs: subprocess_calls.append(command) or _Completed()
    )

    with pytest.raises(ValueError, match="cif_text must contain CIF content, not a file path"):
        module.run_structure_crossover_mvp(
            {"goal": "must not read host file", "candidates": [_candidate(1, str(host_file))]}
        )

    assert subprocess_calls == []
    assert host_file.read_text(encoding="utf-8") == host_marker


def test_schema_rejects_legacy_execution_path_fields() -> None:
    schema = json.loads((SKILL_ROOT / "input.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.validators.validator_for(schema)(schema)
    for legacy_key in ("seed_cifs", "seed_cif_paths", "seed_cif_files"):
        with pytest.raises(jsonschema.ValidationError):
            validator.validate(
                {"goal": "legacy path replay", "candidates": [_candidate(1)], "task_instruction": {legacy_key: ["/tmp/seed.cif"]}}
            )


def test_cif_with_comments_and_normal_structured_fields_is_not_misclassified_as_path() -> None:
    goal, candidates, task_instruction = _adapter_module()._public_input(
        {
            "goal": "ordinary user goal",
            "candidates": [{
                **_candidate(1, "\n# ordinary CIF comment\ndata_valid_material\n"),
                "structured_metadata": {"tags": ["candidate", "user-provided"]},
            }],
            "task_instruction": {"objectives": {"ehull": "minimize"}},
        }
    )
    assert goal == "ordinary user goal"
    assert candidates[0]["structured_metadata"]["tags"] == ["candidate", "user-provided"]
    assert task_instruction == {"objectives": {"ehull": "minimize"}}
@pytest.mark.parametrize("host_path", [r"C:\Users\someone\sample.cif", "/home/user/sample.cif"])
def test_formal_schema_rejects_windows_and_posix_host_paths(host_path: str) -> None:
    schema = json.loads((SKILL_ROOT / "input.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.validators.validator_for(schema)(schema)
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({"goal": "must reject host paths", "candidates": [_candidate(1, host_path)]})

def test_production_runtime_rejects_path_with_the_formal_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    sys.path.insert(0, str(ROOT / "services/api"))
    try:
        from app.execution import agent_service
    finally:
        sys.path.pop(0)
    monkeypatch.setattr(agent_service, "repo_root", lambda: ROOT)
    with pytest.raises(RuntimeError, match="python adapter input invalid"):
        agent_service._validate_python_adapter_input(
            "skills/materials/crystal-design-structure-crossover-py",
            {"goal": "must reject host paths", "candidates": [_candidate(1, "/home/user/sample.cif")]},
        )
