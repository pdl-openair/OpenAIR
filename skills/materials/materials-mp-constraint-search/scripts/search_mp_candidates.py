#!/usr/bin/env python3
"""
search_mp_candidates.py — 在 Materials Project 数据库中查找满足 DEMO 演化约束的候选材料。

约束集合（与 Evoloop.py 中 my_constraints 对齐）：
    bandgap_eV              ∈ [0.2, 3.5]
    exfoliation_energy_meV  ∈ [-5, 350]   ← MP 无此字段，需 crystal-property-prediction MCP 验证
    formation_energy_eV     ≤ 0
    cl_score                ≥ 0.50         ← MP 无此字段，需 crystal-property-prediction MCP 验证
    f_max_eV_A              ≤ 2.0          ← MP 无此字段，需 crystal-property-prediction MCP 验证
    ehull_eV                ≤ 1.0
    num_elements            == 3
    element_set             不含 Hg/Tl/Na/K/F
    sg_number               ≠ 1（可选，默认开）

策略：
    1. 服务端过滤（MP API 直接支持）：bandgap / formation_energy / ehull /
       num_elements / 排除 banned 元素
    2. 客户端过滤：sg_number ≠ 1
    3. 可选 cross-check：调 crystal-property-prediction MCP 跑 ALIGNN+CHGNet+JACS 全链路，验证
       cl_score / f_max / exfoliation_energy（默认关闭，--verify-cppred 开启）

用法：
    python3 search_mp_candidates.py
    python3 search_mp_candidates.py --max-results 50 --verify-cppred
    python3 search_mp_candidates.py --banned-elements Hg Tl Na K F --num-elements 3 3
    python3 search_mp_candidates.py --output-dir ./candidates

输出（output_dir 默认 ./mp_candidates_<timestamp>/）：
    candidates.json               — 通过 MP 过滤的候选清单（结构化 JSON）
    cifs/<mp_id>_<formula>.cif    — 每个候选的 CIF 结构
    summary.json                  — 完整数据（含 crystal-property-prediction MCP 验证结果，若启用）
    report.md                     — 人类可读报告
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

# ──────────────────────────────────────────────────────────────────────────────
# 环境初始化（与 pymatgen/scripts/mp_query.py 同款，复用 .env 加载逻辑）
# ──────────────────────────────────────────────────────────────────────────────

def _load_env() -> None:
    """从项目根目录 .env 加载 MP_API_KEY 等环境变量"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # python-dotenv 优先
    try:
        from dotenv import load_dotenv  # type: ignore
        for _ in range(6):
            env_path = os.path.join(script_dir, ".env")
            if os.path.exists(env_path):
                load_dotenv(env_path)
                return
            parent = os.path.dirname(script_dir)
            if parent == script_dir:
                break
            script_dir = parent
        load_dotenv()
    except ImportError:
        # 手动解析
        sd = os.path.dirname(os.path.abspath(__file__))
        for _ in range(6):
            env_path = os.path.join(sd, ".env")
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        s = line.strip()
                        if s and not s.startswith("#") and "=" in s:
                            k, _, v = s.partition("=")
                            os.environ.setdefault(k.strip(), v.strip())
                return
            p = os.path.dirname(sd)
            if p == sd:
                break
            sd = p


def _get_api_key() -> str:
    key = os.environ.get("MP_API_KEY", "").strip()
    if not key:
        sys.exit(
            "[FATAL] MP_API_KEY 未设置。请在项目根目录 .env 中添加：\n"
            "  MP_API_KEY=your_key_here\n"
            "申请地址：https://next-gen.materialsproject.org/api"
        )
    return key


# ──────────────────────────────────────────────────────────────────────────────
# 默认约束（与 Evoloop.py 中 my_constraints 一致）
# ──────────────────────────────────────────────────────────────────────────────

DEFAULT_CONSTRAINTS = {
    "bandgap_eV":              {"min": 0.2,  "max": 3.5},
    "exfoliation_energy_meV":  {"min": -5.0, "max": 350.0},   # 需 crystal-property-prediction MCP
    "formation_energy_eV":     {"max": 0.0},
    "cl_score":                {"min": 0.50},                  # 需 crystal-property-prediction MCP
    "f_max_eV_A":              {"max": 2.0},                   # 需 crystal-property-prediction MCP
    "ehull_eV":                {"max": 1.0},
    "num_elements":            {"min": 3, "max": 3},
    "element_set":             {"banned": ["Hg", "Tl", "Na", "K", "F"]},
    "sg_number":               {"banned": [1]},
}

# crystal-property-prediction MCP 静默失败时返回的占位值(三件套)。
# 触发条件:ALIGNN/CHGNet 推理 OOM/超时(经验阈值:num_sites > ~10)。
# 检测策略:三件套全中 → 判 cppred 失败,不让占位污染候选评分。
PENALTY_VALUES = {
    "cl_score": -1.0,
    "f_max_ev_a": 999.0,
    "exfoliation_energy_mev": 9999.0,
    "bandgap_ev": 0.0,
    "formation_energy_ev": 999.0,
    "hull_ev": 999.0,
}

# 稀有/放射性/超重合成元素 —— 这些在 JACS CL-score 训练集（ICSD）中样本极少，
# 默认排除可显著提升 Top-N 命中率。可用 --keep-radioactive 关闭。
# 来源：锕系（Ac–Lr 全部）+ Tc/Pm/Po/At/Rn/Fr/Ra
RARE_ELEMENTS = [
    "Ac", "Th", "Pa", "U", "Np", "Pu", "Am", "Cm",
    "Bk", "Cf", "Es", "Fm", "Md", "No", "Lr",
    "Tc", "Pm", "Po", "At", "Rn", "Fr", "Ra",
]


# ──────────────────────────────────────────────────────────────────────────────
# MP API 查询
# ──────────────────────────────────────────────────────────────────────────────

# 服务端可过滤字段（MP 数据库原生字段）→ DEMO 约束键
SERVER_FILTERABLE = {
    "bandgap": "bandgap_eV",
    "formation_energy_per_atom": "formation_energy_eV",
    "energy_above_hull": "ehull_eV",
}


def query_mp(
    api_key: str,
    *,
    bandgap: tuple[float, float],
    formation_energy_max: float,
    ehull_max: float,
    num_elements: tuple[int, int],
    banned_elements: list[str],
    chemsys: str | None = None,
    pool_size: int = 1000,
) -> list[dict[str, Any]]:
    """向 MP 提交查询，返回候选材料清单。

    返回每个候选的 dict，至少包含：
        material_id, formula_pretty, bandgap_eV, formation_energy_eV,
        ehull_eV, num_elements, element_set, sg_number, structure (pymatgen)

    pool_size:
        从 MP 拉取的原始池大小上限。**关键反偏置参数**——mp_api 客户端
        不支持服务端排序（pyproject 锁定 0.46.4，sort_by 未暴露），MP
        默认返回顺序对 Ag/F 等字母靠前元素有系统性偏置（见 audit 1.2 D2 /
        1.5 U3）。通过把池子从旧的 ~max_results 扩大到 pool_size（默认
        1000），让后续 diversity_filter 的 rank_by="stability" 排序能见到
        足够多的非 Ag/F 候选，从而真正实现 ehull/formation_energy 显式
        排序的效果。客户端排序由 diversity_filter._rank_key 完成。
    """
    from mp_api.client import MPRester

    fields = [
        "material_id",
        "formula_pretty",
        "band_gap",
        "formation_energy_per_atom",
        "energy_above_hull",
        "nelements",
        "elements",
        "symmetry",
        "structure",
    ]

    # num_chunks 单位是 100 条。pool_size 决定拉多少原始数据；客户端
    # 排序 + 多样性 dedup 之后才截断到 max_results（在 main() 里）。
    chunks_needed = max(1, (pool_size + 99) // 100)

    with MPRester(api_key) as mpr:
        try:
            search_kwargs = dict(
                num_elements=num_elements,
                band_gap=bandgap,
                formation_energy_per_atom=(None, formation_energy_max),
                energy_above_hull=(None, ehull_max),
                exclude_elements=banned_elements,
                fields=fields,
                num_chunks=chunks_needed,
                chunk_size=100,
            )
            if chemsys:
                search_kwargs["chemsys"] = chemsys
            docs = mpr.materials.summary.search(**search_kwargs)
        except TypeError as e:
            # 兼容旧版 mp_api：尝试 nelements
            if "unexpected keyword argument" in str(e):
                search_kwargs = dict(
                    nelements=num_elements,
                    band_gap=bandgap,
                    formation_energy_per_atom=(None, formation_energy_max),
                    energy_above_hull=(None, ehull_max),
                    exclude_elements=banned_elements,
                    fields=fields,
                    num_chunks=chunks_needed,
                    chunk_size=100,
                )
                if chemsys:
                    search_kwargs["chemsys"] = chemsys
                docs = mpr.materials.summary.search(**search_kwargs)
            else:
                raise

    rows: list[dict[str, Any]] = []
    for d in docs:
        sg_info = getattr(d, "symmetry", None)
        sg_number = getattr(sg_info, "number", None) if sg_info else None
        sg_symbol = getattr(sg_info, "symbol", None) if sg_info else None
        rows.append({
            "material_id": str(d.material_id),
            "formula_pretty": d.formula_pretty,
            "bandgap_eV": float(d.band_gap) if d.band_gap is not None else None,
            "formation_energy_eV": (
                float(d.formation_energy_per_atom)
                if d.formation_energy_per_atom is not None else None
            ),
            "ehull_eV": (
                float(d.energy_above_hull)
                if d.energy_above_hull is not None else None
            ),
            "num_elements": int(d.nelements) if d.nelements is not None else None,
            # MP 返回 Element 对象（pymatgen enum），统一转 str
            "element_set": [str(el) for el in d.elements] if d.elements else [],
            "sg_number": int(sg_number) if sg_number is not None else None,
            "sg_symbol": sg_symbol,
            "structure": d.structure,
        })
        if len(rows) >= pool_size:
            break
    return rows


# ──────────────────────────────────────────────────────────────────────────────
# 客户端过滤（MP 不直接支持的条件）
# ──────────────────────────────────────────────────────────────────────────────

def client_filter(
    rows: Iterable[dict[str, Any]],
    *,
    banned_sg: list[int],
    extra_banned_elements: list[str] | None = None,
    required_elements: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """应用客户端约束（如 sg_number 黑名单、稀有元素排除）。

    extra_banned_elements 用于在客户端追加 MP server 不支持的元素过滤
    （MP API 的 exclude_elements URL 参数上限 60 字符）。
    """
    extra = set(extra_banned_elements or [])
    required = set(required_elements or [])
    out = []
    for r in rows:
        sg = r.get("sg_number")
        if sg is not None and sg in banned_sg:
            continue
        # 二次保险：检查 banned 元素（MP 排除有时不完全可靠）
        banned = set(DEFAULT_CONSTRAINTS["element_set"]["banned"]) | extra
        if any(el in banned for el in r.get("element_set", [])):
            continue
        if required and set(r.get("element_set", [])) != required:
            continue
        out.append(r)
    return out


# ──────────────────────────────────────────────────────────────────────────────
# 化学系统多样性过滤
# ──────────────────────────────────────────────────────────────────────────────

def _chemsys_key(row: dict[str, Any]) -> str:
    """化学系统键：排序后的元素元组字符串，如 'Ag-Bi-O'。"""
    return "-".join(sorted(row.get("element_set", [])))


def _rank_key(row: dict[str, Any], rank_by: str) -> tuple:
    """候选排序键（升序，越小越好）。

    stability:   ehull ↑（稳定优先），formation_energy ↑（深形成能优先）
    cl_score:    cl_score ↓（高合成可行性优先），ehull ↑ 兜底
    """
    ehull = row.get("ehull_eV") if row.get("ehull_eV") is not None else 999.0
    form = row.get("formation_energy_eV") if row.get("formation_energy_eV") is not None else 0.0

    if rank_by == "cl_score":
        # 从 cppred_verification 字典里取 CL-score（如果已验证），未验证则视为最差
        fv = row.get("cppred_verification", {})
        cl = fv.get("cl_score") if isinstance(fv, dict) else None
        cl = cl if cl is not None else -1.0
        # 升序：高 CL-score 排前 → 用 (-cl, ehull, form)
        return (-cl, ehull, form)
    # default: stability
    return (ehull, form, 0.0)


def diversity_filter(
    rows: list[dict[str, Any]],
    *,
    max_per_chemsys: int,
    rank_by: str,
) -> list[dict[str, Any]]:
    """按 chemsys 分组，每组保留 top-N。

    输出顺序：每组内按 rank_by 排序；组间按该组最佳候选的 rank_by 排序。
    """
    if max_per_chemsys <= 0:
        return rows

    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(_chemsys_key(r), []).append(r)

    # 组内排序 + 截断
    kept: dict[str, list[dict[str, Any]]] = {}
    for key, group in groups.items():
        group.sort(key=lambda x: _rank_key(x, rank_by))
        kept[key] = group[:max_per_chemsys]

    # 组间排序：按该组最佳候选的 rank_key 升序
    sorted_keys = sorted(kept.keys(), key=lambda k: _rank_key(kept[k][0], rank_by))

    out: list[dict[str, Any]] = []
    for k in sorted_keys:
        out.extend(kept[k])
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Crystal-property-prediction MCP 验证（cl_score / f_max_eV_A / exfoliation_energy_meV）
# ──────────────────────────────────────────────────────────────────────────────

def _is_penalty_result(cppred_props: dict[str, Any]) -> bool:
    """检测 crystal-property-prediction MCP 静默失败的三件套占位。

    触发条件:cl_score/f_max/exfoliation_energy 三个关键值同时命中 PENALTY。
    单个值命中不算(允许真实数据中偶有 -1 / 999)。
    """
    if not cppred_props:
        return True
    hits = 0
    for k, pen in PENALTY_VALUES.items():
        v = cppred_props.get(k)
        if v is None:
            continue
        try:
            if abs(float(v) - pen) < 1e-6:
                hits += 1
        except (TypeError, ValueError):
            continue
    # 三件套关键值(cl_score / f_max / exfoliation_energy)全占位
    critical = (
        abs(float(cppred_props.get("cl_score", 0) or 0) - PENALTY_VALUES["cl_score"]) < 1e-6
        and abs(float(cppred_props.get("f_max_ev_a", 0) or 0)
                - PENALTY_VALUES["f_max_ev_a"]) < 1e-6
        and abs(float(cppred_props.get("exfoliation_energy_mev", 0) or 0)
                - PENALTY_VALUES["exfoliation_energy_mev"]) < 1e-6
    )
    return critical or hits >= 4


def _elements_match(cppred_props: dict[str, Any], expected: list[str]) -> bool:
    """cppred 返回的 element_set 必须与 CIF 解析出的元素集一致。

    不一致 → 服务返回了别人的结果(缓存串号或并发污染)。
    """
    got = cppred_props.get("element_set") or []
    if not got:
        return True  # 服务没返回元素集,不阻拦
    return sorted(str(e) for e in got) == sorted(str(e) for e in expected)


def _cppred_predict(cif_content: str, name: str, timeout: float = 180.0) -> dict[str, Any]:
    """调用 crystal-property-prediction MCP 的 predict_flux_properties 工具，返回 9 项性质。

    返回 dict 含 "properties"(模型输出)和 "elapsed_s"。
    不会抛 PenaltyError —— 调用方用 _is_penalty_result 自检。
    """
    import json as _json
    import urllib.request
    import urllib.error

    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "predict_flux_properties",
                   "arguments": {"cif_content": cif_content, "name": name,
                                 "fields": ["bandgap_ev", "exfoliation_energy_mev",
                                            "formation_energy_ev", "cl_score",
                                            "f_max_ev_a", "hull_ev",
                                            "num_elements", "element_set", "sg_number"]}},
    }
    req = urllib.request.Request(
        os.environ.get("CPPRED_MCP_URL", "http://mcp.example.internal:22222/mcp/flux/mcp"),
        data=_json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")

    # 解析 SSE 或纯 JSON
    if body.lstrip().startswith("event:") or "\ndata:" in body:
        lines = [ln for ln in body.splitlines() if ln.startswith("data:")]
        if not lines:
            raise RuntimeError(f"crystal-property-prediction MCP SSE 无 data 行: {body[:200]}")
        result = _json.loads(lines[-1][len("data:"):].strip())
    else:
        result = _json.loads(body)

    if "error" in result:
        raise RuntimeError(f"crystal-property-prediction MCP error: {result['error']}")

    for item in result.get("result", {}).get("content", []):
        if item.get("type") == "text":
            return _json.loads(item["text"])
    return result


def verify_with_cppred(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """对每个候选调 crystal-property-prediction MCP，补充 cl_score / f_max / exfoliation_energy。

    返回 (rows, mode),mode 取值:
      - "strict"   :cppred 全部健康,所有候选都跑了 cppred
      - "degraded" :cppred 整体不可用(健康预检失败),全部跳过 → 仅 MP 约束有效
      - "partial"  :部分候选 cppred 成功,部分失败(典型场景:大 cell 占位)
    """
    print(f"\n[cppred] 开始验证 {len(rows)} 个候选（每个 ~10-30s）...")
    for i, r in enumerate(rows, 1):
        struct = r.pop("structure", None)
        if struct is None:
            r["cppred_verification"] = {"status": "skipped", "reason": "no structure"}
            r["structure"] = None
            continue
        cif = struct.to(fmt="cif")
        name = f"{r['material_id']}_{r['formula_pretty']}.cif"
        expected_elements = list(r.get("element_set", []))
        t0 = time.time()
        try:
            props = _cppred_predict(cif, name)
            cppred_props = props.get("properties", {})
            elapsed = round(time.time() - t0, 1)

            if _is_penalty_result(cppred_props):
                # 占位三件套 —— 大 cell 静默失败,不污染评分
                r["cppred_verification"] = {
                    "status": "penalty",
                    "elapsed_s": elapsed,
                    "num_sites": struct.num_sites,
                    "reason": "cppred returned PENALTY_VALUES (likely large cell OOM/timeout)",
                }
            elif not _elements_match(cppred_props, expected_elements):
                # 元素集不一致 —— 缓存串号 / 并发污染
                r["cppred_verification"] = {
                    "status": "polluted",
                    "elapsed_s": elapsed,
                    "expected_elements": expected_elements,
                    "got_elements": cppred_props.get("element_set"),
                    "reason": "cppred element_set mismatch (server-side state pollution)",
                }
            else:
                r["cppred_verification"] = {
                    "status": "ok",
                    "elapsed_s": elapsed,
                    "cl_score": cppred_props.get("cl_score"),
                    "f_max_eV_A": cppred_props.get("f_max_ev_a"),
                    "exfoliation_energy_meV": cppred_props.get("exfoliation_energy_mev"),
                    "bandgap_ev_cppred": cppred_props.get("bandgap_ev"),
                    "formation_energy_ev_cppred": cppred_props.get("formation_energy_ev"),
                    "hull_ev_cppred": cppred_props.get("hull_ev"),
                }
        except Exception as e:
            r["cppred_verification"] = {
                "status": "failed",
                "elapsed_s": round(time.time() - t0, 1),
                "error": str(e)[:200],
            }
        # 把结构放回（后续写 CIF 要用）
        r["structure"] = struct
        fv = r["cppred_verification"]
        print(f"  [{i}/{len(rows)}] {r['material_id']:>12} {r['formula_pretty']:<20} "
              f"sites={struct.num_sites:>3} → {fv.get('status')} "
              f"({fv.get('elapsed_s', '?')}s)")

    # 判定 mode
    statuses = [r.get("cppred_verification", {}).get("status") for r in rows]
    n_ok = sum(1 for s in statuses if s == "ok")
    if n_ok == 0:
        mode = "degraded"
    elif n_ok == len(rows):
        mode = "strict"
    else:
        mode = "partial"

    print(f"[cppred] mode={mode}  (ok={n_ok}/{len(rows)})")
    return rows, mode


# ──────────────────────────────────────────────────────────────────────────────
# 输出
# ──────────────────────────────────────────────────────────────────────────────

def write_outputs(
    rows: list[dict[str, Any]],
    output_dir: Path,
    *,
    cppred_verified: bool,
    cppred_mode: str = "strict",
    query_params: dict[str, Any] | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    cif_dir = output_dir / "cifs"
    cif_dir.mkdir(exist_ok=True)

    # 1. candidates.json（结构化候选清单；替代旧的 candidates.csv，便于下游直接消费）
    candidates_path = output_dir / "candidates.json"
    candidates_rows: list[dict[str, Any]] = []
    for r in rows:
        struct = r.get("structure")
        if struct is None:
            # A candidate without a structure cannot be a formal seed Artifact.
            continue
        row = {
            "material_id": r["material_id"],
            "formula_pretty": r["formula_pretty"],
            "cif_text": struct.to(fmt="cif"),
            "chemsys": _chemsys_key(r),
            "bandgap_eV": r["bandgap_eV"],
            "formation_energy_eV": r["formation_energy_eV"],
            "ehull_eV": r["ehull_eV"],
            "num_elements": r["num_elements"],
            "element_set": list(r["element_set"]),
            "sg_number": r["sg_number"],
            "sg_symbol": r["sg_symbol"],
        }
        if cppred_verified:
            fv = r.get("cppred_verification", {})
            row.update({
                "cl_score": fv.get("cl_score"),
                "f_max_eV_A": fv.get("f_max_eV_A"),
                "exfoliation_energy_meV": fv.get("exfoliation_energy_meV"),
                "cppred_status": fv.get("status"),
                "cppred_bandgap_ev": fv.get("bandgap_ev_cppred"),
                "cppred_hull_ev": fv.get("hull_ev_cppred"),
            })
        candidates_rows.append(row)
    candidates_path.write_text(
        json.dumps(
            {
                "query_time": datetime.now().isoformat(timespec="seconds"),
                "cppred_verified": cppred_verified,
                "count": len(candidates_rows),
                "candidates": candidates_rows,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # 2. CIF 文件
    for r in rows:
        struct = r.get("structure")
        if struct is None:
            continue
        fname = f"{r['material_id']}_{r['formula_pretty']}.cif".replace("/", "_")
        struct.to(filename=str(cif_dir / fname))

    # 3. JSON
    json_path = output_dir / "summary.json"
    json_rows = []
    for r in rows:
        # structure 不可 JSON 序列化，单独处理
        rr = {k: v for k, v in r.items() if k != "structure"}
        json_rows.append(rr)
    json_path.write_text(
        json.dumps({
            "query_time": datetime.now().isoformat(timespec="seconds"),
            "constraints": DEFAULT_CONSTRAINTS,
            "count": len(rows),
            "candidates": json_rows,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # 4. Markdown 报告
    md_path = output_dir / "report.md"
    md = [
        f"# MP 候选材料报告",
        f"",
        f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}",
        f"- 候选数量：{len(rows)}",
        f"- Crystal-property-prediction MCP 验证：{'✅ 已开启' if cppred_verified else '⏭️ 未开启'}",
        f"",
        f"## 约束集合",
        f"",
        f"| 约束 | 阈值 | 来源 |",
        f"|------|------|------|",
    ]
    src_map = {
        "bandgap_eV": "MP", "formation_energy_eV": "MP", "ehull_eV": "MP",
        "num_elements": "MP", "element_set": "MP", "sg_number": "client",
        "exfoliation_energy_meV": "cppred", "cl_score": "cppred", "f_max_eV_A": "cppred",
    }
    for k, v in DEFAULT_CONSTRAINTS.items():
        md.append(f"| `{k}` | `{v}` | {src_map.get(k, '?')} |")
    md += ["", "## 候选清单", "",
           "| MP ID | 公式 | Eg (eV) | E_form (eV) | ehull (eV) | 元素 | SG |"]
    md.append("|-------|------|---------|-------------|------------|------|-----|")
    for r in rows:
        md.append(
            f"| {r['material_id']} | {r['formula_pretty']} | "
            f"{r['bandgap_eV']:.3f} | {r['formation_energy_eV']:.3f} | "
            f"{r['ehull_eV']:.3f} | {'-'.join(r['element_set'])} | "
            f"{r['sg_number']} ({r['sg_symbol']}) |"
        )
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")

    # 5. passing_candidates.json（仅在 cppred 验证开启时输出，结构化便于下游消费）
    if cppred_verified:
        _write_passing_json(rows, output_dir, cif_dir, query_params or {}, cppred_mode=cppred_mode)

    print(f"\n[output] 写入完成：{output_dir}")
    print(f"  - candidates.json  ({len(rows)} 条候选)")
    print(f"  - cifs/            ({len(list(cif_dir.glob('*.cif')))} CIF 文件)")
    print(f"  - summary.json")
    print(f"  - report.md")
    if cppred_verified:
        print(f"  - passing_candidates.json   (通过全部约束的候选 + base64 CIF)")


# ──────────────────────────────────────────────────────────────────────────────
# 结构化 JSON 输出：通过全部约束的候选 + 直接可用的 evolution seeds
# ──────────────────────────────────────────────────────────────────────────────

import base64 as _base64  # noqa: E402


def _check_constraint(value: Any, spec: dict[str, Any]) -> bool:
    """通用约束检查。spec 形如 {"min": x, "max": y} / {"max": z} / {"min": a}。"""
    if value is None:
        return False
    if "min" in spec and value < spec["min"]:
        return False
    if "max" in spec and value > spec["max"]:
        return False
    return True


def _write_passing_json(
    rows: list[dict[str, Any]],
    output_dir: Path,
    cif_dir: Path,
    query_params: dict[str, Any],
    *,
    cppred_mode: str = "strict",
) -> None:
    """生成 passing_candidates.json。

    cppred_mode:
      - "strict"   :cppred 全部成功 → 候选必须通过 cppred 约束才进 passing
      - "degraded" :cppred 全部失败 → 候选只需通过 MP 约束即可(cl/f_max/exf 字段为 null)
      - "partial"  :cppred 部分成功 → 成功的走 strict,失败的走 MP-only
    """
    constraints = DEFAULT_CONSTRAINTS
    degraded = cppred_mode != "strict"

    passing: list[dict[str, Any]] = []
    for r in rows:
        fv = r.get("cppred_verification", {})
        if not isinstance(fv, dict):
            continue
        fv_status = fv.get("status")

        # 决定该候选是否参与 cppred 约束检查
        cppred_ok = fv_status == "ok"
        # degraded / partial 模式下,cppred 失败的候选仍可进 passing(MP-only)
        if not cppred_ok and not degraded:
            continue
        # degraded 模式下,完全跳过 cppred("skipped"/"no structure"也允许)
        # partial 模式下,只放行 ok 与同批失败(penalty/polluted/failed)的,不放行 "skipped"
        if degraded and fv_status not in ("ok", "penalty", "polluted", "failed", "skipped_unhealthy"):
            # 兜底:无 structure 的 "skipped" 在 degraded 模式也允许(本就只有 MP 数据)
            pass

        props = {
            "bandgap_eV":              r["bandgap_eV"],
            "formation_energy_eV":     r["formation_energy_eV"],
            "ehull_eV":                r["ehull_eV"],
            "num_elements":            r["num_elements"],
            "sg_number":               r["sg_number"],
            "sg_symbol":               r["sg_symbol"],
            "cl_score":                fv.get("cl_score"),
            "f_max_eV_A":              fv.get("f_max_eV_A"),
            "exfoliation_energy_meV":  fv.get("exfoliation_energy_meV"),
            # crystal-property-prediction MCP 交叉验证值（ALIGNN/CHGNet 重算）
            "cppred_bandgap_ev":         fv.get("bandgap_ev_cppred"),
            "cppred_hull_ev":            fv.get("hull_ev_cppred"),
        }

        # MP 约束(始终检查)
        checks = {
            "bandgap_eV":             _check_constraint(props["bandgap_eV"], constraints["bandgap_eV"]),
            "formation_energy_eV":    _check_constraint(props["formation_energy_eV"], constraints["formation_energy_eV"]),
            "ehull_eV":               _check_constraint(props["ehull_eV"], constraints["ehull_eV"]),
            "num_elements":           (constraints["num_elements"]["min"]
                                       <= props["num_elements"]
                                       <= constraints["num_elements"]["max"]),
            "sg_number":              props["sg_number"] not in constraints["sg_number"]["banned"],
            "element_set_banned":     not any(el in r["element_set"]
                                              for el in constraints["element_set"]["banned"]),
        }
        # cppred 约束(仅 strict 候选才检查)
        if cppred_ok:
            checks["cl_score"] = _check_constraint(props["cl_score"], constraints["cl_score"])
            checks["f_max_eV_A"] = _check_constraint(props["f_max_eV_A"], constraints["f_max_eV_A"])
            checks["exfoliation_energy_meV"] = _check_constraint(
                props["exfoliation_energy_meV"], constraints["exfoliation_energy_meV"])
        else:
            # degraded/partial 失败的候选 → cppred 字段为 null,约束检查标 "skipped"
            checks["cl_score"] = None
            checks["f_max_eV_A"] = None
            checks["exfoliation_energy_meV"] = None

        if not all(v is not False for v in checks.values()):
            # False = 明确违反;None = 跳过(degraded 模式);True = 通过
            continue
        # 二次保险:任何 False 都剔除(None 视为通过)
        if any(v is False for v in checks.values()):
            continue

        # 找对应 CIF 文件并 base64 编码（便于直接作为 evolution seed）
        cif_name = f"{r['material_id']}_{r['formula_pretty']}.cif".replace("/", "_")
        cif_path = cif_dir / cif_name
        cif_b64 = ""
        if cif_path.exists():
            cif_b64 = _base64.b64encode(cif_path.read_bytes()).decode("ascii")

        passing.append({
            "mp_id": r["material_id"],
            "formula": r["formula_pretty"],
            "chemsys": _chemsys_key(r),
            "element_set": r["element_set"],
            "properties": props,
            "passes_constraints": checks,
            "cppred_status": fv_status,
            "cif_path": str(cif_path.relative_to(output_dir)) if cif_path.exists() else None,
            "cif_base64": cif_b64,
        })

    # 排序:strict 候选(CL-score 已知)优先;degraded 候选按 ehull 升序
    def _sort_key(p: dict[str, Any]) -> tuple:
        cl = p["properties"].get("cl_score")
        ehull = p["properties"].get("ehull_eV") or 999.0
        if cl is not None:
            return (0, -cl, ehull)  # 高 CL 优先
        return (1, 0.0, ehull)  # degraded 候选排后
    passing.sort(key=_sort_key)

    # 直接可用的 evolution seed 列表（mattergen MCP run_autonomous_evolution 的格式）
    seed_cif_files = [
        {"name": f"{p['mp_id']}_{p['formula']}.cif", "cif_base64": p["cif_base64"]}
        for p in passing if p["cif_base64"]
    ]

    payload = {
        "metadata": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "constraint_set": constraints,
            "query_params": query_params,
            "cppred_mode": cppred_mode,
        },
        "summary": {
            "total_candidates": len(rows),
            "cppred_verified_ok": sum(1 for r in rows
                                    if isinstance(r.get("cppred_verification"), dict)
                                    and r["cppred_verification"].get("status") == "ok"),
            "passing_all_constraints": len(passing),
            "passing_strict": sum(1 for p in passing if p["cppred_status"] == "ok"),
            "passing_degraded": sum(1 for p in passing if p["cppred_status"] != "ok"),
            "unique_chemsys": len({p["chemsys"] for p in passing}),
            "mode": cppred_mode,
            "degraded_note": (
                "Crystal-property-prediction MCP unhealthy or returned PENALTY_VALUES for all candidates. "
                "Passing candidates verified against MP-only constraints (cl_score/f_max/exf=null). "
                "Stage 2 should re-validate these candidates when crystal-property-prediction MCP recovers."
                if degraded else None
            ),
        },
        "passing_candidates": passing,
        "seed_cif_files": seed_cif_files,  # 直接喂给 run_autonomous_evolution(seed_cif_files=...)
    }

    out_path = output_dir / "passing_candidates.json"
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ──────────────────────────────────────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="查询 MP 数据库中满足 DEMO 约束的候选材料",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--max-results", type=int, default=200,
                        help="最大返回数量")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="输出目录（默认 ./mp_candidates_<timestamp>/）")
    parser.add_argument("--banned-elements", type=str, nargs="+",
                        default=DEFAULT_CONSTRAINTS["element_set"]["banned"],
                        help="禁用元素列表")
    parser.add_argument("--keep-radioactive", action="store_true",
                        help="保留锕系/Tc/Pm/Po/At/Rn/Fr/Ra 等稀有元素（默认排除）")
    parser.add_argument("--num-elements", type=int, nargs=2,
                        default=[3, 3], metavar=("MIN", "MAX"),
                        help="元素数量范围（min max）")
    parser.add_argument("--chemsys", type=str, default=None,
                        help="exact chemical system, e.g. Li-Fe-O-P")
    parser.add_argument("--bandgap", type=float, nargs=2,
                        default=[0.2, 3.5], metavar=("MIN", "MAX"),
                        help="带隙范围（eV）")
    parser.add_argument("--formation-energy-max", type=float, default=0.0,
                        help="形成能上限（eV/atom）")
    parser.add_argument("--ehull-max", type=float, default=1.0,
                        help="凸包能上限（eV/atom）")
    parser.add_argument("--banned-sg", type=int, nargs="*", default=[1],
                        help="禁用空间群编号列表")
    parser.add_argument("--max-per-chemsys", type=int, default=1, metavar="N",
                        help="每个化学系统保留前 N 个候选（默认 1，确保化学多样性）")
    parser.add_argument("--pool-size", type=int, default=1000, metavar="N",
                        help="MP 拉取的原始池大小上限（默认 1000）。"
                             "mp_api 客户端不支持服务端排序，MP 默认返回顺序对 Ag/F "
                             "等字母靠前元素有系统性偏置（audit 1.2 D2 / 1.5 U3）。"
                             "扩大池子让后续 rank_by=stability（ehull→formation_energy）"
                             "排序能见到足够多非 Ag/F 候选；最后才截断到 --max-results。")
    parser.add_argument("--rank-by", type=str, default="auto",
                        choices=["auto", "stability", "cl_score"],
                        help="组内排序准则（auto=按是否开启 cppred 验证选）")
    parser.add_argument("--verify-cppred", action="store_true",
                        help="调 crystal-property-prediction MCP 验证 cl_score/f_max/exfoliation（耗时）")
    parser.add_argument("--cppred-mcp-url", type=str,
                        default="http://mcp.example.internal:22222/mcp/flux/mcp",
                        help="crystal-property-prediction MCP 端点 URL")
    args = parser.parse_args()

    _load_env()
    api_key = _get_api_key()
    os.environ["CPPRED_MCP_URL"] = args.cppred_mcp_url

    # 合并稀有元素到 banned_elements（除非 --keep-radioactive）。
    # 注意：MP API 的 exclude_elements URL 参数上限 60 字符，所以稀有元素
    # 在客户端过滤（不传 MP），只把原始 banned（Hg/Tl/Na/K/F）传 MP server。
    server_banned = list(args.banned_elements)
    client_extra_banned: list[str] = []
    if not args.keep_radioactive:
        client_extra_banned = RARE_ELEMENTS
    banned_display = sorted(set(server_banned) | set(client_extra_banned))

    # rank_by 解析：auto = 跟随 verify-cppred
    rank_by = args.rank_by
    if rank_by == "auto":
        rank_by = "cl_score" if args.verify_cppred else "stability"
    elif rank_by == "cl_score" and not args.verify_cppred:
        print("[warn] --rank-by cl_score 需要 --verify-cppred 才有意义，回退为 stability")
        rank_by = "stability"

    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = Path(f"mp_candidates_{ts}")

    print(f"=" * 70)
    print(f"MP 候选材料查询")
    print(f"=" * 70)
    print(f"约束：")
    print(f"  num_elements       = {tuple(args.num_elements)}")
    print(f"  chemsys            = {args.chemsys or 'any'}")
    print(f"  bandgap            ∈ [{args.bandgap[0]}, {args.bandgap[1]}] eV")
    print(f"  formation_energy   ≤ {args.formation_energy_max} eV/atom")
    print(f"  ehull              ≤ {args.ehull_max} eV/atom")
    print(f"  banned_elements    = {banned_display}")
    print(f"    └─ 含稀有/锕系:   {'保留 (--keep-radioactive)' if args.keep_radioactive else '已排除（客户端过滤）'}")
    print(f"  banned_sg          = {args.banned_sg}")
    print(f"  max_per_chemsys    = {args.max_per_chemsys}  (rank_by={rank_by})")
    print(f"  pool_size          = {args.pool_size}  (打散 MP 默认 Ag/F 偏置)")
    print(f"  cppred verification  = {args.verify_cppred}")
    print(f"  output_dir         = {output_dir}")
    print()

    # ── 1. MP 服务端查询 ──
    t0 = time.time()
    print(f"[mp] 提交查询...")
    rows = query_mp(
        api_key,
        bandgap=tuple(args.bandgap),
        formation_energy_max=args.formation_energy_max,
        ehull_max=args.ehull_max,
        num_elements=tuple(args.num_elements),
        banned_elements=server_banned,
        chemsys=args.chemsys,
        pool_size=args.pool_size,
    )
    print(f"[mp] 返回 {len(rows)} 条原始结果（{time.time() - t0:.1f}s）")

    if not rows:
        print("[warn] 未找到候选材料。可尝试放宽 --ehull-max 或 --bandgap 范围，")
        print("       或加 --keep-radioactive 看是否是稀有元素过滤过严。")
        return 1

    # ── 2. 客户端过滤（sg + banned 元素二次校验 + 稀有元素排除）──
    before = len(rows)
    rows = client_filter(
        rows,
        banned_sg=args.banned_sg,
        extra_banned_elements=client_extra_banned,
        required_elements=args.chemsys.split("-") if args.chemsys else None,
    )
    if args.chemsys and not rows:
        print(f"[warn] no candidates matched exact chemsys {args.chemsys}")
        return 1
    print(f"[client] sg/banned/稀有元素 过滤后 {len(rows)} 条（剔除 {before - len(rows)}）")

    # ── 3. 化学系统多样性过滤（在 cppred 验证前，省时间）──
    before = len(rows)
    n_chemsys_before = len({"-".join(sorted(r["element_set"])) for r in rows})
    rows = diversity_filter(
        rows,
        max_per_chemsys=args.max_per_chemsys,
        rank_by=rank_by,
    )
    n_chemsys_after = len({"-".join(sorted(r["element_set"])) for r in rows})
    print(f"[diversity] chemsys 去重后 {len(rows)} 条 "
          f"(覆盖 {n_chemsys_after} 个化学系统，原 {n_chemsys_before} 个)")

    # 多样性过滤后可能仍超过 max_results（每个 chemsys 各贡献 N 个）
    rows = rows[: args.max_results]

    # ── 4. 可选 crystal-property-prediction MCP 验证 ──
    cppred_mode = "strict"
    if args.verify_cppred:
        rows, cppred_mode = verify_with_cppred(rows)
        # 验证后按 cl_score 重排（仅显示用，不改变成员）
        if rank_by == "cl_score" and cppred_mode == "strict":
            rows.sort(key=lambda x: _rank_key(x, "cl_score"))
        elif rank_by == "cl_score" and cppred_mode != "strict":
            print(f"[warn] cppred_mode={cppred_mode},--rank-by cl_score 失效 → 回退 stability 排序")
            rank_by = "stability"
            rows.sort(key=lambda x: _rank_key(x, rank_by))

    # ── 5. 输出 ──
    write_outputs(
        rows, output_dir,
        cppred_verified=args.verify_cppred,
        cppred_mode=cppred_mode,
        query_params={
            "max_results": args.max_results,
            "max_per_chemsys": args.max_per_chemsys,
            "rank_by": rank_by,
            "verify_cppred": args.verify_cppred,
            "keep_radioactive": args.keep_radioactive,
            "banned_elements_user": args.banned_elements,
            "num_elements": args.num_elements,
            "bandgap": args.bandgap,
            "formation_energy_max": args.formation_energy_max,
            "ehull_max": args.ehull_max,
            "pool_size": args.pool_size,
        },
    )

    print()
    print("=" * 70)
    if args.verify_cppred:
        if cppred_mode == "degraded":
            print(f"⚠️  Crystal-property-prediction MCP 降级模式:所有候选 cppred 验证失败(占位/异常)。")
            print(f"   passing_candidates.json 仅含 MP 约束通过者(cl/f_max/exf=null)。")
            print(f"   Stage 2 应在 cppred 恢复后重新验证这些候选。")
        elif cppred_mode == "partial":
            print(f"⚠️  Crystal-property-prediction MCP 部分降级:部分候选 cppred 失败(可能 cell 过大)。")
            print(f"   passing_candidates.json 含 strict + degraded 两类,见 cppred_status 字段。")
        else:
            print(f"✅ Crystal-property-prediction MCP 严格模式:全部候选 cppred 验证成功。")
    print(f"完成。Top {min(10, len(rows))} 候选：")
    print("=" * 70)
    for r in rows[:10]:
        line = (
            f"  {r['material_id']:>12}  {r['formula_pretty']:<20}  "
            f"Eg={r['bandgap_eV']:.3f}  E_form={r['formation_energy_eV']:.3f}  "
            f"ehull={r['ehull_eV']:.3f}  SG={r['sg_number']}"
        )
        if args.verify_cppred and r.get("cppred_verification", {}).get("status") == "ok":
            fv = r["cppred_verification"]
            line += (
                f"  | cppred: CL={fv.get('cl_score'):.3f} "
                f"f_max={fv.get('f_max_eV_A'):.3f} "
                f"exf={fv.get('exfoliation_energy_meV'):.1f}"
            )
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
