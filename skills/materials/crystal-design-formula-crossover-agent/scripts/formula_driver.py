"""Deterministic driver for crystal-design-formula-crossover-agent.

Subcommands: init / step / steer / finalize / status

This driver is a PURE ENGINE: it never calls an LLM. The crossover LLM
(Claude in the agent flow, or LLM_PROVIDER_BASE_URL in the -py adapter
flow) produces formulas; this driver accepts them via `step --new-formulas`
and does MCP fan-out + filter + reward + snapshot.

State files in <run_dir>/:
  runplan.json            - locked objectives + hard_filter (written by init)
  config.json             - mutable loop config (iter, batch_size, top_k)
  constraints.json        - mutable element/constraint rules (steer mutates)
  crossover_hints.json    - mutable crossover hints (steer mutates)
  population.json         - current top-K pool
  records/<f>__<i>.json   - per-candidate detail
  snapshot_iter{N}.json   - per-iter snapshot
  snapshot_latest.json    - alias to latest
  mattergen_eval.json     - last submitted eval job state
  leaderboard.csv / pareto/ / summary.json - finalize output

Exit codes: 0 ok, 2 args/state error, 3 MCP probe failure, 4 empty new-formulas.
Never exits 1 (reserved for unknown — must classify).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


# ============================================================================
# JSON I/O helpers (atomic writes)
# ============================================================================

def _save_json(path: Path, obj: Any) -> None:
    """Atomic JSON write via temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, prefix=path.name + ".", suffix=".tmp",
        delete=False, encoding="utf-8",
    ) as tf:
        json.dump(obj, tf, ensure_ascii=False, indent=2, default=str)
        tmp_path = Path(tf.name)
    os.replace(tmp_path, path)


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================================
# State readers
# ============================================================================

def _read_config(run_dir: Path) -> dict:
    return _load_json(run_dir / "config.json")


def _read_constraints(run_dir: Path) -> dict:
    return _load_json(run_dir / "constraints.json")


def _read_hints(run_dir: Path) -> dict:
    return _load_json(run_dir / "crossover_hints.json")


def _read_runplan(run_dir: Path) -> dict:
    return _load_json(run_dir / "runplan.json")


def _read_population(run_dir: Path) -> list:
    return _load_json(run_dir / "population.json").get("pool", [])


def _require_run_dir(args) -> Path:
    rd = Path(args.run_dir)
    if not rd.is_dir():
        sys.exit(2)
    return rd


# ============================================================================
# Filter / normalize / reward (canonical math, pure functions)
# ============================================================================

_OPS = {
    "<":  lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">":  lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


def apply_hard_filters(
    candidates: list[dict],
    hard_filter: list[dict],
    failures: dict,
) -> list[dict]:
    """Drop candidates whose named property violates the predicate.
    None values are kept (treated as 'unknown'). Records drops into failures['filtered_out'].
    """
    kept = list(candidates)
    for f in hard_filter or []:
        op = _OPS.get(f.get("op"))
        if op is None:
            continue
        next_round = []
        for c in kept:
            val = c.get(f["name"])
            if val is None:
                next_round.append(c)  # unknown → keep
                continue
            try:
                if op(float(val), float(f["value"])):
                    next_round.append(c)
                else:
                    failures.setdefault("filtered_out", []).append(
                        {"formula": c.get("formula"), "reason": f"{f['name']}={val} {f['op']} {f['value']}"}
                    )
            except (TypeError, ValueError):
                next_round.append(c)  # unparseable → keep
        kept = next_round
    return kept


def normalize_and_reward(pool: list[dict], objectives: list[dict]) -> list[dict]:
    """Min-max normalize per objective, then weighted reward.
    Mutates pool entries in place (adds _norm_<name> and reward fields).
    """
    for o in objectives:
        name = o["name"]
        values = [float(c[name]) for c in pool if c.get(name) is not None]
        if not values:
            for c in pool:
                c[f"_norm_{name}"] = 0.5
            continue
        vmin, vmax = min(values), max(values)
        for c in pool:
            if vmax == vmin:
                c[f"_norm_{name}"] = 0.5
            else:
                c[f"_norm_{name}"] = (float(c[name]) - vmin) / (vmax - vmin)
    for c in pool:
        reward = 0.0
        for o in objectives:
            w = float(o.get("weight") or 1.0)
            n = c[f"_norm_{o['name']}"]
            contrib = n if o["direction"] == "max" else (1.0 - n)
            reward += w * contrib
        c["reward"] = reward
    return pool


def select_topk(pool: list[dict], top_k: int) -> list[dict]:
    """Sort by reward desc, tiebreak ehull asc then formula asc. Return top_k."""
    return sorted(
        pool,
        key=lambda c: (-(c.get("reward") or 0.0), c.get("ehull") or 0.0, c.get("formula") or ""),
    )[:top_k]


# ============================================================================
# Phase 2b-2h pipeline (per-formula MCP fan-out)
# ============================================================================

def _safe_hhi(formula: str) -> dict:
    try:
        from pymatgen.analysis.hhi import HHIModel
        m = HHIModel()
        hhi_prod, hhi_res = m.get_hhi(formula)
        return {"hhi": float(hhi_prod), "hhi_reserve": float(hhi_res)}
    except Exception:
        return {"hhi": None, "hhi_reserve": None}


def _safe_mag_density(magmom: float, relaxed_cif: str) -> dict | None:
    try:
        from pymatgen.core import Structure
        s = Structure.from_str(relaxed_cif, fmt="cif")
        vol = s.volume
        n_atoms = len(s)
        if vol <= 0 or n_atoms == 0:
            return None
        return {"mag_density": float(magmom) / vol, "volume": vol, "n_atoms": n_atoms}
    except Exception:
        return None


def _pipeline_single(
    *, formula: str, iter_idx: int, runplan: dict, run_dir: Path,
) -> tuple[dict | None, dict]:
    """Run DiffCSP → MatterSim → ALIGNN → local scorers for ONE formula.
    Returns (record, failures_dict_for_this_formula) or (None, failures)."""
    import mcp_client
    fails: dict = {"diffcsp": [], "mattersim_unconverged": [], "alignn_null": []}

    # 2b. DiffCSP
    diffcsp = mcp_client.call_diffcsp(formula)
    if not diffcsp:
        fails["diffcsp"].append({"formula": formula, "error": "value:null"})
        return None, fails
    cif = diffcsp["cif"]

    # 2c. MatterSim relax
    relax = mcp_client.call_mattersim_relax(cif, max_steps=50)
    if not relax:
        fails["diffcsp"].append({"formula": formula, "error": "mattersim_no_response"})
        return None, fails
    if relax["f_max_eV_A"] > 1.0:
        fails["mattersim_unconverged"].append({"formula": formula, "f_max": relax["f_max_eV_A"]})
        return None, fails
    relaxed_cif = relax["relaxed_cif"]

    # 2d. ALIGNN
    props = mcp_client.call_alignn(relaxed_cif, runplan["alignn_models"])
    if not props:
        fails["alignn_null"].append({"formula": formula, "model": "all"})
        return None, fails
    # Check each required model came back non-null
    for model in runplan["alignn_models"]:
        cell = props.get(model) or {}
        if cell.get("value") is None and not cell.get("error"):
            # treat as null
            fails["alignn_null"].append({"formula": formula, "model": model})

    # 2e. Local scorers — always run hhi; run mag_density if any objective needs it
    values: dict[str, Any] = {}
    for model in runplan["alignn_models"]:
        cell = props.get(model) or {}
        v = cell.get("value")
        if v is not None:
            values[model] = v
    hhi = _safe_hhi(formula)
    if hhi.get("hhi") is not None:
        values["hhi"] = hhi["hhi"]
    if "magmom" in values:
        md = _safe_mag_density(float(values["magmom"]), relaxed_cif)
        if md:
            values["mag_density"] = md["mag_density"]

    # Build record
    record = {
        "formula": formula,
        "iter": iter_idx,
        "source": f"iter{iter_idx}_crossover",
        "cif": cif,
        "relaxed_cif": relaxed_cif,
        "energy_per_atom_eV": relax["energy_per_atom_eV"],
        "total_energy_eV": relax["total_energy_eV"],
        "f_max_eV_A": relax["f_max_eV_A"],
        "num_atoms": relax["num_atoms"],
        "properties": props,
        "values": values,
        "reward": None,
        "is_valid": True,
        "total_violation": 0.0,
    }
    # Flatten objective-name values onto record top-level (for filter/reward)
    for k, v in values.items():
        record[k] = v

    # Persist record file
    safe_formula = formula.replace("/", "_").replace(" ", "_")
    record_path = run_dir / "records" / f"{safe_formula}__iter{iter_idx}.json"
    record["record_path"] = str(record_path.relative_to(run_dir))
    _save_json(record_path, record)
    return record, fails


def _pipeline_batch(
    *, formulas: list[str], iter_idx: int, runplan: dict, run_dir: Path,
) -> tuple[list[dict], dict]:
    """Run _pipeline_single over a batch. Aggregate failures."""
    records: list[dict] = []
    failures: dict = {"diffcsp": [], "mattersim_unconverged": [], "alignn_null": [], "filtered_out": []}
    for f in formulas:
        rec, single_fails = _pipeline_single(
            formula=f, iter_idx=iter_idx, runplan=runplan, run_dir=run_dir,
        )
        for k, v in single_fails.items():
            failures.setdefault(k, []).extend(v)
        if rec is not None:
            records.append(rec)
    return records, failures


def _filter_reward_select(
    *, records: list[dict], existing_pool: list[dict],
    runplan: dict, top_k: int, failures: dict,
) -> list[dict]:
    """Apply constraints + hard_filter, normalize, reward, select top-K."""
    all_candidates = existing_pool + records
    kept = apply_hard_filters(all_candidates, runplan.get("hard_filter", []), failures)
    # Relaxation rule: if <2 survive and we had records, keep all records
    if len(kept) < 2 and records:
        kept = list(records)

    # Only consider records with all objective values present
    obj_names = [o["name"] for o in runplan["objectives"]]
    scored = [c for c in kept if all(c.get(n) is not None for n in obj_names)]
    if not scored:
        return existing_pool[:top_k]

    normalize_and_reward(scored, runplan["objectives"])
    return select_topk(scored, top_k)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ============================================================================
# Snapshot writer
# ============================================================================

def _write_snapshot(
    *,
    run_dir: Path,
    iteration: int,
    max_iters: int,
    pool: list[dict],
    objectives: list[dict],
    hard_filter: list[dict],
    constraints: dict,
    hints: dict,
    config: dict,
    mattergen_eval: dict,
    failures: dict,
) -> None:
    """Write snapshot_iter{N}.json + snapshot_latest.json with current state."""
    valid = [c for c in pool if c.get("is_valid", False)]
    best = pool[0] if pool else {}
    best_values = {o["name"]: best.get(o["name"]) for o in objectives} if best else {}

    snap = {
        "iteration": iteration,
        "max_iters": max_iters,
        "pool_size": len(pool),
        "n_valid": len(valid),
        "best_reward": best.get("reward"),
        "best_formula": best.get("formula"),
        "best_values": best_values,
        "objectives": objectives,
        "hard_filter": hard_filter,
        "constraints": constraints,
        "crossover_hints": hints,
        "top_pool": [
            {
                "formula": c.get("formula"),
                "reward": c.get("reward"),
                "values": {o["name"]: c.get(o["name"]) for o in objectives},
                "is_valid": c.get("is_valid", False),
                "total_violation": c.get("total_violation", 0.0),
                "record_path": c.get("record_path"),
                "source": c.get("source", "unknown"),
            }
            for c in pool
        ],
        "mattergen_eval": mattergen_eval,
        "failures": failures,
        "config_snapshot": {
            "batch_size": config.get("batch_size"),
            "top_k": config.get("top_k"),
        },
        "early_stop": False,
    }
    _save_json(run_dir / f"snapshot_iter{iteration}.json", snap)
    _save_json(run_dir / "snapshot_latest.json", snap)


# ============================================================================
# Subcommand stubs (implemented in subsequent tasks)
# ============================================================================

def cmd_init(args):
    rd = Path(args.run_dir)
    rd.mkdir(parents=True, exist_ok=True)

    # 1. Resolve RunPlan (from --runplan file or inline args)
    if args.runplan:
        runplan = _load_json(Path(args.runplan))
    else:
        objectives_raw = json.loads(args.objectives)
        objectives = [
            {"name": k, "direction": v if isinstance(v, str) else v.get("direction", "max"),
             "weight": 1.0, "alignn_deps": [k], "scorer": None, "report_threshold": None}
            for k, v in objectives_raw.items()
        ]
        runplan = {
            "goal": args.goal,
            "objectives": objectives,
            "hard_filter": json.loads(args.hard_filter),
            "alignn_models": json.loads(args.alignn_models) or [o["name"] for o in objectives],
            "crossover_hints": json.loads(args.crossover_hints),
            "init_elements": [e.strip() for e in args.init_elements.split(",") if e.strip()],
            "fallback_formulas": [f.strip() for f in args.fallback_formulas.split(",") if f.strip()],
            "config": {
                "init_n": args.init_n, "batch_size": args.batch_size,
                "max_iters": args.max_iters, "top_k": args.top_k,
                "run_mattergen_eval": args.run_mattergen_eval, "seed": args.seed,
            },
        }

    # 2. Probe MCPs (sys.exit(3) on failure)
    sys.path.insert(0, str(Path(__file__).parent))
    from probe_mcps import main as probe_main  # reuse existing script
    try:
        probe_main()
    except SystemExit as exc:
        if exc.code and exc.code != 0:
            print(f"(init) MCP probe failed (code={exc.code})", file=sys.stderr)
            sys.exit(3)

    # 3. Phase 1: initial pool — prefer upstream --seed-formulas if provided,
    #    else call initial_formulas.query_mp (with fallback list as backup).
    seed_formulas_raw = args.seed_formulas or ""
    seed_formulas = [f.strip() for f in seed_formulas_raw.split(",") if f.strip()]

    if seed_formulas:
        print(f"(init) using {len(seed_formulas)} upstream seed formulas "
              f"(skipping MP query)", file=sys.stderr)
        init_pool_raw = [{"formula": f, "mp_id": None} for f in seed_formulas]
    else:
        sys.path.insert(0, str(Path(__file__).parent))
        import initial_formulas
        try:
            elements = set(runplan.get("init_elements") or [])
            pool = initial_formulas.query_mp(
                n=runplan["config"]["init_n"],
                seed=runplan["config"].get("seed", 0),
                with_structures=False,
                elements=elements or None,
            )
            if not pool:
                print("(init) MP query returned nothing — using fallback list", file=sys.stderr)
                pool = initial_formulas.fallback(
                    n=runplan["config"]["init_n"],
                    seed=runplan["config"].get("seed", 0),
                    formulas=runplan.get("fallback_formulas") or None,
                )
            init_pool_raw = [{"formula": r["formula"], "mp_id": r.get("material_id")}
                             for r in pool]
        except Exception as exc:
            print(f"(init) initial_formulas failed: {exc!r}", file=sys.stderr)
            init_pool_raw = [{"formula": f, "mp_id": None}
                             for f in runplan.get("fallback_formulas", [])]

    formulas = [r["formula"] for r in init_pool_raw if r.get("formula")]
    if not formulas:
        print("(init) no initial formulas — aborting", file=sys.stderr)
        sys.exit(2)

    # 4. Write state files
    _save_json(rd / "runplan.json", runplan)
    config = {
        "iter": 0,
        "batch_size": runplan["config"]["batch_size"],
        "max_iters": runplan["config"]["max_iters"],
        "top_k": runplan["config"]["top_k"],
        "run_mattergen_eval": runplan["config"]["run_mattergen_eval"],
        "seed": runplan["config"]["seed"],
    }
    _save_json(rd / "config.json", config)
    _save_json(rd / "constraints.json", {})  # start empty
    _save_json(rd / "crossover_hints.json", runplan.get("crossover_hints", {}))
    _save_json(rd / "mattergen_eval.json", {"status": "skipped"})

    # 5. Run Phase 2b-2h on initial pool to get baseline rewards
    records, failures = _pipeline_batch(
        formulas=formulas, iter_idx=0, runplan=runplan, run_dir=rd,
    )
    pool = _filter_reward_select(
        records=records, existing_pool=[], runplan=runplan, top_k=config["top_k"],
        failures=failures,
    )
    _save_json(rd / "population.json", {"pool": pool})

    # 6. Write snapshot_iter0
    _write_snapshot(
        run_dir=rd, iteration=0, max_iters=config["max_iters"], pool=pool,
        objectives=runplan["objectives"], hard_filter=runplan.get("hard_filter", []),
        constraints=_read_constraints(rd), hints=_read_hints(rd), config=config,
        mattergen_eval=_load_json(rd / "mattergen_eval.json"),
        failures=failures,
    )
    print(f"(init) snapshot_iter0 written: {len(pool)} candidates, best={pool[0]['formula'] if pool else 'none'}")

def cmd_step(args):
    rd = _require_run_dir(args)
    config = _read_config(rd)
    runplan = _read_runplan(rd)
    iter_idx = int(config.get("iter", 0)) + 1

    # Parse --new-formulas
    formulas = [f.strip() for f in args.new_formulas.split(",") if f.strip()]
    if not formulas:
        print("(step) no formulas provided", file=sys.stderr)
        sys.exit(4)
    if len(formulas) != config.get("batch_size", len(formulas)):
        # not fatal — just log
        print(f"(step) warning: got {len(formulas)} formulas, batch_size={config.get('batch_size')}")

    # Drain prior mattergen-eval job if pending
    me_state = _load_json(rd / "mattergen_eval.json")
    if me_state.get("job_id") and me_state.get("status") == "pending":
        import mcp_client
        drained = mcp_client.drain_mattergen_eval(me_state["job_id"])
        if drained:
            me_state.update(drained)
            _save_json(rd / "mattergen_eval.json", me_state)

    # Run pipeline
    records, failures = _pipeline_batch(
        formulas=formulas, iter_idx=iter_idx, runplan=runplan, run_dir=rd,
    )

    # Filter + reward + select
    existing_pool = _read_population(rd)
    pool = _filter_reward_select(
        records=records, existing_pool=existing_pool, runplan=runplan,
        top_k=config["top_k"], failures=failures,
    )
    _save_json(rd / "population.json", {"pool": pool})

    # Submit mattergen-eval (async, don't block)
    me_new = {"status": "skipped"}
    if config.get("run_mattergen_eval") and not args.no_mattergen_eval and records:
        import mcp_client
        structures = [{"cif_content": r["relaxed_cif"], "name": r["formula"]} for r in records]
        energies = [r["total_energy_eV"] for r in records]
        job_id = mcp_client.submit_mattergen_eval(structures, energies)
        if job_id:
            me_new = {"job_id": job_id, "status": "pending",
                      "submitted_at": _now_iso()}

    # Write snapshot
    _write_snapshot(
        run_dir=rd, iteration=iter_idx, max_iters=config["max_iters"], pool=pool,
        objectives=runplan["objectives"], hard_filter=runplan.get("hard_filter", []),
        constraints=_read_constraints(rd), hints=_read_hints(rd), config=config,
        mattergen_eval=me_new, failures=failures,
    )

    # Bump iter in config
    config["iter"] = iter_idx
    _save_json(rd / "config.json", config)
    _save_json(rd / "mattergen_eval.json", me_new)

    print(f"(step) iter{iter_idx} done: pool_size={len(pool)}, "
          f"best={pool[0]['formula'] if pool else 'none'}")

def _parse_crossover_hint(spec: str) -> tuple[str, str, str]:
    """Parse 'key+val' (append) or 'key:val' (replace). Returns (key, op, val)."""
    if "+" in spec:
        key, val = spec.split("+", 1)
        return key.strip(), "+", val.strip()
    if ":" in spec:
        key, val = spec.split(":", 1)
        return key.strip(), ":", val.strip()
    raise ValueError(f"bad --crossover-hint format: {spec!r} (expected key+val or key:val)")


def cmd_steer(args):
    rd = Path(args.run_dir)
    if not (rd / "config.json").exists():
        print("(steer) no config.json — run init first", file=sys.stderr)
        sys.exit(2)
    constraints = _read_constraints(rd)
    hints = _read_hints(rd)
    config = _read_config(rd)
    mutated = False

    # --ban Pb,Cd
    if args.ban:
        rules = constraints.setdefault("element_set", {})
        banned = list(rules.get("banned", []))
        for e in args.ban.split(","):
            e = e.strip()
            if e and e not in banned:
                banned.append(e)
        rules["banned"] = banned
        rules["weight"] = max(float(rules.get("weight", 10)), 10.0)
        mutated = True

    # --allow-only
    if args.allow_only:
        rules = constraints.setdefault("element_set", {})
        rules["allowed_only"] = [e.strip() for e in args.allow_only.split(",") if e.strip()]
        rules["weight"] = max(float(rules.get("weight", 10)), 10.0)
        mutated = True

    # --clear-ban
    if args.clear_ban:
        constraints.pop("element_set", None)
        mutated = True

    # --constraint KEY:MIN:MAX:WEIGHT
    for c in args.constraint or []:
        parts = c.split(":")
        key = parts[0]
        rule = {}
        if len(parts) > 1 and parts[1]:
            rule["min"] = float(parts[1])
        if len(parts) > 2 and parts[2]:
            rule["max"] = float(parts[2])
        rule["weight"] = float(parts[3]) if len(parts) > 3 and parts[3] else 10.0
        constraints[key] = rule
        mutated = True

    # --weight KEY:WEIGHT
    for w in args.weight or []:
        key, _, val = w.partition(":")
        if key in constraints and val:
            constraints[key]["weight"] = float(val)
            mutated = True

    # --crossover-hint
    for hint_spec in args.crossover_hint or []:
        key, op, val = _parse_crossover_hint(hint_spec)
        if op == "+":
            # append: parse val as comma-list
            new_items = [v.strip() for v in val.split(",") if v.strip()]
            existing = hints.get(key) or []
            if key == "swap_groups":
                # swap_groups is list of lists
                hints[key] = list(existing) + [new_items]
            else:
                hints[key] = list(existing) + new_items
        else:  # op == ":"
            items = [v.strip() for v in val.split(",") if v.strip()]
            if key == "swap_groups":
                hints[key] = [items]  # replace with single group
            else:
                hints[key] = items
        mutated = True

    # --batch-size / --top-k (config.json)
    if args.batch_size is not None:
        config["batch_size"] = int(args.batch_size)
        mutated = True
    if args.top_k is not None:
        config["top_k"] = int(args.top_k)
        mutated = True

    if mutated:
        _save_json(rd / "constraints.json", constraints)
        _save_json(rd / "crossover_hints.json", hints)
        _save_json(rd / "config.json", config)
    print("(steer) constraints:", json.dumps(constraints, ensure_ascii=False))
    print("(steer) hints:", json.dumps(hints, ensure_ascii=False))

def cmd_finalize(args):
    rd = _require_run_dir(args)
    pool = _read_population(rd)
    runplan = _read_runplan(rd)
    config = _read_config(rd)

    # Drain any pending mattergen-eval job
    me_state = _load_json(rd / "mattergen_eval.json")
    if me_state.get("job_id") and me_state.get("status") == "pending":
        import mcp_client
        drained = mcp_client.drain_mattergen_eval(me_state["job_id"])
        if drained:
            me_state.update(drained)
            _save_json(rd / "mattergen_eval.json", me_state)

    # Optionally resubmit union batch
    final_metrics = me_state.get("metrics")
    if args.evaluate:
        import mcp_client
        # Collect all records from all iterations
        all_records = []
        for rec_path in (rd / "records").glob("*.json"):
            all_records.append(_load_json(rec_path))
        if all_records:
            structures = [{"cif_content": r["relaxed_cif"], "name": r["formula"]}
                          for r in all_records if r.get("relaxed_cif")]
            energies = [r.get("total_energy_eV", 0) for r in all_records if r.get("relaxed_cif")]
            job_id = mcp_client.submit_mattergen_eval(structures, energies)
            if job_id:
                # Poll until done (with timeout)
                import time
                for _ in range(60):  # up to ~10 minutes
                    drained = mcp_client.drain_mattergen_eval(job_id)
                    if drained and drained.get("status") in ("done", "error"):
                        final_metrics = drained.get("metrics")
                        break
                    time.sleep(10)

    # Export leaderboard.csv
    obj_names = [o["name"] for o in runplan["objectives"]]
    import csv
    with (rd / "leaderboard.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["formula", "reward", "ehull"] + obj_names + ["source_iter"])
        for c in pool[:args.top_n]:
            rec = _load_json(rd / c["record_path"]) if c.get("record_path") else {}
            w.writerow([
                c.get("formula"), c.get("reward"),
                c.get("ehull") or rec.get("values", {}).get("ehull"),
            ] + [c.get(n) for n in obj_names] + [rec.get("iter")])

    # Export pareto/<formula>__<reward>.cif
    pareto_dir = rd / "pareto"
    pareto_dir.mkdir(exist_ok=True)
    for c in pool[:args.top_n]:
        if not c.get("record_path"):
            continue
        rec = _load_json(rd / c["record_path"])
        if not rec.get("relaxed_cif"):
            continue
        safe = (c.get("formula") or "unknown").replace("/", "_").replace(" ", "_")
        reward_str = f"{c.get('reward', 0):.3f}"
        (pareto_dir / f"{safe}__{reward_str}.cif").write_text(rec["relaxed_cif"], encoding="utf-8")

    # Export summary.json
    summary = {
        "runplan_recap": runplan,
        "final_best": pool[0] if pool else None,
        "top_n_table_count": min(args.top_n, len(pool)),
        "mattergen_eval_metrics": final_metrics,
        "total_iterations": config.get("iter", 0),
    }
    _save_json(rd / "summary.json", summary)
    print(f"(finalize) exported leaderboard.csv + {len(pool[:args.top_n])} pareto CIFs + summary.json")

def cmd_status(args):
    rd = _require_run_dir(args)
    snap = _load_json(rd / "snapshot_latest.json")
    if not snap:
        print(f"(no snapshot yet in {rd})")
        return
    print(json.dumps({
        "iteration": snap.get("iteration"),
        "pool_size": snap.get("pool_size"),
        "best_reward": snap.get("best_reward"),
        "best_formula": snap.get("best_formula"),
        "n_valid": snap.get("n_valid"),
    }, ensure_ascii=False, indent=2))


# ============================================================================
# CLI entry
# ============================================================================

def main():
    p = argparse.ArgumentParser(description="formula-crossover-agent driver")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Build run_dir + Phase 1 + snapshot_iter0")
    p_init.add_argument("--run-dir", required=True)
    p_init.add_argument("--runplan")
    p_init.add_argument("--goal", default="")
    p_init.add_argument("--objectives", default="{}")
    p_init.add_argument("--hard-filter", default="[]")
    p_init.add_argument("--alignn-models", default="[]")
    p_init.add_argument("--crossover-hints", default="{}")
    p_init.add_argument("--init-elements", default="")
    p_init.add_argument("--fallback-formulas", default="")
    p_init.add_argument("--seed-formulas", default="",
                        help="Comma-separated pretty formulas (e.g. 'AgPO3,Fe2O3'). "
                             "When non-empty, skips initial_formulas.query_mp entirely "
                             "and uses these as the init pool — enables upstream chaining "
                             "(e.g. from materials-mp-constraint-search output).")
    p_init.add_argument("--init-n", type=int, default=10)
    p_init.add_argument("--batch-size", type=int, default=3)
    p_init.add_argument("--max-iters", type=int, default=3)
    p_init.add_argument("--top-k", type=int, default=5)
    p_init.add_argument("--run-mattergen-eval", action="store_true")
    p_init.add_argument("--seed", type=int, default=0)
    p_init.set_defaults(func=cmd_init)

    p_step = sub.add_parser("step", help="Run Phase 2b-2h for one iteration")
    p_step.add_argument("--run-dir", required=True)
    p_step.add_argument("--new-formulas", required=True,
                        help="Comma-separated formula strings from the crossover LLM")
    p_step.add_argument("--no-mattergen-eval", action="store_true")
    p_step.set_defaults(func=cmd_step)

    p_steer = sub.add_parser("steer", help="Mutate constraints/hints/config without stepping")
    p_steer.add_argument("--run-dir", required=True)
    p_steer.add_argument("--ban", default=None)
    p_steer.add_argument("--allow-only", default=None)
    p_steer.add_argument("--clear-ban", action="store_true")
    p_steer.add_argument("--constraint", action="append", default=[])
    p_steer.add_argument("--weight", action="append", default=[])
    p_steer.add_argument("--crossover-hint", action="append", default=[],
                         help="FORMAT: key+value (append) or key:value (replace). "
                              "e.g. swap_groups+Fe,Co,Ni  or  prototypes:Heusler,YCo5")
    p_steer.add_argument("--batch-size", type=int, default=None)
    p_steer.add_argument("--top-k", type=int, default=None)
    p_steer.set_defaults(func=cmd_steer)

    p_status = sub.add_parser("status", help="Print snapshot_latest.json summary")
    p_status.add_argument("--run-dir", required=True)
    p_status.set_defaults(func=cmd_status)

    p_fin = sub.add_parser("finalize", help="Drain eval + export leaderboard/pareto/summary")
    p_fin.add_argument("--run-dir", required=True)
    p_fin.add_argument("--top-n", type=int, default=10)
    p_fin.add_argument("--evaluate", action="store_true")
    p_fin.set_defaults(func=cmd_finalize)

    args = p.parse_args()
    try:
        args.func(args)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"(formula_driver) uncaught: {exc!r}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
