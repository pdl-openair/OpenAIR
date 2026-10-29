"""
进化选择算子（自包含内联；NSGA-II/CDP + StructureMatcher 剪枝）。

为什么内联而非 import：上游参考实现在模块顶层 `from characterize_functions import *`，
会拖入 CHGNet/MatterSim/JACS 等重依赖（JACS 已废弃）。本模块只保留"选择"那部分
逻辑（约束评估 / CDP 非支配排序 / StructureMatcher 相似度剪枝 / 精英截断），
仅依赖 pymatgen + numpy，让 evolve_driver 能在精简环境里直接跑。

个体字典契约（driver 负责构造）：
  必含: "structure_obj"(pymatgen Structure，供相似度比对；dict_to_structure 会缓存命中它)
  性质键: 由当前 fitness backend 决定（bandgap / formation_energy / ehull / dielectric /
          bulk_modulus / ... 来自 alignn；或 energy_per_atom / f_max / s_max 来自 mattersim）
  约束相关: element_set(列表) / sg_number (标量) 等
  本模块写入: total_violation / constraint_violation / is_valid / pareto_rank / fitness
"""
from __future__ import annotations

import numpy as np
from pymatgen.core import Structure
from pymatgen.analysis.structure_matcher import StructureMatcher


# ================= 结构互转（仅供相似度计算用）=================

def dict_to_structure(data_dict: dict) -> Structure:
    """mattergen/DEMO 风格字典 -> pymatgen Structure。

    优先读缓存的 "structure_obj"；否则用 pos/cell/atomic_numbers 张量重建。
    driver 始终会先把 CIF 解析成的 Structure 存进 "structure_obj"。
    """
    if "structure_obj" in data_dict:
        return data_dict["structure_obj"]
    import torch  # 仅在无缓存时需要
    lattice = data_dict["cell"].squeeze(0).cpu().numpy()
    coords = data_dict["pos"].cpu().numpy()
    species = data_dict["atomic_numbers"].cpu().numpy()
    struct = Structure(lattice=lattice, species=species, coords=coords, coords_are_cartesian=False)
    data_dict["structure_obj"] = struct
    return struct


# ================= 1. 相似度与剪枝 =================

def calculate_crystal_similarity(dict_a: dict, dict_b: dict) -> dict:
    """两个晶体的相似度（StructureMatcher.fit + get_rms_dist）。"""
    try:
        struct_a = dict_to_structure(dict_a)
        struct_b = dict_to_structure(dict_b)
        if struct_a.composition != struct_b.composition:
            return {"is_match": False, "rms_distance": 1.0, "similarity_score": 0.0}
        matcher = StructureMatcher(ltol=0.2, stol=0.3, angle_tol=5)
        is_match = matcher.fit(struct_a, struct_b)
        rms_data = matcher.get_rms_dist(struct_a, struct_b)
        if rms_data is not None:
            rms_distance = rms_data[0]
            similarity_score = float(np.exp(-rms_distance))
        else:
            rms_distance = 1.0
            similarity_score = 0.0
    except Exception as e:
        print(f"相似度计算异常: {e}")
        is_match = False
        rms_distance = 1.0
        similarity_score = 0.0
    return {
        "is_match": bool(is_match),
        "rms_distance": float(rms_distance),
        "similarity_score": float(similarity_score),
    }


def _is_too_similar(sim_info: dict, score_threshold: float, rms_threshold: float) -> bool:
    score = float(sim_info.get("similarity_score", 0.0))
    rms = float(sim_info.get("rms_distance", 1.0))
    is_match = bool(sim_info.get("is_match", False))
    return is_match and score >= score_threshold and rms <= rms_threshold


def prune_similar_population(
    population: list[dict],
    similar_score_threshold: float,
    similar_rms_threshold: float,
    very_similar_score_threshold: float,
    very_similar_rms_threshold: float,
) -> list[dict]:
    """按 fitness 从高到低保留，去掉过于相似的个体。"""
    sorted_pop = sorted(population, key=lambda x: x.get("fitness", -999), reverse=True)
    kept: list[dict] = []
    for cand in sorted_pop:
        discard = False
        for old in kept:
            sim_info = calculate_crystal_similarity(cand, old)
            both_valid = cand.get("is_valid", False) and old.get("is_valid", False)
            if both_valid:
                if _is_too_similar(
                    sim_info,
                    score_threshold=very_similar_score_threshold,
                    rms_threshold=very_similar_rms_threshold,
                ):
                    discard = True
                    break
            else:
                if _is_too_similar(
                    sim_info,
                    score_threshold=similar_score_threshold,
                    rms_threshold=similar_rms_threshold,
                ):
                    discard = True
                    break
        if not discard:
            kept.append(cand)
    return kept


# ================= 2. 约束评估与 CDP 非支配排序 =================

def evaluate_constraints(data_dict: dict, constraints: dict) -> dict:
    """根据 constraints 字典计算约束违反值，写入 total_violation / is_valid。"""
    violation = 0.0
    for key, rules in constraints.items():
        val = data_dict.get(key)
        if val is None:
            violation += 100.0
            continue
        weight = rules.get("weight", 1.0)
        if type(val) is bool:  # 布尔约束
            expected = rules.get("expected", True)
            if val != expected:
                violation += weight
        elif "allowed_only" in rules or "banned" in rules:  # 白名单/黑名单
            if isinstance(val, (list, set, tuple)):
                if "banned" in rules:
                    overlap = set(val).intersection(set(rules["banned"]))
                    if overlap:
                        violation += weight * len(overlap)
                if "allowed_only" in rules:
                    out_of_bounds = set(val) - set(rules["allowed_only"])
                    if out_of_bounds:
                        violation += weight * len(out_of_bounds)
            else:  # 标量（如 sg_number）
                if "banned" in rules and val in rules["banned"]:
                    violation += weight
                if "allowed_only" in rules and val not in rules["allowed_only"]:
                    violation += weight
        else:  # 连续 min/max
            if "min" in rules and val < rules["min"]:
                violation += (rules["min"] - val) * weight
            if "max" in rules and val > rules["max"]:
                violation += (val - rules["max"]) * weight
    data_dict["total_violation"] = violation
    data_dict["constraint_violation"] = violation
    data_dict["is_valid"] = bool(violation <= 1e-5)
    return data_dict


def _objective_value(ind: dict, obj_key: str, direction: str):
    val = ind.get(obj_key)
    if val is None:
        return float("inf") if direction == "minimize" else -float("inf")
    try:
        v = float(val)
    except (TypeError, ValueError):
        return float("inf") if direction == "minimize" else -float("inf")
    if v != v:  # NaN
        return float("inf") if direction == "minimize" else -float("inf")
    return v


def dominates_cdp_safe(ind1: dict, ind2: dict, objectives: dict) -> bool:
    """基于约束支配原则 (CDP) 判断 ind1 是否支配 ind2。"""
    v1 = float(ind1.get("total_violation", ind1.get("constraint_violation", float("inf"))))
    v2 = float(ind2.get("total_violation", ind2.get("constraint_violation", float("inf"))))
    if v1 < v2 - 1e-5:
        return True
    if v1 > v2 + 1e-5:
        return False
    better_in_all = True
    strictly_better_in_one = False
    for obj_key, direction in objectives.items():
        val1 = _objective_value(ind1, obj_key, direction)
        val2 = _objective_value(ind2, obj_key, direction)
        if direction == "minimize":
            if val1 > val2:
                better_in_all = False
            if val1 < val2:
                strictly_better_in_one = True
        elif direction == "maximize":
            if val1 < val2:
                better_in_all = False
            if val1 > val2:
                strictly_better_in_one = True
    return better_in_all and strictly_better_in_one


def _assign_missing_pareto_ranks(population: list[dict]) -> None:
    max_rank = 0
    for ind in population:
        rank = ind.get("pareto_rank")
        if rank is not None:
            try:
                max_rank = max(max_rank, int(rank))
            except (TypeError, ValueError):
                pass
    for ind in population:
        if ind.get("pareto_rank") is None:
            max_rank += 1
            ind["pareto_rank"] = max_rank


def fast_non_dominated_sorting(population: list[dict], objectives: dict) -> list[dict]:
    """FNDS + CDP：计算 pareto_rank 与 fitness(=-rank)。"""
    if not population:
        return population
    for p in population:
        p.pop("pareto_rank", None)
        p.pop("dominated_set", None)
        p.pop("domination_count", None)
    fronts = [[]]
    for p in population:
        p["dominated_set"] = []
        p["domination_count"] = 0
        for q in population:
            if p is q:
                continue
            if dominates_cdp_safe(p, q, objectives):
                p["dominated_set"].append(q)
            elif dominates_cdp_safe(q, p, objectives):
                p["domination_count"] += 1
        if p["domination_count"] == 0:
            p["pareto_rank"] = 1
            fronts[0].append(p)
    if not fronts[0]:
        min_dom = min(p["domination_count"] for p in population)
        for p in population:
            if p["domination_count"] == min_dom:
                p["pareto_rank"] = 1
                fronts[0].append(p)
    i = 0
    while i < len(fronts) and len(fronts[i]) > 0:
        next_front = []
        for p in fronts[i]:
            for q in p["dominated_set"]:
                q["domination_count"] -= 1
                if q["domination_count"] == 0 and q.get("pareto_rank") is None:
                    q["pareto_rank"] = i + 2
                    next_front.append(q)
        i += 1
        fronts.append(next_front)
    _assign_missing_pareto_ranks(population)
    for ind in population:
        ind.pop("dominated_set", None)
        ind.pop("domination_count", None)
        ind["fitness"] = -float(ind.get("pareto_rank", 999))
    return population


# ================= 3. 环境选择流水线 =================

def evolutionary_environmental_selection(
    combined_population: list[dict],
    constraints: dict,
    objectives: dict,
    pop_size: int,
    similar_score_threshold: float = 0.95,
    similar_rms_threshold: float = 0.1,
    very_similar_score_threshold: float = 0.999,
    very_similar_rms_threshold: float = 0.01,
) -> list[dict]:
    """约束评估 -> FNDS-CDP -> 相似度剪枝 -> 精英截断。返回新一代种群。"""
    for item in combined_population:
        evaluate_constraints(item, constraints)
    fast_non_dominated_sorting(combined_population, objectives)
    pruned_pop = prune_similar_population(
        combined_population,
        similar_score_threshold=similar_score_threshold,
        similar_rms_threshold=similar_rms_threshold,
        very_similar_score_threshold=very_similar_score_threshold,
        very_similar_rms_threshold=very_similar_rms_threshold,
    )
    return pruned_pop[:pop_size]
