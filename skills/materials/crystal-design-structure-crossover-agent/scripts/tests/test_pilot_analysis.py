# scripts/tests/test_pilot_analysis.py
import json
from pathlib import Path

from pymatgen.core import Lattice, Structure

from pilot_analysis import (
    aggregate_metrics,
    compare_modes,
    _distinct_metrics,
    _mann_whitney_u,
    _cliffs_delta,
)


def _seed_run(run_dir: Path, sun: int, ehull_median: float, mode: str, rep: int) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "snapshot_latest.json").write_text(json.dumps({
        "generation": 4,
        "sun_count": sun,
        "ehull_median": ehull_median,
        "pareto_front_size": 4,
        "stable_count": 16,
    }))
    (run_dir / "config.json").write_text(json.dumps({"mode": mode, "replicate": rep}))


def _cif_si() -> str:
    s = Structure(Lattice.cubic(4.0), ["Si", "Si"],
                  [[0.0, 0.0, 0.0], [0.25, 0.25, 0.25]])
    return s.to(fmt="cif")


def _cif_distinct() -> str:
    # different composition → different reduced-formula bucket → never a duplicate
    s = Structure(Lattice.cubic(3.0), ["C"], [[0.0, 0.0, 0.0]])
    return s.to(fmt="cif")


def _indiv(cif: str, sun: bool) -> dict:
    return {"name": None, "cif": cif,
            "novel": 1.0 if sun else 0.0, "stable": 1.0 if sun else 0.0,
            "unique": 1.0, "pareto_rank": 1}


def _seed_population(run_dir: Path, indivs: list[dict]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "population_gen10.json").write_text(json.dumps(indivs))


def test_aggregate_groups_by_mode(tmp_path: Path) -> None:
    base = tmp_path / "ablation"
    _seed_run(base / "static_rules_rep0", sun=8, ehull_median=0.020, mode="static_rules", rep=0)
    _seed_run(base / "static_rules_rep1", sun=10, ehull_median=0.015, mode="static_rules", rep=1)
    _seed_run(base / "agent_with_mem_rep0", sun=14, ehull_median=0.005, mode="agent_with_mem", rep=0)
    _seed_run(base / "agent_with_mem_rep1", sun=15, ehull_median=0.003, mode="agent_with_mem", rep=1)

    metrics = aggregate_metrics(base)
    assert "static_rules" in metrics
    assert "agent_with_mem" in metrics
    assert metrics["static_rules"]["sun_count_mean"] == 9.0
    assert metrics["agent_with_mem"]["sun_count_mean"] == 14.5
    assert metrics["agent_with_mem"]["ehull_median_mean"] < metrics["static_rules"]["ehull_median_mean"]


def test_aggregate_reads_legacy_pareto_size(tmp_path: Path) -> None:
    run_dir = tmp_path / "legacy_rep0"
    run_dir.mkdir()
    (run_dir / "snapshot_latest.json").write_text(json.dumps({
        "generation": 3, "sun_count": 5, "ehull_median": 0.01,
        "pareto_size": 2, "stable_count": 10,
    }))
    (run_dir / "config.json").write_text(json.dumps({"mode": "legacy"}))
    metrics = aggregate_metrics(tmp_path)
    assert metrics["legacy"]["pareto_front_size_mean"] == 2.0


def test_aggregate_skips_missing_runs(tmp_path: Path) -> None:
    base = tmp_path / "ab"
    base.mkdir()
    metrics = aggregate_metrics(base)
    assert metrics == {}


def test_compare_modes_fully_separated(tmp_path: Path) -> None:
    # Every with_mem replicate beats every no_mem replicate on sun_count
    for i, sun in enumerate([8, 9, 10]):
        _seed_run(tmp_path / f"agent_no_mem_rep{i}", sun=sun, ehull_median=0.02,
                  mode="agent_no_mem", rep=i)
    for i, sun in enumerate([14, 15, 16]):
        _seed_run(tmp_path / f"agent_with_mem_rep{i}", sun=sun, ehull_median=0.005,
                  mode="agent_with_mem", rep=i)
    cmp = compare_modes(tmp_path, metric="sun_count")
    key = "agent_no_mem_vs_agent_with_mem"  # pairs are emitted in sorted order
    assert key in cmp
    # a = no_mem (lower), b = with_mem (higher) → delta = -1.0
    assert cmp[key]["cliffs_delta"] == -1.0  # complete separation
    # 3v3 two-sided Mann-Whitney cannot reach p<0.05 (min ≈ 0.10) — this
    # floor is exactly why the ablation uses ≥5 replicates per mode.
    assert cmp[key]["p_value"] < 0.10


def test_compare_modes_significant_at_5v5(tmp_path: Path) -> None:
    for i, sun in enumerate(range(8, 13)):
        _seed_run(tmp_path / f"agent_no_mem_rep{i}", sun=sun, ehull_median=0.02,
                  mode="agent_no_mem", rep=i)
    for i, sun in enumerate(range(15, 20)):
        _seed_run(tmp_path / f"agent_with_mem_rep{i}", sun=sun, ehull_median=0.005,
                  mode="agent_with_mem", rep=i)
    cmp = compare_modes(tmp_path, metric="sun_count")
    key = "agent_no_mem_vs_agent_with_mem"
    assert cmp[key]["p_value"] < 0.05  # achievable at paper-grade n=5
    assert cmp[key]["cliffs_delta"] == -1.0


def test_mann_whitney_identical_distributions(tmp_path: Path) -> None:
    u, p = _mann_whitney_u([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
    assert u == 4.5  # symmetric, both halves equal
    assert p > 0.5   # no evidence of difference


def test_cliffs_delta_sign(tmp_path: Path) -> None:
    assert _cliffs_delta([10.0, 11.0], [1.0, 2.0]) == 1.0
    assert _cliffs_delta([1.0, 2.0], [10.0, 11.0]) == -1.0
    assert _cliffs_delta([1.0, 2.0], [1.0, 2.0]) == 0.0


# ================= distinct-structure audit (batch-of-1 uniqueness fix) =================

def test_distinct_metrics_dedups_final_population(tmp_path: Path) -> None:
    # two identical Si structures (both SUN) + one distinct structure (not SUN)
    run = tmp_path / "agent_rep0"
    _seed_population(run, [_indiv(_cif_si(), sun=True),
                           _indiv(_cif_si(), sun=True),
                           _indiv(_cif_distinct(), sun=False)])
    m = _distinct_metrics(run)
    assert m is not None
    assert m["n_pop"] == 3.0
    assert m["n_distinct"] == 2.0
    assert abs(m["unique_frac"] - 2.0 / 3.0) < 1e-9
    # raw sun_count would be 2; duplicates collapse it to 1
    assert m["sun_count_distinct"] == 1.0


def test_distinct_metrics_uses_last_generation(tmp_path: Path) -> None:
    run = tmp_path / "agent_rep0"
    run.mkdir()
    (run / "population_gen1.json").write_text(json.dumps([_indiv(_cif_si(), sun=True)]))
    _seed_population(run, [])  # gen10 empty → n_pop 0, not gen1's value
    m = _distinct_metrics(run)
    assert m is not None
    assert m["n_pop"] == 0.0


def test_distinct_metrics_missing_population(tmp_path: Path) -> None:
    run = tmp_path / "legacy_rep0"
    run.mkdir()
    assert _distinct_metrics(run) is None


def test_aggregate_includes_distinct_keys(tmp_path: Path) -> None:
    base = tmp_path / "ab"
    _seed_run(base / "m_rep0", sun=2, ehull_median=0.01, mode="m", rep=0)
    _seed_population(base / "m_rep0", [_indiv(_cif_si(), sun=True),
                                       _indiv(_cif_si(), sun=True)])
    _seed_run(base / "m_rep1", sun=1, ehull_median=0.02, mode="m", rep=1)
    _seed_population(base / "m_rep1", [_indiv(_cif_si(), sun=True)])
    metrics = aggregate_metrics(base)
    assert metrics["m"]["sun_count_distinct_mean"] == 1.0
    assert abs(metrics["m"]["unique_frac_mean"] - 0.75) < 1e-9  # mean(0.5, 1.0)


def test_aggregate_without_population_omits_distinct_keys(tmp_path: Path) -> None:
    base = tmp_path / "ab"
    _seed_run(base / "m_rep0", sun=2, ehull_median=0.01, mode="m", rep=0)
    metrics = aggregate_metrics(base)
    assert metrics["m"]["sun_count_mean"] == 2.0
    assert "sun_count_distinct_mean" not in metrics["m"]


def test_compare_modes_on_sun_count_distinct(tmp_path: Path) -> None:
    for i in range(3):
        d = tmp_path / f"no_rep{i}"
        _seed_run(d, sun=2, ehull_median=0.02, mode="no", rep=i)
        _seed_population(d, [_indiv(_cif_si(), sun=True), _indiv(_cif_si(), sun=True)])
    for i in range(3):
        d = tmp_path / f"yes_rep{i}"
        _seed_run(d, sun=2, ehull_median=0.02, mode="yes", rep=i)
        _seed_population(d, [_indiv(_cif_si(), sun=True), _indiv(_cif_distinct(), sun=True)])
    cmp = compare_modes(tmp_path, metric="sun_count_distinct")
    key = "no_vs_yes"
    assert key in cmp
    assert cmp[key]["mean_a"] == 1.0
    assert cmp[key]["mean_b"] == 2.0
