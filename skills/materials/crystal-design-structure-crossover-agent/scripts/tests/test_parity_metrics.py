# tests/test_parity_metrics.py
"""evo_sun_parity 指标管线的离线单元测试（不依赖 MCP）。

覆盖：uniqueness 先到先得判重、AMD 向量单调性、FMD⁻¹ 区分度、
评估失败个体（ehull=None）的分母剔除、collect_arm 简单目录摄入。
真实 CIF 取自 mattergen-base-200 的固定几个（仓库内测试数据）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from evaluators import cif_to_structure  # noqa: E402
from parity_metrics import (  # noqa: E402
    _amd_vector,
    analyze_arm,
    collect_arm,
    fmd_inv,
    uniqueness_flags,
)

CIF_DIR = (Path(__file__).parent.parent.parent.parent.parent
           / "mattergen-results" / "mattergen-base-200" / "generated_crystals_cif")

pytestmark = pytest.mark.skipif(
    not CIF_DIR.exists(), reason="测试 CIF 数据不在本仓库检出里"
)


def _cif(i: int) -> str:
    return (CIF_DIR / f"gen_{i}.cif").read_text(encoding="utf-8")


def test_uniqueness_first_wins():
    samples = [{"cif": _cif(0), "name": "a"},
               {"cif": _cif(0), "name": "a_dup"},
               {"cif": _cif(1), "name": "b"}]
    flags = uniqueness_flags(samples)
    assert flags.tolist() == [1.0, 0.0, 1.0]


def test_amd_sorted_and_dim():
    v = _amd_vector(cif_to_structure(_cif(0)), k=100)
    assert len(v) == 100
    assert all(x <= y for x, y in zip(v, v[1:]))  # 近邻距离序列应单调不减


def test_fmd_discriminates():
    x = np.array([_amd_vector(cif_to_structure(_cif(i)), 100) for i in (0, 1)])
    rng = np.random.default_rng(0)
    same = fmd_inv(x, x + rng.normal(0, 1e-6, x.shape))
    diff = fmd_inv(x, x + rng.normal(0, 1.0, x.shape))
    assert same > 0.99 > diff


def test_analyze_arm_masks_failed_evals():
    # 6 个体：1 个 ehull=None（评估失败）——metastability/msun 分母应为 5
    samples = [
        {"name": "a", "cif": _cif(0), "ehull_eval": 0.05, "novel": 1.0, "comp_valid_eval": 1.0},
        {"name": "b", "cif": _cif(1), "ehull_eval": 0.20, "novel": 1.0, "comp_valid_eval": 1.0},
        {"name": "c", "cif": _cif(2), "ehull_eval": None, "novel": 1.0, "comp_valid_eval": 1.0},
    ]
    out = analyze_arm("t", samples, boot=50, rng=np.random.default_rng(1))
    assert out["metastability"]["n"] == 2
    assert out["msun"]["n"] == 2
    assert out["metastability"]["point"] == 0.5


def test_collect_arm_simple_dir(tmp_path):
    d = tmp_path / "rw_rep0"
    d.mkdir()
    for i, src in enumerate((0, 7)):
        (d / f"rw0_chain{i}_final.cif").write_text(_cif(src), encoding="utf-8")
    (d / "per_structure_metrics.json").write_text(
        '[{"name": "rw0_chain0", "ehull_eval": 0.05, "novel": 1.0,'
        ' "stable": 1.0, "comp_valid_eval": 1.0},'
        ' {"name": "rw0_chain1", "ehull_eval": 0.3, "novel": 1.0,'
        ' "stable": 0.0, "comp_valid_eval": 1.0}]', encoding="utf-8")
    samples = collect_arm(str(d), hof_only=False)
    assert len(samples) == 2
    assert all("cif" in s for s in samples)
