# scripts/tests/test_evaluators_batch_eval.py
"""mattergen_eval 批量评估路径的离线单测(mock MCP,不碰真实 server)。

覆盖:
1. 批量路径:per_structure 行按 index 对齐到个体,键取值正确(ehull_eval/ehull_sat/
   novel/stable/unique≡1/comp_valid_eval/structure_valid)
2. 分块:EVALUATE_BATCH_SIZE < 个体数时按块多次调用
3. 行缺失(server 端该 CIF 解析失败)→ 该个体 evaluate 键全 None
4. 老 server 回退:响应无 per_structure → batch-of-1 逐个调用,聚合口径不变
5. 批量调用抛异常 → 键全 None,不崩
"""
import os

import pytest
from pymatgen.core import Lattice, Structure

import evaluators
from evaluators import mattergen_eval_characterize_batch


def _simple_cif(formula="Si", a=5.4) -> str:
    st = Structure(Lattice.cubic(a), [formula], [[0, 0, 0]])
    return st.to(fmt="cif")


@pytest.fixture(autouse=True)
def _no_relax(monkeypatch):
    """绕过 mattersim-relax:直接返回原始 CIF(评估路径不关心弛豫真假)。"""
    monkeypatch.setattr(
        evaluators, "relax_cif", lambda cif, eps, name="": (cif, {"ok": True})
    )
    # relax 成功时 rmsd_relax 走自算路径;mock 掉避免 StructureMatcher 开销
    monkeypatch.setattr(evaluators, "relax_rmsd_angstrom", lambda a, b: 0.0)


def _fake_async_response(rows):
    return {
        "value": None,  # engine 信封里无 value 键也无妨;这里只模拟成功路径字段
        "num_evaluated": len(rows),
        "n_failed_jobs": 0,
        "metrics": {"avg_energy_above_hull_per_atom": 0.0},
        "per_structure": rows,
    }


def test_batch_per_structure_alignment(monkeypatch):
    calls = []

    def fake_async(endpoint, submit, poll, arguments, *, total_wait, interval):
        calls.append(arguments)
        n = len(arguments["structures"])
        # 每块返回 n 行,index 与输入同序
        rows = [
            {
                "index": j,
                "formula": "Si",
                "energy_above_hull_per_atom": 0.05 * (j + 1),
                "novel": j % 2 == 0,
                "stable": True,
                "comp_validity": True,
                "structure_validity": True,
            }
            for j in range(n)
        ]
        return _fake_async_response(rows)

    monkeypatch.setattr(evaluators, "call_async_tool", fake_async)
    monkeypatch.delenv("EVALUATE_BATCH_SIZE", raising=False)

    items = [(_simple_cif(), f"ind{i}") for i in range(3)]
    out = mattergen_eval_characterize_batch(items, ["ehull_eval"], {"evaluate": "x"})

    assert len(calls) == 1 and len(calls[0]["structures"]) == 3
    assert out[0]["ehull_eval"] == pytest.approx(0.05)
    assert out[1]["ehull_eval"] == pytest.approx(0.10)
    # ehull_sat = clip(ehull, 0, 0.1)
    assert out[0]["ehull_sat"] == pytest.approx(0.05)
    assert out[2]["ehull_sat"] == pytest.approx(0.1)  # 0.15 -> clip 到 0.1
    assert out[0]["novel"] == 1.0 and out[1]["novel"] == 0.0
    assert all(x["stable"] == 1.0 for x in out)
    assert all(x["unique"] == 1.0 for x in out)  # ≡1 约定
    assert all(x["comp_valid_eval"] == 1.0 for x in out)


def test_chunking(monkeypatch):
    monkeypatch.setenv("EVALUATE_BATCH_SIZE", "2")
    sizes = []

    def fake_async(endpoint, submit, poll, arguments, *, total_wait, interval):
        sizes.append(len(arguments["structures"]))
        rows = [{"index": j, "novel": True, "stable": True} for j in range(sizes[-1])]
        return _fake_async_response(rows)

    monkeypatch.setattr(evaluators, "call_async_tool", fake_async)
    items = [(_simple_cif(), f"ind{i}") for i in range(5)]
    out = mattergen_eval_characterize_batch(items, ["ehull_eval"], {"evaluate": "x"})
    assert sizes == [2, 2, 1]
    assert all(x["novel"] == 1.0 for x in out)


def test_missing_row_keeps_none(monkeypatch):
    def fake_async(endpoint, submit, poll, arguments, *, total_wait, interval):
        # 只返回第 0 个结构;index=1 行缺失(server 端解析失败)
        return _fake_async_response(
            [{"index": 0, "novel": True, "stable": True,
              "energy_above_hull_per_atom": 0.02}]
        )

    monkeypatch.setattr(evaluators, "call_async_tool", fake_async)
    items = [(_simple_cif(), f"ind{i}") for i in range(2)]
    out = mattergen_eval_characterize_batch(items, ["ehull_eval"], {"evaluate": "x"})
    assert out[0]["ehull_eval"] == pytest.approx(0.02)
    assert out[0]["novel"] == 1.0
    assert out[1]["ehull_eval"] is None
    assert out[1]["novel"] is None
    assert out[1]["unique"] is None  # 行缺失不触发 ≡1,保持 None


def test_legacy_server_falls_back_to_batch_of_1(monkeypatch):
    singles = []

    def fake_async(endpoint, submit, poll, arguments, *, total_wait, interval):
        # 老 server:只有聚合 metrics
        return {"num_evaluated": 1, "n_failed_jobs": 0,
                "metrics": {"avg_energy_above_hull_per_atom": 0.0,
                            "frac_novel_structures": 1.0,
                            "frac_stable_structures": 1.0,
                            "frac_unique_structures": 1.0,
                            "avg_comp_validity": 1.0,
                            "avg_structure_validity": 1.0}}

    def fake_single(endpoint, name, arguments, timeout=120):
        singles.append(arguments)
        return fake_async(None, None, None, None, total_wait=0, interval=0)

    monkeypatch.setattr(evaluators, "call_async_tool", fake_async)
    monkeypatch.setattr(evaluators, "call_tool", fake_single)
    items = [(_simple_cif(), f"ind{i}") for i in range(3)]
    out = mattergen_eval_characterize_batch(items, ["ehull_eval"], {"evaluate": "x"})
    assert len(singles) == 3  # 逐个 batch-of-1
    assert all(len(s["structures"]) == 1 for s in singles)
    assert all(x["novel"] == 1.0 and x["unique"] == 1.0 for x in out)


def test_batch_exception_keeps_none(monkeypatch):
    def fake_async(endpoint, submit, poll, arguments, *, total_wait, interval):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(evaluators, "call_async_tool", fake_async)
    items = [(_simple_cif(), f"ind{i}") for i in range(2)]
    out = mattergen_eval_characterize_batch(items, ["ehull_eval"], {"evaluate": "x"})
    assert all(x["ehull_eval"] is None and x["novel"] is None for x in out)
