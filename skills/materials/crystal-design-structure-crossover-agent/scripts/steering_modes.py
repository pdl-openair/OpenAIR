# scripts/steering_modes.py
"""Steering-mode dispatch for the 4-way ablation.

The agent (Claude) IS the AGENT_NO_MEM / AGENT_WITH_MEM strategist —
those modes differ only in whether memory is injected into the prompt.
STATIC_RULES and ORACLE are pure-Python replacements used to control
the ablation without invoking an LLM.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any


class SteeringMode(str, Enum):
    STATIC_RULES = "static_rules"
    AGENT_NO_MEM = "agent_no_mem"
    AGENT_WITH_MEM = "agent_with_mem"
    ORACLE = "oracle"


@dataclass(frozen=True, slots=True)
class SteerAction:
    action: str  # "ban" | "constraint" | "noise" | "crossover" | "noop"
    args: dict[str, Any]
    reason: str


def parse_steer_action(json_str: str) -> SteerAction:
    """Parse the JSON object the agent emits per the agent_prompt contract."""
    try:
        d = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid json: {e}") from e
    if not isinstance(d, dict):
        raise ValueError("steer action must be a JSON object")
    for key in ("action", "args", "reason"):
        if key not in d:
            raise ValueError(f"missing field: {key}")
    return SteerAction(action=str(d["action"]), args=dict(d["args"]), reason=str(d["reason"]))


def static_rules_decide(snapshot: dict, family: str, constraints: dict) -> SteerAction:
    """Deterministic rule-based steering for the STATIC_RULES ablation mode.

    These rules encode the highest-priority heuristics from
    references/steering-playbook.md. The intent is to provide a
    competitive non-LLM baseline; the AGENT modes must beat it to
    justify the agent framing.
    """
    # Rule 1: anti-collapse
    singles = snapshot.get("single_element_individuals", [])
    if singles:
        # ban the element with lowest ehull (the "winning" spurious one)
        singles_sorted = sorted(singles, key=lambda x: x.get("ehull", 1.0))
        return SteerAction(
            action="ban",
            args={"element": singles_sorted[0]["element"]},
            reason=f"static_rule_1: {len(singles)} single-element individuals detected",
        )

    # Rule 2: lower noise when stable saturated
    if (
        snapshot.get("stable_count", 0) == snapshot.get("pop_size", -1)
        and snapshot.get("pareto_size_unchanged_gens", 0) >= 2
    ):
        return SteerAction(
            action="noise",
            args={"level": 0.2},
            reason="static_rule_2: stable saturated and front static",
        )

    # Rule 3: raise noise when rmsd is suspiciously low (search stagnating)
    if snapshot.get("rmsd_median", 1.0) < 0.0005 and snapshot.get("generation", 0) > 2:
        return SteerAction(
            action="noise",
            args={"level": 0.5},
            reason="static_rule_3: rmsd_median near zero, diversify",
        )

    # Rule 4: tighten most-violated constraint
    if snapshot.get("constraint_violation_rate", 0.0) > 0.5:
        key = snapshot.get("most_violated_constraint")
        if key:
            return SteerAction(
                action="constraint",
                args={"key": key, "weight_delta": 5},
                reason=f"static_rule_4: violation_rate > 0.5 on {key}",
            )

    return SteerAction(action="noop", args={}, reason="front healthy")


# ================= steering_parity2 实验二:bandgap 权重阈值调度(G-rule) =================
# 预注册规则(docs/exp-plan/steering_parity2.md §B3):
#   violation 率 > 0.5 → w − 5;带内率 > 0.8 且 front 停滞 → w + 5;边界 [0,10]。
# 数据源是 write_snapshot 的 constraint_key_stats(key 级 violation/inband 率,
# 分母含性质缺失个体)——快照缺该字段时保守 noop(parity-1 教训:字段缺位
# 会让规则表整体退化)。

BANDGAP_RULE_KEY = "bandgap"
BANDGAP_RULE_STEP = 5.0
BANDGAP_RULE_W_BOUNDS = (0.0, 10.0)
BANDGAP_RULE_VIOLATION_TRIGGER = 0.5
BANDGAP_RULE_INBAND_TRIGGER = 0.8
BANDGAP_RULE_STALL_GENS = 2


def bandgap_rule_decide(snapshot: dict, constraints: dict) -> SteerAction:
    """Threshold scheduling of the bandgap constraint weight (G-rule arm).

    Soften-first priority: feasibility (violation rate) dominates tightening
    (in-band rate), so an OOD hard-push cannot starve the population.
    The emitted action sets the ABSOLUTE weight via `steer --constraint
    KEY:MIN:MAX:W` (cmd_steer's weight semantics are absolute, not delta).
    """
    key = BANDGAP_RULE_KEY
    rules = (constraints or {}).get(key) or {}
    stats = (snapshot.get("constraint_key_stats") or {}).get(key)
    if not stats or "min" not in rules and "max" not in rules:
        return SteerAction(
            action="noop", args={},
            reason="bandgap_rule: snapshot/constraints missing bandgap stats",
        )

    w = float(rules.get("weight", 5.0))
    lo, hi = BANDGAP_RULE_W_BOUNDS
    step = BANDGAP_RULE_STEP
    viol = float(stats.get("violation_rate", 0.0))
    inband = float(stats.get("inband_rate", 0.0))
    stall = int(snapshot.get("pareto_size_unchanged_gens", 0))

    if viol > BANDGAP_RULE_VIOLATION_TRIGGER:
        new_w = max(w - step, lo)
        return SteerAction(
            action="constraint",
            args={"key": key, "min": rules.get("min"), "max": rules.get("max"),
                  "weight": new_w},
            reason=f"bandgap_rule: violation_rate {viol:.2f} > 0.5 -> w {w:g}->{new_w:g}",
        )
    if inband > BANDGAP_RULE_INBAND_TRIGGER and stall >= BANDGAP_RULE_STALL_GENS:
        new_w = min(w + step, hi)
        return SteerAction(
            action="constraint",
            args={"key": key, "min": rules.get("min"), "max": rules.get("max"),
                  "weight": new_w},
            reason=(f"bandgap_rule: inband_rate {inband:.2f} > 0.8 and front stalled "
                    f"{stall} gens -> w {w:g}->{new_w:g}"),
        )
    return SteerAction(
        action="noop", args={},
        reason=(f"bandgap_rule: healthy (violation {viol:.2f}, inband {inband:.2f}, "
                f"stall {stall})"),
    )


def family_weight_rule_decide(snapshot: dict, constraints: dict) -> SteerAction:
    """W-rule family_dedup weight scheduling (steering_parity2 实验一,预注册 A3).

    阈值规则(±5 步进,边界 [0,10],权重为绝对值语义):
      - family_concentration > 0.5 → w + 5(家族坍缩,加重 diversity 罚)
      - uniqueness_rate > 0.9 且前沿停滞(≥2 代) → w − 5(多样性已足,放松换质量)
    两规则同时满足时收紧优先(坍缩是本实验要防的主风险)。
    family_dedup 未启用(不在 constraints)时 noop —— 旋钮不存在,无从调度。
    """
    rules = constraints.get("family_dedup")
    if not isinstance(rules, dict) or rules.get("weight") is None:
        return SteerAction(action="noop", args={}, reason="family_dedup not enabled")

    cur = float(rules["weight"])
    conc = float(snapshot.get("family_concentration", 0.0))
    uniq = float(snapshot.get("uniqueness_rate", 0.0))
    stalled = int(snapshot.get("pareto_size_unchanged_gens", 0)) >= 2

    if conc > 0.5:
        new = min(cur + 5.0, 10.0)
        return SteerAction(
            action="weight",
            args={"key": "family_dedup", "weight": new},
            reason=f"w_rule_up: family_concentration {conc:.2f} > 0.5, w {cur:g}->{new:g}",
        )
    if uniq > 0.9 and stalled:
        new = max(cur - 5.0, 0.0)
        return SteerAction(
            action="weight",
            args={"key": "family_dedup", "weight": new},
            reason=f"w_rule_down: uniqueness {uniq:.2f} > 0.9 and front stalled, "
                   f"w {cur:g}->{new:g}",
        )
    return SteerAction(
        action="noop", args={},
        reason=f"w_rule_hold: conc {conc:.2f} <= 0.5, uniqueness {uniq:.2f} / front active",
    )


def _action_key(action: SteerAction) -> str:
    """Stable serialization key for matching against outcome dicts.

    Uses compact separators (no whitespace) so callers can hand-write keys
    like `'{"action":"ban","args":{"element":"Au"}}'` in test fixtures and
    short outcome files. ``sort_keys`` makes element-order irrelevant.
    """
    return json.dumps(
        {"action": action.action, "args": action.args},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def oracle_decide(
    snapshot: dict,
    all_candidate_actions: list[SteerAction],
    next_gen_outcomes: dict[str, dict],
) -> SteerAction:
    """Post-hoc upper bound: pick the action that maximized ΔSUN.

    Requires a fully-measured trajectory. Used only for the ablation's
    upper-bound baseline; never deployed in live search.
    """
    if not all_candidate_actions:
        return SteerAction(action="noop", args={}, reason="oracle: no candidates")
    best = None
    best_score = float("-inf")
    for a in all_candidate_actions:
        score = next_gen_outcomes.get(_action_key(a), {}).get("delta_sun", float("-inf"))
        if score > best_score:
            best_score = score
            best = a
    return best  # type: ignore[return-value]


# ============ steering_parity3:非平稳双相靶向(相位切换 + noise/ML 规则) ============
# 预注册 docs/exp-plan/steering_parity3_dualphase.md(2026-09-19,跑前写死)。
# 关键纪律:w 恒 10 不进旋钮(parity-2 已证 w∈(0,10] CDP 保序无效);
# 判级永远按名义 Phase B 带 [2.7,3.3],边界微调只影响违反判定(可行性管理)。

PHASE_A_BAND = (1.5, 2.0)
PHASE_B_BAND = (2.7, 3.3)
PHASE_SWITCH_INBAND_TRIGGER = 0.60   # Phase A 带内率 ≥0.60 即切 B
PHASE_SWITCH_HARD_GEN = 8            # 硬上限:gen 8 未触发则强制切换(防死锁)
PARITY3_BAND_WEIGHT = 10.0           # w 恒 10

NOISE_BOUNDS = (0.1, 0.5)
NOISE_RULE_STEP = 0.1
NOISE_RULE_VIOLATION_TRIGGER = 0.6   # off-viol(=constraint_violation_rate)> 0.6
NOISE_RULE_PLATEAU_GENS = 2          # SUN/inband 平台 ≥2 代

ML_RULE_POSTSWITCH_WINDOW = 2        # 规则①只在切换后 2 代内允许放宽
ML_RULE_WIDEN_TRIGGER = 0.6          # 切换窗口内 violation > 0.6 → 放宽 +0.3
ML_RULE_WIDEN_MAX = 0.3              # 边界微调 ⊆ 名义带 ±0.3(与 §3 杠杆合法范围一致)
ML_RULE_WIDEN_RETRACT = 0.15         # inband > 0.1 后每代收回 0.15
ML_RULE_INBAND_RETRACT_TRIGGER = 0.1
ML_RULE_VIOLATION_TRIGGER = 0.3      # 规则② off-viol > 0.3 → noise −0.05
ML_RULE_NOISE_STEP = 0.05
ML_RULE_FAMILY_TRIGGER = 0.3         # 规则④ 最大家族占比 > 0.3 → ban 主元素
ML_RULE_MAX_BANS = 2                 # 每 run ≤2 次 ban


def phase_switch_decide(snapshot: dict, phase_state: dict) -> dict:
    """环境事件判定(所有臂一致,非调度决策):Phase A → B 是否切换。

    数据源 constraint_key_stats.bandgap.inband_rate(Phase A 约束下的带内率,
    分母含性质缺失个体,保守口径)。返回 dict 而非 SteerAction —— 这是环境
    事件不是 steering,但复用 steer 通道下发(所有臂同步边界变化)。
    """
    if phase_state.get("phase") != "A":
        return {"switch": False, "forced": False,
                "reason": f"phase={phase_state.get('phase')}, no switch"}
    gen = int(snapshot.get("generation", 0))
    stats = (snapshot.get("constraint_key_stats") or {}).get("bandgap") or {}
    inband = float(stats.get("inband_rate", 0.0))
    if inband >= PHASE_SWITCH_INBAND_TRIGGER:
        return {"switch": True, "forced": False, "gen": gen, "inband_rate": inband,
                "reason": f"inband_rate {inband:.2f} >= {PHASE_SWITCH_INBAND_TRIGGER}"}
    if gen >= PHASE_SWITCH_HARD_GEN:
        return {"switch": True, "forced": True, "gen": gen, "inband_rate": inband,
                "reason": f"hard cap gen {gen} >= {PHASE_SWITCH_HARD_GEN}, force switch"}
    return {"switch": False, "forced": False, "gen": gen, "inband_rate": inband,
            "reason": f"inband_rate {inband:.2f} < trigger, gen {gen} < hard cap"}


def _bandgap_inband(snapshot: dict) -> float:
    stats = (snapshot.get("constraint_key_stats") or {}).get("bandgap") or {}
    return round(float(stats.get("inband_rate", 0.0)), 4)


def _plateau_streak(snapshot: dict, prior_stats: list) -> int:
    """(SUN, bandgap inband 率)二元组连续未变的代数(不含当前代)。

    prior_stats 为之前各代的二元组(旧→新)。streak ≥ NOISE_RULE_PLATEAU_GENS
    即「平台 ≥2 代」。inband 率四舍五入到 1e-4 后比较,容忍浮点噪声。
    """
    cur = (snapshot.get("sun_count"), _bandgap_inband(snapshot))
    streak = 0
    for prev in reversed(prior_stats or []):
        if prev == cur:
            streak += 1
        else:
            break
    return streak


def noise_rule_decide(snapshot: dict, config: dict, prior_stats: list) -> SteerAction:
    """noise-rule 臂(单杠杆规则,补 parity-2 缺失对照)。预注册 §4:

      - off-viol(= snapshot.constraint_violation_rate) > 0.6 → noise −0.1
      - SUN/inband 平台 ≥2 代 → noise +0.1
      - 边界 [0.1, 0.5];步进后若被边界夹住无变化 → noop(不记假动作)。

    soften-first:违反率规则优先于平台规则(parity-2「软化优先」教训,
    与 ML-rule 臂 ②→③ 的预注册优先级一致)。noise 语义为绝对值
    (steer --noise-level 写回 config)。
    """
    cur = float((config or {}).get("noise_level", 0.3))
    lo, hi = NOISE_BOUNDS
    viol = float(snapshot.get("constraint_violation_rate", 0.0))
    if viol > NOISE_RULE_VIOLATION_TRIGGER:
        new = round(max(cur - NOISE_RULE_STEP, lo), 4)
        if new == cur:
            return SteerAction("noop", {},
                               f"noise_rule: violation {viol:.2f} but noise already at lo {lo}")
        return SteerAction("noise", {"level": new},
                           f"noise_rule: off-viol {viol:.2f} > 0.6 -> noise {cur}->{new}")
    if _plateau_streak(snapshot, prior_stats) >= NOISE_RULE_PLATEAU_GENS:
        new = round(min(cur + NOISE_RULE_STEP, hi), 4)
        if new == cur:
            return SteerAction("noop", {},
                               f"noise_rule: plateau but noise already at hi {hi}")
        return SteerAction("noise", {"level": new},
                           f"noise_rule: SUN/inband plateau >=2 gens -> noise {cur}->{new}")
    return SteerAction("noop", {},
                       f"noise_rule: healthy (viol {viol:.2f}, noise {cur})")


def _current_widen_offset(constraints: dict, nominal: tuple) -> float:
    """当前边界相对名义带的放宽量(对称放宽假设下取两侧最大值)。"""
    rules = (constraints or {}).get("bandgap") or {}
    cur_lo, cur_hi = rules.get("min"), rules.get("max")
    if cur_lo is None or cur_hi is None:
        return 0.0
    return round(max(nominal[0] - float(cur_lo), float(cur_hi) - nominal[1]), 4)


def _widen_band(nominal: tuple, offset: float) -> dict:
    """名义带两侧对称放宽 offset(夹在 [0, ML_RULE_WIDEN_MAX])→ 规则 dict。"""
    offset = min(max(offset, 0.0), ML_RULE_WIDEN_MAX)
    return {"min": round(nominal[0] - offset, 4), "max": round(nominal[1] + offset, 4),
            "weight": PARITY3_BAND_WEIGHT}


def ml_rule_decide(snapshot: dict, constraints: dict, config: dict,
                   prior_stats: list, phase_state: dict) -> SteerAction:
    """ML-rule 臂(多杠杆阈值规则,预注册 §4,优先级从上到下,每代至多一个动作):

      ① 切换后 2 代内 violation>0.6 → 边界放宽 +0.3;放宽后 inband>0.1 → 每代收回 0.15
      ② off-viol(= constraint_violation_rate)>0.3 → noise −0.05
      ③ SUN/inband 平台 ≥2 代 → noise +0.1
      ④ 最大家族占比 >0.3 → ban 该族主元素(≤2 次/run)

    无独立状态文件:放宽量从 constraints 当前带 vs 名义带推出(ban 次数从
    element_set.banned 长度推出),断电重跑不丢账。noise 为绝对值语义。
    """
    phase = phase_state.get("phase", "A")
    nominal = PHASE_B_BAND if phase == "B" else PHASE_A_BAND
    gen = int(snapshot.get("generation", 0))
    switch_gen = phase_state.get("switch_gen")
    viol = float(snapshot.get("constraint_violation_rate", 0.0))
    inband = _bandgap_inband(snapshot)
    offset = _current_widen_offset(constraints, nominal)

    # ① 边界放宽/收回(仅 Phase B;放宽只在切换后窗口内,收回不限窗)
    if phase == "B" and switch_gen is not None:
        since = gen - int(switch_gen)
        if since <= ML_RULE_POSTSWITCH_WINDOW and viol > ML_RULE_WIDEN_TRIGGER \
                and offset < ML_RULE_WIDEN_MAX:
            new_band = _widen_band(nominal, offset + ML_RULE_WIDEN_MAX)
            return SteerAction("constraint", {"key": "bandgap", **new_band},
                               f"ml_rule_1: {since} gen(s) post-switch, viol {viol:.2f} > "
                               f"{ML_RULE_WIDEN_TRIGGER} -> widen band to "
                               f"[{new_band['min']}, {new_band['max']}]")
        if offset > 0 and inband > ML_RULE_INBAND_RETRACT_TRIGGER:
            new_band = _widen_band(nominal, offset - ML_RULE_WIDEN_RETRACT)
            return SteerAction("constraint", {"key": "bandgap", **new_band},
                               f"ml_rule_1: inband {inband:.2f} > "
                               f"{ML_RULE_INBAND_RETRACT_TRIGGER} -> retract band to "
                               f"[{new_band['min']}, {new_band['max']}]")

    # ② 违反率高 → 降 noise(软化优先)
    cur = float((config or {}).get("noise_level", 0.3))
    lo, hi = NOISE_BOUNDS
    if viol > ML_RULE_VIOLATION_TRIGGER:
        new = round(max(cur - ML_RULE_NOISE_STEP, lo), 4)
        if new != cur:
            return SteerAction("noise", {"level": new},
                               f"ml_rule_2: off-viol {viol:.2f} > {ML_RULE_VIOLATION_TRIGGER} "
                               f"-> noise {cur}->{new}")

    # ③ 平台 → 升 noise
    if _plateau_streak(snapshot, prior_stats) >= NOISE_RULE_PLATEAU_GENS:
        new = round(min(cur + NOISE_RULE_STEP, hi), 4)
        if new != cur:
            return SteerAction("noise", {"level": new},
                               f"ml_rule_3: SUN/inband plateau >=2 gens -> noise {cur}->{new}")

    # ④ 家族坍缩 → ban 主导族主元素
    fam = snapshot.get("dominant_family") or {}
    banned = list(((constraints or {}).get("element_set") or {}).get("banned") or [])
    if float(fam.get("share", 0.0)) > ML_RULE_FAMILY_TRIGGER and fam.get("main_element") \
            and len(banned) < ML_RULE_MAX_BANS and fam["main_element"] not in banned:
        return SteerAction("ban", {"element": fam["main_element"]},
                           f"ml_rule_4: family {fam.get('bucket')} share "
                           f"{fam['share']:.2f} > {ML_RULE_FAMILY_TRIGGER} -> ban "
                           f"{fam['main_element']} ({len(banned)}/{ML_RULE_MAX_BANS} bans)")

    return SteerAction("noop", {},
                       f"ml_rule: hold (phase {phase}, viol {viol:.2f}, inband {inband:.2f}, "
                       f"conc {float((snapshot.get('family_concentration') or 0.0)):.2f})")
