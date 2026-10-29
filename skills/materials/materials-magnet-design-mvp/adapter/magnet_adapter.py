"""Durable Python adapter for the SAGA-Opt Magnet-Materials MVP skill.

This is the worker-side counterpart to SKILL.md. The workbench (sync) path
lets a Claude agent orchestrate the loop by reading SKILL.md; the durable
(async) path runs this adapter end-to-end via the python_adapter_runner.

Pipeline (per SKILL.md):
    Phase 1: init formula pool (MP query or fallback list)
    Phase 2: max_iters × {
        2a. drain prior mattergen-evaluate job → batch_SUN_prev
        2b. LLM crossover → batch_size new formulas
        2c. DiffCSP (formula → CIF)         parallel fan-out
        2d. MatterSim relax                  parallel fan-out
        2e. ALIGNN (magmom, ehull)           parallel fan-out
        2f. local HHI + mag_density          (in-process)
        2g. mattergen-evaluate async submit  (background job_id)
        2h. hard filter + normalize + reward + select top-K
    }
    Phase 3: drain any pending job, build final top-K table + summary

FAILURE SEMANTICS — see plan §B.5. The durable_skill_runtime fail-closes
on any raised exception (state=failed, NO artifact persisted). Therefore
this adapter must NEVER raise. Every MCP timeout / parse error / LLM 5xx
is captured into `partial_failure` and the run continues with whatever
survived. The return dict is always populated.

Per-step evidence (plan §B.4): each MCP call appends a record to the
current iteration's `mcp_calls` list. The orchestrator table constraint
(openair_provider_http_attempts UNIQUE capability_call_id) blocks per-MCP
SQL rows, so evidence lives inside the artifact JSON.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# Bootstrap import path for the shared MCP client. The MCPClient lives at
# services/api/app/modules/mcp_client.py (moved there in this PR); make sure
# that directory is importable as `import mcp_client` from any cwd. The
# runner subprocess already adds it to sys.path, but this also covers tests
# and ad-hoc invocation.
_REPO_ROOT = Path(__file__).resolve()
while _REPO_ROOT.parent != _REPO_ROOT and not (_REPO_ROOT / "services").is_dir():
    _REPO_ROOT = _REPO_ROOT.parent
_MODULES_DIR = _REPO_ROOT / "services" / "api" / "app" / "modules"
if _MODULES_DIR.is_dir() and str(_MODULES_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULES_DIR))

# ----------------------------------------------------------------------------
# Config defaults — overridable via task_instruction JSON in input_payload.
# ----------------------------------------------------------------------------

DEFAULT_CONFIG: dict[str, Any] = {
    "init_n": 10,
    "batch_size": 3,
    "max_iters": 3,
    "top_k": 5,
    "filter_ehull": True,
    "ehull_lt": 0.1,
    "mag_density_gt": 0.2,
    "hhi_lt": 1500,
    "run_mattergen_eval": True,
    "w_sun": 0.5,
    "seed": 0,
    # Force the bundled fallback list (skips MP network query). Useful in
    # tests and when MP_API_KEY is known to be unavailable. The Phase 1
    # subprocess inherits a clean env (no MP_API_KEY) when this is True.
    "init_skip_mp": False,
}

MCP_BASE_URL = os.environ.get("MATERIALS_MCP_BASE_URL", "").rstrip("/")


def _require_mcp_base() -> str:
    """Fail-fast at first MCP call. Source files MUST NOT hardcode the endpoint IP."""
    if not MCP_BASE_URL:
        raise RuntimeError(
            "MATERIALS_MCP_BASE_URL is not set; the materials MCP gateway URL "
            "must be provided via environment (see .env.example)."
        )
    return MCP_BASE_URL

LLM_PROVIDER_BASE_URL = os.environ.get("LLM_PROVIDER_BASE_URL", "").rstrip("/")
LLM_PROVIDER_API_KEY = os.environ.get("LLM_PROVIDER_API_KEY", "")
LLM_MODEL = os.environ.get("MAGNET_MVP_LLM_MODEL", "gpt-4o-mini")

MAX_MCP_WORKERS = 5
DIFFCSP_TIMEOUT = 120.0
MATTERSIM_TIMEOUT = 180.0
ALIGNN_TIMEOUT = 90.0
MATTERGEN_EVAL_TIMEOUT = 600.0


# ----------------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------------

def run_magnet_mvp(input_payload: dict[str, Any]) -> dict[str, Any]:
    """Run the full SAGA-Opt MVP loop. Never raises.

    Args (per durable_skill_runtime contract):
        input_payload: {"goal": str, "task_instruction": str|dict|None}

    Returns:
        {
          "state": "succeeded" | "partial_failure",
          "top_k_table": str (markdown),
          "iterations": list[dict],
          "run_summary": {...},
          "partial_failure": list[str] | None,
        }
    """
    partial_failures: list[str] = []
    goal, overrides = _parse_input(input_payload, partial_failures)
    config = {**DEFAULT_CONFIG, **overrides}
    random.seed(config["seed"])

    iterations: list[dict[str, Any]] = []
    pool: list[dict[str, Any]] = []  # current top-K parents, schema = candidate record
    pending_mattergen_job: dict[str, Any] | None = None  # carry across iterations

    try:
        # --- Phase 1: init pool ---
        init_records, init_provenance = _phase_1_init(config, partial_failures)
        iterations.append({
            "phase": 1,
            "init": init_provenance,
        })
        # Seed pool: skip DiffCSP/MatterSim for init formulas — we only have
        # composition. Reward them as 0 / neutral until the first iteration
        # produces real candidates with measured mag_density / hhi.
        for r in init_records:
            pool.append({
                "formula": r["formula"],
                "source": "init",
                "mp_id": r.get("mp_id"),
                "cif": None,
                "relaxed_cif": None,
                "magmom": None,
                "volume": None,
                "mag_density": None,
                "hhi": _safe_hhi(r["formula"], partial_failures),
                "ehull": None,
                "reward": None,
            })

        # --- Phase 2: the loop ---
        for iter_idx in range(1, int(config["max_iters"]) + 1):
            iter_log: dict[str, Any] = {"phase": 2, "iter": iter_idx, "mcp_calls": []}

            # 2a. drain prior mattergen-evaluate job → batch_SUN_prev
            batch_sun_prev = 0.0
            if pending_mattergen_job is not None:
                batch_sun_prev, drained = _drain_mattergen_eval(
                    pending_mattergen_job, partial_failures
                )
                iter_log["mattergen_eval_drained"] = drained
                pending_mattergen_job = None
            iter_log["batch_sun_prev"] = batch_sun_prev

            # 2b. LLM crossover
            parents_brief = [
                {"formula": p["formula"], "mag_density": p["mag_density"],
                 "hhi": p["hhi"], "reward": p["reward"]}
                for p in pool
            ]
            new_formulas = _llm_crossover(
                goal=goal,
                parents=parents_brief,
                n=int(config["batch_size"]),
                iter_idx=iter_idx,
                partial_failures=partial_failures,
            )
            iter_log["crossover_formulas"] = new_formulas
            if not new_formulas:
                iter_log["crossover_error"] = "no formulas returned"
                iterations.append(iter_log)
                continue

            # 2c + 2d + 2e + 2f, parallelized per formula
            candidates = _run_pipeline_for_batch(
                formulas=new_formulas,
                iter_log=iter_log,
                partial_failures=partial_failures,
            )
            iter_log["candidates"] = [{k: v for k, v in c.items() if k != "relaxed_cif" and k != "cif"}
                                       for c in candidates]

            # 2g. mattergen-evaluate async submit (carry job_id to next iter)
            if config["run_mattergen_eval"] and candidates:
                pending_mattergen_job = _submit_mattergen_eval(
                    candidates, iter_log, partial_failures
                )

            # 2h. hard filter + normalize + reward + select top-K
            pool = _select_topk(
                pool=pool,
                new_candidates=candidates,
                config=config,
                batch_sun_prev=batch_sun_prev,
                iter_log=iter_log,
            )

            iterations.append(iter_log)

        # --- Phase 3: drain final pending job, build final report ---
        if pending_mattergen_job is not None:
            _, drained = _drain_mattergen_eval(pending_mattergen_job, partial_failures)
            iterations.append({"phase": 3, "mattergen_eval_drained_final": drained})

    except Exception as exc:  # noqa: BLE001
        # Last-resort net. Should be unreachable; subsystems swallow their own.
        partial_failures.append(f"FATAL uncaught: {exc!r}")

    top_k_table, run_summary = _build_final_report(pool, iterations, config)
    state = "succeeded" if not partial_failures else "partial_failure"
    return {
        "state": state,
        "top_k_table": top_k_table,
        "iterations": iterations,
        "run_summary": run_summary,
        "partial_failure": partial_failures or None,
    }


# ----------------------------------------------------------------------------
# Input parsing
# ----------------------------------------------------------------------------

def _parse_input(
    input_payload: dict[str, Any],
    partial_failures: list[str],
) -> tuple[str, dict[str, Any]]:
    """Extract goal + config overrides per durable_skill_runtime contract."""
    goal = str(input_payload.get("goal") or "").strip()
    if not goal:
        partial_failures.append("input.goal empty; proceeding with default goal")
        goal = "find high-mag-density low-HHI magnet candidates"

    raw_ti = input_payload.get("task_instruction")
    overrides: dict[str, Any] = {}
    if isinstance(raw_ti, str):
        raw_ti = raw_ti.strip()
        if raw_ti:
            try:
                parsed = json.loads(raw_ti)
                if isinstance(parsed, dict):
                    overrides = parsed
                else:
                    partial_failures.append(
                        f"task_instruction JSON not object ({type(parsed).__name__}); ignored"
                    )
            except json.JSONDecodeError as exc:
                partial_failures.append(
                    f"task_instruction not valid JSON ({exc.msg}); ignored"
                )
    elif isinstance(raw_ti, dict):
        overrides = raw_ti
    elif raw_ti is not None:
        partial_failures.append(
            f"task_instruction unexpected type {type(raw_ti).__name__}; ignored"
        )

    # Filter overrides to known config keys
    clean = {k: v for k, v in overrides.items() if k in DEFAULT_CONFIG}
    unknown = sorted(set(overrides) - set(DEFAULT_CONFIG))
    if unknown:
        partial_failures.append(f"task_instruction ignored unknown keys: {unknown}")
    return goal, clean


# ----------------------------------------------------------------------------
# Phase 1 — init pool
# ----------------------------------------------------------------------------

def _phase_1_init(
    config: dict[str, Any],
    partial_failures: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Subprocess call to scripts/initial_formulas.py; fallback in-process.

    When ``config['init_skip_mp']`` is True, the subprocess is spawned with
    a scrubbed environment (no ``MP_API_KEY``) and the ``--fallback`` flag,
    guaranteeing deterministic offline behavior. Used by tests.
    """
    n = int(config["init_n"])
    seed = int(config["seed"])
    skip_mp = bool(config.get("init_skip_mp"))
    script = Path(__file__).resolve().parents[1] / "scripts" / "initial_formulas.py"
    if not script.exists():
        partial_failures.append(f"initial_formulas.py missing at {script}; using in-process fallback")
        return _in_process_fallback(n, seed), {"source": "in_process_fallback", "n": n}

    # Build subprocess env. Scrub MP_API_KEY when skip_mp is set so the
    # script's _load_dotenv() cannot reintroduce it from a repo-local .env.
    sub_env = dict(os.environ)
    if skip_mp:
        sub_env.pop("MP_API_KEY", None)
        sub_env.pop("MP_API_ENDPOINT_KEY", None)
        # Disable .env discovery from inside the subprocess.
        sub_env["OPENAIR_DOTENV"] = "/dev/null"

    cmd = [sys.executable, str(script), "--n", str(n), "--seed", str(seed)]
    if skip_mp:
        cmd.append("--fallback")
    else:
        cmd.append("--with-structures")

    try:
        import subprocess
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            env=sub_env,
        )
        if proc.returncode != 0:
            partial_failures.append(
                f"initial_formulas.py exit={proc.returncode}; stderr={proc.stderr[:300]}"
            )
            return _in_process_fallback(n, seed), {"source": "in_process_fallback", "n": n}
        payload = json.loads(proc.stdout)
        formulas = payload.get("formulas") or []
        if not formulas:
            partial_failures.append("initial_formulas.py returned empty list")
            return _in_process_fallback(n, seed), {"source": "in_process_fallback", "n": n}
        return formulas, {
            "source": payload.get("source"),
            "n": payload.get("n"),
            "fallback_used": payload.get("source") == "fallback",
        }
    except Exception as exc:  # noqa: BLE001
        partial_failures.append(f"initial_formulas.py invocation failed: {exc!r}")
        return _in_process_fallback(n, seed), {"source": "in_process_fallback", "n": n}


def _in_process_fallback(n: int, seed: int) -> list[dict[str, Any]]:
    """Inline copy of FALLBACK list from initial_formulas.py."""
    pool = [
        "Fe3O4", "Fe2O3", "FeO", "CoFe2O4", "NiFe2O4",
        "MnFe2O4", "BaFe12O19", "SrFe12O19", "CrO2", "Cr2O3",
        "Co3O4", "NiO", "MnO", "Mn3O4", "Fe3Sn2",
        "Co3Sn2", "Fe2B", "FePt", "CoPt", "MnAl",
    ]
    rng = random.Random(seed)
    rng.shuffle(pool)
    return [{"formula": f, "mp_id": None, "fallback": True} for f in pool[:n]]


# ----------------------------------------------------------------------------
# Phase 2b — LLM crossover (OpenAI-compatible)
# ----------------------------------------------------------------------------

CROSSOVER_SYSTEM = """\
You are a materials-discovery crossover engine for the SAGA-Opt magnet MVP.

You receive a JSON list of parent candidates (formula + mag_density + hhi +
reward, any field may be null on iteration 1). Propose {n} NEW candidate
formulas that explore the chemistry of high-mag-density low-supply-risk
magnets.

Apply real magnetic-materials domain knowledge:
- substitution: swap one element for a same-group/row peer (Co<->Fe<->Ni,
  Cr<->Mn, Te<->Se<->S, B<->C<->N)
- stoichiometry tweak within known families (Nd2Fe14B -> Nd2Fe13CoB,
  SmCo5 -> Sm2Co7)
- structure-type transfer (Heusler, perovskite, ThMn12, YCo5)
- never propose a formula already in the parents list
- no noble gases, no actinides beyond U, no monatomic formulas

Return ONLY a JSON object of the form:
{"formulas": ["Nd2Fe13CoB", "Sm2Co7", "Fe3Sn2"]}
No commentary, no markdown fences.\
"""


def _llm_crossover(
    *,
    goal: str,
    parents: list[dict[str, Any]],
    n: int,
    iter_idx: int,
    partial_failures: list[str],
) -> list[str]:
    if not LLM_PROVIDER_BASE_URL:
        partial_failures.append(
            f"iter {iter_idx}: LLM_PROVIDER_BASE_URL unset — skipping crossover"
        )
        return []
    user_msg = json.dumps(
        {"goal": goal, "iteration": iter_idx, "n": n, "parents": parents},
        ensure_ascii=False,
    )
    body = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": CROSSOVER_SYSTEM.replace("{n}", str(n))},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.8,
        "max_tokens": 400,
    }
    try:
        resp = _http_post_json(
            f"{LLM_PROVIDER_BASE_URL}/v1/chat/completions",
            body=body,
            headers={"Authorization": f"Bearer {LLM_PROVIDER_API_KEY}"},
            timeout=60.0,
        )
    except Exception as exc:  # noqa: BLE001
        partial_failures.append(f"iter {iter_idx}: LLM crossover HTTP failed: {exc!r}")
        return []

    try:
        content = resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        partial_failures.append(f"iter {iter_idx}: LLM response missing choices[0].message.content")
        return []

    formulas = _extract_formulas(content)
    # de-dup against parents
    parent_set = {p["formula"] for p in parents}
    formulas = [f for f in formulas if f not in parent_set]
    return formulas[:n]


def _extract_formulas(text: str) -> list[str]:
    """Tolerant extraction: prefer JSON parse, fall back to regex."""
    text = text.strip()
    # strip code fences if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and "formulas" in parsed:
            return [str(f) for f in parsed["formulas"]]
        if isinstance(parsed, list):
            return [str(f) for f in parsed]
    except json.JSONDecodeError:
        pass
    # regex fallback: capture "Fe2O3", "Nd2Fe13CoB" etc.
    return re.findall(r'"([A-Z][A-Za-z0-9]*)"', text)


# ----------------------------------------------------------------------------
# Phase 2c-f — per-candidate pipeline (parallel fan-out)
# ----------------------------------------------------------------------------

def _run_pipeline_for_batch(
    *,
    formulas: list[str],
    iter_log: dict[str, Any],
    partial_failures: list[str],
) -> list[dict[str, Any]]:
    """For each formula: DiffCSP -> MatterSim -> ALIGNN -> HHI/mag_density.

    Parallelize via ThreadPoolExecutor. Each MCP call appends to
    iter_log["mcp_calls"] under a lock-free schema (list append is atomic
    in CPython, and we only mutate our own records).
    """
    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_MCP_WORKERS) as ex:
        futs = {
            ex.submit(_pipeline_single, formula, iter_log): formula
            for formula in formulas
        }
        for fut in concurrent.futures.as_completed(futs):
            formula = futs[fut]
            try:
                rec = fut.result()
            except Exception as exc:  # noqa: BLE001
                partial_failures.append(f"pipeline {formula}: {exc!r}")
                rec = {"formula": formula, "dropped": True, "drop_reason": f"exception: {exc!r}"}
            results.append(rec)
    return [r for r in results if not r.get("dropped")]


def _pipeline_single(formula: str, iter_log: dict[str, Any]) -> dict[str, Any]:
    """DiffCSP → MatterSim → ALIGNN → HHI + mag_density. Drop-fast on failure."""
    record: dict[str, Any] = {
        "formula": formula,
        "source": "crossover",
        "cif": None,
        "relaxed_cif": None,
        "magmom": None,
        "volume": None,
        "mag_density": None,
        "hhi": None,
        "ehull": None,
        "reward": None,
    }

    # 2c. DiffCSP
    cif = _call_diffcsp(formula, iter_log)
    if cif is None:
        record["dropped"] = True
        record["drop_reason"] = "diffcsp_no_cif"
        return record
    record["cif"] = _sha(cif)

    # 2d. MatterSim relax
    relaxed = _call_mattersim(cif, iter_log)
    if relaxed is None:
        record["dropped"] = True
        record["drop_reason"] = "mattersim_fail"
        return record
    record["relaxed_cif"] = _sha(relaxed["relaxed_cif"])
    record["total_energy_eV"] = relaxed["energy_per_atom_eV"] * relaxed["num_atoms"]
    record["num_atoms"] = relaxed["num_atoms"]
    record["energy_per_atom_eV"] = relaxed["energy_per_atom_eV"]
    if relaxed.get("f_max_eV_A") is not None and relaxed["f_max_eV_A"] > 1.0:
        record["dropped"] = True
        record["drop_reason"] = f"mattersim_unconverged f_max={relaxed['f_max_eV_A']:.2f}"
        return record

    # 2e. ALIGNN
    props = _call_alignn(relaxed["relaxed_cif"], iter_log)
    if props is None:
        record["dropped"] = True
        record["drop_reason"] = "alignn_fail"
        return record
    record["magmom"] = props.get("magmom")
    record["ehull"] = props.get("ehull")
    if record["magmom"] is None or record["ehull"] is None:
        record["dropped"] = True
        record["drop_reason"] = "alignn_null_property"
        return record

    # 2f. local HHI + mag_density
    record["hhi"] = _safe_hhi(formula, iter_log.setdefault("_partial", [])).get("hhi")
    md = _safe_mag_density(record["magmom"], relaxed["relaxed_cif"])
    if md is None:
        record["dropped"] = True
        record["drop_reason"] = "mag_density_fail"
        return record
    record["mag_density"] = md["mag_density"]
    record["volume"] = md["volume"]

    return record


# ----------------------------------------------------------------------------
# MCP wrappers — each one never raises, logs to iter_log["mcp_calls"]
# ----------------------------------------------------------------------------

def _record_call(
    iter_log: dict[str, Any],
    *,
    mcp_name: str,
    tool: str,
    args: dict[str, Any],
    result: Any,
    error: str | None,
    latency_ms: int,
) -> None:
    """Append a per-call evidence record to the iteration log."""
    call = {
        "mcp_name": mcp_name,
        "tool": tool,
        "request_sha256": _sha(json.dumps(args, sort_keys=True, default=str)),
        "transport_outcome": (
            "ok" if error is None
            else "timeout" if "timeout" in error.lower() or "timed out" in error.lower()
            else "http_error" if "HTTP" in error
            else "mcp_error"
        ),
        "latency_ms": latency_ms,
        "input": _truncate(args),
        "error": error,
    }
    if isinstance(result, (dict, list)):
        call["output_sha256"] = _sha(json.dumps(result, sort_keys=True, default=str))
        call["output_size"] = len(json.dumps(result, default=str))
    iter_log.setdefault("mcp_calls", []).append(call)


def _call_diffcsp(formula: str, iter_log: dict[str, Any]) -> str | None:
    from mcp_client import MCPClient, MCPError  # type: ignore
    args = {"formula": formula, "model_name": "mp_csp", "num_evals": 1}
    t0 = time.monotonic()
    try:
        client = MCPClient(f"{_require_mcp_base()}/diffcsp-generate/mcp", timeout=DIFFCSP_TIMEOUT, verbose=False)
        client.initialize()
        result = client.call_tool("generate_structures", args)
        latency = int((time.monotonic() - t0) * 1000)
        _record_call(iter_log, mcp_name="diffcsp-generate", tool="generate_structures",
                     args=args, result=result, error=None, latency_ms=latency)
        if not isinstance(result, dict):
            return None
        structures = result.get("structures") or []
        if not structures:
            return None
        cif = structures[0].get("cif")
        return cif if isinstance(cif, str) and cif.strip() else None
    except (MCPError, TimeoutError, OSError, urllib.error.URLError) as exc:
        latency = int((time.monotonic() - t0) * 1000)
        _record_call(iter_log, mcp_name="diffcsp-generate", tool="generate_structures",
                     args=args, result=None, error=str(exc), latency_ms=latency)
        return None
    except Exception as exc:  # noqa: BLE001
        latency = int((time.monotonic() - t0) * 1000)
        _record_call(iter_log, mcp_name="diffcsp-generate", tool="generate_structures",
                     args=args, result=None, error=f"unexpected: {exc!r}", latency_ms=latency)
        return None


def _call_mattersim(cif: str, iter_log: dict[str, Any]) -> dict[str, Any] | None:
    from mcp_client import MCPClient, MCPError  # type: ignore
    args = {"cif_content": cif, "max_steps": 50}
    t0 = time.monotonic()
    try:
        client = MCPClient(f"{_require_mcp_base()}/mattersim-relax/mcp", timeout=MATTERSIM_TIMEOUT, verbose=False)
        client.initialize()
        result = client.call_tool("relax_structure", args)
        latency = int((time.monotonic() - t0) * 1000)
        _record_call(iter_log, mcp_name="mattersim-relax", tool="relax_structure",
                     args={**args, "cif_content": f"<{len(cif)} chars>"},
                     result=_redact_relax(result), error=None, latency_ms=latency)
        if not isinstance(result, dict) or not result.get("relaxed_cif"):
            return None
        return {
            "relaxed_cif": result["relaxed_cif"],
            "energy_per_atom_eV": float(result.get("energy_per_atom_eV") or 0.0),
            "f_max_eV_A": result.get("f_max_eV_A"),
            "num_atoms": int(result.get("num_atoms") or 0),
        }
    except (MCPError, TimeoutError, OSError, urllib.error.URLError) as exc:
        latency = int((time.monotonic() - t0) * 1000)
        _record_call(iter_log, mcp_name="mattersim-relax", tool="relax_structure",
                     args={**args, "cif_content": f"<{len(cif)} chars>"},
                     result=None, error=str(exc), latency_ms=latency)
        return None
    except Exception as exc:  # noqa: BLE001
        latency = int((time.monotonic() - t0) * 1000)
        _record_call(iter_log, mcp_name="mattersim-relax", tool="relax_structure",
                     args={**args, "cif_content": f"<{len(cif)} chars>"},
                     result=None, error=f"unexpected: {exc!r}", latency_ms=latency)
        return None


def _redact_relax(result: Any) -> Any:
    """Drop CIF text from evidence (keep only scalar fields)."""
    if isinstance(result, dict):
        return {k: v for k, v in result.items() if k != "relaxed_cif"}
    return result


def _call_alignn(cif: str, iter_log: dict[str, Any]) -> dict[str, float | None] | None:
    from mcp_client import MCPClient, MCPError  # type: ignore
    args = {"cif_content": cif, "models": ["magmom", "ehull"]}
    t0 = time.monotonic()
    try:
        client = MCPClient(f"{_require_mcp_base()}/alignn/mcp", timeout=ALIGNN_TIMEOUT, verbose=False)
        client.initialize()
        result = client.call_tool("predict_alignn_properties", args)
        latency = int((time.monotonic() - t0) * 1000)
        _record_call(iter_log, mcp_name="alignn_prediction", tool="predict_alignn_properties",
                     args={**args, "cif_content": f"<{len(cif)} chars>"},
                     result=result, error=None, latency_ms=latency)
        if not isinstance(result, dict):
            return None
        props = result.get("properties") or {}
        out = {}
        for k in ("magmom", "ehull"):
            v = props.get(k)
            if isinstance(v, dict):
                out[k] = v.get("value")
            else:
                out[k] = None
        return out
    except (MCPError, TimeoutError, OSError, urllib.error.URLError) as exc:
        latency = int((time.monotonic() - t0) * 1000)
        _record_call(iter_log, mcp_name="alignn_prediction", tool="predict_alignn_properties",
                     args={**args, "cif_content": f"<{len(cif)} chars>"},
                     result=None, error=str(exc), latency_ms=latency)
        return None
    except Exception as exc:  # noqa: BLE001
        latency = int((time.monotonic() - t0) * 1000)
        _record_call(iter_log, mcp_name="alignn_prediction", tool="predict_alignn_properties",
                     args={**args, "cif_content": f"<{len(cif)} chars>"},
                     result=None, error=f"unexpected: {exc!r}", latency_ms=latency)
        return None


# ----------------------------------------------------------------------------
# Phase 2g — mattergen-evaluate (async submit only; polling happens next iter)
# ----------------------------------------------------------------------------

def _submit_mattergen_eval(
    candidates: list[dict[str, Any]],
    iter_log: dict[str, Any],
    partial_failures: list[str],
) -> dict[str, Any] | None:
    """Submit batch; return job descriptor (job_id + metadata) or None."""
    from mcp_client import MCPClient, MCPError  # type: ignore
    # Only candidates with relaxed CIF + total_energy_eV qualify.
    qualified = [c for c in candidates if c.get("relaxed_cif") and c.get("total_energy_eV") is not None]
    if not qualified:
        partial_failures.append("mattergen-eval: no qualified candidates (missing relaxed CIF or energy)")
        return None
    structures = [
        {"cif_content": f"<{len(c['relaxed_cif'])} chars>", "name": c["formula"]}
        for c in qualified
    ]
    energies = [c["total_energy_eV"] for c in qualified]
    args = {
        "structures": structures,
        "relax": False,
        "energies": energies,
        "energy_correction_scheme": "MP2020",
        "structure_matcher": "disordered",
    }
    t0 = time.monotonic()
    try:
        client = MCPClient(f"{_require_mcp_base()}/mattergen-evaluate/mcp", timeout=120.0, verbose=False)
        client.initialize()
        result = client.call_tool("evaluate_structures_async", args)
        latency = int((time.monotonic() - t0) * 1000)
        _record_call(iter_log, mcp_name="mattergen-evaluate",
                     tool="evaluate_structures_async", args=args,
                     result=result, error=None, latency_ms=latency)
        if not isinstance(result, dict) or not result.get("job_id"):
            partial_failures.append(f"mattergen-eval: submit missing job_id: {result!r}")
            return None
        return {
            "job_id": result["job_id"],
            "submitted_at": time.time(),
            "n_structures": len(qualified),
            "formulas": [c["formula"] for c in qualified],
        }
    except Exception as exc:  # noqa: BLE001
        latency = int((time.monotonic() - t0) * 1000)
        _record_call(iter_log, mcp_name="mattergen-evaluate",
                     tool="evaluate_structures_async", args=args,
                     result=None, error=str(exc), latency_ms=latency)
        partial_failures.append(f"mattergen-eval submit failed: {exc!r}")
        return None


def _drain_mattergen_eval(
    job: dict[str, Any],
    partial_failures: list[str],
) -> tuple[float, dict[str, Any]]:
    """Poll once (non-blocking). If still pending, returns batch_SUN=0 and
    re-stashes the job for the next iteration by mutating the dict in place."""
    from mcp_client import MCPClient, MCPError  # type: ignore
    job_id = job.get("job_id")
    if not job_id:
        return 0.0, {"status": "missing_job_id"}
    args = {"job_id": job_id}
    t0 = time.monotonic()
    try:
        client = MCPClient(f"{_require_mcp_base()}/mattergen-evaluate/mcp", timeout=60.0, verbose=False)
        client.initialize()
        result = client.call_tool("get_evaluation_result", args)
        latency = int((time.monotonic() - t0) * 1000)
        if not isinstance(result, dict):
            return 0.0, {"status": "non_dict_response"}
        status = result.get("status")
        if status == "done":
            metrics = (result.get("result") or {}).get("metrics") or {}
            sun = float(metrics.get("frac_novel_unique_stable_structures") or 0.0)
            return sun, {
                "status": "done",
                "job_id": job_id,
                "metrics": metrics,
                "latency_ms": latency,
            }
        if status == "error":
            partial_failures.append(f"mattergen-eval job {job_id} status=error")
            return 0.0, {"status": "error", "job_id": job_id, "error": result.get("error")}
        # pending / running — signal stale; caller will re-steward next iter.
        # For simplicity in MVP, we treat pending as 0 and don't carry forward
        # (job is dropped to avoid leaks). Caller may set job=None on return.
        return 0.0, {"status": status or "unknown", "job_id": job_id}
    except Exception as exc:  # noqa: BLE001
        partial_failures.append(f"mattergen-eval drain failed for {job_id}: {exc!r}")
        return 0.0, {"status": "drain_exception", "job_id": job_id, "error": str(exc)}


# ----------------------------------------------------------------------------
# Phase 2h — filter + normalize + reward + select
# ----------------------------------------------------------------------------

def _select_topk(
    *,
    pool: list[dict[str, Any]],
    new_candidates: list[dict[str, Any]],
    config: dict[str, Any],
    batch_sun_prev: float,
    iter_log: dict[str, Any],
) -> list[dict[str, Any]]:
    """Apply ehull filter, min-max normalize, compute reward, return top-K."""
    # hard filter
    if config["filter_ehull"]:
        kept_new = [c for c in new_candidates if c.get("ehull") is not None
                    and c["ehull"] < config["ehull_lt"]]
        dropped = len(new_candidates) - len(kept_new)
        if dropped:
            iter_log["ehull_filter_dropped"] = dropped
        # if everything got filtered, relax for this iter
        if not kept_new and new_candidates:
            kept_new = new_candidates
            iter_log["ehull_filter_relaxed"] = True
    else:
        kept_new = list(new_candidates)

    # Combine pool + kept_new. Only consider records with measurable
    # mag_density + hhi for normalization (init records lack these).
    candidates_with_metrics = [
        c for c in (pool + kept_new)
        if c.get("mag_density") is not None and c.get("hhi") is not None
    ]
    if not candidates_with_metrics:
        iter_log["reward_warning"] = "no candidates with full metrics; pool unchanged"
        return pool[: int(config["top_k"])]

    mags = [c["mag_density"] for c in candidates_with_metrics]
    hhis = [c["hhi"] for c in candidates_with_metrics]
    mag_min, mag_max = min(mags), max(mags)
    hhi_min, hhi_max = min(hhis), max(hhis)
    w_sun = float(config["w_sun"])

    for c in candidates_with_metrics:
        mag_norm = 0.5 if mag_max == mag_min else (c["mag_density"] - mag_min) / (mag_max - mag_min)
        hhi_norm = 0.5 if hhi_max == hhi_min else (c["hhi"] - hhi_min) / (hhi_max - hhi_min)
        c["reward"] = mag_norm + (1 - hhi_norm) + w_sun * batch_sun_prev

    # Sort by reward desc, take top-K
    candidates_with_metrics.sort(key=lambda c: c.get("reward") or 0.0, reverse=True)
    top_k = candidates_with_metrics[: int(config["top_k"])]
    iter_log["best_reward"] = top_k[0].get("reward") if top_k else None
    iter_log["best_formula"] = top_k[0].get("formula") if top_k else None
    iter_log["mag_range"] = [mag_min, mag_max]
    iter_log["hhi_range"] = [hhi_min, hhi_max]
    return top_k


# ----------------------------------------------------------------------------
# Local helpers (HHI, mag_density) — pymatgen in-process
# ----------------------------------------------------------------------------

def _safe_hhi(formula: str, partial_failures: list[str]) -> dict[str, Any]:
    try:
        from pymatgen.analysis.hhi import HHIModel
        m = HHIModel()
        hhi_prod, hhi_res = m.get_hhi(formula)
        return {"hhi": float(hhi_prod), "hhi_reserve": float(hhi_res)}
    except Exception as exc:  # noqa: BLE001
        partial_failures.append(f"hhi({formula}): {exc!r}")
        return {"hhi": None, "hhi_reserve": None}


def _safe_mag_density(magmom: float, relaxed_cif: str) -> dict[str, Any] | None:
    try:
        from pymatgen.core import Structure
        import io
        s = Structure.from_str(relaxed_cif, fmt="cif")
        return {
            "mag_density": float(magmom) / s.volume,
            "volume": float(s.volume),
            "n_atoms": len(s),
        }
    except Exception:  # noqa: BLE001
        return None


# ----------------------------------------------------------------------------
# Phase 3 — final report
# ----------------------------------------------------------------------------

def _build_final_report(
    pool: list[dict[str, Any]],
    iterations: list[dict[str, Any]],
    config: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Markdown top-K table + run summary dict."""
    qualified = [c for c in pool if c.get("reward") is not None]
    qualified.sort(key=lambda c: c.get("reward") or 0.0, reverse=True)
    top = qualified[: int(config["top_k"])]

    if not top:
        table = "_(no candidates with measurable reward — check partial_failure list)_"
    else:
        header = "| rank | formula | mag_density (μB/Å³) | HHI | ehull (eV/atom) | reward |"
        sep = "|------|---------|----------------------|-----|------------------|--------|"
        rows = []
        for i, c in enumerate(top, 1):
            md = f"{c['mag_density']:.4f}" if c.get("mag_density") is not None else "—"
            hhi = f"{c['hhi']:.1f}" if c.get("hhi") is not None else "—"
            ehull = f"{c['ehull']:.3f}" if c.get("ehull") is not None else "—"
            reward = f"{c['reward']:.3f}" if c.get("reward") is not None else "—"
            rows.append(f"| {i} | {c['formula']} | {md} | {hhi} | {ehull} | {reward} |")
        table = "\n".join([header, sep, *rows])

    n_iters_completed = sum(1 for it in iterations if it.get("phase") == 2)
    best = top[0] if top else None
    summary = {
        "best_reward": best.get("reward") if best else None,
        "best_formula": best.get("formula") if best else None,
        "best_mag_density": best.get("mag_density") if best else None,
        "best_hhi": best.get("hhi") if best else None,
        "iters_executed": n_iters_completed,
        "top_k_size": len(top),
        "config": config,
    }
    return table, summary


# ----------------------------------------------------------------------------
# Utilities
# ----------------------------------------------------------------------------

def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="replace")).hexdigest()[:16]


def _truncate(obj: Any, limit: int = 1024) -> Any:
    """Cap string values in nested structures to keep logs bounded."""
    if isinstance(obj, dict):
        return {k: _truncate(v, limit) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_truncate(v, limit) for v in obj]
    if isinstance(obj, str) and len(obj) > limit:
        return obj[:limit] + f"...<{len(obj)} chars>"
    return obj


def _http_post_json(
    url: str,
    *,
    body: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    """stdlib POST; raises on non-2xx or transport error."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError(f"non-dict JSON response from {url}: {type(parsed).__name__}")
    return parsed
