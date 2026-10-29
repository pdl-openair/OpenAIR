# scripts/tests/test_ablation_runner.py
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ablation_runner import AblationSpec, scaffold_ablation_runs
from steering_modes import SteeringMode


def test_scaffold_creates_run_dirs(tmp_path: Path) -> None:
    spec = AblationSpec(
        base_run_dir=tmp_path / "ablation",
        family="rare_earth",
        fitness_backend="mattergen_eval",
        objectives={"ehull_eval": "minimize"},
        constraints={},
        seeds_cifs=[tmp_path / "a.cif"],
        modes=[SteeringMode.STATIC_RULES, SteeringMode.AGENT_NO_MEM],
        replicates=2,
        pop_size=16,
        max_generations=4,
        parallel=1,
    )
    runs = scaffold_ablation_runs(spec)
    # 2 modes × 2 replicates = 4 run_dirs
    assert len(runs) == 4
    for r in runs:
        assert r.run_dir.exists()
        assert r.init_script_path.exists()
        # AGENT modes also get MANUAL_ORCHESTRATION.md
        if r.mode in (SteeringMode.AGENT_NO_MEM, SteeringMode.AGENT_WITH_MEM):
            assert (r.run_dir / "MANUAL_ORCHESTRATION.md").exists()


def test_static_runs_have_runbook_script(tmp_path: Path) -> None:
    spec = AblationSpec(
        base_run_dir=tmp_path / "ab",
        family="x",
        fitness_backend="alignn",
        objectives={"bandgap": "maximize"},
        constraints={},
        seeds_cifs=[],
        modes=[SteeringMode.STATIC_RULES],
        replicates=1,
        pop_size=8,
        max_generations=2,
    )
    runs = scaffold_ablation_runs(spec)
    assert runs[0].mode == SteeringMode.STATIC_RULES
    assert "static" in runs[0].init_script_path.name


def test_static_decider_applies_ban(tmp_path: Path, monkeypatch) -> None:
    """Given a snapshot with single-element individuals, the decider script
    emits a ban action into steer_log.jsonl."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "constraints.json").write_text(json.dumps({}))
    # Build a synthetic snapshot_latest.json
    snapshot = {
        "generation": 2,
        "pop_size": 4,
        "pareto_size": 2,
        "stable_count": 2,
        "rmsd_median": 0.001,
        "constraint_violation_rate": 0.0,
        "single_element_individuals": [{"element": "Au", "ehull": 0.001}],
        "pareto_size_unchanged_gens": 0,
    }
    (run_dir / "snapshot_latest.json").write_text(json.dumps(snapshot))

    scripts_dir = Path(__file__).resolve().parent.parent
    # Use --dry-run to test decision logic without invoking the actual steer code
    result = subprocess.run(
        [
            sys.executable,
            str(scripts_dir / "ablation_static_decide.py"),
            "--run-dir",
            str(run_dir),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert '"action": "ban"' in result.stdout
    assert "Au" in result.stdout


def test_scaffold_subcommand_end_to_end(tmp_path: Path) -> None:
    scripts_dir = Path(__file__).resolve().parent.parent
    a_cif = tmp_path / "a.cif"
    a_cif.write_text("data_dummy", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(scripts_dir / "ablation_runner.py"),
            "scaffold",
            "--base-run-dir",
            str(tmp_path / "ab"),
            "--family",
            "rare_earth",
            "--fitness-backend",
            "alignn",
            "--objectives",
            '{"bandgap": "maximize"}',
            "--constraints",
            "{}",
            "--seeds",
            str(a_cif),
            "--modes",
            "static_rules,agent_no_mem",
            "--replicates",
            "1",
            "--pop-size",
            "8",
            "--max-generations",
            "2",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "ab" / "static_rules_rep0" / "run_static.sh").exists()
    assert (tmp_path / "ab" / "agent_no_mem_rep0" / "MANUAL_ORCHESTRATION.md").exists()
