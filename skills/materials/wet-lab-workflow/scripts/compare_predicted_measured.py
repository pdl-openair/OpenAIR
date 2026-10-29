#!/usr/bin/env python3
"""compare_predicted_measured.py — 预测(ML/DFT) vs 实测 三方对比报告生成器。

用法:
    python compare_predicted_measured.py --research-dir openair/<rn> [--tol 0.25]
        [--out wetlab/comparison/comparison_<date>.md]

输入（全部可选，缺什么标什么，绝不编数）:
    <rd>/pipeline_state.json                  candidates[]（ml_metrics / dft）
    <rd>/wetlab/sample_ledger.csv             样品台账
    <rd>/wetlab/characterization_results.csv  表征回收表
    兜底直读: dft/<id>/band_gap/final/band_gap.json（最小格式 {"<NAME>": gap}）
              dft/<id>/stability/stability_result.json（formation_energy / ehull）

输出 markdown 三表: 总览 / 三方对比 / 缺数据清单。
仅用标准库; 所有读入显式 utf-8(-sig)，容忍 BOM 与 '#' 注释行。
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from datetime import date
from pathlib import Path


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(
            line for line in f if not line.lstrip().startswith("#")
        )
        return [r for r in reader if any((v or "").strip() for v in r.values())]


def load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def load_candidates(rd: Path) -> tuple[list[dict], str]:
    """候选来源: state 优先，否则扫 dft/<id>/ 目录。返回 (候选列表, 来源说明)。"""
    state = load_json(rd / "pipeline_state.json")
    if state and state.get("candidates"):
        return state["candidates"], "pipeline_state.json"
    d = rd / "dft"
    if d.exists():
        cands = [
            {"candidate_id": sub.name, "formula": "", "ml_metrics": {}, "dft": {}}
            for sub in sorted(d.iterdir())
            if sub.is_dir() and re.fullmatch(r"C\d+", sub.name)
        ]
        if cands:
            return cands, "dft/ 目录扫描（无 pipeline_state.json）"
    return [], ""


def dft_values(rd: Path, cand: dict) -> dict:
    """取候选的 DFT 数值（state 优先，文件兜底）。返回键: bandgap_eV/ehull/formation_energy + *_src。"""
    out: dict = {}
    cid = cand.get("candidate_id", "")
    d = cand.get("dft") or {}
    if d.get("bandgap_eV") is not None:
        out["bandgap_eV"] = d["bandgap_eV"]
        out["bandgap_src"] = d.get("bandgap_result", "")
    else:
        bg = load_json(rd / "dft" / cid / "band_gap" / "final" / "band_gap.json")
        if isinstance(bg, dict):
            for v in bg.values():
                if isinstance(v, (int, float)):
                    out["bandgap_eV"] = v
                    out["bandgap_src"] = f"dft/{cid}/band_gap/final/band_gap.json"
                    break
    if d.get("ehull") is not None:
        out["ehull"] = d["ehull"]
        out["stability_src"] = d.get("stability_result", "")
    else:
        st = load_json(rd / "dft" / cid / "stability" / "stability_result.json")
        if isinstance(st, dict):
            for k in ("ehull", "formation_energy"):
                if isinstance(st.get(k), (int, float)):
                    out[k] = st[k]
            out["stability_src"] = f"dft/{cid}/stability/stability_result.json"
    return out


def cid_from_sample(sample_id: str) -> str:
    m = re.match(r"S(C\d+)-\d+", (sample_id or "").strip())
    return m.group(1) if m else ""


def collect_measurements(rows: list[dict]) -> dict:
    """(candidate_id, 规范性质) -> [row, ...]；candidate_id 缺失时从 sample_id 推导。"""
    m: dict = {}
    for r in rows:
        prop = (r.get("property") or "").strip()
        if prop not in ("bandgap_eV", "lattice_a", "phase_purity", "space_group"):
            continue
        cid = (r.get("candidate_id") or "").strip() or cid_from_sample(
            r.get("sample_id", "")
        )
        if cid:
            m.setdefault((cid, prop), []).append(r)
    return m


def fmt_exp(rows: list[dict]) -> tuple[str, float | None, int]:
    """实测列: '1.42±0.08 eV (n=2)' 样式；返回 (文本, 均值, n)。"""
    nums = []
    for r in rows:
        v = r.get("value_num")
        try:
            v = float(v)
            nums.append(v)
        except (TypeError, ValueError):
            continue
    n = len(nums)
    if n == 0:
        return ("—", None, 0)
    mean = statistics.mean(nums)
    spread = max(nums) - min(nums)
    unit = (rows[0].get("unit") or "").strip()
    unit = "" if unit in ("–", "-") else f" {unit}"
    if n == 1:
        return (f"{mean:g}{unit} (n=1)", mean, 1)
    return (f"{mean:g}±{spread / 2:g}{unit} (n={n})", mean, n)


MISS = "—"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--research-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--tol", type=float, default=0.25, help="bandgap 相对偏差容限")
    args = ap.parse_args()

    rd = args.research_dir
    ledger = read_csv(rd / "wetlab" / "sample_ledger.csv")
    results = read_csv(rd / "wetlab" / "characterization_results.csv")
    cands, cand_src = load_candidates(rd)
    meas = collect_measurements(results)
    today = date.today().isoformat()

    samples_by_cid: dict[str, list[dict]] = {}
    for r in ledger:
        cid = (r.get("candidate_id") or "").strip() or cid_from_sample(
            r.get("sample_id", "")
        )
        if cid:
            samples_by_cid.setdefault(cid, []).append(r)

    lines: list[str] = []
    lines.append(f"# 预测 vs 实测 三方对比 — {rd.name}（{today}）")
    lines.append("")
    lines.append(f"- 候选来源：{cand_src or '⚠️ 无（既无 state 也无 dft/ 目录）'}")
    lines.append(f"- 输入：sample_ledger.csv {len(ledger)} 行；"
                 f"characterization_results.csv {len(results)} 行；tol={args.tol:.0%}")
    lines.append("")

    # 表1 总览
    lines.append("## 1. 总览")
    lines.append("")
    lines.append("| candidate_id | formula | 样品数 | 表征条数 | 状态 |")
    lines.append("|---|---|---|---|---|")
    for c in cands:
        cid = c["candidate_id"]
        ns, nr = len(samples_by_cid.get(cid, [])), len(
            [1 for (k, _) in meas if k == cid]
        )
        has_gap = bool(meas.get((cid, "bandgap_eV")))
        if ns == 0:
            status = "pending_experiment"
        elif has_gap:
            status = "complete"
        else:
            status = "partial"
        lines.append(f"| {cid} | {c.get('formula') or '?'} | {ns} | {nr} | {status} |")
    if not cands:
        lines.append("| — | — | 0 | 0 | 无候选 |")
    lines.append("")

    # 表2 三方对比
    lines.append("## 2. 三方对比（ML | DFT | 实测）")
    lines.append("")
    lines.append("| candidate_id | formula | 性质 | ML 预测 | DFT | 实测 | Δ(ML,实验) | Δ(DFT,实验) | 判定 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")

    missing: list[tuple[str, str, str]] = []  # (cid, 性质, 缺什么)
    for c in cands:
        cid = c["candidate_id"]
        formula = c.get("formula") or "?"
        ml = c.get("ml_metrics") or {}
        dft = dft_values(rd, c)

        def ml_cell(key):
            v = ml.get(key)
            if isinstance(v, (int, float)):
                return f"{v:g}"
            missing.append((cid, key, "ML"))
            return f"{MISS}(ML未评)"

        # bandgap 三方
        exp_rows = meas.get((cid, "bandgap_eV"), [])
        exp_txt, exp_mean, n = fmt_exp(exp_rows)
        m_txt, d_txt = ml_cell("bandgap"), (
            f"{dft['bandgap_eV']:g}" if "bandgap_eV" in dft else f"{MISS}(DFT未算)"
        )
        if "bandgap_eV" not in dft:
            missing.append((cid, "bandgap_eV", "DFT"))
        dml = ddft = "—"
        verdict = "pending_experiment"
        if exp_mean is not None:
            if isinstance(ml.get("bandgap"), (int, float)) and exp_mean:
                dml = f"{abs(ml['bandgap'] - exp_mean):.3g}"
            if "bandgap_eV" in dft and exp_mean:
                ddft = f"{abs(dft['bandgap_eV'] - exp_mean):.3g}"
            if "bandgap_eV" in dft and exp_mean:
                ok = abs(dft["bandgap_eV"] - exp_mean) / exp_mean <= args.tol
                verdict = "一致" if ok else "偏差>tol"
            else:
                verdict = "无DFT参照"
        lines.append(
            f"| {cid} | {formula} | bandgap (eV) | {m_txt} | {d_txt} | {exp_txt} | {dml} | {ddft} | {verdict} |"
        )

        # ehull 定性
        exp_pp = meas.get((cid, "phase_purity"), [])
        pp_txt = "; ".join(
            sorted({(r.get("value") or "").strip() for r in exp_pp if r.get("value")})
        ) or "—"
        m_txt = ml_cell("ehull")
        d_txt = f"{dft['ehull']:g}" if "ehull" in dft else f"{MISS}(DFT未算)"
        if "ehull" not in dft:
            missing.append((cid, "ehull", "DFT"))
        lines.append(
            f"| {cid} | {formula} | ehull (eV/atom) / 相纯度 | {m_txt} | {d_txt} | {pp_txt} | 定性 | 定性 | {'有相纯度记录' if exp_pp else 'pending_experiment'} |"
        )

        # formation_energy（无实验对应，仅 ML/DFT）
        m_txt = ml_cell("formation_energy")
        d_txt = (
            f"{dft['formation_energy']:g}"
            if "formation_energy" in dft
            else f"{MISS}(DFT未算)"
        )
        if "formation_energy" not in dft:
            missing.append((cid, "formation_energy", "DFT"))
        lines.append(
            f"| {cid} | {formula} | formation_energy (eV/atom) | {m_txt} | {d_txt} | —(无实验对应) | — | — | 记录 |"
        )

        # lattice_a（仅实验）
        la_rows = meas.get((cid, "lattice_a"), [])
        la_txt, _, _ = fmt_exp(la_rows)
        if la_rows:
            lines.append(
                f"| {cid} | {formula} | lattice_a (Å) | —(无ML对应) | —(无DFT对应) | {la_txt} | — | — | 记录 |"
            )
    if not cands:
        lines.append("| — | — | — | — | — | — | — | — | 无候选 |")
    lines.append("")

    # 表3 缺数据清单
    lines.append("## 3. 缺数据清单")
    lines.append("")
    lines.append("| candidate_id | 性质 | 缺失 |")
    lines.append("|---|---|---|")
    if missing:
        for cid, prop, what in missing:
            lines.append(f"| {cid} | {prop} | {what} |")
    else:
        lines.append("| — | — | 无（各来源齐全或候选为空） |")
    lines.append("")

    lines.append("## 4. 下一步建议")
    lines.append("")
    if cands:
        pend = [c["candidate_id"] for c in cands if not samples_by_cid.get(c["candidate_id"])]
        no_gap = [
            c["candidate_id"]
            for c in cands
            if samples_by_cid.get(c["candidate_id"]) and not meas.get((c["candidate_id"], "bandgap_eV"))
        ]
        if pend:
            lines.append(f"- 待开样：{', '.join(pend)}（无样品记录，判定均为 pending_experiment）")
        if no_gap:
            lines.append(f"- 待补测带隙（uv_vis）：{', '.join(no_gap)}")
        if not pend and not no_gap:
            lines.append("- 各候选均有样品与带隙实测，可进入报告汇聚（experiment-report）。")
    else:
        lines.append("- 无候选数据：先跑设计阶段（materials-discovery-pipeline Phase 2）再回来对比。")
    lines.append("")

    out = args.out or (rd / "wetlab" / "comparison" / f"comparison_{today}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[compare] 写出 {out}（候选 {len(cands)}，样品 {len(ledger)}，表征 {len(results)}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
