from __future__ import annotations

import importlib.util
from pathlib import Path

_ADAPTER = Path(__file__).resolve().parent / "adapter" / "structure_crossover_adapter.py"
_SPEC = importlib.util.spec_from_file_location("structure_crossover_adapter", _ADAPTER)
assert _SPEC and _SPEC.loader
adapter = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(adapter)


class _Completed:
    returncode = 0
    stdout = ""
    stderr = ""


def _candidate(index: int, cif_text: str) -> dict:
    return {"material_id": f"interactive-{index}", "formula_pretty": "AB", "cif_text": cif_text}


def test_interactive_profile_uses_canonical_material_content(monkeypatch, tmp_path: Path) -> None:
    first_cif = "data_interactive_first\n_cell_length_a 3.1\n"
    second_cif = "data_interactive_second\n_cell_length_a 3.2\n"
    calls: list[list[str]] = []
    observed_seed_texts: list[str] = []
    run_sh = tmp_path / "run.sh"
    run_sh.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    monkeypatch.setattr(adapter, "RUN_SH", run_sh)

    def fake_run(command, **_kwargs):
        calls.append(command)
        if "init" in command:
            seed_paths = command[command.index("--seed-cifs") + 1].split(",")
            observed_seed_texts.extend(Path(path).read_text(encoding="utf-8") for path in seed_paths)
        return _Completed()

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    result = adapter.run_structure_crossover_mvp({
        "goal": "interactive profile",
        "candidates": [_candidate(1, first_cif), _candidate(2, second_cif)],
        "task_instruction": {"skip_runplan_llm": True, "run_dir": str(tmp_path / "run")},
    })

    init = calls[0]
    assert init[init.index("--pop-size") + 1] == "1"
    assert observed_seed_texts == [first_cif]
    assert len([call for call in calls if "step" in call]) == 1
    assert result["state"] == "succeeded"
