#!/usr/bin/env python3
"""collect_evidence.py — 扫描研究目录，产出证据清单 evidence_manifest.json。

用法:
    python collect_evidence.py --research-dir openair/<rn> [--out report/evidence_manifest.json]

按 experiment-report 的 section↔来源映射表逐项扫描，抽取关键数值
（bandgap/ehull/formation_energy/代数/Pareto 规模/leaderboard 行/csv 行数等），
标注 found|missing|empty。missing 列表供报告 §9（缺失环节声明）直接消费。
仅标准库；显式 utf-8；本脚本只读，不修改任何上游产物。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(p: Path):
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeError, OSError) as e:
        print(f"[evidence] 无法读取 UTF-8 JSON: {p}: {e}", file=sys.stderr)
        return {"_error": str(e)}


def read_csv_rows(p: Path) -> list[dict]:
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(
            line for line in f if not line.lstrip().startswith("#")
        )
        return [r for r in reader if any((v or "").strip() for v in r.values())]


class Manifest:
    def __init__(self, rd: Path):
        self.rd = rd
        self.artifacts: dict = {}
        self.missing: list = []

    def add(self, key: str, path: Path, extracted: dict | None = None,
            optional: bool = False, rel: str | None = None):
        rel = rel or path.relative_to(self.rd).as_posix()
        if not path.exists():
            status = "missing"
            extracted = {}
        else:
            status = "found"
            extracted = extracted or {}
        self.artifacts[key] = {"path": rel, "status": status, "extracted": extracted}
        if status == "missing" and not optional:
            self.missing.append({"key": key, "reason": f"文件不存在: {rel}"})
        return status == "found"

    def add_text(self, key: str, path: Path, optional=False):
        if path.exists():
            n = len(path.read_text(encoding="utf-8", errors="replace").strip())
            self.add(key, path, {"chars": n}, optional=optional)
            if n == 0:
                self.artifacts[key]["status"] = "empty"
        else:
            self.add(key, path, optional=optional)

    def add_glob(self, key: str, pattern: str, optional=False, count_only=True):
        hits = sorted(self.rd.glob(pattern))
        names = [h.relative_to(self.rd).as_posix() for h in hits]
        self.artifacts[key] = {
            "path": pattern,
            "status": "found" if hits else "missing",
            "extracted": {"count": len(names), "items": names if not count_only else names[:20]},
        }
        if not hits and not optional:
            self.missing.append({"key": key, "reason": f"无匹配: {pattern}"})
        return hits


def build(rd: Path) -> dict:
    m = Manifest(rd)

    # 根契约
    m.add_text("goal", rd / "goal.yaml")
    state = read_json(rd / "pipeline_state.json")
    if isinstance(state, dict):
        cands = state.get("candidates", [])
        m.add("state", rd / "pipeline_state.json", {
            "current_stage": state.get("current_stage"),
            "n_candidates": len(cands),
            "candidates": [
                {"candidate_id": c.get("candidate_id"), "formula": c.get("formula")}
                for c in cands
            ],
        })
    else:
        m.add("state", rd / "pipeline_state.json",
              {"note": "缺失或不可解析，报告将退化为目录扫描"})
        cands = []
    # 候选兜底：dft/<id>/ 目录
    dft_ids = sorted(
        d.name for d in (rd / "dft").iterdir()
        if d.is_dir() and d.name.startswith("C")
    ) if (rd / "dft").exists() else []
    if not cands and dft_ids:
        m.artifacts["state"]["extracted"]["fallback_dft_ids"] = dft_ids
        cands = [{"candidate_id": i} for i in dft_ids]

    # Phase 1
    mp = rd / "proposal" / "mp_search_report.md"
    if mp.exists():
        m.add_text("mp_report", mp)
    else:
        deg = rd / "proposal" / "degraded_proposal.md"
        m.add_text("mp_report", mp, optional=True)
        if deg.exists():
            m.add_text("mp_report_degraded", deg)
            m.artifacts["mp_report"]["extracted"]["note"] = "MP 检索降级，见 degraded_proposal.md"
    m.add_glob("seed_cifs", "design/seeds/*.cif", optional=True)
    m.add_glob("generation_pool", "design/generation/*/*.cif", optional=True)

    # Phase 2
    cfg = read_json(rd / "design" / "evo" / "config.json")
    m.add("evo_config", rd / "design" / "evo" / "config.json",
          {k: cfg.get(k) for k in ("objectives", "fitness_backend", "pop_size",
                                   "max_generations", "noise_level", "crossover_rate")}
          if isinstance(cfg, dict) else {}, optional=True)
    snap = read_json(rd / "design" / "evo" / "snapshot_latest.json")
    m.add("evo_snapshot", rd / "design" / "evo" / "snapshot_latest.json",
          {k: snap.get(k) for k in ("generation", "pareto_front_size", "n_valid",
                                    "pop_size", "ehull_median", "summary")}
          if isinstance(snap, dict) else {}, optional=True)
    lb = read_csv_rows(rd / "design" / "evo" / "leaderboard.csv")
    m.add("evo_leaderboard", rd / "design" / "evo" / "leaderboard.csv",
          {"rows": len(lb), "columns": list(lb[0].keys()) if lb else []}, optional=True)
    m.add("evo_evaluate", rd / "design" / "evo" / "evaluate_report.json", optional=True)
    m.add_text("steer_log", rd / "design" / "evo" / "steer_log.jsonl", optional=True)
    m.add_glob("pareto_cifs", "design/evo/pareto/*.cif", optional=True, count_only=False)
    m.add_text("alt_ledger", rd / "design" / "evo" / "evo-alternative" / "ledger_alt.csv",
               optional=True)
    m.add_glob("candidate_cifs", "design/candidates/*.cif", optional=True, count_only=False)

    # Phase 2.2 合成
    adv_dirs = sorted(
        d.name for d in (rd / "synthesis-advisor").glob("*_20*")
        if (d / "assessment_report.md").exists()
    ) if (rd / "synthesis-advisor").exists() else []
    m.artifacts["assessment_reports"] = {
        "path": "synthesis-advisor/*/assessment_report.md",
        "status": "found" if adv_dirs else "missing",
        "extracted": {"dirs": adv_dirs},
    }
    if not adv_dirs:
        m.missing.append({"key": "assessment_reports",
                          "reason": "无 synthesis-advisor/<式>_<日期>/assessment_report.md"})
    recipes = sorted((rd / "recommend_parameter" / "data" / "recommand_recipe").glob("recommend_output_*.jsonl")) \
        if (rd / "recommend_parameter").exists() else []
    m.artifacts["recipes"] = {
        "path": "recommend_parameter/data/recommand_recipe/recommend_output_*.jsonl",
        "status": "found" if recipes else "missing",
        "extracted": {
            "files": [r.relative_to(rd).as_posix() for r in recipes],
            "line_counts": {r.name: sum(1 for _ in r.open(encoding="utf-8")) for r in recipes},
        },
    }
    if not recipes:
        m.missing.append({"key": "recipes", "reason": "无 recommend_output_*.jsonl"})

    # Phase 3 DFT（逐候选）
    dft_extract: dict = {}
    for c in cands:
        cid = c.get("candidate_id", "")
        if not cid:
            continue
        entry: dict = {}
        bg = read_json(rd / "dft" / cid / "band_gap" / "final" / "band_gap.json")
        if isinstance(bg, dict) and "_error" not in bg:
            entry["band_gap"] = bg  # 最小格式 {"<NAME>": gap}，原样保留
        st = read_json(rd / "dft" / cid / "stability" / "stability_result.json")
        if isinstance(st, dict) and "_error" not in st:
            entry["stability"] = {k: st.get(k) for k in (
                "formula", "formation_energy", "ehull", "status",
                "pass_formation", "pass_hull")}
        pd_png = rd / "dft" / cid / "stability" / "phase_diagram.png"
        if pd_png.exists():
            entry["phase_diagram"] = pd_png.relative_to(rd).as_posix()
        if entry:  # 无任何 DFT 产物的候选不计入 found
            dft_extract[cid] = entry
    m.artifacts["dft_results"] = {
        "path": "dft/<candidate_id>/{band_gap/final/band_gap.json, stability/stability_result.json}",
        "status": "found" if dft_extract else "missing",
        "extracted": dft_extract,
    }
    if not dft_extract:
        m.missing.append({"key": "dft_results", "reason": "无任何 dft/<id>/ 结果"})

    # Phase 3w 湿实验
    led = read_csv_rows(rd / "wetlab" / "sample_ledger.csv")
    m.add("wetlab_ledger", rd / "wetlab" / "sample_ledger.csv",
          {"rows": len(led),
           "outcome_counts": {
               o: sum(1 for r in led if r.get("outcome") == o)
               for o in ("success", "partial", "failed", "pending")}}, optional=True)
    res = read_csv_rows(rd / "wetlab" / "characterization_results.csv")
    m.add("wetlab_results", rd / "wetlab" / "characterization_results.csv",
          {"rows": len(res)}, optional=True)
    comps = sorted((rd / "wetlab" / "comparison").glob("comparison_*.md")) \
        if (rd / "wetlab" / "comparison").exists() else []
    m.artifacts["wetlab_comparison"] = {
        "path": "wetlab/comparison/comparison_*.md",
        "status": "found" if comps else "missing",
        "extracted": {"files": [p.relative_to(rd).as_posix() for p in comps]},
    }

    return {
        "generated_at": now(),
        "research_dir": rd.as_posix(),
        "artifacts": m.artifacts,
        "missing": m.missing,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--research-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    rd = args.research_dir
    if not rd.is_dir():
        print(f"[evidence] 研究目录不存在: {rd}")
        return 1
    manifest = build(rd)
    out = args.out or (rd / "report" / "evidence_manifest.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    n_found = sum(1 for a in manifest["artifacts"].values() if a["status"] == "found")
    print(f"[evidence] 写出 {out}")
    print(f"[evidence] artifacts {len(manifest['artifacts'])} 项（found {n_found}），"
          f"必选项缺失 {len(manifest['missing'])} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
