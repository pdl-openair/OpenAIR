# scripts/tests/test_steering_parity3.py
"""steering_parity3 新增件的单元测试:
相位切换判定 / noise-rule / ML-rule / 旋钮秩扰动审计(选择层)。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audit_rank_perturbation import audit_allow_only, audit_boundary  # noqa: E402
from steering_modes import (  # noqa: E402
    ML_RULE_MAX_BANS,
    NOISE_BOUNDS,
    PHASE_A_BAND,
    PHASE_B_BAND,
    _plateau_streak,
    _widen_band,
    ml_rule_decide,
    noise_rule_decide,
    phase_switch_decide,
)


def _snap(gen=3, sun=5, inband=0.3, viol=0.2, conc=0.2, fam=None):
    return {
        "generation": gen,
        "sun_count": sun,
        "constraint_violation_rate": viol,
        "constraint_key_stats": {"bandgap": {"inband_rate": inband,
                                             "violation_rate": viol}},
        "family_concentration": conc,
        "dominant_family": fam or {"bucket": None, "share": 0.0, "main_element": None},
    }


def _cfg(noise=0.3):
    return {"noise_level": noise}


# ================= phase_switch_decide =================

def test_phase_switch_by_inband_trigger() -> None:
    d = phase_switch_decide(_snap(gen=4, inband=0.61), {"phase": "A"})
    assert d["switch"] and not d["forced"] and d["gen"] == 4


def test_phase_switch_hard_cap_forces() -> None:
    d = phase_switch_decide(_snap(gen=8, inband=0.05), {"phase": "A"})
    assert d["switch"] and d["forced"]


def test_phase_switch_no_trigger_below_threshold() -> None:
    d = phase_switch_decide(_snap(gen=4, inband=0.59), {"phase": "A"})
    assert not d["switch"]


def test_phase_switch_not_in_phase_b() -> None:
    d = phase_switch_decide(_snap(gen=9, inband=1.0), {"phase": "B", "switch_gen": 5})
    assert not d["switch"]


# ================= noise_rule_decide =================

def test_noise_rule_violation_lowers_noise() -> None:
    a = noise_rule_decide(_snap(viol=0.7), _cfg(0.3), [])
    assert a.action == "noise" and a.args["level"] == 0.2


def test_noise_rule_violation_clamped_at_lo() -> None:
    a = noise_rule_decide(_snap(viol=0.7), _cfg(0.15), [])
    assert a.action == "noise" and a.args["level"] == NOISE_BOUNDS[0]


def test_noise_rule_violation_at_bound_is_noop() -> None:
    a = noise_rule_decide(_snap(viol=0.7), _cfg(0.1), [])
    assert a.action == "noop"


def test_noise_rule_plateau_raises_noise() -> None:
    snap = _snap(sun=5, inband=0.3, viol=0.1)
    prior = [(5, 0.3), (5, 0.3)]
    a = noise_rule_decide(snap, _cfg(0.3), prior)
    assert a.action == "noise" and a.args["level"] == 0.4


def test_noise_rule_plateau_needs_both_signals_flat() -> None:
    snap = _snap(sun=5, inband=0.3, viol=0.1)
    assert noise_rule_decide(snap, _cfg(0.3), [(5, 0.3), (6, 0.3)]).action == "noop"
    assert noise_rule_decide(snap, _cfg(0.3), [(5, 0.3), (5, 0.4)]).action == "noop"
    assert noise_rule_decide(snap, _cfg(0.3), [(5, 0.3)]).action == "noop"  # 仅 1 代


def test_noise_rule_soften_first_priority() -> None:
    """违反与平台同时满足:违反规则优先(降噪),不升噪。"""
    snap = _snap(sun=5, inband=0.3, viol=0.7)
    a = noise_rule_decide(snap, _cfg(0.3), [(5, 0.3), (5, 0.3)])
    assert a.action == "noise" and a.args["level"] == 0.2


def test_noise_rule_healthy_noop() -> None:
    assert noise_rule_decide(_snap(viol=0.2), _cfg(0.3), []).action == "noop"


# ================= ml_rule_decide =================

def _cons(band=PHASE_B_BAND, banned=None, offset=0.0):
    return {
        "bandgap": {"min": round(band[0] - offset, 4), "max": round(band[1] + offset, 4),
                    "weight": 10},
        "num_elements": {"min": 2, "weight": 10},
        "comp_valid": {"expected": True, "weight": 10},
        "element_set": {"banned": banned or []},
    }


def _pstate(phase="B", switch_gen=5):
    return {"phase": phase, "switch_gen": switch_gen}


def test_ml_rule_widen_in_postswitch_window() -> None:
    snap = _snap(gen=6, viol=0.7, inband=0.0)  # 切换后 1 代
    a = ml_rule_decide(snap, _cons(), _cfg(), [], _pstate())
    assert a.action == "constraint"
    assert a.args["min"] == pytest.approx(PHASE_B_BAND[0] - 0.3)
    assert a.args["max"] == pytest.approx(PHASE_B_BAND[1] + 0.3)
    assert a.args["weight"] == 10  # w 恒 10


def test_ml_rule_no_widen_outside_window() -> None:
    snap = _snap(gen=8, viol=0.7, inband=0.0)  # 切换后 3 代,窗口已过
    a = ml_rule_decide(snap, _cons(), _cfg(), [], _pstate())
    assert a.action != "constraint" or a.args["min"] > PHASE_B_BAND[0] - 0.31


def test_ml_rule_no_double_widen() -> None:
    snap = _snap(gen=6, viol=0.9, inband=0.0)
    a = ml_rule_decide(snap, _cons(offset=0.3), _cfg(), [], _pstate())
    assert a.action != "constraint"


def test_ml_rule_retract_when_inband_recovers() -> None:
    snap = _snap(gen=7, viol=0.2, inband=0.2)
    a = ml_rule_decide(snap, _cons(offset=0.3), _cfg(), [], _pstate())
    assert a.action == "constraint"
    assert a.args["min"] == pytest.approx(PHASE_B_BAND[0] - 0.15)  # 收回 0.15
    assert a.args["max"] == pytest.approx(PHASE_B_BAND[1] + 0.15)


def test_ml_rule_no_widen_in_phase_a() -> None:
    snap = _snap(gen=6, viol=0.7, inband=0.0)
    a = ml_rule_decide(snap, _cons(band=PHASE_A_BAND), _cfg(), [], {"phase": "A"})
    assert a.action != "constraint"


def test_ml_rule_violation_lowers_noise_half_step() -> None:
    snap = _snap(gen=9, viol=0.4)
    a = ml_rule_decide(snap, _cons(), _cfg(0.3), [], _pstate())
    assert a.action == "noise" and a.args["level"] == 0.25


def test_ml_rule_plateau_raises_noise() -> None:
    snap = _snap(gen=9, sun=5, inband=0.3, viol=0.1)
    a = ml_rule_decide(snap, _cons(), _cfg(0.3), [(5, 0.3), (5, 0.3)], _pstate())
    assert a.action == "noise" and a.args["level"] == 0.4


def test_ml_rule_family_ban() -> None:
    fam = {"bucket": "CrSiTe3", "share": 0.4, "main_element": "Te"}
    snap = _snap(gen=9, viol=0.1, conc=0.4, fam=fam)
    a = ml_rule_decide(snap, _cons(), _cfg(), [], _pstate())
    assert a.action == "ban" and a.args["element"] == "Te"


def test_ml_rule_family_ban_capped_and_dedup() -> None:
    fam = {"bucket": "CrSiTe3", "share": 0.4, "main_element": "Te"}
    snap = _snap(gen=9, viol=0.1, conc=0.4, fam=fam)
    # 已 ban 2 个(上限)→ 不再 ban
    assert ml_rule_decide(snap, _cons(banned=["Te", "Cr"]), _cfg(), [], _pstate()).action == "noop"
    # Te 已在 ban 列表 → 不重复 ban
    assert ml_rule_decide(snap, _cons(banned=["Te"]), _cfg(), [], _pstate()).action == "noop"


def test_ml_rule_priority_boundary_over_noise() -> None:
    """规则①与②同时满足:边界优先。"""
    snap = _snap(gen=6, viol=0.7, inband=0.0)
    a = ml_rule_decide(snap, _cons(), _cfg(0.3), [], _pstate())
    assert a.action == "constraint"


def test_ml_rule_no_main_element_skips_ban() -> None:
    fam = {"bucket": "?", "share": 0.4, "main_element": None}
    snap = _snap(gen=9, viol=0.1, conc=0.4, fam=fam)
    assert ml_rule_decide(snap, _cons(), _cfg(), [], _pstate()).action == "noop"


# ================= 杠杆边界与秩扰动审计 =================

def test_widen_band_respects_legal_range() -> None:
    b = _widen_band(PHASE_B_BAND, 99.0)  # 超界夹到 ±0.3
    assert b["min"] == pytest.approx(PHASE_B_BAND[0] - 0.3)
    assert b["max"] == pytest.approx(PHASE_B_BAND[1] + 0.3)
    assert b["max"] - b["min"] >= 0.4    # 带宽下限


def test_plateau_streak_counts_consecutive() -> None:
    snap = _snap(sun=5, inband=0.3)
    assert _plateau_streak(snap, [(4, 0.3), (5, 0.2), (5, 0.3)]) == 1
    assert _plateau_streak(snap, [(5, 0.3), (5, 0.3)]) == 2


def test_audit_boundary_rank_perturbation() -> None:
    assert audit_boundary()


def test_audit_allow_only_rank_perturbation() -> None:
    assert audit_allow_only()


def test_ban_budget_constant() -> None:
    assert ML_RULE_MAX_BANS == 2  # 预注册 §3:每 run ≤2 次 ban


# ================= parity3_metrics.lever_usage =================

def _write_jsonl(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    import json as _json
    with path.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(_json.dumps(e, ensure_ascii=False) + "\n")


def test_lever_usage_classification(tmp_path) -> None:
    from parity3_metrics import lever_usage
    _write_jsonl(tmp_path / "steer_log.jsonl", [
        {"action": "constraint", "args": {"rules": ["bandgap:2.7:3.3:10"]}},   # 环境
        {"action": "constraint", "args": {"rules": ["bandgap:2.4:3.6:10"]}},   # 放宽
        {"action": "noise", "args": {"level": 0.25}},
        {"action": "constraint", "args": {"rules": ["bandgap:2.55:3.45:10"]}},  # 收回
        {"action": "ban", "args": {"element": "Te"}},
        {"action": "constraint", "args": {"rules": ["bandgap:2.7:3.3:10"]}},   # 收回至名义 → boundary
        {"action": "noop", "args": {}},
    ])
    _write_jsonl(tmp_path / "phase_switch.jsonl", [{"gen": 5, "forced": False}])
    u = lever_usage(tmp_path)
    assert u["environment_switch"] == 1
    assert u["boundary"] == 3
    assert u["noise"] == 1 and u["noise_levels"] == [0.25]
    assert u["chem"] == 1
    assert u["noop"] == 1
