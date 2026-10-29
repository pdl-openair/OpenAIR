"""Durable Python adapter for the materials-mp-constraint-search skill.

This is the worker-side counterpart to ``scripts/search_mp_candidates.py`` and
``SKILL.md``. The workbench (sync) path lets a Claude agent run the script via
Bash; the durable (async) path runs this adapter end-to-end via the
``python_adapter_runner``.

Pipeline (mirrors ``search_mp_candidates.py`` main()):
    1. _load_env() + _get_api_key()
    2. query_mp(server-side filters)
    3. client_filter(sg + extra banned elements)
    4. diversity_filter(per-chemsys top-N)
    5. (optional) verify_with_cppred
    6. truncate to max_results
    7. build candidates list + markdown report

FAILURE SEMANTICS — durable_skill_runtime fail-closes on any raised exception
(state=failed, NO artifact persisted). Therefore this adapter NEVER raises.
MP API errors / argparse errors are captured into ``partial_failure`` and the
run returns ``state=partial_failure`` with whatever survived (often an empty
candidate list). The return dict is always populated.
"""
from __future__ import annotations

import os
import sys
import time
import traceback
import hashlib
import json
import re
from pathlib import Path
from typing import Any

# Bootstrap import path for the sibling ``scripts/search_mp_candidates.py``.
# The python_adapter_runner puts the skill dir on sys.path; we still resolve
# explicitly so the adapter also works under ad-hoc invocation.
_SKILL_DIR = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _SKILL_DIR / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Import the script as a module. The filename has hyphens so use importlib.
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "_search_mp_candidates", _SCRIPTS_DIR / "search_mp_candidates.py"
)
if _spec is None or _spec.loader is None:
    raise RuntimeError("failed to load search_mp_candidates.py as a module")
_search = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_search)


DEFAULT_CONFIG: dict[str, Any] = {
    "max_results": 50,
    "banned_elements": ["Hg", "Tl", "Na", "K", "F", "Pb", "Cd"],
    "keep_radioactive": False,
    "num_elements": [3, 3],
    "bandgap": [0.2, 3.5],
    "chemsys": None,
    "formation_energy_max": 0.0,
    "ehull_max": 1.0,
    "banned_sg": [1],
    "max_per_chemsys": 1,
    "pool_size": 1000,
    "rank_by": "auto",
    "verify_cppred": False,
    "cppred_mcp_url": "http://mcp.example.internal:22222/mcp/flux/mcp",
}


def _merge_config(payload: dict[str, Any]) -> dict[str, Any]:
    """Merge task_instruction overrides from the input payload over defaults.

    The payload schema (skill.yaml input_schema) accepts:
        {
            "goal": "<natural language>",
            "task_instruction": { ...overrides... } | "<json string>" | null
        }
    """
    cfg = dict(DEFAULT_CONFIG)
    # The canonical workflow passes the user's immutable query as ``goal``.
    # Interpret only constraints this adapter can actually execute; explicit
    # task_instruction overrides still take precedence.
    cfg.update(_goal_constraint_overrides(str(payload.get("goal") or "")))
    instr_raw = payload.get("task_instruction")
    if isinstance(instr_raw, str):
        import json as _json
        try:
            instr_raw = _json.loads(instr_raw)
        except Exception:
            instr_raw = None
    if isinstance(instr_raw, dict):
        for k, v in instr_raw.items():
            if k in cfg:
                cfg[k] = v
    return cfg


def _goal_constraint_overrides(goal: str) -> dict[str, Any]:
    """Extract the small, auditable subset of MP constraints from a goal."""
    normalized = goal.lower()
    overrides: dict[str, Any] = {}
    bandgap = re.search(
        r"(?:band\s*gap|bandgap|带隙|禁带)\s*(?:为|在|between)?\s*"
        r"([0-9]+(?:\.[0-9]+)?)\s*(?:-|–|—|to|~|至)\s*"
        r"([0-9]+(?:\.[0-9]+)?)\s*(?:ev|电子伏特)?",
        normalized,
        flags=re.IGNORECASE,
    )
    if bandgap:
        lower, upper = float(bandgap.group(1)), float(bandgap.group(2))
        if lower <= upper:
            overrides["bandgap"] = [lower, upper]
    if re.search(r"(?:ternary|三元)", normalized, flags=re.IGNORECASE):
        overrides["num_elements"] = [3, 3]
    if re.search(r"(?:binary|二元)", normalized, flags=re.IGNORECASE):
        overrides["num_elements"] = [2, 2]
    # 显式连字符化学体系（如 “Mg-O”“Zn-O”）优先级最高：限定 chemsys 并
    # 同步收紧元素数（与 skill_script 桥 _explicit_chemical_system 同一语义）
    chemsys = re.search(
        r"(?<![a-z])((?:[A-Z][a-z]?)(?:-[A-Z][a-z]?)+)(?![a-z])", goal
    )
    if chemsys:
        elements = sorted(set(chemsys.group(1).split("-")))
        overrides["chemsys"] = "-".join(elements)
        overrides["num_elements"] = [len(elements), len(elements)]
    return overrides


def _mp_query_evidence(cfg: dict[str, Any], outcome: str) -> dict[str, str]:
    request = {
        "bandgap": cfg["bandgap"],
        "formation_energy_max": cfg["formation_energy_max"],
        "ehull_max": cfg["ehull_max"],
        "num_elements": cfg["num_elements"],
        "banned_elements": cfg["banned_elements"],
        "pool_size": cfg["pool_size"],
    }
    encoded = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "service": "materials_project",
        "operation": "materials.summary.search",
        "request_sha256": hashlib.sha256(encoded).hexdigest(),
        "transport_outcome": outcome,
    }


def _build_markdown(
    candidates: list[dict[str, Any]],
    cfg: dict[str, Any],
    *,
    pool_size_returned: int,
    after_client_filter: int,
    after_diversity_filter: int,
    cppred_mode: str | None,
    elapsed_s: float,
    goal: str,
) -> str:
    """Build a human-readable markdown report (the substage artifact)."""
    lines: list[str] = []
    lines.append("# MP 种子候选清单（durable adapter run）\n")
    lines.append(f"- 用户目标：{goal or '(未提供)'}")
    lines.append(f"- 生成时间：{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    lines.append(f"- skill：`materials-mp-constraint-search`（L3, python_adapter durable）")
    lines.append(f"- 耗时：{elapsed_s:.1f}s")
    lines.append("")
    lines.append("## constraint_set\n")
    lines.append("| 约束 | 阈值 | 数据来源 |")
    lines.append("|---|---|---|")
    lines.append(f"| `bandgap_eV` | `[{cfg['bandgap'][0]}, {cfg['bandgap'][1]}]` | MP（服务端过滤） |")
    lines.append(f"| `formation_energy_eV` | `≤ {cfg['formation_energy_max']}` | MP（服务端过滤） |")
    lines.append(f"| `ehull_eV` | `≤ {cfg['ehull_max']}` | MP（服务端过滤） |")
    lines.append(f"| `num_elements` | `[{cfg['num_elements'][0]}, {cfg['num_elements'][1]}]` | MP（服务端过滤） |")
    if cfg.get("chemsys"):
        lines.append(f"| `chemsys` | `{cfg['chemsys']}`（goal 显式体系） | MP（服务端过滤） |")
    banned_disp = ", ".join(cfg["banned_elements"])
    extra = "" if cfg["keep_radioactive"] else "（含稀有/锕系自动排除）"
    lines.append(f"| `banned_elements` | `{banned_disp}`{extra} | MP（客户端过滤） |")
    lines.append(f"| `sg_number` | `not in {cfg['banned_sg']}` | 客户端 |")
    if cfg["verify_cppred"]:
        lines.append(f"| `cppred` | verify=on (mode={cppred_mode}) | cppred MCP |")
    else:
        lines.append("| `cppred` | 未启用 | cppred MCP |")
    lines.append("")
    lines.append("## candidates\n")
    if not candidates:
        lines.append("_无候选命中（详见 run_summary.partial_failure）。_\n")
    else:
        lines.append("| MP ID | 公式 | Eg (eV) | E_form (eV/atom) | ehull (eV/atom) | 元素 | SG |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in candidates:
            elem = "-".join(sorted(r.get("element_set", [])))
            lines.append(
                f"| {r['material_id']} | {r['formula_pretty']} | "
                f"{r['bandgap_eV']:.3f} | {r['formation_energy_eV']:.3f} | "
                f"{r['ehull_eV']:.3f} | {elem} | "
                f"{r['sg_number']} ({r.get('sg_symbol', '')}) |"
            )
        lines.append("")
    lines.append("## coverage_summary\n")
    lines.append(f"- 命中数：{len(candidates)}（cap={cfg['max_results']}）")
    lines.append(f"- MP 服务端返回原始池：{pool_size_returned}")
    lines.append(f"- 客户端 sg/banned 过滤后：{after_client_filter}")
    lines.append(f"- chemsys 去重后：{after_diversity_filter}")
    if cppred_mode:
        lines.append(f"- cppred 验证模式：{cppred_mode}")
    lines.append("")
    lines.append("### 给下游演化的引导\n")
    if candidates:
        stable = [r for r in candidates if r["ehull_eV"] <= 0.05]
        lines.append(f"- {len(stable)} 个候选 ehull≤0.05（热力学稳定），可直接进入下游演化")
        lines.append("- 建议下游 `candidate_generation` 在上述化学空间周围做扩散变异")
    else:
        lines.append("- 无候选 — 建议放宽 bandgap 或 ehull 约束后重试")
    return "\n".join(lines)


def run_mp_constraint_search(payload: dict[str, Any]) -> dict[str, Any]:
    """Durable entry point.

    Input payload (see skill.yaml input_schema):
        {
            "goal": str,                    # natural-language user goal
            "task_instruction": dict|str|null  # optional config overrides
        }

    Returns:
        {
            "state": "succeeded" | "partial_failure",
            "candidates": list[dict],       # top-level candidate rows
            "report_markdown": str,         # human-readable report
            "run_summary": {
                "elapsed_s": float,
                "pool_returned": int,
                "after_client_filter": int,
                "after_diversity_filter": int,
                "cppred_mode": str | None,
                "config": dict,             # the merged config used
            },
            "partial_failure": list[str] | None,
        }
    """
    t0 = time.time()
    partial: list[str] = []
    goal = str(payload.get("goal") or "").strip()
    cfg = _merge_config(payload)

    pool_returned = 0
    after_client = 0
    after_diversity = 0
    cppred_mode: str | None = None
    candidates: list[dict[str, Any]] = []
    external_evidence = _mp_query_evidence(cfg, "not_attempted")

    try:
        _search._load_env()
    except Exception as exc:
        partial.append(f"_load_env failed: {exc!r}")

    # MP_API_KEY is mandatory; fail-closed into partial_failure.
    try:
        api_key = _search._get_api_key()
    except SystemExit as exc:
        partial.append(f"MP_API_KEY missing: {exc!r}")
        return _build_result(
            candidates, cfg, goal, t0,
            pool_returned, after_client, after_diversity, cppred_mode, partial, external_evidence,
        )

    # Optional: cppred URL override
    if cfg.get("cppred_mcp_url"):
        os.environ["CPPRED_MCP_URL"] = str(cfg["cppred_mcp_url"])

    # 1. MP server-side query
    try:
        external_evidence = _mp_query_evidence(cfg, "error")
        rows = _search.query_mp(
            api_key,
            bandgap=tuple(cfg["bandgap"]),
            formation_energy_max=float(cfg["formation_energy_max"]),
            ehull_max=float(cfg["ehull_max"]),
            num_elements=tuple(cfg["num_elements"]),
            banned_elements=list(cfg["banned_elements"]),
            pool_size=int(cfg["pool_size"]),
            chemsys=cfg.get("chemsys"),
        )
        pool_returned = len(rows)
        external_evidence = _mp_query_evidence(cfg, "ok")
    except Exception as exc:
        partial.append(f"query_mp failed: {exc!r}")
        return _build_result(
            candidates, cfg, goal, t0,
            pool_returned, after_client, after_diversity, cppred_mode, partial, external_evidence,
        )

    if not rows:
        partial.append("MP query returned 0 rows (try relaxing bandgap/ehull).")
        return _build_result(
            candidates, cfg, goal, t0,
            pool_returned, after_client, after_diversity, cppred_mode, partial, external_evidence,
        )

    # 2. client_filter (sg + extra banned + rare elements)
    extra_banned: list[str] = []
    if not cfg["keep_radioactive"]:
        extra_banned = list(getattr(_search, "RARE_ELEMENTS", []))
    try:
        rows = _search.client_filter(
            rows,
            banned_sg=list(cfg["banned_sg"]),
            extra_banned_elements=extra_banned,
        )
        after_client = len(rows)
    except Exception as exc:
        partial.append(f"client_filter failed: {exc!r}")
        # continue with whatever rows we have pre-filter

    # 3. diversity_filter (per-chemsys top-N)
    rank_by = cfg["rank_by"]
    if rank_by == "auto":
        rank_by = "cl_score" if cfg["verify_cppred"] else "stability"
    try:
        rows = _search.diversity_filter(
            rows,
            max_per_chemsys=int(cfg["max_per_chemsys"]),
            rank_by=rank_by,
        )
        after_diversity = len(rows)
    except Exception as exc:
        partial.append(f"diversity_filter failed: {exc!r}")

    # 4. truncate
    rows = rows[: int(cfg["max_results"])]

    # 5. optional cppred verify
    if cfg["verify_cppred"] and rows:
        try:
            rows, cppred_mode = _search.verify_with_cppred(rows)
        except Exception as exc:
            partial.append(f"verify_with_cppred failed: {exc!r}")
            cppred_mode = "degraded"

    # 6. Persist the formal structure representation in the durable candidate Artifact.
    # Execution-local CIF paths are deliberately not part of the public handoff.
    for r in rows:
        structure = r.get("structure")
        if structure is None:
            raise ValueError("materials candidate is missing its formal structure")
        r["cif_text"] = structure.to(fmt="cif")
        r.pop("structure", None)

    candidates = rows
    return _build_result(
        candidates, cfg, goal, t0,
        pool_returned, after_client, after_diversity, cppred_mode, partial, external_evidence,
    )


def _build_result(
    candidates: list[dict[str, Any]],
    cfg: dict[str, Any],
    goal: str,
    t0: float,
    pool_returned: int,
    after_client: int,
    after_diversity: int,
    cppred_mode: str | None,
    partial: list[str],
    external_action_evidence: dict[str, str],
) -> dict[str, Any]:
    elapsed = time.time() - t0
    state = "succeeded" if not partial else "partial_failure"
    report = _build_markdown(
        candidates, cfg,
        pool_size_returned=pool_returned,
        after_client_filter=after_client,
        after_diversity_filter=after_diversity,
        cppred_mode=cppred_mode,
        elapsed_s=elapsed,
        goal=goal,
    )
    return {
        "state": state,
        "candidates": candidates,
        "report_markdown": report,
        "run_summary": {
            "elapsed_s": round(elapsed, 2),
            "pool_returned": pool_returned,
            "after_client_filter": after_client,
            "after_diversity_filter": after_diversity,
            "cppred_mode": cppred_mode,
            "config": cfg,
            "goal": goal,
        },
        "partial_failure": partial or None,
        "external_action_evidence": [external_action_evidence],
    }
