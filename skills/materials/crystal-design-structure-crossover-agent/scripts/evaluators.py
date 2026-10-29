"""
Fitness-backend registry for crystal-design-structure-crossover-agent.

每代的"个体表征"通过这里注册的 backend 派发。Backend 契约:

    fn(items: list[tuple[cif_text, name]],
       keys: list[str],          # 需要计算的性质键
       eps: dict, tag: str) -> list[dict]

每个返回 dict 必含:
  - 共享结构键(由 structure_meta 在松弛后结构上算出):
      formula / num_atoms / sg_number / element_set / charge_neutral / comp_valid
  - 该 backend 特有的性质键(从 keys 里挑);失败写 None,SAES 当极不可行处理

新增 backend:
  1) 实现 <name>_characterize_batch(items, keys, eps, tag) -> list[dict]
  2) 把可用键加进 BACKEND_KEYS
  3) 注册进 FITNESS_BACKENDS
driver 端不动。

为什么不用 per-objective 的 source 映射 (`{"ehull":{"source":"evaluate"}}`)?
  会让每个体跑多个 MCP,YAGNI for v1。一个 backend = 一次 characterize,
  等价语义将来用"fan-out backend"组合多个子 backend 即可。

mattergen_eval backend 的批量设计(2026-09 起):evaluate_structures MCP 已暴露
per_structure 逐结构明细(novel/stable/energy_above_hull_per_atom 等,与 batch-of-1
数值一致:匹配逐结构 vs 参考集、ehull 用普通 hull,批内互不影响)。这里把整代
relaxed CIFs 分块一次调用,读 per_structure[index] 对齐到个体;老 server 响应无
per_structure 时自动回退 batch-of-1 逐个调用。

unique 保持 ≡1 约定(仅评估调用失败时 None):批内 unique 是"分块去重"语义,
块大小/分块方式不同结果不同 —— 种群级去重由 pilot_analysis 的 distinct 审计
(uniqueness_flags) 负责,sun_count 口径因此与 batch-of-1 时代跨 rep/跨臂可比。
"""
from __future__ import annotations

import os
import sys
from typing import Callable

import numpy as np
from mcp_client import call_tool, call_async_tool
from pymatgen.analysis.structure_matcher import StructureMatcher
from pymatgen.core import Lattice, Structure
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

# alignn-predict MCP 的模型友好名(与 alignn_predict_server.REGISTRY 一致)
ALIGNN_KEYS: set[str] = {
    "formation_energy", "bandgap", "dielectric", "ehull",
    "bulk_modulus", "shear_modulus", "exfoliation_energy",
    "slme", "supercon_tc", "magmom",
}

# mattersim-relax 输出键(MatterSim 弛豫结果直接拿)
MATTERSIM_KEYS: set[str] = {"energy_per_atom", "total_energy", "f_max", "s_max"}

# mattergen-evaluate 输出键 —— 批量调用的 per_structure 明细按个体取值(老 server
# 回退 batch-of-1 时聚合 == 个体,口径一致)。
# SUN = Stable ∩ Unique ∩ Novel;本 backend 把三者各自暴露成可优化的 0/1 键,加上连续
# 的 ehull_eval 作为 SUN 的稳定性梯度(纯 0/1 在 Pareto 排序里太离散,梯度让搜索有方向)。
MATTERGEN_EVAL_KEYS: set[str] = {
    "ehull_eval",        # 连续:energy_above_hull_per_atom(minimize;SUN 稳定性梯度)
    "ehull_sat",         # 连续:clip(ehull_eval,0,0.1)(minimize;镜像 Chemeleon2 R_stability,
                         #        阈值=mSUN stable 判据,凸包以下不再区分防过度优化)
    "novel",             # 0/1:不在 845k 参考集里(maximize)
    "stable",            # 0/1:ehull<阈值(maximize)
    "unique",            # 0/1:批内语义不可用,保持 ≡1 约定(见模块 docstring),种群级
                         #        去重走 pilot_analysis 的 distinct 审计
    "comp_valid_eval",   # 0/1:电荷守恒(smact,与 mattergen-evaluate 一致)
    "structure_valid",   # 0/1:最小间距/无重叠检查通过
    "rmsd_relax",        # 连续:弛豫位移 RMSD(Å),自算 raw vs relaxed(可作 objective/constraint)
}

# 各 backend 暴露给 objectives/constraints 的合法键
BACKEND_KEYS: dict[str, set[str]] = {
    "alignn": ALIGNN_KEYS,
    "mattersim": MATTERSIM_KEYS,
    "mattergen_eval": MATTERGEN_EVAL_KEYS,
}

# 当 objectives/constraints 不引用任何性质键时的默认计算集
DEFAULT_KEYS: dict[str, list[str]] = {
    "alignn": ["formation_energy", "bandgap", "ehull"],
    "mattersim": ["energy_per_atom"],
    "mattergen_eval": ["ehull_eval", "novel", "stable"],
}

# ================ 共享 helpers(被 driver 也复用,放这里避免循环依赖) ================

def cif_to_structure(cif_text: str) -> Structure:
    return Structure.from_str(cif_text, fmt="cif")


def is_charge_neutral(formula: str) -> bool:
    """组成合法性:是否存在一组已知氧化态使净电荷为零。

    优先用 smact(与 mattergen-evaluate 一致);smact 缺失时回退到
    pymatgen 的氧化态猜测;都失败则保守返回 False。
    扩散变异会改变原子数/计量比,常漂移到电荷不守恒的组成,
    本函数用于在选择阶段把它们判为不可行。
    """
    if not formula:
        return False
    try:
        from smact.screening import smact_validity
        return bool(smact_validity(formula))
    except Exception:
        pass
    try:
        from pymatgen.core import Composition
        return bool(Composition(formula).oxi_state_guesses())
    except Exception:
        return False


def structure_meta(st: Structure) -> dict:
    try:
        sg = st.get_space_group_info()[1]
    except Exception:
        sg = None
    formula = st.composition.reduced_formula
    cn = is_charge_neutral(formula)
    return {
        "formula": formula,
        "num_atoms": int(st.num_sites),
        "num_elements": len(st.composition.elements),
        "sg_number": sg,
        "element_set": sorted({str(el) for el in st.composition.elements}),
        "charge_neutral": cn,
        "comp_valid": cn,
    }


def relax_cif(cif_text: str, eps: dict, name: str = "") -> tuple[str, dict | None]:
    """CIF -> mattersim-relax -> (relaxed_cif, relax_result)。

    弛豫失败:返回 (原始 CIF, None),不抛 —— 上层 fallback 用原始结构。
    """
    try:
        r = call_async_tool(
            eps["relax"], "relax_structure_async", "get_relax_result",
            {"cif_content": cif_text, "name": name or "ind"},
            total_wait=int(os.environ.get("RELAX_WAIT", "300")), interval=5.0,
        )
        if isinstance(r, dict) and r.get("value") is None and "error" in r:
            print(f"   [relax] 失败({r['error'][:80]})，用原始结构继续")
            return cif_text, None
        if isinstance(r, dict) and r.get("relaxed_cif"):
            return r["relaxed_cif"], r
        return cif_text, None
    except Exception as e:
        print(f"   [relax] 异常: {e}；用原始结构继续")
        return cif_text, None


# ================ 共享:弛豫位移 RMSD(Å) ================
# 移植自 mattergen.evaluation(utils.utils.compute_rmsd_angstrom +
# structure_matcher.RMSDStructureMatcher):松容差保证结构不完全匹配时
# 也能拿到原子对齐;matcher 的 rmsd 是 (V/atom)^(-1/3) 归一化单位,
# 乘回 (V/atom)^(1/3) 才是 Å。
_MAX_RMSD = 0.5  # matcher 失败时的归一化上限哨兵,与 mattergen.evaluation.globals 一致

_RMSD_MATCHER = StructureMatcher(
    ltol=0.5, stol=_MAX_RMSD, angle_tol=10.0,
    primitive_cell=True, scale=False, attempt_supercell=True, allow_subset=False,
)


def relax_rmsd_angstrom(raw_cif: str, relaxed_cif: str) -> float | None:
    """弛豫位移:原始 CIF 与弛豫后 CIF 的 RMSD(Å)。

    与 mattergen-evaluate 的 rmsd_from_relaxation 同公式,但比较的是
    driver-relax 前后的结构 —— 恢复"原始结构离势阱多远"的本义。
    (evaluate 报的 avg_rmsd_from_relaxation 是在已弛豫结构上的第二次弛豫
    位移,恒 ~0,不能当 fitness 维度。)
    解析失败返回 None,SAES 当极不可行处理。
    """
    try:
        relaxed = cif_to_structure(relaxed_cif)
        raw = cif_to_structure(raw_cif)
        # mattergen 对原始结构做 refine→primitive→LLL 预处理,
        # 缓解弛豫改变晶胞设定导致的匹配失败;失败则退回直接匹配
        try:
            sga = SpacegroupAnalyzer(raw)
            raw = (
                sga.get_refined_structure()
                .get_primitive_structure()
                .get_reduced_structure(reduction_algo="LLL")
            )
        except Exception:
            pass
        match = _RMSD_MATCHER.get_rms_dist(relaxed, raw)
        params = (
            np.array(relaxed.lattice.parameters) + np.array(raw.lattice.parameters)
        ) / 2
        avg_l = Lattice.from_parameters(*params)
        normalization = (len(relaxed) / avg_l.volume) ** (1 / 3)
        if match is None:  # 匹配失败(如组成漂移):返回上限哨兵而非崩溃
            return _MAX_RMSD / normalization
        return match[0] / normalization
    except Exception:
        return None


# ================ 共享:解析失败兜底 ================

def _failed_indiv(cif_text: str, name: str, models: list[str], err: str) -> dict:
    ind = {"cif": cif_text, "name": name or "failed",
           "formula": None, "num_atoms": None, "sg_number": None,
           "element_set": [], "charge_neutral": False, "comp_valid": False,
           "_parse_error": str(err)}
    for m in models:
        ind[m] = None
    return ind


# ================ Backend: alignn(默认,fast/rough) ================

def alignn_characterize_batch(
    items: list[tuple[str, str]],
    keys: list[str],
    eps: dict,
    tag: str = "",
) -> list[dict]:
    """每个体:relax → alignn-predict。Keys = alignn 模型友好名。"""
    out: list[dict] = []
    for i, (cif, name) in enumerate(items):
        print(f"   [{tag}] 表征 {i+1}/{len(items)}  {name}")
        relaxed_cif, _ = relax_cif(cif, eps, name=name or "ind")

        props: dict = {}
        try:
            res = call_tool(
                eps["alignn"], "predict_alignn_properties",
                {"cif_content": relaxed_cif, "models": keys, "name": name or "ind"},
                timeout=180,
            )
            props = res.get("properties", {}) if isinstance(res, dict) else {}
        except Exception as e:
            print(f"   [alignn] 异常: {e}")

        try:
            st = cif_to_structure(relaxed_cif)
        except Exception as e:
            print(f"   [characterize] 结构解析失败: {e}；该个体标记失败")
            out.append(_failed_indiv(relaxed_cif, name, keys, e))
            continue

        ind = structure_meta(st)
        ind["structure_obj"] = st
        ind["cif"] = relaxed_cif
        ind["name"] = name or ind["formula"]
        for m in keys:
            env = props.get(m, {}) if isinstance(props, dict) else {}
            val = env.get("value") if isinstance(env, dict) else None
            ind[m] = val
        out.append(ind)
    return out


# ================ Backend: mattersim(每体能量/受力/应力) ================

# relax 结果字段名 → 我们暴露给 SAES 的键名
_MATTERSIM_FIELD_MAP = {
    "energy_per_atom": "energy_per_atom_eV",
    "total_energy": "total_energy_eV",
    "f_max": "f_max_eV_A",
    "s_max": "s_max_GPa",
}

def mattersim_characterize_batch(
    items: list[tuple[str, str]],
    keys: list[str],
    eps: dict,
    tag: str = "",
) -> list[dict]:
    """每个体:仅 relax —— 直接复用 relax_cif 的能量/受力/应力输出。

    注意 energy_per_atom 与组成强相关,作纯 objective 时优先级无意义
    (小原子/少原子组合天然低能量)。务必配合 element_set/sg_number 约束,
    或用作 constraint 而非 objective。见 references/property-keys.md。
    """
    out: list[dict] = []
    for i, (cif, name) in enumerate(items):
        print(f"   [{tag}] 表征(mattersim-only) {i+1}/{len(items)}  {name}")
        relaxed_cif, r = relax_cif(cif, eps, name=name or "ind")

        try:
            st = cif_to_structure(relaxed_cif)
        except Exception as e:
            print(f"   [characterize] 结构解析失败: {e}；该个体标记失败")
            out.append(_failed_indiv(relaxed_cif, name, keys, e))
            continue

        ind = structure_meta(st)
        ind["structure_obj"] = st
        ind["cif"] = relaxed_cif
        ind["name"] = name or ind["formula"]
        # r 在 relax 失败时为 None —— 所有性质写 None,SAES 当极不可行
        for k in keys:
            src_field = _MATTERSIM_FIELD_MAP.get(k, k)
            ind[k] = (r.get(src_field) if isinstance(r, dict) else None)
        out.append(ind)
    return out


# ================ Backend: mattergen_eval(SUN 准确,批量化) ================

# 批量调用分块大小(relax 的 BatchRelaxer 常驻 GPU,块太大有显存压力;
# V100-16GB 上 20 个 ~10-30 原子结构实测安全)
_EVAL_BATCH_SIZE_ENV = "EVALUATE_BATCH_SIZE"

# per_structure 行的 evaluate 键取值(数值口径与 batch-of-1 聚合一致)
def _eval_flag(x) -> float | None:
    """0/1 列 -> 1.0/0.0;行缺失(None)保持 None,SAES 当极不可行。"""
    return None if x is None else (1.0 if x else 0.0)


def _fill_ind_from_row(ind: dict, row: dict) -> None:
    """server per_structure 行 -> 个体的 evaluate 键。unique 不从行取(≡1 约定)。"""
    ind["ehull_eval"] = row.get("energy_above_hull_per_atom")
    # ehull_sat = clip(ehull, 0, 0.1):0-0.1 带内保留排序梯度,带外平地
    ind["ehull_sat"] = (
        min(max(ind["ehull_eval"], 0.0), 0.1) if ind["ehull_eval"] is not None else None
    )
    ind["novel"] = _eval_flag(row.get("novel"))
    ind["stable"] = _eval_flag(row.get("stable"))
    ind["unique"] = 1.0  # 批内语义不可用,≡1 约定;种群级去重走 pilot_analysis 审计
    ind["comp_valid_eval"] = _eval_flag(row.get("comp_validity"))
    ind["structure_valid"] = _eval_flag(row.get("structure_validity"))


def _evaluate_single_aggregate(relaxed_cif: str, eps: dict) -> dict | None:
    """batch-of-1 调用(老 server 回退路径):聚合 metrics == 个体取值。"""
    try:
        return call_tool(
            eps["evaluate"], "evaluate_structures",
            {"structures": [{"cif_content": relaxed_cif}], "relax": True},
            timeout=int(os.environ.get("EVALUATE_WAIT", "900")),
        )
    except Exception as e:
        print(f"   [evaluate] 异常: {e}")
        return None


def _fill_ind_from_aggregate(ind: dict, res: dict | None) -> None:
    """batch-of-1 聚合结果 -> 个体的 evaluate 键(与旧版逐字一致,含 unique 语义)。"""
    metrics = res.get("metrics", {}) if isinstance(res, dict) else {}
    n_eval = res.get("num_evaluated", 0) if isinstance(res, dict) else 0
    n_fail = res.get("n_failed_jobs", 0) if isinstance(res, dict) else 0
    valid_call = isinstance(res, dict) and n_eval >= 1 and n_fail == 0 and bool(metrics)

    ind["ehull_eval"] = metrics.get("avg_energy_above_hull_per_atom") if valid_call else None
    ind["ehull_sat"] = (
        min(max(ind["ehull_eval"], 0.0), 0.1) if ind["ehull_eval"] is not None else None
    )
    ind["novel"] = 1.0 if (valid_call and metrics.get("frac_novel_structures") == 1.0) else (0.0 if valid_call else None)
    ind["stable"] = 1.0 if (valid_call and metrics.get("frac_stable_structures") == 1.0) else (0.0 if valid_call else None)
    ind["unique"] = 1.0 if (valid_call and metrics.get("frac_unique_structures", 1.0) == 1.0) else (0.0 if valid_call else None)
    ind["comp_valid_eval"] = 1.0 if (valid_call and metrics.get("avg_comp_validity") == 1.0) else (0.0 if valid_call else None)
    ind["structure_valid"] = 1.0 if (valid_call and metrics.get("avg_structure_validity") == 1.0) else (0.0 if valid_call else None)


def mattergen_eval_characterize_batch(
    items: list[tuple[str, str]],
    keys: list[str],
    eps: dict,
    tag: str = "",
) -> list[dict]:
    """每个体:relax(导出用) → 分块批量 evaluate_structures(per_structure 对齐)。

    server 已返回逐结构明细(novel/stable/ehull,与 batch-of-1 数值一致),整代
    relaxed CIFs 按 EVALUATE_BATCH_SIZE(默认 20)分块、每块一次 async 调用,
    读 per_structure[index] 对齐到个体;响应无 per_structure(老 server)自动
    回退 batch-of-1 逐个调用,口径与旧版完全一致。

    unique 恒 1.0(≡1 约定,见模块 docstring);种群级去重由 pilot_analysis 负责。
    keys 默认 ["ehull_eval","novel","stable"] —— ehull_eval 给稳定性梯度,
    novel/stable 是 SUN 的另两维。
    """
    eval_keys = list(MATTERGEN_EVAL_KEYS)

    # 1) 全员 relax + 结构 meta(逐个;evaluate 内部仍 relax=true,两次 relax 与旧协议相同)
    out: list[dict] = []
    for i, (cif, name) in enumerate(items):
        print(f"   [{tag}] 表征(mattergen-eval) {i+1}/{len(items)}  {name}")
        relaxed_cif, relax_res = relax_cif(cif, eps, name=name or "ind")
        try:
            st = cif_to_structure(relaxed_cif)
        except Exception as e:
            print(f"   [characterize] 结构解析失败: {e}；该个体标记失败")
            out.append(_failed_indiv(relaxed_cif, name, keys, e))
            continue
        ind = structure_meta(st)
        ind["structure_obj"] = st
        ind["cif"] = relaxed_cif
        ind["name"] = name or ind["formula"]
        for k in eval_keys:
            ind[k] = None  # 先占位:下面任何路径失败即保持 None,SAES 当极不可行
        # 弛豫位移自算 raw vs relaxed(evaluate 的同名指标在已弛豫结构上恒 ~0);
        # relax 本身失败时无位移可言,置 None 让 SAES 当极不可行处理
        ind["rmsd_relax"] = (
            relax_rmsd_angstrom(cif, relaxed_cif) if relax_res is not None else None
        )
        out.append(ind)

    # 2) 分块批量 evaluate;行缺失/调用失败 → 对应键保持 None
    eval_pos = [i for i, ind in enumerate(out) if not ind.get("_parse_error")]
    batch_size = max(1, int(os.environ.get(_EVAL_BATCH_SIZE_ENV, "20")))
    for c0 in range(0, len(eval_pos), batch_size):
        chunk = eval_pos[c0:c0 + batch_size]
        cifs = [{"cif_content": out[p]["cif"]} for p in chunk]
        print(f"   [{tag}] evaluate 批量 {c0 // batch_size + 1}"
              f"/{(len(eval_pos) + batch_size - 1) // batch_size}"
              f"({len(chunk)} 个结构,分钟级)")
        try:
            res = call_async_tool(
                eps["evaluate"], "evaluate_structures_async", "get_evaluation_result",
                {"structures": cifs, "relax": True},
                # 每体预留 EVALUATE_WAIT 秒(默认 900),批量只会更快,宁松勿紧
                total_wait=int(os.environ.get("EVALUATE_WAIT", "900")) * len(chunk),
                interval=10.0,
            )
        except Exception as e:
            print(f"   [evaluate] 批量异常: {e}")
            res = None

        rows = res.get("per_structure") if isinstance(res, dict) else None
        if rows:
            # index 是该块 structures 列表内的下标 -> 映射回 out 的位置
            by_pos: dict[int, dict | None] = {p: None for p in chunk}
            for r in rows:
                if isinstance(r, dict) and isinstance(r.get("index"), int) \
                        and 0 <= r["index"] < len(chunk):
                    by_pos[chunk[r["index"]]] = r
            for p in chunk:
                row = by_pos.get(p)
                if row is not None:
                    _fill_ind_from_row(out[p], row)
                # 行缺失(server 端该 CIF 解析失败)→ 键保持 None
        else:
            # 老 server:无 per_structure → 回退 batch-of-1(与旧版逐字一致)
            if res is not None:
                print("   [evaluate] 响应无 per_structure,回退 batch-of-1")
            for p in chunk:
                _fill_ind_from_aggregate(
                    out[p], _evaluate_single_aggregate(out[p]["cif"], eps)
                )
    return out


# ================ 注册表 ================

FITNESS_BACKENDS: dict[str, Callable] = {
    "alignn": alignn_characterize_batch,
    "mattersim": mattersim_characterize_batch,
    "mattergen_eval": mattergen_eval_characterize_batch,
}


def get_backend(name: str) -> Callable:
    if name not in FITNESS_BACKENDS:
        raise ValueError(
            f"未知 fitness backend: {name!r};已注册: {list(FITNESS_BACKENDS)}"
        )
    return FITNESS_BACKENDS[name]
