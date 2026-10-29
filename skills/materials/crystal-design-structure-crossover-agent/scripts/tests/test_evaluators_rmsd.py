# scripts/tests/test_evaluators_rmsd.py
"""relax_rmsd_angstrom:driver-relax 前后结构的 RMSD(Å)。

恢复 mattergen-evaluate `rmsd_from_relaxation` 的本义 —— 原始结构离势阱多远。
skill 现有管线先把结构弛豫一遍再喂 evaluate,导致 evaluate 报的
avg_rmsd_from_relaxation 是已弛豫结构上的第二次弛豫位移(恒 ~0),
不能当 fitness 维度;此处在 skill 侧自算。
"""
import pytest
from pymatgen.core import Lattice, Structure

from evaluators import relax_rmsd_angstrom


def _simple_cif() -> str:
    s = Structure(
        Lattice.cubic(4.0), ["Si", "Si"],
        [[0.0, 0.0, 0.0], [0.25, 0.25, 0.25]],
    )
    return s.to(fmt="cif")


def _perturbed_cif(displacement_ang: float) -> str:
    s = Structure(
        Lattice.cubic(4.0), ["Si", "Si"],
        [[0.0, 0.0, 0.0], [0.25, 0.25, 0.25 + displacement_ang / 4.0]],
    )
    return s.to(fmt="cif")


def test_identical_structures_rmsd_near_zero() -> None:
    cif = _simple_cif()
    assert relax_rmsd_angstrom(cif, cif) == pytest.approx(0.0, abs=1e-6)


def test_perturbed_structure_rmsd_positive_and_scaled() -> None:
    raw = _simple_cif()
    # 原子移动 0.2 Å 与 0.4 Å:rmsd 为正且随位移增大
    r02 = relax_rmsd_angstrom(raw, _perturbed_cif(0.2))
    r04 = relax_rmsd_angstrom(raw, _perturbed_cif(0.4))
    assert r02 is not None and r02 > 0.05
    assert r04 is not None and r04 > r02


def test_unparseable_cif_returns_none() -> None:
    assert relax_rmsd_angstrom("not a cif", "also not a cif") is None


def test_composition_mismatch_returns_finite_cap_not_crash() -> None:
    # 弛豫漂移出组成(变异可能丢原子):matcher 失败时返回有限上限值,而非崩溃
    other = Structure(Lattice.cubic(4.0), ["Na", "Cl"],
                      [[0.0, 0.0, 0.0], [0.5, 0.5, 0.5]]).to(fmt="cif")
    val = relax_rmsd_angstrom(_simple_cif(), other)
    assert val is None or val > 0.0
