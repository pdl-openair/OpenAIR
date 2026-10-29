# scripts/tests/test_parity2_exp1.py
"""steering_parity2 实验一新增件的单元测试:
family_dedup 键 / 快照调度读数 / W-rule 规则 / quota-mSUN 指标。
"""
import sys
from pathlib import Path

import numpy as np
import pytest
from pymatgen.core import Lattice, Structure

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evolve_driver import (  # noqa: E402
    _family_concentration,
    _family_dedup_k,
    _ind_bucket,
    _uniqueness_rate,
    apply_family_dedup,
)
from parity_metrics import quota_msun_flags  # noqa: E402
from steering_modes import family_weight_rule_decide  # noqa: E402


def _cif(formula_pairs, a=4.0) -> str:
    sites = []
    fracs = []
    step = 0.5
    for i, el in enumerate(formula_pairs):
        sites.append(el)
        fracs.append([i * step, i * step, i * step])
    return Structure(Lattice.cubic(a), sites, fracs).to(fmt="cif")


def _nacl(a=4.0) -> str:
    return _cif(["Na", "Cl"], a=a)


def _kcl() -> str:
    return _cif(["K", "Cl"], a=4.2)


# ================= apply_family_dedup =================

def test_family_dedup_first_k_in_batch_exempt() -> None:
    inds = [{"cif": _nacl(a=4.0 + 0.01 * i)} for i in range(5)]
    apply_family_dedup(inds, ref_pops=None, k=3)
    assert [x["family_dedup"] for x in inds] == [True, True, True, False, False]


def test_family_dedup_refs_preoccupy_quota() -> None:
    ref_pop = [{"cif": _nacl(a=3.9 + 0.01 * i)} for i in range(3)]
    inds = [{"cif": _nacl()}, {"cif": _kcl()}]
    apply_family_dedup(inds, ref_pops=[ref_pop], k=3)
    assert inds[0]["family_dedup"] is False  # NaCl 桶已被 ref 占满 3 个
    assert inds[1]["family_dedup"] is True


def test_family_dedup_independent_buckets() -> None:
    inds = [{"cif": _nacl()}, {"cif": _kcl()}, {"cif": _nacl(a=4.1)}, {"cif": _kcl()}]
    apply_family_dedup(inds, ref_pops=None, k=2)
    assert [x["family_dedup"] for x in inds] == [True, True, True, True]


def test_family_dedup_unparseable_is_violation() -> None:
    inds = [{"cif": "not a cif"}]
    apply_family_dedup(inds, ref_pops=None, k=3)
    assert inds[0]["family_dedup"] is False


def test_family_dedup_default_k_three() -> None:
    assert _family_dedup_k({}) == 3
    assert _family_dedup_k({"family_dedup": {"expected": True, "weight": 5}}) == 3
    assert _family_dedup_k({"family_dedup": {"weight": 5, "k": 7}}) == 7


# ================= 快照调度读数 =================

def _ind(cif: str) -> dict:
    return {"cif": cif, "structure_obj": Structure.from_str(cif, fmt="cif")}


def test_family_concentration_max_bucket_share() -> None:
    pop = [_ind(_nacl()), _ind(_nacl(a=4.05)), _ind(_nacl(a=4.1)), _ind(_kcl())]
    assert _family_concentration(pop) == pytest.approx(0.75)
    assert _family_concentration([]) == 0.0


def test_ind_bucket_prefers_structure_obj() -> None:
    assert _ind_bucket(_ind(_nacl())) == "NaCl"
    assert _ind_bucket({"cif": "junk"}) == "?"


def test_uniqueness_rate_identical_vs_distinct() -> None:
    dup = [_ind(_nacl())] * 4
    assert _uniqueness_rate(dup) == pytest.approx(0.25)
    distinct = [_ind(_nacl()), _ind(_kcl()),
                _ind(_cif(["Cs", "Cl"], a=4.5)), _ind(_cif(["Li", "Cl"], a=3.8))]
    assert _uniqueness_rate(distinct) == pytest.approx(1.0)
    assert _uniqueness_rate([]) == 0.0


# ================= W-rule 规则 =================

def _wrule_snap(**kw) -> dict:
    base = {
        "generation": 3,
        "family_concentration": 0.0,
        "uniqueness_rate": 0.0,
        "pareto_size_unchanged_gens": 0,
    }
    base.update(kw)
    return base


_WCONS = {"family_dedup": {"expected": True, "weight": 5, "k": 3}}


def test_wrule_noop_when_knob_absent() -> None:
    action = family_weight_rule_decide(_wrule_snap(family_concentration=0.9), {})
    assert action.action == "noop"


def test_wrule_tightens_on_collapse() -> None:
    action = family_weight_rule_decide(_wrule_snap(family_concentration=0.55), _WCONS)
    assert action.action == "weight"
    assert action.args == {"key": "family_dedup", "weight": 10.0}


def test_wrule_tighten_capped_at_ten() -> None:
    cons = {"family_dedup": {"expected": True, "weight": 8, "k": 3}}
    action = family_weight_rule_decide(_wrule_snap(family_concentration=0.6), cons)
    assert action.args["weight"] == 10.0


def test_wrule_relaxes_on_unique_stagnation() -> None:
    snap = _wrule_snap(uniqueness_rate=0.95, pareto_size_unchanged_gens=2)
    action = family_weight_rule_decide(snap, _WCONS)
    assert action.action == "weight"
    assert action.args["weight"] == 0.0


def test_wrule_relax_floor_zero_and_requires_stall() -> None:
    # 不停滞则不放松
    snap = _wrule_snap(uniqueness_rate=0.95, pareto_size_unchanged_gens=1)
    assert family_weight_rule_decide(snap, _WCONS).action == "noop"
    # 已在 0 且规则再降:保持 0(下界)
    cons = {"family_dedup": {"expected": True, "weight": 0, "k": 3}}
    snap = _wrule_snap(uniqueness_rate=0.95, pareto_size_unchanged_gens=3)
    action = family_weight_rule_decide(snap, cons)
    assert action.args["weight"] == 0.0


def test_wrule_collapse_wins_over_relax() -> None:
    snap = _wrule_snap(family_concentration=0.7, uniqueness_rate=0.95,
                       pareto_size_unchanged_gens=3)
    action = family_weight_rule_decide(snap, _WCONS)
    assert action.args["weight"] == 10.0  # 收紧优先


def test_wrule_hold_in_deadzone() -> None:
    snap = _wrule_snap(family_concentration=0.5, uniqueness_rate=0.8)
    action = family_weight_rule_decide(snap, _WCONS)
    assert action.action == "noop"
    assert "hold" in action.reason


# ================= quota-mSUN =================

def test_quota_caps_per_bucket() -> None:
    n = np.ones(5)
    buckets = ["NaCl"] * 5
    q = quota_msun_flags(np.ones(5), n, np.ones(5), buckets, k=3)
    assert q.sum() == 3.0
    assert q[:3].tolist() == [1.0, 1.0, 1.0]  # 先到先得


def test_quota_independent_buckets() -> None:
    buckets = ["NaCl", "KCl", "NaCl", "KCl", "NaCl"]
    q = quota_msun_flags(np.ones(5), np.ones(5), np.ones(5), buckets, k=2)
    assert q.sum() == 4.0  # NaCl 2 + KCl 2,第 5 个 NaCl 超额


def test_quota_nan_propagates_and_unknown_bucket_zeroed() -> None:
    novel = np.array([1.0, np.nan, 1.0])
    buckets = ["NaCl", "NaCl", None]
    q = quota_msun_flags(np.ones(3), novel, np.ones(3), buckets, k=3)
    assert q[0] == 1.0
    assert np.isnan(q[1])
    assert q[2] == 0.0  # 无法分桶:保守按超额


def test_quota_not_msun_never_counted() -> None:
    metastable = np.array([1.0, 0.0, 1.0])
    buckets = ["NaCl"] * 3
    q = quota_msun_flags(metastable, np.ones(3), np.ones(3), buckets, k=10)
    assert q.sum() == 2.0  # 非 mSUN 的不进配额计数,但也不被标记
