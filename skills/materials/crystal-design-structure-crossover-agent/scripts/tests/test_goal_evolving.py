# scripts/tests/test_goal_evolving.py
import json
from pathlib import Path

import pytest

from goal_evolving import pivot_objectives


def _seed_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "config.json").write_text(json.dumps({
        "objectives": {"bandgap": "maximize"},
        "fitness_backend": "alignn",
        "pop_size": 4,
    }))
    pop = [
        {"id": f"i{i}", "cif": "x", "properties": {"bandgap": 1.0 + 0.1 * i, "ehull": 0.05 - 0.01 * i}, "rank": 1}
        for i in range(4)
    ]
    (run_dir / "population.json").write_text(json.dumps(pop))
    return run_dir


def test_pivot_rewrites_objectives(tmp_path: Path) -> None:
    run_dir = _seed_run(tmp_path)
    summary = pivot_objectives(run_dir, new_objectives={"ehull": "minimize"})
    cfg = json.loads((run_dir / "config.json").read_text())
    assert cfg["objectives"] == {"ehull": "minimize"}
    assert summary["old"] == {"bandgap": "maximize"}
    assert summary["new"] == {"ehull": "minimize"}
    assert summary["individuals_re_ranked"] == 4


def test_pivot_rejects_unknown_key(tmp_path: Path) -> None:
    run_dir = _seed_run(tmp_path)
    with pytest.raises(KeyError, match="unknown property key"):
        pivot_objectives(run_dir, new_objectives={"missing_key": "maximize"})


def test_pivot_logs_to_jsonl(tmp_path: Path) -> None:
    run_dir = _seed_run(tmp_path)
    pivot_objectives(run_dir, new_objectives={"ehull": "minimize"})
    log_path = run_dir / "pivot_log.jsonl"
    assert log_path.exists()
    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["new"] == {"ehull": "minimize"}


import subprocess
import sys


def test_pivot_subcommand_end_to_end(tmp_path: Path) -> None:
    run_dir = _seed_run(tmp_path)
    scripts_dir = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [
            sys.executable,
            str(scripts_dir / "evolve_driver.py"),
            "pivot",
            "--run-dir",
            str(run_dir),
            "--objectives",
            '{"ehull": "minimize"}',
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    cfg = json.loads((run_dir / "config.json").read_text())
    assert cfg["objectives"] == {"ehull": "minimize"}
