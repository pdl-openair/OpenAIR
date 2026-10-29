"""Unit tests for formula_driver.py — pure logic only (no MCP, no LLM)."""
import json
import sys
from pathlib import Path

# Add scripts/ to path for import
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))


def test_load_save_json_roundtrip(tmp_path):
    from formula_driver import _load_json, _save_json
    p = tmp_path / "x.json"
    _save_json(p, {"a": 1, "b": [2, 3]})
    assert _load_json(p) == {"a": 1, "b": [2, 3]}


def test_save_json_atomic(tmp_path):
    """_save_json must not leave a partial file on disk if serialization fails."""
    from formula_driver import _save_json, _load_json
    p = tmp_path / "x.json"
    try:
        _save_json(p, {"bad": object()})  # object() is not JSON-serializable
    except TypeError:
        pass
    # Either the temp file was cleaned up before rename, or no x.json exists.
    assert not p.exists() or _load_json(p) is not None


def test_read_config_missing_returns_empty(tmp_path):
    from formula_driver import _read_config
    assert _read_config(tmp_path) == {}


def test_read_constraints_uses_defaults_when_missing(tmp_path):
    from formula_driver import _read_constraints
    c = _read_constraints(tmp_path)
    assert c == {} or "element_set" not in c  # empty by default


def test_apply_hard_filters_drops_violating_candidates():
    from formula_driver import apply_hard_filters
    candidates = [
        {"formula": "A", "ehull": 0.05},
        {"formula": "B", "ehull": 0.31},  # dropped
        {"formula": "C", "ehull": None},  # kept (None = unknown)
    ]
    hard_filter = [{"name": "ehull", "op": "<", "value": 0.1}]
    failures = {"filtered_out": []}
    kept = apply_hard_filters(candidates, hard_filter, failures)
    formulas = [c["formula"] for c in kept]
    assert "A" in formulas and "C" in formulas and "B" not in formulas
    assert any(f["formula"] == "B" for f in failures["filtered_out"])


def test_normalize_and_reward_min_max_per_objective():
    from formula_driver import normalize_and_reward
    pool = [
        {"formula": "A", "bandgap": 1.0, "hhi": 800},
        {"formula": "B", "bandgap": 3.0, "hhi": 200},
    ]
    objectives = [
        {"name": "bandgap", "direction": "max", "weight": 1.0},
        {"name": "hhi", "direction": "min", "weight": 1.0},
    ]
    result = normalize_and_reward(pool, objectives)
    by_formula = {c["formula"]: c for c in result}
    # B has higher bandgap (norm=1) and lower hhi (norm=1, since min) → reward=2.0
    assert by_formula["B"]["reward"] == 2.0
    assert by_formula["A"]["reward"] == 0.0


def test_normalize_constant_column_uses_05():
    from formula_driver import normalize_and_reward
    pool = [{"formula": "A", "bandgap": 2.0}, {"formula": "B", "bandgap": 2.0}]
    objectives = [{"name": "bandgap", "direction": "max", "weight": 1.0}]
    result = normalize_and_reward(pool, objectives)
    for c in result:
        assert c["reward"] == 0.5  # constant → 0.5 norm


def test_select_topk_sorts_by_reward_desc():
    from formula_driver import select_topk
    pool = [{"formula": "A", "reward": 0.3}, {"formula": "B", "reward": 0.9},
            {"formula": "C", "reward": 0.6}]
    top2 = select_topk(pool, top_k=2)
    assert [c["formula"] for c in top2] == ["B", "C"]


def test_write_snapshot_has_required_fields(tmp_path):
    from formula_driver import _write_snapshot
    pool = [{"formula": "GaN", "reward": 0.8, "values": {"bandgap": 3.0}, "is_valid": True,
             "total_violation": 0.0, "record_path": "records/GaN__iter0.json",
             "source": "init", "bandgap": 3.0}]
    _write_snapshot(
        run_dir=tmp_path, iteration=0, max_iters=3, pool=pool,
        objectives=[{"name": "bandgap", "direction": "max", "weight": 1.0}],
        hard_filter=[{"name": "ehull", "op": "<", "value": 0.1}],
        constraints={}, hints={}, config={"batch_size": 3, "top_k": 5},
        mattergen_eval={"status": "skipped"}, failures={},
    )
    import json
    snap = json.loads((tmp_path / "snapshot_iter0.json").read_text())
    assert snap["iteration"] == 0
    assert snap["pool_size"] == 1
    assert snap["n_valid"] == 1
    assert snap["best_reward"] == 0.8
    assert snap["best_formula"] == "GaN"
    assert snap["top_pool"][0]["formula"] == "GaN"
    # latest alias
    latest = json.loads((tmp_path / "snapshot_latest.json").read_text())
    assert latest == snap


def test_parse_crossover_hint_append():
    from formula_driver import _parse_crossover_hint
    key, op, val = _parse_crossover_hint("swap_groups+Fe,Co,Ni")
    assert key == "swap_groups" and op == "+" and val == "Fe,Co,Ni"


def test_parse_crossover_hint_replace():
    from formula_driver import _parse_crossover_hint
    key, op, val = _parse_crossover_hint("prototypes:Heusler,YCo5")
    assert key == "prototypes" and op == ":" and val == "Heusler,YCo5"


def test_steer_ban_adds_to_constraints(tmp_path):
    from formula_driver import cmd_steer
    import json
    class A: pass
    a = A()
    a.run_dir = str(tmp_path)
    a.ban = "Pb,Cd"
    a.allow_only = None
    a.clear_ban = False
    a.constraint = []
    a.weight = []
    a.crossover_hint = []
    a.batch_size = None
    a.top_k = None
    # need a config.json to exist for steer to proceed
    (tmp_path / "config.json").write_text("{}")
    cmd_steer(a)
    c = json.loads((tmp_path / "constraints.json").read_text())
    assert "Pb" in c["element_set"]["banned"]
    assert "Cd" in c["element_set"]["banned"]
    assert c["element_set"]["weight"] >= 10.0


def test_steer_crossover_hint_replace_prototypes(tmp_path):
    from formula_driver import cmd_steer
    import json
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "crossover_hints.json").write_text(
        json.dumps({"swap_groups": [["A"]], "prototypes": ["old"], "avoid": []})
    )
    class A: pass
    a = A()
    a.run_dir = str(tmp_path)
    a.ban = None
    a.allow_only = None
    a.clear_ban = False
    a.constraint = []
    a.weight = []
    a.crossover_hint = ["prototypes:Heusler,YCo5"]
    a.batch_size = None
    a.top_k = None
    cmd_steer(a)
    h = json.loads((tmp_path / "crossover_hints.json").read_text())
    assert h["prototypes"] == ["Heusler", "YCo5"]
    assert h["swap_groups"] == [["A"]]  # unchanged


def test_steer_crossover_hint_append_swap_group(tmp_path):
    from formula_driver import cmd_steer
    import json
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "crossover_hints.json").write_text(
        json.dumps({"swap_groups": [["A"]], "prototypes": [], "avoid": []})
    )
    class A: pass
    a = A()
    a.run_dir = str(tmp_path)
    a.ban = None
    a.allow_only = None
    a.clear_ban = False
    a.constraint = []
    a.weight = []
    a.crossover_hint = ["swap_groups+Fe,Co,Ni"]
    a.batch_size = None
    a.top_k = None
    cmd_steer(a)
    h = json.loads((tmp_path / "crossover_hints.json").read_text())
    assert ["Fe", "Co", "Ni"] in h["swap_groups"]
    assert ["A"] in h["swap_groups"]  # original preserved


def test_cmd_init_calls_query_mp_with_correct_signature(tmp_path, monkeypatch):
    """cmd_init must call initial_formulas.query_mp(n, seed, with_structures, elements).

    Regression guard: an earlier version passed non-existent kwargs (fallback_list,
    out_path) which raised TypeError and silently degraded every run to the
    bundled fallback list. This test pins the signature contract.
    """
    import formula_driver

    captured: dict = {}

    def fake_query_mp(n, seed, with_structures=False, elements=None):
        captured["n"] = n
        captured["seed"] = seed
        captured["with_structures"] = with_structures
        captured["elements"] = elements
        return [{"formula": "GaN", "material_id": "mp-1"},
                {"formula": "GaP", "material_id": "mp-2"}]

    def fake_fallback(n, seed, formulas=None):
        raise AssertionError("fallback should not run when query_mp returns data")

    # probe_mcps reads MATERIALS_MCP_BASE_URL at import time and sys.exit(2)s
    # if unset; set a placeholder before cmd_init imports it, then stub main.
    monkeypatch.setenv("MATERIALS_MCP_BASE_URL", "http://mock.local/mcp")
    import probe_mcps
    monkeypatch.setattr(probe_mcps, "main", lambda argv=None: 0)

    import initial_formulas
    monkeypatch.setattr(initial_formulas, "query_mp", fake_query_mp)
    monkeypatch.setattr(initial_formulas, "fallback", fake_fallback)

    # _pipeline_batch hits MCPs; replace with a no-op that returns records shaped
    # like the engine expects.
    def fake_pipeline_batch(formulas, iter_idx, runplan, run_dir):
        return ([{"formula": f, "bandgap": 1.0, "ehull": 0.05, "is_valid": True,
                  "total_violation": 0.0, "source": "init", "record_path": "",
                  "values": {"bandgap": 1.0, "ehull": 0.05}} for f in formulas],
                {"filtered_out": [], "partial": []})
    monkeypatch.setattr(formula_driver, "_pipeline_batch", fake_pipeline_batch)

    class A: pass
    a = A()
    a.run_dir = str(tmp_path)
    a.runplan = None
    a.goal = "test goal"
    a.objectives = '{"bandgap": "max"}'
    a.hard_filter = '[]'
    a.alignn_models = '["bandgap"]'
    a.crossover_hints = '{}'
    a.init_elements = "Ga,N"
    a.fallback_formulas = "GaN,GaP"
    a.init_n = 2
    a.batch_size = 2
    a.max_iters = 1
    a.top_k = 2
    a.run_mattergen_eval = False
    a.seed = 7

    formula_driver.cmd_init(a)

    # query_mp was called with the documented kwargs (no TypeError, no fallback)
    assert captured["n"] == 2
    assert captured["seed"] == 7
    assert captured["with_structures"] is False
    assert captured["elements"] == {"Ga", "N"}

    # snapshot_iter0.json was written and contains both candidates ranked
    import json
    snap = json.loads((tmp_path / "snapshot_iter0.json").read_text())
    assert snap["pool_size"] == 2
    assert snap["best_formula"] in {"GaN", "GaP"}
    # population.json reflects query_mp output, not the fallback list
    pop = json.loads((tmp_path / "population.json").read_text())
    formulas_in_pool = {c["formula"] for c in pop["pool"]}
    assert formulas_in_pool == {"GaN", "GaP"}
