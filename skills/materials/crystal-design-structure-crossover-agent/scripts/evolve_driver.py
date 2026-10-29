#!/usr/bin/env python3
"""
crystal-design-structure-crossover-agent 的确定性进化驱动（step-CLI）。

本驱动把进化循环 + 选择逻辑放进 skill 自己掌控（无独立 engine 服务）：
  - 选择/约束/CDP 非支配排序/相似度剪枝：scripts/saes_selection.py（自包含）
  - 计算原语：已部署 MCP（mutate / relax / alignn / evaluate / generate），见 mcp_client.py
  - fitness backend 可插拔：scripts/evaluators.py 注册 alignn / mattersim 等
  - 默认流程：每代锦标赛选父代 -> diffusion-mutate 变异/交叉
              -> relax+<fitness backend> 表征 -> SAES 环境选择

子命令：
  init      建初始种群（种子 CIF 或 mattergen-generate）+ 表征 + 排序
  step      推进一代（变异 -> 表征 -> 选择）+ 写快照
  steer     更新约束/权重/噪声（Claude 在 step 之间调用）
  status    打印当前代 / Pareto 前沿摘要
  finalize  导出 Pareto CIF + leaderboard，可选 evaluate 新颖性

运行环境：宿主机直跑（pymatgen/numpy in venv，通过 scripts/run.sh 选择解释器），
经 HTTP 反代访问 5 个已部署 MCP 端点。

状态文件（--run-dir）：
  config.json      不可变运行参数（pop_size / max_generations / noise_level /
                                   objectives / fitness_backend / endpoints）
  constraints.json 可变约束（steer 修改它）
  population.json  当代种群（已剥离 structure_obj，只存 cif + 性质 + 排序状态）
  generation.txt   当前代序号
  snapshot_genN.json 每代快照（供 Claude 决策 steering）
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import statistics
import subprocess
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# 同目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcp_client import get_endpoints, call_async_tool  # noqa: E402
from saes_selection import (  # noqa: E402
    evolutionary_environmental_selection,
    evaluate_constraints,
    fast_non_dominated_sorting,
)
# 评估 backend 注册表 + 共享 helpers（relax_cif / structure_meta / cif_to_structure /
# is_charge_neutral 都集中在 evaluators.py，避免与 driver 循环依赖）
from evaluators import (  # noqa: E402
    FITNESS_BACKENDS,
    BACKEND_KEYS,
    DEFAULT_KEYS,
    get_backend,
    cif_to_structure,
    is_charge_neutral,
    structure_meta,
    relax_cif,
)
from pymatgen.analysis.structure_matcher import StructureMatcher  # noqa: E402

# 跨 run 的自然语言记忆（Task 1）；可选依赖，缺失时所有 memory hook 静默 no-op
try:
    from memory_store import MemoryStore, MemoryEntry  # noqa: E402
    _MEMORY_AVAILABLE = True
except ImportError:
    _MEMORY_AVAILABLE = False

# 不依赖 backend 的约束/结构键（在各 backend 的 characterize 结果里都存在）
BACKEND_INDEPENDENT_KEYS = {
    "element_set", "sg_number", "charge_neutral", "comp_valid",
    "num_elements", "num_atoms",  # 标量连续约束：ban 单元素 / 控原子数
    "archive_dedup",  # 结构性键:由 driver 的 apply_archive_dedup 写入(见下),
                      # 非 backend 指标;作为 bool 硬约束用
    "family_dedup",   # 结构性键:由 driver 的 apply_family_dedup 写入(见下,
                      # steering_parity2 实验一);组成家族(桶)级判重,bool 约束,
                      # 权重即 diversity 旋钮(w=0 关闭,w=10 硬压)
}


# ================= IO =================

def load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def save_json(p: Path, obj) -> None:
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


# ================= Memory hook helpers (Task 10) =================
# 这些 helper 让 `step` 子命令把每代的 (context, action, Δ_outcome) 记入跨 run
# 记忆，从而支持 AGENT_WITH_MEM 模式：下一代 step 时先查 memory_query_result.json
# 再决定是否 steer。当 _MEMORY_AVAILABLE=False 时调用方应整段跳过。


def write_memory_query_for_snapshot(
    run_dir: Path,
    snapshot: dict,
    memory_store: "MemoryStore",
    family: str,
    top_k: int = 5,
) -> None:
    """Query the cross-run memory store and write the result next to the snapshot.

    Called by `step` when config.memory_dir is set. The agent (Claude) reads
    this file in addition to snapshot_latest.json before deciding to steer.
    """
    context = snapshot.get("summary", json.dumps(snapshot, ensure_ascii=False))
    retrieved = memory_store.query(context=context, family=family, top_k=top_k)
    out_path = run_dir / "memory_query_result.json"
    save_json(out_path, [e.to_dict() for e in retrieved])


def write_memory_entry_after_step(
    run_dir: Path,
    family: str,
    prev_snapshot: dict,
    curr_snapshot: dict,
    last_steer_action: dict | None,
    memory_store: "MemoryStore",
) -> None:
    """Append a new memory entry capturing (prev_context, last_action, Δ_outcome).

    Called by `step` after writing the new snapshot. Decays store by 0.98.
    """
    def delta(key: str) -> float:
        return float(curr_snapshot.get(key, 0.0)) - float(prev_snapshot.get(key, 0.0))

    outcome = {
        "delta_sun": delta("sun_count"),
        "delta_ehull_median": delta("ehull_median"),
        "delta_stable_count": delta("stable_count"),
    }
    # Outcome-driven confidence (fix: retrieval-frequency boosts would upvote
    # failures). Improvement = SUN count up, or (flat SUN) ehull median down.
    improved = bool(
        outcome["delta_sun"] > 0
        or (outcome["delta_sun"] == 0 and outcome["delta_ehull_median"] < 0)
    )
    action_str = "noop"
    if last_steer_action:
        action_str = last_steer_action.get("action", "noop")
        if action_str != "noop" and last_steer_action.get("args"):
            action_str += f" {json.dumps(last_steer_action['args'], ensure_ascii=False)}"

    entry = MemoryEntry(
        entry_id=uuid.uuid4().hex[:12],
        run_id=str(run_dir.name),
        family=family,
        gen=int(curr_snapshot.get("generation", 0)),
        context=str(prev_snapshot.get("summary", "")),
        action=action_str,
        outcome={k: v for k, v in outcome.items() if v != 0.0},
        confidence=0.5,  # initial; immediately adjusted by outcome below
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    # Decay existing entries first, then append the new entry (a newborn
    # memory must not decay in the step that created it), then apply the
    # outcome verdict last so the ±0.05 signal lands un-eroded.
    memory_store.decay(factor=0.98)
    memory_store.append(entry)
    memory_store.apply_outcome(entry.entry_id, improved=improved)


def _emit_steer_log(action: str, args: dict | None, reason: str, run_dir: Path) -> None:
    """Append one JSON-line entry to steer_log.jsonl.

    The memory hook in `step` reads this file to recover the most recent steer
    action since the prior snapshot. Without this, AGENT-mode runs would
    always record `action="noop"` in memory entries.
    """
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "action": action,
        "args": args or {},
        "reason": reason or "",
    }
    log_path = run_dir / "steer_log.jsonl"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")


def _derive_steer_action(args) -> tuple[str, dict | None]:
    """Map cmd_steer argparse flags into a single (action, args) summary.

    Priority (highest first): ban > allow_only > clear_ban > constraint > weight > noise > crossover.
    Returns ("noop", None) when no steer flag was supplied.
    """
    if getattr(args, "ban", None):
        return "ban", {"element": args.ban}
    if getattr(args, "allow_only", None):
        return "allow_only", {"elements": args.allow_only}
    if getattr(args, "clear_ban", False):
        return "clear_ban", None
    if getattr(args, "constraint", None):
        return "constraint", {"rules": list(args.constraint)}
    if getattr(args, "weight", None):
        return "weight", {"rules": list(args.weight)}
    if getattr(args, "noise_level", None) is not None:
        return "noise", {"level": float(args.noise_level)}
    if getattr(args, "crossover_rate", None) is not None:
        return "crossover", {"rate": float(args.crossover_rate)}
    return "noop", None


def needed_keys(objectives: dict, constraints: dict, backend: str) -> list:
    """objectives ∪ constraints 里引用的性质键 → backend 需要计算的键。

    过滤掉 backend-独立的特殊键（element_set / sg_number / charge_neutral /
    comp_valid —— 它们由 structure_meta 在 characterize 里直接算，不需要走 backend）。
    若剩下空集，回退到该 backend 的 DEFAULT_KEYS。
    """
    referenced = (set(objectives) | set(constraints)) - BACKEND_INDEPENDENT_KEYS
    keys = sorted(referenced & BACKEND_KEYS[backend])
    return keys or list(DEFAULT_KEYS[backend])


def validate_objective_constraint_keys(
    objectives: dict, constraints: dict, backend: str
) -> None:
    """init/steer 时硬校验：所有非特殊键必须在 BACKEND_KEYS[backend] 里。

    不通过直接 sys.exit —— 避免跑到第一代 step 才隐性失败
    （SAES 会把缺值当 100.0 违反，看似种群健康实则选择失效）。
    """
    available = BACKEND_KEYS[backend] | BACKEND_INDEPENDENT_KEYS
    referenced = set(objectives) | set(constraints)
    unknown = sorted(referenced - available)
    if unknown:
        sys.exit(
            f"objective/constraint 键 {unknown} 在 backend {backend!r} 中不可用。"
            f" 可用键: {sorted(available)}"
        )


# ================= 种群序列化（剥离 structure_obj）=================

def serialize_indiv(ind: dict) -> dict:
    out = {k: v for k, v in ind.items() if k not in ("structure_obj",)}
    return out


def deserialize_indiv(d: dict) -> dict:
    ind = dict(d)
    try:
        ind["structure_obj"] = cif_to_structure(ind["cif"])
    except Exception as e:
        ind.setdefault("_parse_error", str(e))
    return ind


def load_population(run_dir: Path) -> list:
    p = run_dir / "population.json"
    if not p.exists():
        return []
    return [deserialize_indiv(d) for d in load_json(p)]


def save_population(run_dir: Path, pop: list) -> None:
    save_json(run_dir / "population.json", [serialize_indiv(i) for i in pop])


# ================= archive 去重约束（evo_sun_parity） =================
# 镜像 Chemeleon2 reward 的 uniqueness 维:新个体若与「历代种群档案 ∪ 当前种群 ∪
# 同批其他个体」任一结构 StructureMatcher 匹配,则 archive_dedup=False,经
# constraints({"archive_dedup":{"expected":true,"weight":10}}) 变为硬罚。
# 与 saes_selection.prune_similar_population 互补:后者只在合并批内剪枝,
# 不惩罚"复活已淘汰个体/克隆种子"。

_DEDUP_MATCHER = StructureMatcher()  # 默认容差,与 evaluate/novelty 判定同协议


def _dedup_bucket(st) -> str:
    """StructureMatcher 匹配的必要条件是组成相同,按 reduced_formula 分桶预筛。"""
    try:
        return st.composition.reduced_formula
    except Exception:
        return "?"


def _dedup_refs(ref_pops: list) -> list:
    """把若干 population(dict 列表)里的 cif 解析为 Structure,供匹配。"""
    refs = []
    for ref_pop in ref_pops or []:
        for ind in ref_pop or []:
            cif = ind.get("cif") if isinstance(ind, dict) else None
            if not cif:
                continue
            try:
                refs.append(cif_to_structure(cif))
            except Exception:
                continue
    return refs


def apply_archive_dedup(new_inds: list, ref_pops: list | None = None) -> None:
    """为 new_inds 每个体写入 archive_dedup(bool)。

    ref_pops 为空时仅做批内互比(init 场景:种子互比,不与种子档案比,否则全灭)。
    解析失败的个体保守判 False(约束罚后 SAES 当不可行,与 metric=None 同路)。
    """
    ref_buckets: dict = {}
    for st in _dedup_refs(ref_pops):
        ref_buckets.setdefault(_dedup_bucket(st), []).append(st)

    parsed = []  # (index, structure | None)
    for i, ind in enumerate(new_inds):
        st = None
        if ind.get("cif"):
            try:
                st = cif_to_structure(ind["cif"])
            except Exception:
                st = None
        parsed.append((i, st))

    for i, st in parsed:
        ind = new_inds[i]
        if st is None:
            ind["archive_dedup"] = False
            continue
        key = _dedup_bucket(st)
        dup = any(_DEDUP_MATCHER.fit(ref, st) for ref in ref_buckets.get(key, []))
        if not dup:
            for j, st2 in parsed:  # 批内互比,先到先得:只与更早的批内成员比
                if j >= i or st2 is None or _dedup_bucket(st2) != key:
                    continue
                if _DEDUP_MATCHER.fit(st2, st):
                    dup = True
                    break
        ind["archive_dedup"] = not dup


# ================= 家族去重约束（steering_parity2 实验一） =================
# 镜像 Chemeleon2 diversity 权重维的组成级近似:个体按 reduced_formula 分桶,
# 与「历代种群档案 ∪ 当前种群 ∪ 同批更早个体」中同桶计数比较,同桶已有数达到
# 阈值 K → family_dedup=False,经 constraints({"family_dedup":{"expected":true,
# "weight":w,"k":3}}) 按 w 罚。与 archive_dedup 互补:后者管结构级克隆,本键管
# "组成家族坍缩"(同族变体挤占种群)。w=0 ≡ 关闭(允许坍缩域),w=10 ≡ 硬压。
# 纯桶计数不做结构匹配 —— K 是"先到先得"语义:每桶前 K 个个体豁免。

_FAMILY_DEDUP_DEFAULT_K = 3


def _family_dedup_k(constraints: dict) -> int:
    """从 constraints 读家族桶阈值 K(缺省 3);规则键不存在时同样返回缺省
    —— 键照常写入(供快照/分析读),是否受罚由 weight 决定。"""
    rules = (constraints or {}).get("family_dedup")
    if isinstance(rules, dict) and rules.get("k") is not None:
        return int(rules["k"])
    return _FAMILY_DEDUP_DEFAULT_K


def apply_family_dedup(new_inds: list, ref_pops: list | None = None, k: int | None = None) -> None:
    """为 new_inds 每个体写入 family_dedup(bool)。

    ref_pops 为空时仅批内互计(init 场景:种子互计,无历史档案)。同批个体按
    顺序先到先得:每桶前 K 个(含 ref_pops 已占额度)豁免,其后判 violation。
    解析失败保守判 False(与 archive_dedup 同路,SAES 当不可行)。
    """
    k = _FAMILY_DEDUP_DEFAULT_K if k is None else int(k)

    counts: Counter = Counter()
    for ref_pop in ref_pops or []:
        for ind in ref_pop or []:
            cif = ind.get("cif") if isinstance(ind, dict) else None
            if not cif:
                continue
            try:
                counts[cif_to_structure(cif).composition.reduced_formula] += 1
            except Exception:
                continue

    for ind in new_inds:
        key = None
        if ind.get("cif"):
            try:
                key = cif_to_structure(ind["cif"]).composition.reduced_formula
            except Exception:
                key = None
        if key is None:
            ind["family_dedup"] = False
            continue
        ind["family_dedup"] = counts[key] < k
        counts[key] += 1


# ================= 表征（按 backend 派发）=================

def characterize_batch(
    items: list[tuple[str, str]],
    keys: list[str],
    backend: str,
    eps: dict,
    tag: str = "",
) -> list[dict]:
    """按 fitness backend 派发个体表征。

    契约见 evaluators.py：每个 backend 接收 (items, keys, eps, tag)，返回
    个体 dict 列表；结构键由各 backend 内部调用 structure_meta 填充；
    性质缺失写 None，SAES 当极不可行处理。
    """
    fn = get_backend(backend)
    return fn(items, keys, eps, tag=tag)


# ================= 选择父代 =================

def tournament(pop: list, k: int = 3):
    if len(pop) <= 1:
        return pop[0] if pop else None
    contenders = random.sample(pop, min(k, len(pop)))
    return max(contenders, key=lambda x: float(x.get("fitness", -1e9)))


# ================= 子命令：init =================

def cmd_init(args):
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    eps = get_endpoints()

    backend = args.fitness_backend
    if backend not in FITNESS_BACKENDS:
        sys.exit(f"未知 --fitness-backend {backend!r}；可选: {list(FITNESS_BACKENDS)}")

    objectives = json.loads(args.objectives) if args.objectives else {"ehull": "minimize", "bandgap": "maximize"}
    constraints = json.loads(args.constraints) if args.constraints else {}

    # 硬校验：所有引用键必须在该 backend 的可用集合里（或 backend-independent）
    validate_objective_constraint_keys(objectives, constraints, backend)

    config = {
        "objectives": objectives,
        "fitness_backend": backend,
        "pop_size": args.pop_size,
        "n_offspring": args.n_offspring or args.pop_size,
        "max_generations": args.max_generations,
        "noise_level": args.noise_level,
        "tournament_k": args.tournament_k,
        "crossover_rate": args.crossover_rate,
        "model_name": args.model_name,
        "endpoints": eps,
        "initial_pool_mode": args.initial_pool_mode,
        "memory_dir": args.memory_dir,
        "family": args.family,
    }
    save_json(run_dir / "config.json", config)
    save_json(run_dir / "constraints.json", constraints)
    (run_dir / "generation.txt").write_text("0")

    keys = needed_keys(objectives, constraints, backend)

    # 构建初始池 CIF
    items = []  # (cif_text, name)
    if args.seed_cifs:
        for p in args.seed_cifs.split(","):
            pf = Path(p.strip())
            items.append((pf.read_text(encoding="utf-8"), pf.stem))
    if args.generate and len(items) < args.pop_size:
        need = max(0, args.generate)  # 额外用 mattergen-generate 生成
        if need > 0:
            print(f"==> 用 mattergen-generate 生成 {need} 个初始结构")
            gen = call_async_tool(
                eps["generate"], "generate_crystals_async", "get_generation_result",
                {"num_samples": need, "batch_size": min(need, 8)},
                total_wait=int(os.environ.get("GENERATE_WAIT", "600")), interval=8.0,
            )
            structs = (gen or {}).get("structures", []) if isinstance(gen, dict) else []
            for s in structs:
                if s.get("cif"):
                    items.append((s["cif"], f"gen_{s.get('index', len(items))}"))
    if not items:
        sys.exit("init 失败：既无 --seed-cifs 也未通过 --generate 生成初始结构")

    print(f"==> 表征初始池 ({len(items)} 个，backend={backend})")
    pop = characterize_batch(items, keys, backend, eps, tag="init")
    apply_archive_dedup(pop)  # init 仅批内互比（种子互比；无历史档案）
    apply_family_dedup(pop, k=_family_dedup_k(constraints))  # init 仅批内互计
    # 初始排序（约束 + FNDS），超过 pop_size 则剪枝截断
    for ind in pop:
        evaluate_constraints(ind, constraints)
    fast_non_dominated_sorting(pop, objectives)
    pop = evolutionary_environmental_selection(pop, constraints, objectives, args.pop_size)
    save_population(run_dir, pop)
    write_snapshot(run_dir, 0, pop, objectives, config)
    print(f"==> init 完成：{len(pop)} 个个体，generation=0")


# ================= 子命令：step =================

def cmd_step(args):
    run_dir = Path(args.run_dir)
    config = load_json(run_dir / "config.json")
    constraints = load_json(run_dir / "constraints.json")
    eps = config.get("endpoints", get_endpoints())
    objectives = config["objectives"]
    gen = int((run_dir / "generation.txt").read_text().strip())
    pop = load_population(run_dir)
    if not pop:
        sys.exit("种群为空：先跑 init")
    if gen >= config["max_generations"]:
        print(f"已达 max_generations={config['max_generations']}，停止。")
        return

    backend = config.get("fitness_backend", "alignn")  # 旧 run 兼容
    keys = needed_keys(objectives, constraints, backend)
    n_off = config["n_offspring"]
    noise = float(args.noise_level if args.noise_level is not None else config["noise_level"])
    t_k = config.get("tournament_k", 3)
    cross_rate = float(config.get("crossover_rate", 0.0))
    print(f"==> generation {gen+1}: 产 {n_off} 个子代 (t={noise}, crossover_rate={cross_rate})")

    # 1) 产生子代（diffusion-mutate MCP：变异或交叉）
    offspring_items = []  # (cif_text, name)
    for i in range(n_off):
        do_cross = cross_rate > 0 and random.random() < cross_rate and len(pop) >= 2
        try:
            if do_cross:
                p1 = tournament(pop, t_k)
                p2 = tournament([x for x in pop if x is not p1], t_k) or tournament(pop, t_k)
                r = call_async_tool(
                    eps["mutate"], "crossover_structures_async", "get_crossover_result",
                    {"t": noise, "parent1": p1["cif"], "parent2": p2["cif"],
                     "model_name": config["model_name"]},
                    total_wait=int(os.environ.get("MUTATE_WAIT", "300")), interval=5.0,
                )
                tag = f"g{gen+1}_{p1['name']}x{p2['name']}_c{i}"
                kind = "cross"
            else:
                parent = tournament(pop, t_k)
                if parent is None:
                    break
                r = call_async_tool(
                    eps["mutate"], "mutate_structure_async", "get_mutate_result",
                    {"t": noise, "cif_content": parent["cif"], "model_name": config["model_name"],
                     "name": f"{parent['name']}_m{i}"},
                    total_wait=int(os.environ.get("MUTATE_WAIT", "300")), interval=5.0,
                )
                tag = f"g{gen+1}_{parent['name']}_m{i}"
                kind = "mut"
            if isinstance(r, dict) and r.get("child_cif"):
                offspring_items.append((r["child_cif"], tag))
            else:
                print(f"   [{kind}] 子代 {i} 失败: {str(r)[:80]}")
        except Exception as e:
            print(f"   [offspring] 子代 {i} 异常: {e}")

    # 2) 表征子代
    print(f"==> 表征 {len(offspring_items)} 个子代 (backend={backend})")
    offspring = characterize_batch(offspring_items, keys, backend, eps, tag=f"g{gen+1}")

    # 2.5) archive 去重约束：子代 vs 历代种群档案 + 当前种群 + 同批其他子代
    history_pops = []
    for p in sorted(run_dir.glob("population_gen*.json")):
        try:
            history_pops.append(load_json(p))
        except Exception as e:
            print(f"   [dedup] 跳过历史档案 {p.name}: {e}")
    apply_archive_dedup(offspring, ref_pops=history_pops + [pop])
    apply_family_dedup(
        offspring, ref_pops=history_pops + [pop], k=_family_dedup_k(constraints)
    )
    # 选择前的子代违反观测(见 write_snapshot 注)—— 旋钮 binding 的直接读数
    offspring_stats = {}
    if offspring:
        offspring_stats["offspring_family_dedup_violation_rate"] = sum(
            1 for x in offspring if x.get("family_dedup") is False
        ) / len(offspring)

    # 3) 环境选择
    combined = pop + offspring
    new_pop = evolutionary_environmental_selection(
        combined, constraints, objectives, config["pop_size"]
    )
    gen += 1
    (run_dir / "generation.txt").write_text(str(gen))
    save_population(run_dir, new_pop)
    write_snapshot(run_dir, gen, new_pop, objectives, config,
                   offspring_stats=offspring_stats)
    n_valid = sum(1 for x in new_pop if x.get("is_valid"))
    front = [x for x in new_pop if x.get("pareto_rank") == 1]
    print(f"==> generation {gen} 完成：种群 {len(new_pop)}（可行 {n_valid}），Pareto 前沿 {len(front)}")

    # Memory hook (no-op if memory_dir not configured or memory_store module missing)
    memory_dir = config.get("memory_dir")
    family = config.get("family", "default")
    if memory_dir and _MEMORY_AVAILABLE:
        snapshot = load_json(run_dir / "snapshot_latest.json")
        store = MemoryStore(Path(memory_dir) / "store.json")
        # Query for THIS snapshot (so agent has memory when reading it)
        write_memory_query_for_snapshot(run_dir, snapshot, store, family)
        # Record an entry for the PREVIOUS snapshot (now that we know Δ)
        prev_snapshot_path = run_dir / f"snapshot_gen{gen - 1}.json"
        if prev_snapshot_path.exists():
            prev_snapshot = load_json(prev_snapshot_path)
            # Find the most recent steer action since prev snapshot
            steer_log_path = run_dir / "steer_log.jsonl"
            last_steer = None
            if steer_log_path.exists():
                lines = steer_log_path.read_text(encoding="utf-8").strip().splitlines()
                if lines:
                    last_steer = json.loads(lines[-1])
            write_memory_entry_after_step(
                run_dir=run_dir,
                family=family,
                prev_snapshot=prev_snapshot,
                curr_snapshot=snapshot,
                last_steer_action=last_steer,
                memory_store=store,
            )


# ================= 子命令：steer =================

def cmd_steer(args):
    run_dir = Path(args.run_dir)
    cfg_path = run_dir / "config.json"
    cstr_path = run_dir / "constraints.json"
    if not cfg_path.exists():
        sys.exit("找不到 config.json：先跑 init")
    config = load_json(cfg_path)
    backend = config.get("fitness_backend", "alignn")  # 旧 run 兼容
    constraints = load_json(cstr_path)

    # 收集本轮 steer 引入的所有新键，提前一次性校验，避免半写半报错
    new_keys: set[str] = set()
    if args.ban or args.allow_only:
        new_keys.add("element_set")
    for c in args.constraint or []:
        new_keys.add(c.split(":", 1)[0])
    for w in args.weight or []:
        new_keys.add(w.partition(":")[0])
    unknown = sorted(new_keys - (BACKEND_KEYS[backend] | BACKEND_INDEPENDENT_KEYS))
    if unknown:
        sys.exit(
            f"steer 引入的键 {unknown} 在 backend {backend!r} 中不可用。"
            f" 可用键: {sorted(BACKEND_KEYS[backend] | BACKEND_INDEPENDENT_KEYS)}"
        )

    if args.ban:
        rules = constraints.setdefault("element_set", {})
        banned = list(rules.get("banned", []))
        for e in args.ban.split(","):
            e = e.strip()
            if e and e not in banned:
                banned.append(e)
        rules["banned"] = banned
        rules["weight"] = max(float(rules.get("weight", 10)), 10.0)
    if args.allow_only:
        rules = constraints.setdefault("element_set", {})
        rules["allowed_only"] = [e.strip() for e in args.allow_only.split(",") if e.strip()]
        rules["weight"] = max(float(rules.get("weight", 10)), 10.0)
    if args.clear_ban:
        constraints.pop("element_set", None)
    for c in args.constraint or []:
        # 格式 KEY:MIN:MAX:WEIGHT  (MIN/MAX 可空)
        parts = c.split(":")
        key = parts[0]
        rule = {}
        if len(parts) > 1 and parts[1]:
            rule["min"] = float(parts[1])
        if len(parts) > 2 and parts[2]:
            rule["max"] = float(parts[2])
        rule["weight"] = float(parts[3]) if len(parts) > 3 and parts[3] else 10.0
        constraints[key] = rule
    for w in args.weight or []:
        # 格式 KEY:WEIGHT —— 调整已存在约束的权重
        key, _, val = w.partition(":")
        if key in constraints and val:
            constraints[key]["weight"] = float(val)
    save_json(cstr_path, constraints)

    # noise_level 调整（写回 config）
    if args.noise_level is not None:
        config = load_json(cfg_path)
        config["noise_level"] = float(args.noise_level)
        save_json(cfg_path, config)
    # crossover_rate 调整（写回 config；0=纯变异,0.5=半交叉,1=纯交叉）
    if args.crossover_rate is not None:
        config = load_json(cfg_path)
        config["crossover_rate"] = float(args.crossover_rate)
        save_json(cfg_path, config)
        print(f"==> crossover_rate -> {config['crossover_rate']}")
    print("==> steer 完成。当前约束：")
    print(json.dumps(constraints, ensure_ascii=False, indent=2))

    # Append to steer_log.jsonl so the memory hook in `step` (and
    # ablation_static_decide) can recover the action history. One summary
    # entry per cmd_steer invocation, using highest-priority action.
    action, action_args = _derive_steer_action(args)
    _emit_steer_log(action, action_args, reason="", run_dir=run_dir)


# ================= 子命令：status =================

def cmd_status(args):
    run_dir = Path(args.run_dir)
    config = load_json(run_dir / "config.json")
    gen = int((run_dir / "generation.txt").read_text().strip())
    pop = load_population(run_dir)
    objectives = config["objectives"]
    n_valid = sum(1 for x in pop if x.get("is_valid"))
    front = sorted([x for x in pop if x.get("pareto_rank") == 1],
                   key=lambda x: float(x.get("fitness", -1e9)), reverse=True)
    print(f"run: {run_dir}  generation {gen}/{config['max_generations']}  "
          f"pop={len(pop)} valid={n_valid}  pareto_front={len(front)}")
    print(f"objectives: {objectives}")
    keys = list(objectives)
    print("  Pareto 前沿 (top 10):")
    print("    " + "  ".join([f"{'name':<18}", "formula"] + [f"{k}" for k in keys] + ["viol"]))
    for x in front[:10]:
        vals = "  ".join(f"{x.get(k)}" for k in keys)
        print(f"    {str(x.get('name','')):<18} {x.get('formula','')[:14]:<14} {vals}  "
              f"viol={x.get('total_violation',0):.2g}")


# ================= 子命令：pivot =================

def cmd_pivot(args: argparse.Namespace) -> int:
    from goal_evolving import pivot_objectives  # lazy import

    new_objectives = json.loads(args.objectives)
    summary = pivot_objectives(run_dir=args.run_dir, new_objectives=new_objectives)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


# ================= 渲染辅助 =================

def _render_pareto(pareto_dir: Path) -> None:
    """Render all CIFs in pareto_dir to PNG immediately after finalize.

    Uses render_batch.py (same scripts/ dir, needs only numpy+matplotlib).
    Failures are warnings only — CIF artifacts are unaffected.
    """
    render_script = Path(__file__).parent / "render_batch.py"
    if not render_script.exists():
        print(f"   [render] render_batch.py not found, skipping PNG generation")
        return
    try:
        result = subprocess.run(
            [sys.executable, "-I", str(render_script),
             str(pareto_dir),
             "--out-dir", str(pareto_dir),
             "--supercell", "2", "2", "2",
             "--skip-existing"],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            print(f"   [render] PNG 渲染完成 -> {pareto_dir}/")
        else:
            print(f"   [render] 部分渲染失败（不影响 CIF 产物）:\n{(result.stderr or result.stdout)[:400]}")
    except Exception as exc:
        print(f"   [render] 渲染跳过: {exc}")


# ================= 子命令：finalize =================

def cmd_finalize(args):
    run_dir = Path(args.run_dir)
    config = load_json(run_dir / "config.json")
    eps = config.get("endpoints", get_endpoints())
    objectives = config["objectives"]
    backend = config.get("fitness_backend", "alignn")  # 旧 run 兼容
    pop = load_population(run_dir)
    front = sorted([x for x in pop if x.get("pareto_rank") == 1],
                   key=lambda x: float(x.get("fitness", -1e9)), reverse=True)
    front = front[: args.top_n] if args.top_n > 0 else front

    pareto_dir = run_dir / "pareto"
    pareto_dir.mkdir(exist_ok=True)
    # 排行榜列：objectives + 至多 2 个 backend 默认键（用于辅助阅读），避免硬编码 alignn 键
    extra = [k for k in DEFAULT_KEYS[backend] if k not in objectives][:2]
    keys = list(objectives) + extra
    # Pareto 前沿导出 cif（S7 取第 1 名进 DFT 的契约保持：按前沿序命名 01_..）
    for i, x in enumerate(front):
        fname = f"{i+1:02d}_{x.get('formula','x')}.cif"
        (pareto_dir / fname).write_text(x["cif"], encoding="utf-8")
    # 排行榜导出全种群（前沿在前、非前沿按适应度续排），pareto 列标注前沿成员。
    # 只导前沿会把单支配个体的运行压缩成 1 行，用户看不到全部演化候选。
    rest = sorted([x for x in pop if x.get("pareto_rank") != 1],
                  key=lambda x: float(x.get("fitness", -1e9)), reverse=True)
    ordered = list(front) + rest
    front_ids = {id(x) for x in front}
    rows = []
    for i, x in enumerate(ordered):
        rows.append({"rank": i + 1, "pareto": 1 if id(x) in front_ids else 0,
                     "name": x.get("name"), "formula": x.get("formula"),
                     "sg": x.get("sg_number"), "is_valid": x.get("is_valid"),
                     "viol": x.get("total_violation"),
                     **{k: x.get(k) for k in set(keys)}})
    csv_path = run_dir / "leaderboard.csv"
    allcols = sorted({c for r in rows for c in r})
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=allcols)
        w.writeheader()
        w.writerows(rows)

    print(f"==> 导出 {len(front)} 个 Pareto 结构 -> {pareto_dir}/")
    print(f"==> 全种群排行榜 {len(rows)} 条（前沿 {len(front)}） -> {csv_path}")

    if args.evaluate and rows:
        print("==> mattergen-evaluate 新颖性/稳定性评估中（分钟级）...")
        try:
            r = call_async_tool(
                eps["evaluate"], "evaluate_structures_async", "get_evaluation_result",
                {"structures": [{"cif_content": x["cif"]} for x in front]},
                total_wait=int(os.environ.get("EVALUATE_WAIT", "1200")), interval=10.0,
            )
            save_json(run_dir / "evaluate_report.json", r)
            print("   metrics:", json.dumps(r.get("metrics", r), ensure_ascii=False)[:400])
        except Exception as e:
            print(f"   [evaluate] 异常: {e}")

    _render_pareto(pareto_dir)


# ================= 快照 =================

def _is_sun(ind: dict) -> bool | None:
    """Stable ∩ Unique ∩ Novel, or None when the backend lacks these keys."""
    if not all(k in ind for k in ("novel", "stable", "unique")):
        return None
    return bool(ind["novel"]) and bool(ind["stable"]) and bool(ind["unique"])


_EHULL_KEYS = ("ehull_eval", "ehull", "energy_per_atom")


def _pop_stats(pop: list) -> dict:
    suns = [_is_sun(x) for x in pop]
    known = [s for s in suns if s is not None]
    stats: dict = {}
    if known:
        stats["sun_count"] = sum(known)
        stats["stable_count"] = sum(1 for x in pop if x.get("stable"))
        stats["novel_count"] = sum(1 for x in pop if x.get("novel"))
    for key in _EHULL_KEYS:
        vals = [float(x[key]) for x in pop if x.get(key) is not None]
        if vals:
            stats["ehull_key"] = key
            stats["ehull_median"] = statistics.median(vals)
            break
    return stats


def _build_summary(gen: int, pop: list, front: list, stats: dict) -> str:
    """Compact natural-language snapshot context — the text memory entries are
    built from (Jaccard retrieval), so it must always be present."""
    formulas = ", ".join(x.get("formula", "?") for x in front[:5]) or "empty"
    counts = Counter(
        el for x in pop for el in (x.get("element_set") or [])
    ).most_common(3)
    dom = ", ".join(f"{el}×{n}" for el, n in counts) or "none"
    ehull = f"ehull median {stats['ehull_median']:.4f}" if "ehull_median" in stats else "no ehull"
    sun = f"SUN {stats['sun_count']}/{len(pop)}" if "sun_count" in stats else "no SUN"
    fam = f"family concentration {_family_concentration(pop):.2f}" if pop else "no pop"
    return (f"gen {gen}: pareto front [{formulas}]; {sun}; {ehull}; "
            f"dominant elements {dom}; {fam}")


def _pareto_unchanged_streak(run_dir: Path, gen: int, front_size: int) -> int:
    """前沿规模连续未变的代数(读上一代快照递推;首代或读失败为 0)。"""
    prev_path = run_dir / f"snapshot_gen{gen - 1}.json"
    if gen <= 0 or not prev_path.exists():
        return 0
    try:
        prev = load_json(prev_path)
    except Exception:
        return 0
    if prev.get("pareto_front_size") == front_size:
        return int(prev.get("pareto_size_unchanged_gens", 0)) + 1
    return 0


def _ind_bucket(ind: dict) -> str:
    """个体所属 reduced_formula 桶(structure_obj 优先,缺失时解析 cif;失败 '?'）。"""
    st = ind.get("structure_obj")
    if st is not None:
        return _dedup_bucket(st)
    if ind.get("cif"):
        try:
            return _dedup_bucket(cif_to_structure(ind["cif"]))
        except Exception:
            pass
    return "?"


def _family_concentration(pop: list) -> float:
    """当前种群最大同桶占比(steering_parity2 实验一的调度读数)。"""
    if not pop:
        return 0.0
    counts = Counter(_ind_bucket(x) for x in pop)
    return max(counts.values()) / len(pop)


def _dominant_family(pop: list) -> dict:
    """最大同桶家族的描述(steering_parity3 ML-rule 规则④ / agent 的调度读数)。

    main_element = 该桶 reduced_formula 中计量比最大的元素(并列取原子序小者),
    是规则④ ban 的目标;桶占比即 family_concentration,单独返回以便复用。
    解析失败时 main_element 为 None(规则侧保守跳过,不误 ban)。
    """
    out = {"bucket": None, "share": 0.0, "main_element": None, "representative": None}
    if not pop:
        return out
    counts = Counter(_ind_bucket(x) for x in pop)
    bucket, n = max(counts.items(), key=lambda kv: kv[1])
    out["bucket"] = bucket
    out["share"] = n / len(pop)
    rep = next((x for x in pop if _ind_bucket(x) == bucket), None)
    out["representative"] = rep.get("name") if rep else None
    if bucket and bucket != "?":
        try:
            from pymatgen.core import Composition, Element

            amts = Composition(bucket).get_el_amt_dict()
            out["main_element"] = min(amts, key=lambda s: (-amts[s], Element(s).Z))
        except Exception:
            out["main_element"] = None
    return out


def _uniqueness_rate(pop: list) -> float:
    """批内 StructureMatcher 先到先得 uniqueness 占比(调度读数 + 副指标轨迹)。

    与 parity_metrics.uniqueness_flags 同协议(默认容差、桶预筛);
    解析失败个体按不唯一计(保守,与 metric 侧一致)。
    """
    if not pop:
        return 0.0
    seen: dict = {}
    n_unique = 0
    for x in pop:
        key = None
        dup = False
        try:
            st = x.get("structure_obj") or cif_to_structure(x["cif"])
            key = _dedup_bucket(st)
            dup = any(_DEDUP_MATCHER.fit(ref, st) for ref in seen.get(key, []))
        except Exception:
            continue  # 解析/比对失败:按不唯一计(保守,与 metric 侧一致)
        if dup:
            continue
        seen.setdefault(key, []).append(st)
        n_unique += 1
    return n_unique / len(pop)


def _most_violated_constraint(pop: list, constraints: dict):
    """返回违反个体数最多的约束键(全部合规时为 None)。

    语义与 saes_selection.evaluate_constraints 的逐键判定一致(布尔
    expected / banned / allowed_only / min-max),只数违反个数不算幅度。
    """
    counts: dict = {}
    for key, rules in (constraints or {}).items():
        cnt = 0
        for x in pop:
            v = x.get(key)
            if v is None:
                cnt += 1
            elif type(v) is bool:
                if v != rules.get("expected", True):
                    cnt += 1
            elif "banned" in rules or "allowed_only" in rules:
                vals = v if isinstance(v, (list, set, tuple)) else [v]
                if "banned" in rules and set(vals) & set(rules["banned"]):
                    cnt += 1
                if "allowed_only" in rules and (set(vals) - set(rules["allowed_only"])):
                    cnt += 1
            else:
                if ("min" in rules and v < rules["min"]) or \
                   ("max" in rules and v > rules["max"]):
                    cnt += 1
        counts[key] = cnt
    if not counts or max(counts.values()) <= 0:
        return None
    return max(counts, key=counts.get)


def _constraint_key_stats(pop: list, cons: dict) -> dict:
    """逐 min/max 约束键的违反率/带内率/中位数(steering_parity2 的调度输入)。

    分母统一为 len(pop):性质缺失(None,解析/调用失败)的个体计入分母、
    按"不带宽/违反"处理 —— 调度侧因此看到的是保守口径,不会把不可评估
    误读成健康。
    """
    out: dict = {}
    for key, rules in (cons or {}).items():
        if not (isinstance(rules, dict) and ("min" in rules or "max" in rules)):
            continue
        lo, hi = rules.get("min"), rules.get("max")
        known = [float(x[key]) for x in pop
                 if isinstance(x.get(key), (int, float)) and x[key] == x[key]]
        viol = sum(
            1 for v in known
            if (lo is not None and v < lo) or (hi is not None and v > hi)
        )
        inband = sum(
            1 for v in known
            if (lo is None or v >= lo) and (hi is None or v <= hi)
        )
        out[key] = {
            "n_known": len(known),
            "violation_rate": viol / len(pop) if pop else 0.0,
            "inband_rate": inband / len(pop) if pop else 0.0,
            "median": statistics.median(known) if known else None,
        }
    return out


def write_snapshot(run_dir: Path, gen: int, pop: list, objectives: dict, config: dict,
                   offspring_stats: dict | None = None) -> None:
    front = sorted([x for x in pop if x.get("pareto_rank") == 1],
                   key=lambda x: float(x.get("fitness", -1e9)), reverse=True)
    stats = _pop_stats(pop)
    cons = load_json(run_dir / "constraints.json") if (run_dir / "constraints.json").exists() else {}
    snap = {
        "generation": gen,
        "max_generations": config["max_generations"],
        "pop_size": len(pop),
        "n_valid": sum(1 for x in pop if x.get("is_valid")),
        "pareto_front_size": len(front),
        **stats,
        "objectives": objectives,
        "constraints": cons,
        "summary": _build_summary(gen, pop, front, stats),
        "top_front": [{
            "name": x.get("name"), "formula": x.get("formula"),
            "sg_number": x.get("sg_number"), "is_valid": x.get("is_valid"),
            "total_violation": x.get("total_violation"),
            "stable": x.get("stable"), "unique": x.get("unique"),
            **{k: x.get(k) for k in objectives},
        } for x in front[:10]],
        # ---- STATIC_RULES 输入字段(steering_modes.static_rules_decide 读取)----
        # 历史教训:这些字段缺位时规则表整体退化为 noop 兜底。
        # 规则 1:单元素个体(反塌陷 ban 的候选),带 ehull 供排序。
        "single_element_individuals": [
            {"element": (x.get("element_set") or ["?"])[0],
             "ehull": x.get(stats["ehull_key"], 1.0) if stats.get("ehull_key") else 1.0}
            for x in pop
            if x.get("num_elements") == 1 or len(x.get("element_set") or []) == 1
        ],
        # 规则 2:前沿规模连续未变的代数(读上一代快照递推)。
        "pareto_size_unchanged_gens": _pareto_unchanged_streak(run_dir, gen, len(front)),
        # 规则 3:弛豫 rmsd 中位数;无数据时用 1.0(与规则侧 .get 默认一致,不触发)。
        "rmsd_median": (
            statistics.median([float(x["rmsd_relax"]) for x in pop
                               if x.get("rmsd_relax") is not None])
            if any(x.get("rmsd_relax") is not None for x in pop) else 1.0
        ),
        # 规则 4:约束违反率(不可行个体占比)+ 最常被违反的约束键。
        "constraint_violation_rate": (
            1.0 - sum(1 for x in pop if x.get("is_valid")) / len(pop) if pop else 0.0
        ),
        "most_violated_constraint": _most_violated_constraint(pop, cons),
        # ---- steering_parity2 调度输入:逐 min/max 约束键的违反率/带内率 ----
        "constraint_key_stats": _constraint_key_stats(pop, cons),
        # ---- steering_parity2 实验一 W-rule/W-agent 输入字段 ----
        # 历史教训(见上):这些字段缺位时调度规则整体退化为 noop 兜底。
        "family_concentration": _family_concentration(pop),
        # ---- steering_parity3 调度输入:主导家族描述(规则④ ban 目标 + agent 读数) ----
        "dominant_family": _dominant_family(pop),
        "uniqueness_rate": _uniqueness_rate(pop),
        # 选择前子代的约束违反观测(冒烟教训:w=5 下「选择后违反率」恒 0 是约束绑定
        # 的表现而非判重空转;本字段才是旋钮是否 binding 的直接读数)。init 无子代。
        **(offspring_stats or {}),
    }
    save_json(run_dir / f"snapshot_gen{gen}.json", snap)
    save_json(run_dir / "snapshot_latest.json", snap)
    # Archive the full population per generation so per-gen SUN curves are
    # computable post-hoc (top_front alone cannot reconstruct whole-pop stats).
    # 必须过 serialize_indiv：raw pop 带 structure_obj(Structure 不可 JSON 序列化)。
    save_json(run_dir / f"population_gen{gen}.json", [serialize_indiv(i) for i in pop])


# ================= 入口 =================

def main():
    ap = argparse.ArgumentParser(description="crystal-design-structure-crossover-agent 确定性进化驱动")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="建初始种群")
    p_init.add_argument("--run-dir", required=True)
    p_init.add_argument("--fitness-backend", default="alignn",
                        choices=list(FITNESS_BACKENDS),
                        help="每代 fitness 评估后端(默认 alignn); "
                             "alignn=快/粗(性质多),mattersim=准(只能量/受力/应力)")
    p_init.add_argument("--objectives", default=None, help='JSON, 如 {"ehull":"minimize","bandgap":"maximize"}')
    p_init.add_argument("--constraints", default=None, help="JSON")
    p_init.add_argument("--pop-size", type=int, default=20)
    p_init.add_argument("--n-offspring", type=int, default=None)
    p_init.add_argument("--max-generations", type=int, default=30)
    p_init.add_argument("--noise-level", type=float, default=0.3)
    p_init.add_argument("--tournament-k", type=int, default=3)
    p_init.add_argument("--crossover-rate", type=float, default=0.0,
                        help="每代子代中走双亲交叉的比例(0=纯变异,0.5=半交叉半变异,1=纯交叉)")
    p_init.add_argument("--model-name", default="mattergen_base")
    p_init.add_argument("--initial-pool-mode", default="seed")
    p_init.add_argument("--seed-cifs", default=None, help="逗号分隔的种子 CIF 路径")
    p_init.add_argument("--generate", type=int, default=0, help="额外用 mattergen-generate 生成 N 个")
    p_init.add_argument("--memory-dir", type=str, default=None,
                        help="Path to cross-run memory directory; enables AGENT_WITH_MEM mode")
    p_init.add_argument("--family", type=str, default="default",
                        help="Material family tag for memory scoping")
    p_init.set_defaults(func=cmd_init)

    p_step = sub.add_parser("step", help="推进一代")
    p_step.add_argument("--run-dir", required=True)
    p_step.add_argument("--noise-level", type=float, default=None, help="覆盖本代噪声强度")
    p_step.set_defaults(func=cmd_step)

    p_steer = sub.add_parser("steer", help="更新约束/权重/噪声")
    p_steer.add_argument("--run-dir", required=True)
    p_steer.add_argument("--ban", default=None, help="禁用元素，逗号分隔，如 Pb,Cd")
    p_steer.add_argument("--allow-only", default=None, help="仅允许元素，逗号分隔")
    p_steer.add_argument("--clear-ban", action="store_true")
    p_steer.add_argument("--constraint", action="append", help="KEY:MIN:MAX:WEIGHT，可多次")
    p_steer.add_argument("--weight", action="append", help="KEY:WEIGHT 调整已存在约束权重")
    p_steer.add_argument("--noise-level", type=float, default=None)
    p_steer.add_argument("--crossover-rate", type=float, default=None,
                         help="切换交叉比例(0=纯变异,0.5=半交叉,1=纯交叉)")
    p_steer.set_defaults(func=cmd_steer)

    p_status = sub.add_parser("status", help="当前状态")
    p_status.add_argument("--run-dir", required=True)
    p_status.set_defaults(func=cmd_status)

    p_fin = sub.add_parser("finalize", help="导出 Pareto + leaderboard")
    p_fin.add_argument("--run-dir", required=True)
    p_fin.add_argument("--top-n", type=int, default=20)
    p_fin.add_argument("--evaluate", action="store_true", help="对前沿跑 mattergen-evaluate")
    p_fin.set_defaults(func=cmd_finalize)

    p_pivot = sub.add_parser(
        "pivot",
        help="Mid-search objective change (goal-evolving demo). Re-ranks current population in place.",
    )
    p_pivot.add_argument("--run-dir", required=True, type=Path)
    p_pivot.add_argument(
        "--objectives",
        required=True,
        type=str,
        help='JSON dict of new objectives, e.g. \'{"ehull": "minimize", "novel": "maximize"}\'',
    )
    p_pivot.set_defaults(func=cmd_pivot)

    args = ap.parse_args()
    random.seed(int(os.environ.get("EVOLVE_SEED", "42")))
    t0 = time.time()
    args.func(args)
    print(f"(耗时 {time.time()-t0:.1f}s)")


if __name__ == "__main__":
    main()
