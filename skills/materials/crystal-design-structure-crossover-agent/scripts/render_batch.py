#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量渲染 CIF 目录或文件列表为 PNG/SVG 图片。
供 materials-discovery-pipeline 在每个生成阶段后调用。

用法：
    # 渲染一个目录下所有 CIF（图片输出到同目录，与 CIF 同名）
    python -I tools/render_batch.py design/generation/20261011_2050/

    # 渲染多个文件，图片集中输出到指定目录
    python -I tools/render_batch.py design/candidates/C001_MgO.cif design/candidates/C002_CaO.cif \
        --out-dir render/candidates/

    # 递归扫描目录，跳过已渲染（有同名 PNG）的 CIF
    python -I tools/render_batch.py design/ --recursive --skip-existing

    # 2x2x2 超胞，输出 SVG
    python -I tools/render_batch.py design/evo/pareto/ --supercell 2 2 2 --fmt svg

退出码：
    0  全部成功
    1  至少一个 CIF 渲染失败（失败列表打印到 stderr）
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# render_cif_3d.py 与本脚本同目录
_TOOLS_DIR = Path(__file__).parent
sys.path.insert(0, str(_TOOLS_DIR))
from render_cif_3d import render  # noqa: E402


def collect_cif_files(targets: list[str], recursive: bool) -> list[Path]:
    """从路径列表中收集 .cif 文件，去重并排序。"""
    found: list[Path] = []
    for t in targets:
        p = Path(t)
        if p.is_file() and p.suffix.lower() == ".cif":
            found.append(p.resolve())
        elif p.is_dir():
            pattern = "**/*.cif" if recursive else "*.cif"
            found.extend(sorted(p.glob(pattern)))
        else:
            print(f"[warn] 跳过（不存在或非 .cif）: {t}", file=sys.stderr)
    # 去重、保序
    seen: set[Path] = set()
    result: list[Path] = []
    for f in found:
        if f not in seen:
            seen.add(f)
            result.append(f)
    return result


def output_path(cif: Path, out_dir: Path | None, fmt: str) -> Path:
    if out_dir is not None:
        return out_dir / (cif.stem + "." + fmt)
    return cif.with_suffix("." + fmt)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="批量把 CIF 渲染为 PNG/SVG（用于 materials-discovery-pipeline）"
    )
    ap.add_argument("targets", nargs="+", help="CIF 文件或目录（可多个）")
    ap.add_argument("--out-dir", default=None,
                    help="图片统一输出目录；不传则与 CIF 同目录同名")
    ap.add_argument("--fmt", default="png", choices=["png", "svg"],
                    help="输出格式（默认 png）")
    ap.add_argument("--supercell", nargs=3, type=int, default=[2, 2, 2],
                    metavar=("NX", "NY", "NZ"),
                    help="超胞重复数（默认 2 2 2）")
    ap.add_argument("--no-bonds", action="store_true",
                    help="不绘制化学键")
    ap.add_argument("--bg", default="white",
                    help="背景色（默认 white）")
    ap.add_argument("--recursive", action="store_true",
                    help="递归扫描目录子目录")
    ap.add_argument("--skip-existing", action="store_true",
                    help="跳过已存在同名图片的 CIF")
    ap.add_argument("--quiet", action="store_true",
                    help="只打印错误，不打印每个 OK 行")
    args = ap.parse_args()

    cif_files = collect_cif_files(args.targets, args.recursive)
    if not cif_files:
        print("[error] 没有找到任何 .cif 文件", file=sys.stderr)
        return 1

    out_dir: Path | None = None
    if args.out_dir:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

    supercell = tuple(args.supercell)
    failures: list[tuple[Path, str]] = []

    for cif in cif_files:
        out = output_path(cif, out_dir, args.fmt)
        if args.skip_existing and out.exists():
            if not args.quiet:
                print(f"[skip] {cif.name} -> {out} (already exists)")
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            render(
                cif_path=str(cif),
                out_path=str(out),
                supercell=supercell,
                title=cif.stem,
                bonds=not args.no_bonds,
                bg=args.bg,
            )
            if not args.quiet:
                print(f"[ok]   {cif.name} -> {out}")
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            print(f"[fail] {cif.name}: {msg}", file=sys.stderr)
            failures.append((cif, msg))

    total = len(cif_files)
    n_fail = len(failures)
    n_ok = total - n_fail
    print(f"\n完成：{n_ok}/{total} 成功，{n_fail} 失败")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
