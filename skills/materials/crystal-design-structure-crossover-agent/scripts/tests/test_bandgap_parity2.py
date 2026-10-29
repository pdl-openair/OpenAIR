# scripts/tests/test_bandgap_parity2.py
"""steering_parity2 实验二新增代码的单测:snapshot 逐约束键统计 + 带内 mSUN 聚合。"""
import numpy as np

from evolve_driver import _constraint_key_stats
from bandgap_parity2_metrics import _flag, inband_msun_flags, prop_ci


# ---------------- _constraint_key_stats(driver 快照字段) ----------------

def _pop(bgs):
    return [{"bandgap": b, "num_elements": 2} for b in bgs]


def test_constraint_key_stats_counts_none_in_denominator():
    pop = _pop([3.0, 2.0, None, None])  # 2.0 违反下界,None 计入分母
    stats = _constraint_key_stats(pop, {"bandgap": {"min": 2.7, "max": 3.3, "weight": 5}})
    s = stats["bandgap"]
    assert s["n_known"] == 2
    assert s["violation_rate"] == 0.25  # 1/4,None 不算违反键值但占分母
    assert s["inband_rate"] == 0.25
    assert s["median"] == 2.5


def test_constraint_key_stats_skips_non_range_rules():
    pop = _pop([3.0])
    stats = _constraint_key_stats(
        pop, {"comp_valid": {"expected": True, "weight": 10},
              "num_elements": {"min": 2, "weight": 10}})
    # 布尔规则不产出;单边 min 规则(min=2)是合法 range,带内 = v>=min
    assert set(stats) == {"num_elements"}
    assert stats["num_elements"]["inband_rate"] == 1.0
    assert stats["num_elements"]["violation_rate"] == 0.0


def test_constraint_key_stats_empty_pop():
    assert _constraint_key_stats([], {"bandgap": {"min": 2.7, "max": 3.3}}) == {
        "bandgap": {"n_known": 0, "violation_rate": 0.0, "inband_rate": 0.0,
                    "median": None},
    }


# ---------------- 带内 mSUN 聚合(密度口径) ----------------

def _rec(**kw):
    base = {"metastable": 1.0, "novel": 1.0, "inband": 1.0}
    base.update(kw)
    return base


def test_inband_msun_flag_requires_all_four():
    records = [
        _rec(),                                        # 全过
        _rec(inband=0.0),                              # 带外
        _rec(novel=None),                              # 评估失败:分子 0、占分母
        _rec(metastable=0.0),                          # 不稳定
    ]
    uniq = np.array([1.0, 1.0, 1.0, 1.0])
    flags = inband_msun_flags(records, uniq)
    assert flags.tolist() == [1.0, 0.0, 0.0, 0.0]


def test_inband_msun_respects_uniqueness():
    records = [_rec(), _rec()]
    uniq = np.array([1.0, 0.0])  # 第二个是重复结构
    flags = inband_msun_flags(records, uniq)
    assert flags.tolist() == [1.0, 0.0]


def test_flag_none_is_zero():
    assert _flag(None) == 0.0
    assert _flag(0.0) == 0.0
    assert _flag(1) == 1.0


def test_prop_ci_degenerate_all_zero():
    rng = np.random.default_rng(0)
    point, lo, hi = prop_ci(np.zeros(10), boot=200, rng=rng)
    assert point == 0.0 and lo == 0.0 and hi == 0.0


def test_prop_ci_point_estimate():
    rng = np.random.default_rng(0)
    point, _, _ = prop_ci(np.array([1.0] * 3 + [0.0] * 1), boot=200, rng=rng)
    assert point == 0.75
