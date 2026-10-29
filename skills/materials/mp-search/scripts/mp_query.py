"""
Materials Project 数据库查询工具

从 .env 读取 MP_API_KEY，提供以下子命令：
  search    — 按条件搜索材料
  info      — 查询单个材料完整物性
  structure — 获取晶体结构
  xrd       — 计算模拟 XRD 衍射图谱
  elements  — 查询元素基本性质
  compare   — 比较多个材料物性

使用方式：
  python3 mp_query.py search --formula "MoS2"
  python3 mp_query.py info --material-id mp-1434
  python3 mp_query.py structure --material-id mp-1434
  python3 mp_query.py xrd --material-id mp-1434
  python3 mp_query.py elements --symbols Mo S
  python3 mp_query.py compare --material-ids mp-1434 mp-1023939
"""

import argparse
import json
import os
import sys
from typing import Optional


# ─────────── 环境初始化 ───────────

def _load_env():
    """从项目根目录 .env 加载环境变量"""
    # 尝试 python-dotenv
    try:
        from dotenv import load_dotenv
        # 从脚本位置向上查找 .env
        script_dir = os.path.dirname(os.path.abspath(__file__))
        for _ in range(5):
            env_path = os.path.join(script_dir, ".env")
            if os.path.exists(env_path):
                load_dotenv(env_path)
                return
            parent = os.path.dirname(script_dir)
            if parent == script_dir:
                break
            script_dir = parent
        load_dotenv()  # fallback
    except ImportError:
        pass  # 手动解析 .env
        script_dir = os.path.dirname(os.path.abspath(__file__))
        for _ in range(5):
            env_path = os.path.join(script_dir, ".env")
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, _, val = line.partition("=")
                            os.environ.setdefault(key.strip(), val.strip())
                return
            parent = os.path.dirname(script_dir)
            if parent == script_dir:
                break
            script_dir = parent


def _get_api_key() -> str:
    """获取 MP_API_KEY"""
    key = os.environ.get("MP_API_KEY", "")
    if not key:
        print(json.dumps({
            "status": "error",
            "message": "MP_API_KEY 未设置。请在项目根目录 .env 中添加 MP_API_KEY=xxx"
        }, ensure_ascii=False, indent=2))
        sys.exit(1)
    return key


def _get_mpr():
    """创建 MPRester 实例"""
    from mp_api.client import MPRester
    return MPRester(_get_api_key())


def _json_output(data: dict):
    """统一 JSON 输出"""
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


# ─────────── search ───────────

def cmd_search(args):
    """按条件搜索材料"""
    kwargs = {}

    if args.formula:
        kwargs["formula"] = args.formula
    if args.chemsys:
        kwargs["chemsys"] = args.chemsys
    if args.elements:
        kwargs["elements"] = args.elements
    if args.crystal_system:
        kwargs["crystal_system"] = args.crystal_system
    if args.num_elements:
        kwargs["num_elements"] = args.num_elements
    if args.num_sites:
        kwargs["num_sites"] = tuple(args.num_sites) if len(args.num_sites) == 2 else args.num_sites[0]

    if args.band_gap_range and len(args.band_gap_range) == 2:
        kwargs["band_gap"] = tuple(args.band_gap_range)
    if args.density_range and len(args.density_range) == 2:
        kwargs["density"] = tuple(args.density_range)
    if args.formation_energy_range and len(args.formation_energy_range) == 2:
        kwargs["formation_energy"] = tuple(args.formation_energy_range)

    if args.stable:
        kwargs["energy_above_hull"] = (0, 0)
        kwargs["is_stable"] = True
    elif args.e_hull_max is not None:
        kwargs["energy_above_hull"] = (None, args.e_hull_max)

    if args.is_metal is not None:
        kwargs["is_metal"] = args.is_metal
    if args.exclude_elements:
        kwargs["exclude_elements"] = args.exclude_elements

    kwargs["fields"] = [
        "material_id", "formula_pretty", "energy_above_hull",
        "band_gap", "density", "formation_energy_per_atom",
        "symmetry", "is_stable", "volume", "nsites",
    ]

    if not kwargs.get("formula") and not kwargs.get("chemsys") and not kwargs.get("elements"):
        _json_output({"status": "error", "message": "必须指定 --formula, --chemsys 或 --elements 之一"})
        return

    try:
        with _get_mpr() as mpr:
            docs = mpr.materials.summary.search(**kwargs)
            results = []
            for d in docs:
                sym = d.symmetry
                results.append({
                    "material_id": str(d.material_id),
                    "formula_pretty": d.formula_pretty,
                    "energy_above_hull": round(d.energy_above_hull, 6) if d.energy_above_hull is not None else None,
                    "band_gap": round(d.band_gap, 4) if d.band_gap is not None else None,
                    "density": round(d.density, 4) if d.density is not None else None,
                    "formation_energy_per_atom": round(d.formation_energy_per_atom, 6) if d.formation_energy_per_atom is not None else None,
                    "crystal_system": str(sym.crystal_system) if sym and sym.crystal_system else None,
                    "spacegroup": sym.symbol if sym else None,
                    "is_stable": d.is_stable,
                    "volume": round(d.volume, 4) if d.volume is not None else None,
                    "nsites": d.nsites,
                })

            query_desc = {k: v for k, v in kwargs.items() if k != "fields"}
            _json_output({
                "status": "ok",
                "command": "search",
                "query": query_desc,
                "count": len(results),
                "results": results,
            })
    except Exception as e:
        _json_output({"status": "error", "message": f"搜索失败: {e}"})


# ─────────── info ───────────

def cmd_info(args):
    """查询单个材料的完整物性"""
    mid = args.material_id
    try:
        with _get_mpr() as mpr:
            docs = mpr.materials.summary._search(
                material_ids=mid,
                fields=[
                    "material_id", "formula_pretty", "energy_above_hull",
                    "band_gap", "density", "formation_energy_per_atom",
                    "symmetry", "is_stable", "volume", "nsites",
                    "total_magnetization", "efermi",
                    "is_gap_direct", "is_metal",
                    "bulk_modulus", "shear_modulus",
                    "universal_anisotropy", "homogeneous_poisson",
                    "energy_per_atom", "theoretical",
                    "ordering",
                ]
            )
            if not docs:
                _json_output({"status": "error", "message": f"未找到材料 {mid}"})
                return

            d = docs[0]
            sym = d.symmetry

            result = {
                "material_id": str(d.material_id),
                "formula_pretty": d.formula_pretty,
                "is_stable": d.is_stable,
                "theoretical": d.theoretical,
                "basic": {
                    "crystal_system": str(sym.crystal_system) if sym and sym.crystal_system else None,
                    "spacegroup_symbol": sym.symbol if sym else None,
                    "spacegroup_number": sym.number if sym else None,
                    "point_group": sym.point_group if sym else None,
                    "density_g_cm3": round(d.density, 4) if d.density is not None else None,
                    "volume_A3": round(d.volume, 4) if d.volume is not None else None,
                    "nsites": d.nsites,
                },
                "thermodynamics": {
                    "formation_energy_per_atom_eV": round(d.formation_energy_per_atom, 6) if d.formation_energy_per_atom is not None else None,
                    "energy_above_hull_eV": round(d.energy_above_hull, 6) if d.energy_above_hull is not None else None,
                    "energy_per_atom_eV": round(d.energy_per_atom, 6) if d.energy_per_atom is not None else None,
                },
                "electronic": {
                    "band_gap_eV": round(d.band_gap, 4) if d.band_gap is not None else None,
                    "is_gap_direct": d.is_gap_direct,
                    "is_metal": d.is_metal,
                    "efermi_eV": round(d.efermi, 4) if d.efermi is not None else None,
                    "total_magnetization": round(d.total_magnetization, 4) if d.total_magnetization is not None else None,
                    "magnetic_ordering": str(d.ordering) if d.ordering else None,
                },
                "elastic": {
                    "bulk_modulus": d.bulk_modulus,
                    "shear_modulus": d.shear_modulus,
                    "universal_anisotropy": round(d.universal_anisotropy, 4) if d.universal_anisotropy is not None else None,
                    "homogeneous_poisson": round(d.homogeneous_poisson, 4) if d.homogeneous_poisson is not None else None,
                },
            }

            _json_output({"status": "ok", "command": "info", "result": result})
    except Exception as e:
        _json_output({"status": "error", "message": f"查询失败: {e}"})


# ─────────── structure ───────────

def cmd_structure(args):
    """获取晶体结构"""
    mid = args.material_id
    try:
        with _get_mpr() as mpr:
            struct = mpr.get_structure_by_material_id(mid)
            if struct is None:
                _json_output({"status": "error", "message": f"未找到材料 {mid}"})
                return

            lattice = struct.lattice
            frac_coords = []
            cart_coords = []
            for i, site in enumerate(struct):
                frac_coords.append({
                    "species": str(site.specie),
                    "x": round(site.frac_coords[0], 6),
                    "y": round(site.frac_coords[1], 6),
                    "z": round(site.frac_coords[2], 6),
                })
                cart_coords.append({
                    "species": str(site.specie),
                    "x": round(site.coords[0], 6),
                    "y": round(site.coords[1], 6),
                    "z": round(site.coords[2], 6),
                })

            # CIF 格式
            cif_str = struct.to(fmt="cif")

            result = {
                "material_id": mid,
                "formula": struct.composition.reduced_formula,
                "lattice": {
                    "a": round(lattice.a, 6),
                    "b": round(lattice.b, 6),
                    "c": round(lattice.c, 6),
                    "alpha": round(lattice.alpha, 4),
                    "beta": round(lattice.beta, 4),
                    "gamma": round(lattice.gamma, 4),
                    "volume": round(lattice.volume, 4),
                },
                "nsites": len(struct),
                "species": sorted(set(str(s) for s in struct.species)),
                "fractional_coords": frac_coords,
                "cartesian_coords": cart_coords,
                "cif": cif_str,
            }

            _json_output({"status": "ok", "command": "structure", "result": result})
    except Exception as e:
        _json_output({"status": "error", "message": f"获取结构失败: {e}"})


# ─────────── xrd ───────────

def cmd_xrd(args):
    """计算模拟 XRD 衍射图谱"""
    mid = args.material_id
    wavelength = args.wavelength or 1.54184  # Cu Kα default
    try:
        with _get_mpr() as mpr:
            struct = mpr.get_structure_by_material_id(mid)
            if struct is None:
                _json_output({"status": "error", "message": f"未找到材料 {mid}"})
                return

            from pymatgen.analysis.diffraction.xrd import XRDCalculator

            xrd_calc = XRDCalculator(wavelength=wavelength)
            pattern = xrd_calc.get_pattern(struct)

            peaks = []
            for i in range(len(pattern.x)):
                two_theta = float(pattern.x[i])
                intensity = float(pattern.y[i])
                hkl_list = pattern.hkls[i] if i < len(pattern.hkls) else []
                d_spacing = float(pattern.d_hkls[i]) if i < len(pattern.d_hkls) else None

                hkl_strs = []
                for h in hkl_list:
                    if isinstance(h, dict) and "hkl" in h:
                        hkl_strs.append(str(h["hkl"]))
                    else:
                        hkl_strs.append(str(h))

                peaks.append({
                    "two_theta": round(two_theta, 4),
                    "intensity": round(intensity, 4),
                    "hkl": hkl_strs,
                    "d_spacing_A": round(d_spacing, 4) if d_spacing else None,
                })

            # 按强度排序找 top 3
            sorted_peaks = sorted(peaks, key=lambda p: p["intensity"], reverse=True)
            top3 = sorted_peaks[:3]

            result = {
                "material_id": mid,
                "formula": struct.composition.reduced_formula,
                "wavelength_A": wavelength,
                "radiation": "Cu Kα" if abs(wavelength - 1.54184) < 0.001
                             else "Mo Kα" if abs(wavelength - 0.71073) < 0.001
                             else f"custom ({wavelength} Å)",
                "num_peaks": len(peaks),
                "top3_peaks": top3,
                "all_peaks": peaks,
            }

            _json_output({"status": "ok", "command": "xrd", "result": result})
    except Exception as e:
        _json_output({"status": "error", "message": f"XRD 计算失败: {e}"})


# ─────────── elements ───────────

def cmd_elements(args):
    """查询元素基本性质"""
    try:
        from pymatgen.core import Element
        results = []
        for sym in args.symbols:
            el = Element(sym)
            result = {
                "symbol": str(el.symbol),
                "name": el.long_name if hasattr(el, "long_name") else str(el),
                "Z": el.Z,
                "atomic_mass": round(float(el.atomic_mass), 4),
                "atomic_radius_pm": round(float(el.atomic_radius), 2) if el.atomic_radius else None,
                "electronegativity": float(el.X) if el.X else None,
                "electron_configuration": str(el.electronic_structure) if hasattr(el, "electronic_structure") else None,
                "row": el.row,
                "group": el.group,
                "is_metal": el.is_metal if hasattr(el, "is_metal") else None,
                "is_transition_metal": el.is_transition_metal if hasattr(el, "is_transition_metal") else None,
                "is_alkali": el.is_alkali if hasattr(el, "is_alkali") else None,
                "is_halogen": el.is_halogen if hasattr(el, "is_halogen") else None,
                "common_oxidation_states": [int(s) for s in el.common_oxidation_states] if el.common_oxidation_states else [],
                "ionic_radii": {str(k): round(float(v), 4) for k, v in el.ionic_radii.items()} if el.ionic_radii else {},
            }
            results.append(result)

        _json_output({"status": "ok", "command": "elements", "count": len(results), "results": results})
    except Exception as e:
        _json_output({"status": "error", "message": f"元素查询失败: {e}"})


# ─────────── compare ───────────

def cmd_compare(args):
    """比较多个材料的物性"""
    mids = args.material_ids
    try:
        with _get_mpr() as mpr:
            docs = mpr.materials.summary._search(
                material_ids=mids,
                fields=[
                    "material_id", "formula_pretty", "energy_above_hull",
                    "band_gap", "density", "formation_energy_per_atom",
                    "symmetry", "is_stable", "volume", "nsites",
                    "is_gap_direct", "is_metal",
                    "bulk_modulus", "shear_modulus",
                ]
            )

            results = []
            for d in docs:
                sym = d.symmetry
                bm = d.bulk_modulus
                sm = d.shear_modulus
                results.append({
                    "material_id": str(d.material_id),
                    "formula_pretty": d.formula_pretty,
                    "is_stable": d.is_stable,
                    "crystal_system": str(sym.crystal_system) if sym and sym.crystal_system else None,
                    "spacegroup": sym.symbol if sym else None,
                    "band_gap_eV": round(d.band_gap, 4) if d.band_gap is not None else None,
                    "is_metal": d.is_metal,
                    "is_gap_direct": d.is_gap_direct,
                    "formation_energy_eV": round(d.formation_energy_per_atom, 6) if d.formation_energy_per_atom is not None else None,
                    "energy_above_hull_eV": round(d.energy_above_hull, 6) if d.energy_above_hull is not None else None,
                    "density_g_cm3": round(d.density, 4) if d.density is not None else None,
                    "volume_A3": round(d.volume, 4) if d.volume is not None else None,
                    "nsites": d.nsites,
                    "bulk_modulus_vrh_GPa": round(bm["vrh"], 2) if bm and "vrh" in bm else None,
                    "shear_modulus_vrh_GPa": round(sm["vrh"], 2) if sm and "vrh" in sm else None,
                })

            _json_output({
                "status": "ok",
                "command": "compare",
                "count": len(results),
                "results": results,
            })
    except Exception as e:
        _json_output({"status": "error", "message": f"比较查询失败: {e}"})


# ─────────── CLI ───────────

def main():
    parser = argparse.ArgumentParser(
        description="Materials Project 数据库查询工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s search --formula "MoS2"
  %(prog)s search --chemsys "Mo-S" --stable
  %(prog)s search --elements Mo S --band-gap-range 1.0 3.0
  %(prog)s info --material-id mp-1434
  %(prog)s structure --material-id mp-1434
  %(prog)s xrd --material-id mp-1434
  %(prog)s xrd --material-id mp-1434 --wavelength 0.71073
  %(prog)s elements --symbols Mo S O
  %(prog)s compare --material-ids mp-1434 mp-1023939
        """,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── search ──
    sp = subparsers.add_parser("search", help="按条件搜索材料")
    sp.add_argument("--formula", help='化学式，如 "MoS2"')
    sp.add_argument("--chemsys", help='化学体系，如 "Mo-S"')
    sp.add_argument("--elements", nargs="+", help='包含的元素，如 Mo S')
    sp.add_argument("--exclude-elements", nargs="+", help='排除的元素')
    sp.add_argument("--crystal-system", help='晶系: cubic, tetragonal, orthorhombic, hexagonal, trigonal, monoclinic, triclinic')
    sp.add_argument("--num-elements", type=int, help='元素个数')
    sp.add_argument("--num-sites", nargs="+", type=int, help='原子位点数（1或2个值）')
    sp.add_argument("--band-gap-range", nargs=2, type=float, metavar=("MIN", "MAX"), help='带隙范围 eV')
    sp.add_argument("--density-range", nargs=2, type=float, metavar=("MIN", "MAX"), help='密度范围 g/cm3')
    sp.add_argument("--formation-energy-range", nargs=2, type=float, metavar=("MIN", "MAX"), help='形成能范围 eV/atom')
    sp.add_argument("--stable", action="store_true", help='仅返回热力学稳定相')
    sp.add_argument("--e-hull-max", type=float, metavar="EV", help='能量凸包上限 eV/atom')
    sp.add_argument("--is-metal", type=lambda x: x.lower() == "true", help='是否金属 (true/false)')

    # ── info ──
    ip = subparsers.add_parser("info", help="查询单个材料完整物性")
    ip.add_argument("--material-id", required=True, help='材料ID，如 mp-1434')

    # ── structure ──
    stp = subparsers.add_parser("structure", help="获取晶体结构")
    stp.add_argument("--material-id", required=True, help='材料ID')

    # ── xrd ──
    xp = subparsers.add_parser("xrd", help="计算模拟 XRD 衍射图谱")
    xp.add_argument("--material-id", required=True, help='材料ID')
    xp.add_argument("--wavelength", type=float, default=None, help='X射线波长 Å（默认 Cu Ka 1.54184）')

    # ── elements ──
    ep = subparsers.add_parser("elements", help="查询元素基本性质")
    ep.add_argument("--symbols", nargs="+", required=True, help='元素符号，如 Mo S')

    # ── compare ──
    cp = subparsers.add_parser("compare", help="比较多个材料物性")
    cp.add_argument("--material-ids", nargs="+", required=True, help='材料ID列表')

    args = parser.parse_args()

    # 加载环境变量
    _load_env()

    commands = {
        "search": cmd_search,
        "info": cmd_info,
        "structure": cmd_structure,
        "xrd": cmd_xrd,
        "elements": cmd_elements,
        "compare": cmd_compare,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
