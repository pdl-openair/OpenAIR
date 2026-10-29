#!/usr/bin/env python3
"""
从 Materials Project 查询竞争相并生成 VASP 计算输入文件。

用法:
  python setup_competing_phases.py --formula AlInSe3
  python setup_competing_phases.py --formula AlInSe3 --ehull-cutoff 0.05 --encut 450
  python setup_competing_phases.py --formula AlInSe3 --target-poscar /path/to/POSCAR

输出:
  <workdir>/                 工作目录 (默认: competing_phases/)
  ├── submission_info.json   所有相的信息清单
  ├── phase_manifest.json    MP 查询的原始数据
  └── <phase_dir>/           每个相一个目录
      ├── relax/             弛豫计算
      │   ├── POSCAR
      │   ├── INCAR
      │   └── KPOINTS
      └── static/            静态计算
          ├── POSCAR (占位, 弛豫后用 CONTCAR 替换)
          ├── INCAR
          └── KPOINTS

注意: 本脚本只生成 POSCAR/INCAR/KPOINTS. 还需要从 skill 的 scripts/ 目录复制
      generate_all_potcar.sh, submit_all.sh, run_hpc.sh, hpc_results.py 到 <workdir>/ 后运行,
      才能完成 POTCAR 生成、HPC 提交、能量提取、相图分析的完整流程.
"""
import argparse
import json
import os
import sys
import warnings
from pathlib import Path

# 不全局屏蔽警告，避免隐藏下游能量口径检查。

import numpy as np
from pymatgen.core import Structure, Composition
from pymatgen.io.vasp import Kpoints
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer


# ========== 默认参数 ==========
ENCUT_DEFAULT = 450
EHULL_CUTOFF_DEFAULT = 0.05  # eV/atom, 近稳定相阈值
KPPA_RELAX = 3000   # k-points per reciprocal atom for relaxation
KPPA_STATIC = 5000  # k-points per reciprocal atom for static
NCORE_DEFAULT = 4
KPAR_DEFAULT = 2
KPAR_LARGE_SYSTEM = 1  # 原子数 >= 此值时用更小的 KPAR
LARGE_SYSTEM_THRESHOLD = 28


def get_incar_relax(formula: str, natoms: int, encut: int = ENCUT_DEFAULT,
                    ncore: int = NCORE_DEFAULT, kpar: int = None) -> str:
    """生成弛豫 INCAR"""
    if kpar is None:
        kpar = KPAR_LARGE_SYSTEM if natoms >= LARGE_SYSTEM_THRESHOLD else KPAR_DEFAULT
    return f"""SYSTEM = {formula} relax
ENCUT   = {encut}
PREC    = Accurate
EDIFF   = 1E-6
ALGO    = Normal
NELM    = 200
ISMEAR  = 0
SIGMA   = 0.05
LREAL   = A
IBRION  = 2
ISIF    = 3
NSW     = 100
EDIFFG  = -0.01
LCHARG  = .FALSE.
LWAVE   = .FALSE.
NWRITE  = 1
NCORE   = {ncore}
KPAR    = {kpar}
IVDW    = 12
"""


def get_incar_static(formula: str, natoms: int, encut: int = ENCUT_DEFAULT,
                     ncore: int = NCORE_DEFAULT, kpar: int = None) -> str:
    """生成静态计算 INCAR"""
    if kpar is None:
        kpar = KPAR_LARGE_SYSTEM if natoms >= LARGE_SYSTEM_THRESHOLD else KPAR_DEFAULT
    return f"""SYSTEM = {formula} static
ENCUT   = {encut}
PREC    = Accurate
EDIFF   = 1E-7
ALGO    = Normal
NELM    = 200
ISMEAR  = -5
SIGMA   = 0.05
LREAL   = A
NSW     = 0
ISIF    = 2
IBRION  = -1
LCHARG  = .FALSE.
LWAVE   = .FALSE.
NWRITE  = 1
NCORE   = {ncore}
KPAR    = {kpar}
IVDW    = 12
"""


def _query_competing_phases_thermo(elements: list[str], ehull_cutoff: float,
                                   api_key: str = None) -> list[dict]:
    """经 thermo JSON 端点查询（api.materialsproject.org，Cloudflare CDN）。

    服务端已算好 is_stable / energy_above_hull，无需本地建凸包，也不经过
    s3.us-east-1.amazonaws.com 的 delta/parquet 数据面（该通道容器直连会被
    断流，见修复台账 Bug 5）。注意联合 chemsys 查询不含单质参考相，需对
    每个元素单独补查；单质在联合口径下 is_stable 常为 False 但 ehull 极小
    （~0.001-0.01），按 ehull <= cutoff 筛选即可正确纳入。
    """
    from mp_api.client import MPRester

    chemsys = "-".join(sorted(elements))
    systems = [chemsys] + sorted(elements)
    docs = []
    with MPRester(api_key) as mpr:
        for system in systems:
            batch = mpr.thermo.search(
                chemsys=system,
                fields=[
                    "material_id", "formula_pretty", "is_stable",
                    "energy_above_hull", "energy_per_atom", "nsites", "elements",
                ],
            )
            docs.extend(batch or [])

    candidates = []
    for d in docs:
        ehull = d.energy_above_hull
        is_stable = bool(d.is_stable)
        if not is_stable and (ehull is None or ehull > ehull_cutoff):
            continue
        candidates.append({
            "formula": d.formula_pretty,
            "material_id": normalize_material_id(str(d.material_id)),
            "natoms": int(d.nsites or 0),
            "elements": sorted(str(e) for e in (d.elements or [])),
            "energy": float((d.energy_per_atom or 0.0) * (d.nsites or 0)),
            "ehull_mp": float(ehull) if ehull is not None else 0.0,
        })
    return candidates


def _query_competing_phases_entries(elements: list[str], ehull_cutoff: float,
                                    api_key: str = None) -> list[dict]:
    """经 S3 delta 数据面查询（get_entries_in_chemsys + 本地凸包，原实现）。

    保留为兜底：JSON 端点不可用而 S3 可达的环境（如宿主机带代理时）仍可用。
    """
    from mp_api.client import MPRester

    chemsys = "-".join(sorted(elements))
    with MPRester(api_key) as mpr:
        entries = mpr.get_entries_in_chemsys(chemsys)

    from pymatgen.analysis.phase_diagram import PhaseDiagram
    pd = PhaseDiagram(entries)

    stable = []
    near_stable = []

    for entry in pd.stable_entries:
        formula = entry.composition.reduced_formula
        natoms = int(entry.composition.num_atoms)
        elements_set = sorted([str(e) for e in entry.composition.elements])
        mid = normalize_material_id(entry.entry_id)

        stable.append({
            "formula": formula,
            "material_id": mid,
            "natoms": natoms,
            "elements": elements_set,
            "energy": entry.energy,
            "ehull_mp": 0.0,
        })

    for entry in pd.unstable_entries:
        ehull = pd.get_e_above_hull(entry)
        if ehull <= ehull_cutoff:
            formula = entry.composition.reduced_formula
            natoms = int(entry.composition.num_atoms)
            elements_set = sorted([str(e) for e in entry.composition.elements])
            mid = normalize_material_id(entry.entry_id)

            near_stable.append({
                "formula": formula,
                "material_id": mid,
                "natoms": natoms,
                "elements": elements_set,
                "energy": entry.energy,
                "ehull_mp": ehull,
            })

    return stable + near_stable


def query_competing_phases(elements: list[str], ehull_cutoff: float = EHULL_CUTOFF_DEFAULT,
                           api_key: str = None):
    """从 Materials Project 查询竞争相

    返回:
        stable_phases: 凸包上的稳定相列表
        near_stable: ehull <= cutoff 的近稳定相
    """
    chemsys = "-".join(sorted(elements))
    print(f"查询化学体系: {chemsys}")
    print(f"ehull 阈值: {ehull_cutoff} eV/atom")

    try:
        all_phases = _query_competing_phases_thermo(elements, ehull_cutoff, api_key)
        print(f"thermo JSON 端点返回 {len(all_phases)} 个稳定/近稳定相")
    except Exception as e:  # noqa: BLE001
        print(f"thermo JSON 端点失败（{type(e).__name__}: {str(e)[:120]}），回退 S3 delta 通道…")
        all_phases = _query_competing_phases_entries(elements, ehull_cutoff, api_key)

    # 去重 (相同 formula 只保留 ehull 最小的)
    seen = {}
    for phase in all_phases:
        f = phase["formula"]
        if f not in seen:
            seen[f] = phase
        elif phase["ehull_mp"] < seen[f]["ehull_mp"]:
            seen[f] = phase

    all_phases = list(seen.values())
    print(f"\n找到 {len(all_phases)} 个竞争相:")
    for p in all_phases:
        tag = " (STABLE)" if p["ehull_mp"] == 0.0 else f" (ehull={p['ehull_mp']:.3f})"
        print(f"  {p['formula']:15s} {p['natoms']:3d} atoms  {p['material_id']}{tag}")

    return all_phases


def normalize_material_id(entry_id) -> str:
    """解析 ThermoID，并剥离不能用于结构端点的计算方案后缀。"""
    import re
    if isinstance(entry_id, dict):
        identifier = entry_id.get("identifier") or entry_id.get("id") or ""
        suffix = entry_id.get("suffix")
        separator = entry_id.get("separator") or "-"
        value = f"{identifier}{separator}{suffix}" if suffix else str(identifier)
    else:
        identifier = getattr(entry_id, "identifier", None)
        if identifier:
            suffix = getattr(entry_id, "suffix", None)
            separator = getattr(entry_id, "separator", "-")
            value = f"{identifier}{separator}{suffix}" if suffix else str(identifier)
        else:
            value = str(entry_id)
    match = re.fullmatch(r"((?:mp|mvc)-[A-Za-z0-9]+)(?:-(?:GGA(?:\+U)?|r2SCAN|SCAN)(?:[+-].*)?)?", value)
    return match.group(1) if match else value

def download_structure(material_id: str, api_key: str = None) -> Structure:
    """从 MP 下载结构"""
    from mp_api.client import MPRester

    with MPRester(api_key) as mpr:
        struct = mpr.get_structure_by_material_id(normalize_material_id(material_id))
    return struct


def setup_phase_dirs(phases: list[dict], target_formula: str,
                     target_poscar: str = None, workdir: str = "competing_phases",
                     encut: int = ENCUT_DEFAULT, api_key: str = None):
    """为每个相创建计算目录并生成 VASP 输入"""

    workdir_path = Path(workdir)
    workdir_path.mkdir(parents=True, exist_ok=True)

    submission_info = []
    manifest = []

    for phase in phases:
        formula = phase["formula"]
        mid = phase["material_id"]
        natoms = phase["natoms"]
        elements = phase["elements"]
        is_target = (formula == target_formula)

        # 目录名: formula_Nat (如 Al2Se3_10at)
        dirname = f"{formula}_{natoms}at"
        phase_dir = workdir_path / dirname
        relax_dir = phase_dir / "relax"
        static_dir = phase_dir / "static"
        relax_dir.mkdir(parents=True, exist_ok=True)
        static_dir.mkdir(parents=True, exist_ok=True)

        # 下载结构
        if is_target and target_poscar:
            # 使用用户提供的 POSCAR
            struct = Structure.from_file(target_poscar)
            print(f"  {dirname}: 使用用户 POSCAR ({natoms} atoms)")
        else:
            try:
                struct = download_structure(mid, api_key)
                print(f"  {dirname}: 从 MP 下载 ({natoms} atoms)")
            except Exception as e:
                print(f"  {dirname}: MP 下载失败 ({e}), 跳过")
                continue

        # 分析对称性
        try:
            sga = SpacegroupAnalyzer(struct)
            sg_symbol = sga.get_space_group_symbol()
            sg_number = sga.get_space_group_number()
        except Exception:
            sg_symbol = "?"
            sg_number = -1

        # 写 POSCAR
        struct.to(filename=str(relax_dir / "POSCAR"))
        # static POSCAR 先写占位 (弛豫后用 CONTCAR 替换)
        struct.to(filename=str(static_dir / "POSCAR"))

        # 生成 KPOINTS
        kpoints_relax = Kpoints.automatic_density(struct, KPPA_RELAX)
        kpoints_static = Kpoints.automatic_density(struct, KPPA_STATIC)
        kpoints_relax.write_file(str(relax_dir / "KPOINTS"))
        kpoints_static.write_file(str(static_dir / "KPOINTS"))

        # 生成 INCAR
        natoms_actual = len(struct)
        (relax_dir / "INCAR").write_text(get_incar_relax(formula, natoms_actual, encut), encoding="utf-8")
        (static_dir / "INCAR").write_text(get_incar_static(formula, natoms_actual, encut), encoding="utf-8")

        # 获取 kpoint 网格 (用于记录)
        kp_relax = kpoints_relax.kpts[0] if kpoints_relax.kpts else [1, 1, 1]
        kp_static = kpoints_static.kpts[0] if kpoints_static.kpts else [1, 1, 1]

        # 记录信息
        info = {
            "formula": formula,
            "dirname": dirname,
            "natoms": natoms_actual,
            "elements": elements,
            "kpoints_relax": list(kp_relax),
            "kpoints_static": list(kp_static),
            "is_target": is_target,
            "ehull_mp": phase.get("ehull_mp", 0.0),
            "spacegroup": sg_symbol,
            "spacegroup_number": sg_number,
            "material_id": mid,
        }
        submission_info.append(info)
        manifest.append(info)

    # 保存元数据
    with open(workdir_path / "submission_info.json", "w", encoding="utf-8") as f:
        json.dump(submission_info, f, indent=2, ensure_ascii=False)

    with open(workdir_path / "phase_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"\n共 {len(submission_info)} 个相, 目录: {workdir_path}/")
    print(f"  submission_info.json 已生成")
    print(f"\n下一步:")
    print(f"  1. 从 skill scripts/ 拷贝辅助脚本到 {workdir_path}/:")
    print(f"       cp <skill_dir>/scripts/generate_all_potcar.sh {workdir_path}/")
    print(f"       cp <skill_dir>/scripts/submit_all.sh {workdir_path}/")
    print(f"       cp <skill_dir>/scripts/run_hpc.sh {workdir_path}/")
    print(f"       cp <skill_dir>/scripts/hpc_results.py {workdir_path}/")
    print(f"  2. 运行 generate_all_potcar.sh 生成 POTCAR (需 VASP_PSP_DIR)")
    print(f"  3. 运行 submit_all.sh relax 提交弛豫计算 (需 jcc CLI + 凭证环境变量)")

    return submission_info


def main():
    parser = argparse.ArgumentParser(
        description="从 Materials Project 查询竞争相并生成 VASP 输入",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--formula", required=True,
                        help="目标化合物化学式 (如 AlInSe3)")
    parser.add_argument("--target-poscar", type=str, default=None,
                        help="目标化合物的 POSCAR 文件路径 (可选)")
    parser.add_argument("--workdir", type=str, default="competing_phases",
                        help="工作目录 (默认: competing_phases)")
    parser.add_argument("--encut", type=int, default=ENCUT_DEFAULT,
                        help=f"平面波截断能 (默认: {ENCUT_DEFAULT} eV)")
    parser.add_argument("--ehull-cutoff", type=float, default=EHULL_CUTOFF_DEFAULT,
                        help=f"近稳定相 ehull 阈值 (默认: {EHULL_CUTOFF_DEFAULT} eV/atom)")
    parser.add_argument("--api-key", type=str, default=None,
                        help="MP API key (或设置 MP_API_KEY 环境变量)")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("MP_API_KEY")
    if not api_key:
        # 搜索顺序：skill 目录 → scripts 目录 → 向上两级（项目根）
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
        print("  设置 MP_API_KEY 环境变量, 或使用 --api-key 参数")
        sys.exit(1)

    # 解析元素
    comp = Composition(args.formula)
    elements = sorted([str(e) for e in comp.elements])
    print(f"目标化合物: {args.formula}")
    print(f"元素: {elements}")

    # 查询竞争相
    phases = query_competing_phases(elements, args.ehull_cutoff, api_key)

    # 确保目标化合物在列表中
    target_in_list = any(p["formula"] == args.formula for p in phases)
    if not target_in_list and args.target_poscar:
        # 手动添加目标相
        struct = Structure.from_file(args.target_poscar)
        phases.append({
            "formula": args.formula,
            "material_id": "user_provided",
            "natoms": len(struct),
            "elements": elements,
            "energy": 0.0,
            "ehull_mp": None,
        })
        print(f"\n添加用户目标相: {args.formula}")

    # 生成 VASP 输入
    setup_phase_dirs(
        phases=phases,
        target_formula=args.formula,
        target_poscar=args.target_poscar,
        workdir=args.workdir,
        encut=args.encut,
        api_key=api_key,
    )


if __name__ == "__main__":
    main()
