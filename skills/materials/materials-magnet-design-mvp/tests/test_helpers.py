"""Unit tests for the SAGA-MVP helper scripts.

Tests:
- hhi.compute_hhi on known formulas (smoke + range)
- mag_density.compute_mag_density on a synthetic CIF
- initial_formulas.fallback (deterministic, shape only)

Does NOT touch the network. Does NOT call any MCP.

Run:
    cd skills/materials/materials-magnet-design-mvp
    pytest tests/test_helpers.py -v
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"


def _load(module_name: str, filename: str):
    path = SCRIPTS / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


hhi_mod = _load("saga_hhi", "hhi.py")
mag_mod = _load("saga_mag_density", "mag_density.py")
init_mod = _load("saga_initial_formulas", "initial_formulas.py")


# --- HHI tests ----------------------------------------------------------

def test_hhi_fe2o3_returns_tuple_pair():
    r = hhi_mod.compute_hhi("Fe2O3")
    assert r["ok"] is True
    assert r["formula"] == "Fe2O3"
    assert r["hhi"] > 0
    assert r["hhi_reserve"] > 0
    # Sanity range: HHI values in pymatgen's table are bounded
    assert 100 < r["hhi"] < 20000


def test_hhi_rare_earth_higher_than_iron():
    # Nd (rare earth) should contribute a much larger HHI than Fe alone
    nd = hhi_mod.compute_hhi("Nd2Fe14B")
    fe = hhi_mod.compute_hhi("Fe")
    assert nd["ok"] and fe["ok"]
    assert nd["hhi"] > fe["hhi"]


def test_hhi_garbage_formula_returns_ok_false():
    r = hhi_mod.compute_hhi("Xx99NotAFormula")
    assert r["ok"] is False
    assert "error" in r


# --- mag_density tests --------------------------------------------------

# Tiny synthetic CIF: 1-atom primitive cubic Fe, a=2.87 Å → volume = 23.64
FE_CIF = """data_Fe
_cell_length_a 2.87
_cell_length_b 2.87
_cell_length_c 2.87
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_symmetry_space_group_name_H-M 'P 1'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Fe1 Fe 0.0 0.0 0.0
"""


def test_mag_density_basic_volume_division():
    r = mag_mod.compute_mag_density(2.0, FE_CIF)
    assert r["ok"] is True
    assert r["n_atoms"] == 1
    assert r["volume"] == pytest.approx(2.87 ** 3, rel=1e-3)
    assert r["mag_density"] == pytest.approx(2.0 / (2.87 ** 3), rel=1e-3)


def test_mag_density_zero_magmom():
    r = mag_mod.compute_mag_density(0.0, FE_CIF)
    assert r["ok"] is True
    assert r["mag_density"] == 0.0


def test_mag_density_bad_cif():
    r = mag_mod.compute_mag_density(1.0, "not a cif at all")
    assert r["ok"] is False


# --- initial_formulas tests ---------------------------------------------

def test_fallback_returns_requested_count():
    out = init_mod.fallback(5, seed=0)
    assert len(out) == 5
    assert all("formula" in r and r.get("fallback") for r in out)


def test_fallback_is_deterministic():
    a = init_mod.fallback(10, seed=42)
    b = init_mod.fallback(10, seed=42)
    assert a == b


def test_fallback_different_seeds_differ():
    # Not strictly required, but a healthy sign the RNG works
    a = init_mod.fallback(10, seed=1)
    b = init_mod.fallback(10, seed=2)
    assert a != b
