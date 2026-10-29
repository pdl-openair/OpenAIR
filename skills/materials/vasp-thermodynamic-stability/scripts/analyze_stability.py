#!/usr/bin/env python3
"""
AlInSe3 热力学稳定性分析: 构建凸包相图, 计算 ehull 和形成能

用法:
  python analyze_stability.py                    # 从本地 VASP 结果分析
  python analyze_stability.py --use-mp           # 使用 MP 数据库能量 (快速验证)
  python analyze_stability.py --plot             # 生成相图图片
  python analyze_stability.py --energies-file energies.json
"""
import json
import os
import sys
import argparse
import warnings
from pathlib import Path

# Keep input-contract warnings visible; they can indicate invalid hull energies.

import numpy as np
from pymatgen.core import Structure, Composition
from pymatgen.analysis.phase_diagram import PhaseDiagram, PDPlotter
from pymatgen.entries.computed_entries import ComputedEntry


# ========== 参数 ==========
EHULL_THRESHOLD = 0.05   # eV/atom
EFORM_THRESHOLD = 0.0    # eV/atom


def extract_energy_from_outcar(outcar_path: str) -> float | None:
    """从 OUTCAR 提取最终 energy(sigma->0)"""
    energy = None
    with open(outcar_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if "energy(sigma->0)" in line:
                parts = line.split()
                energy = float(parts[-1])
    return energy


def load_local_energies(energies_file: str) -> dict:
    """加载本地 VASP 计算的能量结果"""
    if not os.path.exists(energies_file):
        print(f"ERROR: 能量文件不存在: {energies_file}")
        print("请先运行 submit_all.sh energies 提取能量")
        sys.exit(1)

    with open(energies_file, encoding="utf-8-sig") as f:
        energies = json.load(f)

    if not isinstance(energies, dict):
        raise ValueError("energies.json must map phase dirname to energy records")
    print(f"加载能量数据: {len(energies)} 个相")
    for dirname, record in energies.items():
        print(f"  {dirname:25s} -> {record}")

    return energies


def build_entries_from_local(energies: dict, submission_info_file: str,
                             energy_basis: str = "formula_unit") -> list:
    """能量转为每个 manifest formula 单元；旧标量默认 formula_unit，绝非晶胞总能。

    显式记录: {"energy_eV": -40.0, "basis": "cell", "natoms": 8}。
    cell 记录必须携带实际计算晶胞原子数，不能从目录名猜测。
    """
    with open(submission_info_file, encoding="utf-8-sig") as f:
        sub_info = json.load(f)
    dir_info = {info["dirname"]: info for info in sub_info}
    missing = set(dir_info) - set(energies)
    if missing:
        raise ValueError(f"缺少竞争相能量，拒绝构建不完整凸包: {sorted(missing)}")

    entries = []
    for dirname, record in energies.items():
        if dirname not in dir_info:
            raise ValueError(f"{dirname} 不在 submission_info.json 中")
        info = dir_info[dirname]
        # 使用 manifest 中的化学式，保留 O2 等 pymatgen 约简式约定。
        comp = Composition(info["formula"])
        if isinstance(record, dict):
            energy = record.get("energy_eV")
            basis = record.get("basis")
            natoms = record.get("natoms")
        else:
            energy, basis, natoms = record, energy_basis, info["natoms"]
            warnings.warn(
                f"{dirname}: 旧标量能量按 {basis} 解释；请核实口径，推荐带 basis 的记录。",
                UserWarning,
            )
        if isinstance(energy, bool) or not isinstance(energy, (int, float)) or not np.isfinite(energy):
            raise ValueError(f"{dirname}: energy_eV 必须为有限数值")
        if basis == "cell":
            if (isinstance(natoms, bool) or not isinstance(natoms, (int, float))
                    or not np.isfinite(natoms) or natoms <= 0 or natoms != int(natoms)):
                raise ValueError(f"{dirname}: cell 能量需要实际晶胞 natoms 正整数")
            factor = natoms / comp.num_atoms
            if factor < 1 or not np.isclose(factor, round(factor)):
                raise ValueError(f"{dirname}: natoms 不是 formula 原子数的整数倍")
            energy /= factor
        elif basis == "atom":
            energy *= comp.num_atoms
        elif basis != "formula_unit":
            raise ValueError(f"{dirname}: basis 必须是 formula_unit / cell / atom")
        if abs(energy / comp.num_atoms) > 30:
            warnings.warn(f"{dirname}: |E/atom| > 30 eV，请核查能量口径/赝势；此告警不是物理判据")
        entries.append(ComputedEntry(
            composition=comp, energy=energy, entry_id=dirname,
            parameters={"natoms": info["natoms"], "energy_basis": "formula_unit"},
        ))
    return entries


def build_entries_from_mp(api_key: str, target_formula: str = None) -> list:
    """从 MP 数据库获取能量 (快速验证用)

    Args:
        api_key: MP API key
        target_formula: 目标化合物化学式 (如 AlInSe3); 用于推导 chemsys.
                       若为 None 则报错退出.
    """
    from mp_api.client import MPRester

    if target_formula is None:
        print("ERROR: 使用 --use-mp 时必须通过 --target-formula 指定化学式")
        sys.exit(1)

    comp = Composition(target_formula)
    elements = sorted([str(e) for e in comp.elements])
    chemsys = "-".join(elements)
    print(f"查询化学体系: {chemsys}")

    with MPRester(api_key) as mpr:
        entries = mpr.get_entries_in_chemsys(chemsys)

    print(f"从 MP 加载 {len(entries)} 个 entries")
    return entries


def analyze_stability(entries: list, target_formula: str = None,
                      base_dir: str = ".") -> dict | None:
    """构建相图并分析目标化合物的稳定性"""

    if target_formula is None:
        # 自动检测: 取 is_target=True 的相
        info_file = os.path.join(base_dir, "submission_info.json")
        if os.path.exists(info_file):
            with open(info_file, encoding="utf-8-sig") as f:
                info = json.load(f)
            for item in info:
                if item.get("is_target"):
                    target_formula = item["formula"]
                    break

    if target_formula is None:
        print("ERROR: 未指定目标化合物, 请使用 --target-formula")
        return None

    print(f"\n{'='*60}")
    print(f"  构建相图, 分析 {target_formula} 的稳定性")
    print(f"{'='*60}")

    pd = PhaseDiagram(entries)

    # 输出凸包上的稳定相
    print(f"\n凸包上的稳定相 (ehull = 0):")
    print(f"  {'Formula':12s} {'Entry ID':25s} {'E/atom (eV)':>12s}")
    print(f"  {'-'*12} {'-'*25} {'-'*12}")

    for entry in pd.stable_entries:
        formula = entry.composition.reduced_formula
        eid = str(entry.entry_id)
        e_per_atom = entry.energy_per_atom
        print(f"  {formula:12s} {eid:25s} {e_per_atom:12.6f}")

    # 查找目标相
    target_entries = [
        e for e in entries
        if e.composition.reduced_formula == target_formula
    ]

    if not target_entries:
        print(f"\nERROR: 未找到 {target_formula} 的 entry")
        return None

    print(f"\n{'='*60}")
    print(f"  {target_formula} 稳定性分析")
    print(f"{'='*60}")

    for target_entry in target_entries:
        eid = str(target_entry.entry_id)
        ehull = pd.get_e_above_hull(target_entry)
        e_per_atom = target_entry.energy_per_atom

        # 形成能
        eform = pd.get_form_energy(target_entry) / target_entry.composition.num_atoms

        # 分解产物
        decomp = pd.get_decomposition(target_entry.composition)

        print(f"\n  Entry: {eid}")
        print(f"  Total energy:       {target_entry.energy:.6f} eV")
        print(f"  Energy/atom:        {e_per_atom:.6f} eV/atom")
        print(f"  Formation energy:   {eform:.6f} eV/atom")
        print(f"  Energy above hull:  {ehull:.6f} eV/atom")
        print(f"")
        print(f"  验收标准:")
        print(f"    E_f <= {EFORM_THRESHOLD:.2f} eV/atom:  "
              f"{'PASS' if eform <= EFORM_THRESHOLD else 'FAIL'} "
              f"({eform:.6f})")
        print(f"    e_hull <= {EHULL_THRESHOLD:.2f} eV/atom: "
              f"{'PASS' if ehull <= EHULL_THRESHOLD else 'FAIL'} "
              f"({ehull:.6f})")
        print(f"")

        if decomp:
            print(f"  分解产物:")
            for product, fraction in decomp.items():
                print(f"    {product.composition.reduced_formula:12s} "
                      f"({fraction*100:.1f}%)")

        # 稳定性判定
        if ehull < 0.001:
            status = "THERMODYNAMICALLY STABLE (在凸包上)"
        elif ehull <= 0.05:
            status = "METASTABLE (可合成亚稳态)"
        elif ehull <= 0.1:
            status = "MODERATELY METASTABLE"
        else:
            status = "UNSTABLE"

        print(f"\n  结论: {status}")

        return {
            "formula": target_formula,
            "entry_id": eid,
            "total_energy": target_entry.energy,
            "energy_per_atom": e_per_atom,
            "formation_energy": eform,
            "ehull": ehull,
            "decomposition": {
                str(k.composition): v for k, v in decomp.items()
            },
            "status": status,
            "pass_formation": eform <= EFORM_THRESHOLD,
            "pass_hull": ehull <= EHULL_THRESHOLD,
        }


def plot_phase_diagram(entries: list, output_path: str = None):
    """生成相图"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        pd = PhaseDiagram(entries)

        if output_path is None:
            output_path = "phase_diagram.png"

        try:
            plotter = PDPlotter(pd)
            plotter.write_image(output_path, "png")
            print(f"\n相图保存到: {output_path}")
        except Exception:
            # 手动绘制简图
            fig, ax = plt.subplots(1, 1, figsize=(8, 7))
            for entry in pd.stable_entries:
                comp = entry.composition
                formula = comp.reduced_formula
                e = entry.energy_per_atom
                x_se = comp.get_atomic_fraction("Se") if "Se" in comp else 0
                x_al = comp.get_atomic_fraction("Al") if "Al" in comp else 0
                ax.scatter(x_se, x_al, s=100, c="blue", zorder=5)
                ax.annotate(formula, (x_se, x_al),
                           textcoords="offset points",
                           xytext=(5, 5), fontsize=9)
            ax.set_xlabel("Se fraction")
            ax.set_ylabel("Al fraction")
            ax.set_title("Phase Diagram (Stable Phases)")
            ax.set_xlim(-0.05, 1.05)
            ax.set_ylim(-0.05, 1.05)
            plt.tight_layout()
            plt.savefig(output_path, dpi=150)
            plt.close()
            print(f"\n简图保存到: {output_path}")
    except ImportError:
        print("matplotlib 未安装, 跳过绘图")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="热力学稳定性分析")
    parser.add_argument("--use-mp", action="store_true",
                        help="使用 MP 数据库能量 (快速验证)")
    parser.add_argument("--plot", action="store_true",
                        help="生成相图图片")
    parser.add_argument("--target-formula", type=str, default=None,
                        help="目标化合物化学式")
    parser.add_argument("--energies-file", type=str, default=None,
                        help="能量 JSON；旧标量默认每 formula 单元 eV，不是晶胞总能")
    parser.add_argument("--energy-basis", choices=("formula_unit", "cell", "atom"),
                        default="formula_unit", help="仅用于旧标量；cell 按 manifest natoms 换算")
    parser.add_argument("--api-key", type=str, default=None,
                        help="MP API key")
    parser.add_argument("--workdir", type=str, default=".",
                        help="工作目录")
    args = parser.parse_args()

    if args.use_mp:
        api_key = args.api_key or os.environ.get("MP_API_KEY")
        if not api_key:
            # 搜索顺序：skill 目录 → scripts 目录 → CWD 及其上级（项目根）
            _this = Path(__file__).resolve().parent
            env_candidates = [
                _this.parent / ".env",   # skill 自身
                _this / ".env",          # scripts 内
                Path("..") / ".env",
                Path(".") / ".env",
            ]
            for env_file in env_candidates:
                if env_file.exists():
                    with open(env_file, encoding="utf-8-sig") as f:
                        for line in f:
                            if line.startswith("MP_API_KEY="):
                                api_key = line.strip().split("=", 1)[1]
                                break
                if api_key:
                    break
        if not api_key:
            print("ERROR: 需要 MP API key")
            sys.exit(1)
        entries = build_entries_from_mp(api_key, args.target_formula)
    else:
        energies_file = args.energies_file or os.path.join(args.workdir, "energies.json")
        energies = load_local_energies(energies_file)
        sub_info = os.path.join(args.workdir, "submission_info.json")
        entries = build_entries_from_local(energies, sub_info, args.energy_basis)

    if not entries:
        print("ERROR: 没有有效的 entries")
        sys.exit(1)

    result = analyze_stability(entries, args.target_formula, args.workdir)

    if result:
        result_path = os.path.join(args.workdir, "stability_result.json")
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str, ensure_ascii=False)
        print(f"\n结果保存到: {result_path}")

    if args.plot:
        plot_phase_diagram(entries)


if __name__ == "__main__":
    main()
