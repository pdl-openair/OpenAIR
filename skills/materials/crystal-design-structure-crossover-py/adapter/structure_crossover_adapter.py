"""Durable Python adapter for crystal-design-structure-crossover-py.

Sibling to skills/materials/crystal-design-structure-crossover-agent (the claude_code
agent-guided version). This adapter runs the **same** deterministic evolve_driver
end-to-end via the python_adapter_runner, without Claude steering each generation.

Pipeline:
    Phase 0   LLM RunPlan (goal -> objectives, constraints, pop/gens/noise/crossover/seed)
              Skipped when task_instruction.skip_runplan_llm=true OR when objectives
              and config are already supplied via task_instruction.
    Phase 1   init  : evolve_driver init --objectives ... --constraints ... --seed-cifs ...
    Phase 2   step x N: evolve_driver step (optionally LLM steer between generations
                        when task_instruction.enable_llm_steer=true, default false)
    Phase 3   finalize: evolve_driver finalize --top-n ... [--evaluate]
    Phase 4   Read leaderboard.csv / pareto/ / snapshot_latest.json -> assemble output

FAILURE SEMANTICS. durable_skill_runtime fail-closes on any raised exception.
Therefore this adapter must NEVER raise. Every MCP timeout / parse error / LLM
5xx is captured into `partial_failure` and the run continues with whatever
survived. The return dict is always populated with at least `state` and `run_dir`.

REUSES the original skill's evolve_driver.py — we do NOT duplicate the
deterministic loop here. The only differences vs the claude_code sibling are:
  1. Orchestration runs in Python (this file), not via Claude driving bash.
  2. No human-in-the-loop steering (Claude between gens). Optionally an in-adapter
     LLM call per generation can re-ban elements / tighten constraints.
  3. Single durable worker submission, no multi-turn Claude Agent SDK calls.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import traceback
import shutil
from uuid import uuid4
from pathlib import Path
from typing import Any

# ---- Paths -----------------------------------------------------------------

ADAPTER_DIR = Path(__file__).resolve().parent
SKILL_DIR = ADAPTER_DIR.parent                       # crystal-design-structure-crossover-py/


def _resolve_repo_root(start: Path) -> Path:
    """Find the unique ancestor directory that contains schemas/pvd."""
    candidates: list[Path] = []
    for parent in [start, *start.parents]:
        if (parent / "schemas" / "pvd").is_dir():
            candidates.append(parent)
    if len(candidates) != 1:
        raise RuntimeError("materials_repo_root_ambiguous")
    return candidates[0]


REPO_ROOT = _resolve_repo_root(SKILL_DIR)
SIBLING_SCRIPTS = REPO_ROOT / "skills" / "materials" / "crystal-design-structure-crossover-agent" / "scripts"
RUN_SH = SIBLING_SCRIPTS / "run.sh"

DEFAULT_RUNS_ROOT = REPO_ROOT / "skills" / "materials" / "evaluation" / "high_mobility" / "runs"


# ---- Helpers ---------------------------------------------------------------

def _now_ms() -> int:
    return int(time.time() * 1000)


def _partial(partial: list[dict], phase: str, msg: str, **extra) -> None:
    partial.append({"phase": phase, "message": msg, **extra})


def _run_sh_environment(receipts_path: Path | None = None) -> dict[str, str]:
    """Provide the durable worker's Python to the sibling shell wrapper."""
    env = {**os.environ, "EVOLVE_PY": sys.executable}
    if receipts_path is not None:
        env["MATERIALS_MCP_RECEIPTS_PATH"] = str(receipts_path)
    return env


def _run_subprocess(partial: list[dict], phase: str, cmd: list[str], timeout: int = 1800,
                    receipts_path: Path | None = None) -> tuple[int, str, str]:
    """Run cmd, never raise. Returns (rc, stdout, stderr)."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_run_sh_environment(receipts_path),
        )
        if proc.returncode != 0:
            _partial(partial, phase, f"subprocess rc={proc.returncode}",
                     cmd=cmd[-6:], stderr_tail=proc.stderr[-400:])
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        _partial(partial, phase, f"subprocess timeout after {timeout}s", cmd=cmd[-6:])
        return 124, "", "timeout"
    except Exception as exc:  # pragma: no cover  defensive
        _partial(partial, phase, f"subprocess exception: {exc!r}", cmd=cmd[-6:])
        return 1, "", repr(exc)


def _parse_runplan_via_llm(goal: str, partial: list[dict]) -> dict[str, Any]:
    """Phase 0: translate NL goal -> objectives/constraints/config via Claude API.

    Returns dict with keys: objectives, constraints, pop_size, max_generations,
    noise_level, crossover_rate, top_n, run_evaluate, generate_n (int).
    Falls back to a conservative default on any failure.
    """
    default = {
        "objectives": {"ehull": "minimize", "bandgap": "maximize"},
        "constraints": {"bandgap": {"min": 0.05, "max": 3.0, "weight": 5.0}},
        "pop_size": 1,
        "max_generations": 1,
        "noise_level": 0.3,
        "crossover_rate": 0.0,
        "top_n": 1,
        "run_evaluate": False,
        "generate_n": 0,
    }
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")
    if not api_key:
        _partial(partial, "runplan", "ANTHROPIC_API_KEY missing, using default runplan")
        return default
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        schema_brief = (
            "Respond with a single JSON object (no prose, no markdown) with keys: "
            'objectives (map of {property: "minimize"|"maximize"|"target"}, '
            'properties in {ehull, bandgap, dielectric, bulk_modulus, shear_modulus, '
            'exfoliation_energy, formation_energy, slme, supercon_tc, magmom}), '
            "constraints (map of property -> {min,max,weight}; weight>=10 means hard), "
            "pop_size (int, <=12 for eval), max_generations (int, <=5 for eval), "
            "noise_level (float 0.05-1.0), crossover_rate (float 0.0-1.0), "
            "top_n (int), run_evaluate (bool), generate_n (int 0-20)."
        )
        resp = client.messages.create(
            model=os.environ.get("RUNPLAN_MODEL", "claude-sonnet-4-5-20250929"),
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": (
                    f"User goal: {goal}\n\n"
                    f"Translate to evolutionary-search config. {schema_brief}\n"
                    "Pick property keys only valid for the alignn-predict MCP."
                ),
            }],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
        # Strip code fence if present.
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
        plan = json.loads(text)
        # Merge with defaults so missing keys don't blow up downstream.
        merged = {**default, **plan}
        _partial(partial, "runplan", "LLM runplan ok", objectives=merged["objectives"])
        return merged
    except Exception as exc:
        _partial(partial, "runplan", f"LLM runplan failed ({exc!r}), using default")
        return default


def _llm_steer(run_dir: Path, gen: int, partial: list[dict], base_constraints: dict) -> dict | None:
    """Optional in-adapter steering: read latest snapshot, ask LLM if any steer warranted.

    Returns updated constraints dict (deep-merged) or None to keep current.
    Conservative: only ban one element or nudge one weight per call. Skipped if no API key.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")
    if not api_key:
        return None
    snap = run_dir / "snapshot_latest.json"
    if not snap.exists():
        return None
    try:
        snapshot = json.loads(snap.read_text(encoding="utf-8"))
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        prompt = (
            f"You are steering an evolutionary crystal search at generation {gen}.\n"
            f"Current constraints: {json.dumps(base_constraints)}\n"
            f"Latest snapshot (front formulas, dominant elements, constraint violations): "
            f"{json.dumps(snapshot)[:3000]}\n\n"
            "Decide ONE steering action or 'no-op'. Reply as JSON with keys: "
            "action (one of ban_element, tighten_constraint, adjust_weight, no_op), "
            "args (object), reason (string). Be conservative."
        )
        resp = client.messages.create(
            model=os.environ.get("STEER_MODEL", "claude-haiku-4-5-20251001"),
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
        decision = json.loads(text)
        if decision.get("action") == "no_op":
            return None
        # Apply (shallow merge into constraints)
        new_constraints = json.loads(json.dumps(base_constraints))
        action = decision.get("action")
        args = decision.get("args") or {}
        if action == "ban_element":
            banned = set(new_constraints.get("element_set", {}).get("banned", []))
            banned.add(args.get("element"))
            new_constraints.setdefault("element_set", {})["banned"] = sorted(b for b in banned if b)
            _partial(partial, "steer", f"gen {gen}: banned {args.get('element')}",
                     reason=decision.get("reason", ""))
            return new_constraints
        if action == "tighten_constraint":
            prop = args.get("property")
            if prop:
                new_constraints.setdefault(prop, {}).update(args.get("bounds") or {})
                _partial(partial, "steer", f"gen {gen}: tightened {prop}",
                         reason=decision.get("reason", ""))
                return new_constraints
        if action == "adjust_weight":
            prop = args.get("property")
            if prop is not None:
                new_constraints.setdefault(prop, {})["weight"] = float(args.get("weight", 5.0))
                _partial(partial, "steer", f"gen {gen}: weight {prop}={args.get('weight')}")
                return new_constraints
    except Exception as exc:
        _partial(partial, "steer", f"gen {gen}: steer failed ({exc!r}), skipping")
    return None


def _public_input(input_payload: Any) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Validate the Materials public input without accepting execution-path aliases."""
    if not isinstance(input_payload, dict):
        raise ValueError("structure crossover input must be an object")
    goal = input_payload.get("goal")
    candidates = input_payload.get("candidates")
    task_instruction = input_payload.get("task_instruction", {})
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("structure crossover requires non-empty goal")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("structure crossover requires non-empty candidates")
    if not isinstance(task_instruction, dict):
        raise ValueError("structure crossover requires object task_instruction")
    forbidden_seed_keys = {"seed_cifs", "seed_cif_paths", "seed_cif_files"} & set(task_instruction)
    if forbidden_seed_keys:
        raise ValueError(
            "execution seed paths are Skill-internal and cannot be supplied by the public input: "
            + ", ".join(sorted(forbidden_seed_keys))
        )
    cleaned: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("structure crossover candidate must be an object")
        material_id = candidate.get("material_id")
        formula = candidate.get("formula_pretty")
        cif_text = candidate.get("cif_text")
        if (
            not isinstance(material_id, str) or not material_id.strip()
            or not isinstance(formula, str) or not formula.strip()
            or not isinstance(cif_text, str) or not cif_text.strip()
        ):
            raise ValueError(
                "structure crossover candidate requires material_id, formula_pretty and cif_text"
            )
        if not _is_cif_content(cif_text):
            raise ValueError(
                "structure crossover candidate cif_text must contain CIF content, not a file path"
            )
        cleaned.append(candidate)
    return goal.strip(), cleaned, dict(task_instruction)


def _is_cif_content(value: str) -> bool:
    """Require a CIF data block instead of accepting a path-shaped string as content."""
    for line in value.lstrip("\ufeff").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped.lower().startswith("data_") and len(stripped) > len("data_")
    return False


def _materialize_candidate_cifs(candidates: list[dict[str, Any]], seed_dir: Path) -> list[str]:
    """Materialize formal candidate CIF text as execution-local files inside this Skill."""
    seed_dir.mkdir(parents=True, exist_ok=False)
    paths: list[str] = []
    for index, candidate in enumerate(candidates):
        material_id = str(candidate["material_id"]).strip()
        formula = str(candidate["formula_pretty"]).strip()
        cif_text = str(candidate["cif_text"])
        safe_id = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in material_id)
        safe_formula = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in formula)
        path = seed_dir / f"{index:03d}_{safe_id}_{safe_formula}.cif"
        path.write_text(cif_text, encoding="utf-8")
        paths.append(str(path))
    return paths


# ---- Phase runners ---------------------------------------------------------

def _phase_init(run_dir: Path, plan: dict, seed_cifs: list[str], partial: list[dict], receipts_path: Path) -> bool:
    objectives = json.dumps(plan["objectives"], ensure_ascii=False)
    constraints = json.dumps(plan["constraints"], ensure_ascii=False)
    cmd = [
        "bash", str(RUN_SH), "init",
        "--run-dir", str(run_dir),
        "--fitness-backend", "alignn",
        "--objectives", objectives,
        "--constraints", constraints,
        "--pop-size", str(int(plan["pop_size"])),
        "--n-offspring", str(int(plan["pop_size"])),
        "--max-generations", str(int(plan["max_generations"])),
        "--noise-level", str(float(plan["noise_level"])),
        "--crossover-rate", str(float(plan["crossover_rate"])),
    ]
    if seed_cifs:
        cmd += ["--seed-cifs", ",".join(seed_cifs)]
    if int(plan.get("generate_n", 0)) > 0:
        cmd += ["--generate", str(int(plan["generate_n"]))]
    rc, out, err = _run_subprocess(partial, "init", cmd, timeout=900, receipts_path=receipts_path)
    return rc == 0


def _phase_step(run_dir: Path, gen: int, partial: list[dict], receipts_path: Path) -> bool:
    cmd = ["bash", str(RUN_SH), "step", "--run-dir", str(run_dir)]
    rc, out, err = _run_subprocess(partial, f"step_gen{gen}", cmd, timeout=1800, receipts_path=receipts_path)
    return rc == 0


def _phase_steer_apply(run_dir: Path, new_constraints: dict, partial: list[dict], gen: int) -> None:
    """Persist a steered constraint set via `evolve_driver steer` (multi-flag)."""
    if not new_constraints:
        return
    cmd = ["bash", str(RUN_SH), "steer", "--run-dir", str(run_dir)]
    banned = new_constraints.get("element_set", {}).get("banned")
    if banned:
        cmd += ["--ban", ",".join(banned)]
    for prop, spec in new_constraints.items():
        if prop == "element_set":
            continue
        if isinstance(spec, dict):
            if "weight" in spec:
                cmd += ["--weight", f"{prop}:{spec['weight']}"]
            bounds = []
            if "min" in spec:
                bounds.append(str(spec["min"]))
            if "max" in spec:
                bounds.append(str(spec["max"]))
            if bounds:
                cmd += ["--constraint", f"{prop}:{'::'.join(bounds)}"]
    rc, out, err = _run_subprocess(partial, f"steer_apply_gen{gen}", cmd, timeout=60)
    if rc != 0:
        _partial(partial, f"steer_apply_gen{gen}", f"steer apply rc={rc}, ignoring", stderr_tail=err[-200:])


def _phase_finalize(run_dir: Path, top_n: int, evaluate: bool, partial: list[dict], receipts_path: Path) -> bool:
    cmd = ["bash", str(RUN_SH), "finalize", "--run-dir", str(run_dir), "--top-n", str(top_n)]
    if evaluate:
        cmd += ["--evaluate"]
    rc, out, err = _run_subprocess(partial, "finalize", cmd, timeout=900, receipts_path=receipts_path)
    return rc == 0


# ---- Output assembly -------------------------------------------------------

def _read_leaderboard(run_dir: Path) -> str:
    lb = run_dir / "leaderboard.csv"
    if not lb.exists():
        return ""
    try:
        return lb.read_text(encoding="utf-8")
    except Exception:
        return ""


def _list_pareto_cifs(run_dir: Path) -> list[str]:
    pareto_dir = run_dir / "pareto"
    if not pareto_dir.is_dir():
        return []
    return sorted(str(p) for p in pareto_dir.glob("*.cif"))


def _read_iterations(run_dir: Path) -> list[dict]:
    snaps = sorted(run_dir.glob("snapshot_gen*.json"),
                   key=lambda p: int(re.search(r"gen(\d+)", p.name).group(1))
                   if re.search(r"gen(\d+)", p.name) else 0)
    out = []
    for snap in snaps:
        try:
            data = json.loads(snap.read_text(encoding="utf-8"))
            out.append({
                "snapshot_file": snap.name,
                "generation": data.get("generation"),
                "population_size": data.get("population_size"),
                "front_size": len(data.get("pareto_front", [])) if isinstance(data.get("pareto_front"), list) else None,
            })
        except Exception:
            out.append({"snapshot_file": snap.name, "parse_error": True})
    return out


def _read_mcp_receipts(receipts_path: Path) -> list[dict[str, Any]]:
    """Read only this invocation's boundary receipts, tolerating a failed writer."""
    if not receipts_path.exists():
        return []
    receipts: list[dict[str, Any]] = []
    try:
        for line in receipts_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if isinstance(row, dict):
                receipts.append(row)
    except (OSError, json.JSONDecodeError):
        return receipts
    return receipts


def _create_mcp_receipts_path(run_dir: Path) -> Path:
    """Create an invocation-owned receipt sink without changing scientific run identity."""
    while True:
        receipts_path = run_dir / f"mcp_transport_receipts.{uuid4().hex}.jsonl"
        try:
            receipts_path.touch(exist_ok=False)
            return receipts_path
        except FileExistsError:  # pragma: no cover - UUID collision retry
            continue


def _build_top_k_table(leaderboard_csv: str) -> str:
    """Pass through leaderboard.csv as the top_k_table string.

    The csv already has columns: formula, sg, ehull, bandgap, ..., rank.
    """
    return leaderboard_csv


def _build_run_summary(run_dir: Path, plan: dict, partial: list[dict],
                       init_ok: bool, finalize_ok: bool,
                       t_start: float) -> dict[str, Any]:
    gen_path = run_dir / "generation.txt"
    gen = -1
    if gen_path.exists():
        try:
            gen = int(gen_path.read_text().strip())
        except Exception:
            pass
    return {
        "init_ok": init_ok,
        "finalize_ok": finalize_ok,
        "final_generation": gen,
        "wall_clock_seconds": round(time.time() - t_start, 1),
        "objectives": plan["objectives"],
        "constraints": plan["constraints"],
        "config": {
            "pop_size": plan["pop_size"],
            "max_generations": plan["max_generations"],
            "noise_level": plan["noise_level"],
            "crossover_rate": plan["crossover_rate"],
        },
        "partial_failure_count": len(partial),
    }


# ---- Entry point -----------------------------------------------------------

def run_structure_crossover_mvp(input_payload: dict[str, Any]) -> dict[str, Any]:
    """Run from the exact public input; CIF materialization is Skill-owned."""
    goal, candidates, ti = _public_input(input_payload)
    t_start = time.time()
    partial: list[dict] = []
    plan: dict[str, Any] = {}

    # Phase 0 — runplan
    if ti.get("skip_runplan_llm"):
        plan = {
            "objectives": ti.get("objectives") or {"ehull": "minimize", "bandgap": "maximize"},
            "constraints": ti.get("constraints") or {"bandgap": {"min": 0.05, "max": 3.0, "weight": 5.0}},
            "pop_size": int(ti.get("pop_size", 1)),
            "max_generations": int(ti.get("max_generations", 1)),
            "noise_level": float(ti.get("noise_level", 0.3)),
            "crossover_rate": float(ti.get("crossover_rate", 0.0)),
            "top_n": int(ti.get("top_n", 1)),
            "run_evaluate": bool(ti.get("run_evaluate", False)),
            "generate_n": int(ti.get("generate_n", 0)),
        }
    else:
        plan = _parse_runplan_via_llm(goal, partial)
        # task_instruction overrides win
        for k in ("pop_size", "max_generations", "noise_level", "crossover_rate",
                  "top_n", "run_evaluate", "generate_n"):
            if k in ti:
                plan[k] = ti[k]
        if ti.get("objectives"):
            plan["objectives"] = ti["objectives"]
        if ti.get("constraints"):
            plan["constraints"] = ti["constraints"]

    # The LLM may explain the scientific objective, but it cannot expand the
    # default interactive workload without an explicit task instruction.
    for key, value in {
        "pop_size": 1,
        "max_generations": 1,
        "top_n": 1,
        "crossover_rate": 0.0,
        "generate_n": 0,
        "run_evaluate": False,
    }.items():
        if key not in ti:
            plan[key] = value

    # Pick run_dir
    run_dir_str = ti.get("run_dir") or os.environ.get("EVOLVE_RUN_DIR")
    if run_dir_str:
        run_dir = Path(run_dir_str)
    else:
        eval_id = ti.get("eval_id") or f"run_{int(time.time())}"
        run_dir = DEFAULT_RUNS_ROOT / str(eval_id).replace("/", "_")
    run_dir.mkdir(parents=True, exist_ok=True)
    # The UUID belongs only to the receipt sink, not the scientific run. Each
    # invocation gets an exclusively-created path even when run_dir is reused.
    try:
        receipts_path = _create_mcp_receipts_path(run_dir)
    except OSError as exc:
        _partial(partial, "preflight", f"failed to initialize MCP receipt sink: {exc!r}")
        receipts_path = run_dir / f"mcp_transport_receipts.unavailable.{uuid4().hex}.jsonl"

    # Capture the goal + plan into the run dir for reproducibility
    try:
        (run_dir / "goal.txt").write_text(goal, encoding="utf-8")
        (run_dir / "plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        _partial(partial, "io", f"failed to persist goal/plan: {exc!r}")

    # Sanity: the sibling skill's run.sh must exist
    if not RUN_SH.exists():
        _partial(partial, "preflight", f"sibling run.sh not found at {RUN_SH}")
        return {
            "state": "partial_failure",
            "run_dir": str(run_dir),
            "objectives": plan["objectives"],
            "constraints": plan["constraints"],
            "top_k_table": "",
            "iterations": [],
            "run_summary": _build_run_summary(run_dir, plan, partial, False, False, t_start),
            "pareto_cif_paths": [],
            "evaluate_report_path": None,
            "partial_failure": partial,
            "mcp_receipts": _read_mcp_receipts(receipts_path),
        }

    # Phase 1 — init. Formal CIF text becomes execution-local files here, inside the Skill.
    seed_dir = run_dir / f".input_seed_cifs_{uuid4().hex}"
    candidate_seed_cifs = _materialize_candidate_cifs(candidates, seed_dir)
    execution_seed_cifs = candidate_seed_cifs if "pop_size" in ti else candidate_seed_cifs[:1]
    try:
        init_ok = _phase_init(run_dir, plan, execution_seed_cifs, partial, receipts_path)
    finally:
        shutil.rmtree(seed_dir, ignore_errors=True)

    # Phase 2 — step loop
    if init_ok:
        max_gens = int(plan["max_generations"])
        enable_steer = bool(ti.get("enable_llm_steer"))
        for gen in range(1, max_gens + 1):
            step_ok = _phase_step(run_dir, gen, partial, receipts_path)
            if not step_ok:
                _partial(partial, f"step_gen{gen}", "step failed; aborting further gens")
                break
            if enable_steer and gen < max_gens:
                current_constraints = json.loads(
                    (run_dir / "constraints.json").read_text(encoding="utf-8")
                    if (run_dir / "constraints.json").exists() else "{}"
                )
                new_constraints = _llm_steer(run_dir, gen, partial, current_constraints)
                if new_constraints:
                    _phase_steer_apply(run_dir, new_constraints, partial, gen)

    # Phase 3 — finalize
    finalize_ok = _phase_finalize(run_dir, int(plan["top_n"]), bool(plan["run_evaluate"]), partial, receipts_path)

    # Phase 4 — assemble output
    leaderboard = _read_leaderboard(run_dir)
    pareto_cifs = _list_pareto_cifs(run_dir)
    iterations = _read_iterations(run_dir)
    evaluate_report = run_dir / "evaluate_report.json"
    state = "succeeded" if (init_ok and finalize_ok and not partial) else (
        "succeeded" if (init_ok and finalize_ok) else "partial_failure"
    )

    return {
        "state": state,
        "run_dir": str(run_dir),
        "objectives": plan["objectives"],
        "constraints": plan["constraints"],
        "top_k_table": _build_top_k_table(leaderboard),
        "iterations": iterations,
        "run_summary": _build_run_summary(run_dir, plan, partial, init_ok, finalize_ok, t_start),
        "pareto_cif_paths": pareto_cifs,
        "evaluate_report_path": str(evaluate_report) if evaluate_report.exists() else None,
        "partial_failure": partial if partial else None,
        "mcp_receipts": _read_mcp_receipts(receipts_path),
    }


# ---- CLI for direct invocation ---------------------------------------------
if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: structure_crossover_adapter.py <input_json_file>", file=sys.stderr)
        sys.exit(2)
    _payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    result = run_structure_crossover_mvp(_payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
