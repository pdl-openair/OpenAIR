# scripts/ablation_runner.py
"""Orchestrate the 4-way ablation across modes and replicates.

STATIC_RULES runs are fully automated. AGENT_NO_MEM and AGENT_WITH_MEM
runs are scaffolded and require an external Claude session to drive
(per MANUAL_ORCHESTRATION.md in each run dir). ORACLE runs are
computed post-hoc from a recorded AGENT trajectory.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from steering_modes import SteeringMode


@dataclass(frozen=True, slots=True)
class AblationSpec:
    base_run_dir: Path
    family: str
    fitness_backend: str
    objectives: dict
    constraints: dict
    seeds_cifs: list[Path]
    modes: list[SteeringMode]
    replicates: int
    pop_size: int
    max_generations: int
    parallel: int = 1


@dataclass(frozen=True, slots=True)
class AblationRun:
    mode: SteeringMode
    replicate: int
    run_dir: Path
    init_script_path: Path


_MANUAL_TEMPLATE = """# Manual Orchestration Required — {mode} run {replicate}

This ablation run uses **{mode}** steering, which requires an external
Claude session to drive the evolutionary loop.

## Run dir
{run_dir}

## How to drive
1. Initialize:
   ```
   bash $SKILL/scripts/run.sh init --run-dir {run_dir} \\
       --fitness-backend {backend} \\
       --objectives '{objectives_json}' \\
       --constraints '{constraints_json}' \\
       --pop-size {pop_size} --max-generations {max_generations} \\
       --noise-level 0.3 --crossover-rate 0.5 \\
       {memory_flag} --family {family} \\
       --seed-cifs {seeds}
   ```
2. For each generation 1..{max_generations}:
   a. `bash $SKILL/scripts/run.sh step --run-dir {run_dir}`
   b. Read `snapshot_latest.json` {memory_clause}
   c. Decide a steer action. {memory_decision_clause}
   d. Apply via `bash $SKILL/scripts/run.sh steer --run-dir {run_dir} --<flag> <value>`
3. After gen {max_generations}:
   `bash $SKILL/scripts/run.sh finalize --run-dir {run_dir} --top-n {pop_size} --evaluate`
"""


def _memory_clause(mode: SteeringMode) -> tuple[str, str, str]:
    """Returns (memory_flag_for_init, memory_clause_for_orchestration, decision_clause)."""
    if mode == SteeringMode.AGENT_WITH_MEM:
        return (
            "--memory-dir <ABSOLUTE_PATH_TO_SHARED_MEMORY_DIR>",
            "+ `memory_query_result.json`",
            "Use the retrieved memories as evidence when justifying your steer.",
        )
    return ("", "only", "Do NOT consult any memory store; rely on the snapshot alone.")


def _manual_md(spec: AblationSpec, mode: SteeringMode, replicate: int, run_dir: Path) -> str:
    flag, mem_clause, decision = _memory_clause(mode)
    return _MANUAL_TEMPLATE.format(
        mode=mode.value,
        replicate=replicate,
        run_dir=run_dir,
        backend=spec.fitness_backend,
        objectives_json=json.dumps(spec.objectives, ensure_ascii=False),
        constraints_json=json.dumps(spec.constraints, ensure_ascii=False),
        pop_size=spec.pop_size,
        max_generations=spec.max_generations,
        memory_flag=flag,
        memory_clause=mem_clause,
        memory_decision_clause=decision,
        family=spec.family,
        seeds=",".join(str(p) for p in spec.seeds_cifs),
    )


_STATIC_RUNBOOK = """#!/usr/bin/env bash
# STATIC_RULES ablation run {replicate}
set -euo pipefail
SKILL="{skill_path}"
RUN_DIR="{run_dir}"
SEEDS="{seeds}"

# 宿主机可能没有 `python` 别名（只有 python3 / .venv/bin/python），显式解析。
PY="${{EVOLVE_PY:-$(dirname $SKILL)/../.venv/bin/python}}"
[ -x "$PY" ] || PY="$(command -v python3)"

bash $SKILL/scripts/run.sh init --run-dir $RUN_DIR \\
    --fitness-backend {backend} \\
    --objectives '{objectives_json}' \\
    --constraints '{constraints_json}' \\
    --pop-size {pop_size} --max-generations {max_generations} \\
    --noise-level 0.3 --crossover-rate 0.5 \\
    --family {family} \\
    --seed-cifs "$SEEDS"

for g in $(seq 1 {max_generations}); do
    bash $SKILL/scripts/run.sh step --run-dir $RUN_DIR
    # Apply STATIC_RULES automatically based on current snapshot
    "$PY" $SKILL/scripts/ablation_static_decide.py --run-dir $RUN_DIR
done

bash $SKILL/scripts/run.sh finalize --run-dir $RUN_DIR --top-n {pop_size} --evaluate
"""


def scaffold_ablation_runs(spec: AblationSpec) -> list[AblationRun]:
    spec.base_run_dir.mkdir(parents=True, exist_ok=True)
    # The skill dir is where ablation_runner.py itself lives (scripts/'s
    # parent) — independent of where the ablation's run dirs are placed.
    # .absolute() (NOT .resolve()): keep the repo-local invocation path even
    # when scripts/ is symlinked into a shared skills source tree.
    skill_path = Path(__file__).absolute().parent.parent
    runs: list[AblationRun] = []
    seeds_str = ",".join(str(p) for p in spec.seeds_cifs)

    for mode in spec.modes:
        for rep in range(spec.replicates):
            run_dir = spec.base_run_dir / f"{mode.value}_rep{rep}"
            run_dir.mkdir(parents=True, exist_ok=True)
            # Absolute so run_static.sh works from any cwd
            run_dir_abs = run_dir.absolute()
            if mode == SteeringMode.STATIC_RULES:
                script_path = run_dir / "run_static.sh"
                script_path.write_text(
                    _STATIC_RUNBOOK.format(
                        replicate=rep,
                        skill_path=str(skill_path),
                        run_dir=str(run_dir_abs),
                        seeds=seeds_str,
                        backend=spec.fitness_backend,
                        objectives_json=json.dumps(spec.objectives, ensure_ascii=False),
                        constraints_json=json.dumps(spec.constraints, ensure_ascii=False),
                        pop_size=spec.pop_size,
                        max_generations=spec.max_generations,
                        family=spec.family,
                    ),
                    encoding="utf-8",
                )
                script_path.chmod(0o755)
            else:
                script_path = run_dir / "MANUAL_ORCHESTRATION.md"
                script_path.write_text(_manual_md(spec, mode, rep, run_dir_abs), encoding="utf-8")
            runs.append(AblationRun(mode=mode, replicate=rep, run_dir=run_dir, init_script_path=script_path))
    return runs


def _parse_modes(s: str) -> list[SteeringMode]:
    parts = [p.strip() for p in s.split(",") if p.strip()]
    out: list[SteeringMode] = []
    for p in parts:
        try:
            out.append(SteeringMode(p))
        except ValueError as e:
            raise SystemExit(f"unknown mode '{p}': {e}") from e
    return out


def main() -> int:
    import argparse

    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    scaffold_p = sub.add_parser("scaffold", help="Scaffold run dirs for an ablation")
    scaffold_p.add_argument("--base-run-dir", required=True, type=Path)
    scaffold_p.add_argument("--family", required=True)
    scaffold_p.add_argument("--fitness-backend", required=True)
    scaffold_p.add_argument("--objectives", required=True, help="JSON dict")
    scaffold_p.add_argument("--constraints", required=True, help="JSON dict")
    scaffold_p.add_argument("--seeds", required=True, help="Comma-separated CIF paths")
    scaffold_p.add_argument("--modes", required=True, help="Comma-separated mode names")
    scaffold_p.add_argument("--replicates", required=True, type=int)
    scaffold_p.add_argument("--pop-size", required=True, type=int)
    scaffold_p.add_argument("--max-generations", required=True, type=int)
    scaffold_p.add_argument("--parallel", type=int, default=1)
    args = p.parse_args()

    if args.cmd == "scaffold":
        spec = AblationSpec(
            base_run_dir=args.base_run_dir,
            family=args.family,
            fitness_backend=args.fitness_backend,
            objectives=json.loads(args.objectives),
            constraints=json.loads(args.constraints),
            seeds_cifs=[Path(p) for p in args.seeds.split(",") if p],
            modes=_parse_modes(args.modes),
            replicates=args.replicates,
            pop_size=args.pop_size,
            max_generations=args.max_generations,
            parallel=args.parallel,
        )
        runs = scaffold_ablation_runs(spec)
        for r in runs:
            print(json.dumps({"mode": r.mode.value, "replicate": r.replicate, "run_dir": str(r.run_dir)}, ensure_ascii=False))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
