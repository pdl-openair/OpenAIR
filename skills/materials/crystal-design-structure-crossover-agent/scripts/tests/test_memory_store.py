# scripts/tests/test_memory_store.py
from datetime import datetime, timezone
from pathlib import Path
import json
import sys

from memory_store import MemoryEntry, MEMORY_SCHEMA_VERSION


def make_fixture() -> MemoryEntry:
    return MemoryEntry(
        entry_id="abc123",
        run_id="run_001",
        family="rare_earth_intermetallics",
        gen=2,
        context="Au collapse detected: 3 of 16 individuals are single-element.",
        action="ban element Au",
        outcome={"delta_sun": 0.25, "delta_ehull_median": -0.04},
        confidence=0.8,
        created_at="2026-08-12T10:00:00+00:00",
    )


def test_entry_round_trip(tmp_path: Path) -> None:
    entry = make_fixture()
    serialized = entry.to_dict()
    assert serialized["schema_version"] == MEMORY_SCHEMA_VERSION
    # Write to disk + reload
    p = tmp_path / "entry.json"
    p.write_text(json.dumps(serialized, ensure_ascii=False, indent=2), encoding="utf-8")
    reloaded = MemoryEntry.from_dict(json.loads(p.read_text(encoding="utf-8")))
    assert reloaded == entry


def test_entry_immutable() -> None:
    entry = make_fixture()
    try:
        entry.confidence = 0.5  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("MemoryEntry must be frozen")


from memory_store import MemoryStore


def test_store_append_and_load(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "mem.json")
    assert len(store) == 0
    e1 = make_fixture()
    e2 = make_fixture()
    e2 = MemoryEntry.from_dict({**e1.to_dict(), "entry_id": "def456", "gen": 3})
    store.append(e1)
    store.append(e2)
    assert len(store) == 2
    # New instance reloads from disk
    store2 = MemoryStore(tmp_path / "mem.json")
    loaded = store2.load()
    assert len(loaded) == 2
    assert loaded[0].entry_id == "abc123"
    assert loaded[1].gen == 3


def test_store_save_overwrites(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "mem.json")
    store.append(make_fixture())
    store.save([])  # explicit clear
    assert len(store) == 0


from memory_store import score_context


def test_score_context_exact_overlap() -> None:
    s = "Au collapse detected in population"
    assert score_context(s, s) == 1.0


def test_score_context_no_overlap() -> None:
    assert score_context("alpha beta", "gamma delta") == 0.0


def test_score_context_partial_overlap() -> None:
    score = score_context("Au collapse detected", "Au collapse, multiple gen")
    assert 0.0 < score < 1.0


def test_store_query_ranks_by_context(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "mem.json")
    base = make_fixture().to_dict()
    base.pop("schema_version")
    base.pop("entry_id")

    e_off = MemoryEntry.from_dict({**base, "entry_id": "off", "context": "Au collapse detected"})
    e_on = MemoryEntry.from_dict({**base, "entry_id": "on", "context": "bandgap too low"})
    e_mid = MemoryEntry.from_dict({**base, "entry_id": "mid", "context": "Au collapse, multiple gen"})
    store.save([e_off, e_on, e_mid])

    ranked = store.query("Au collapse detected", top_k=3)
    assert [e.entry_id for e in ranked] == ["off", "mid", "on"]


def test_store_query_filters_family(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "mem.json")
    base = make_fixture().to_dict()
    base.pop("schema_version")
    base.pop("entry_id")
    base["family"] = "rare_earth_intermetallics"
    e1 = MemoryEntry.from_dict({**base, "entry_id": "a"})
    base["family"] = "wide_bandgap_oxide"
    e2 = MemoryEntry.from_dict({**base, "entry_id": "b"})
    store.save([e1, e2])

    ranked = store.query("any context", family="wide_bandgap_oxide", top_k=5)
    assert [e.entry_id for e in ranked] == ["b"]


def test_store_query_min_confidence(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "mem.json")
    base = make_fixture().to_dict()
    base.pop("schema_version")
    base.pop("entry_id")
    base["context"] = "Au collapse"
    base["confidence"] = 0.1
    e_low = MemoryEntry.from_dict({**base, "entry_id": "low"})
    base["confidence"] = 0.9
    e_high = MemoryEntry.from_dict({**base, "entry_id": "high"})
    store.save([e_low, e_high])

    ranked = store.query("Au collapse", min_confidence=0.5)
    assert [e.entry_id for e in ranked] == ["high"]


def test_store_decay_lowers_confidence(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "mem.json")
    store.append(make_fixture())  # confidence=0.8
    store.decay(factor=0.5)
    assert store.load()[0].confidence == 0.4


def test_store_boost_raises_confidence(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "mem.json")
    store.append(make_fixture())  # confidence=0.8
    store.boost("abc123", delta=0.1)
    assert store.load()[0].confidence == 0.9


def test_apply_outcome_upvote_and_downvote(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "mem.json")
    store.append(make_fixture())  # confidence=0.8
    store.apply_outcome("abc123", improved=True, delta=0.05)
    assert store.load()[0].confidence == pytest.approx(0.85)
    store.apply_outcome("abc123", improved=False, delta=0.05)
    assert store.load()[0].confidence == pytest.approx(0.80)


def test_apply_outcome_clamped_at_zero(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "mem.json")
    e = MemoryEntry.from_dict({**make_fixture().to_dict(), "confidence": 0.02})
    store.append(e)
    store.apply_outcome("abc123", improved=False, delta=0.05)
    assert store.load()[0].confidence == 0.0


def test_store_confidence_clamped(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "mem.json")
    store.append(make_fixture())
    store.boost("abc123", delta=0.5)  # 0.8 + 0.5 = 1.3 -> 1.0
    assert store.load()[0].confidence == 1.0


def test_step_writes_memory_query(tmp_path: Path, monkeypatch) -> None:
    """When --memory-dir is configured, step writes memory_query_result.json."""
    # Skip if evolve_driver not importable (host deps missing in CI)
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from evolve_driver import write_memory_query_for_snapshot  # type: ignore
    except ImportError:
        pytest.skip("evolve_driver not importable in this environment")

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    mem_dir = tmp_path / "mem"
    mem_dir.mkdir()
    store = MemoryStore(mem_dir / "store.json")
    store.append(make_fixture())  # context mentions "Au collapse"

    snapshot = {"generation": 2, "summary": "Au collapse detected"}
    write_memory_query_for_snapshot(run_dir=run_dir, snapshot=snapshot, memory_store=store,
                                    family="rare_earth_intermetallics")
    query_path = run_dir / "memory_query_result.json"
    assert query_path.exists()
    data = json.loads(query_path.read_text())
    assert len(data) >= 1
    assert "Au collapse" in data[0]["context"]


def test_cmd_steer_appends_to_log(tmp_path: Path) -> None:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from evolve_driver import _emit_steer_log  # type: ignore
    except (ImportError, AttributeError):
        pytest.skip("_emit_steer_log helper not present or evolve_driver unimportable")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _emit_steer_log(
        action="ban",
        args={"element": "Au"},
        reason="collapse",
        run_dir=run_dir,
    )
    log = (run_dir / "steer_log.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(log) == 1
    entry = json.loads(log[0])
    assert entry["action"] == "ban"
    assert entry["args"]["element"] == "Au"


def test_e2e_memory_flow(tmp_path: Path) -> None:
    """Full flow: init with memory -> step 1 -> step 2 (memory entry written)."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from evolve_driver import write_memory_query_for_snapshot, write_memory_entry_after_step
    except ImportError:
        pytest.skip("evolve_driver not importable")

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    mem_dir = tmp_path / "mem"
    store = MemoryStore(mem_dir / "store.json")

    # Simulate two snapshots from a hypothetical run
    snap0 = {"generation": 0, "summary": "Au collapse detected", "sun_count": 4}
    snap1 = {"generation": 1, "summary": "Population diversified", "sun_count": 8}

    # Step 1 writes memory query for snap0
    write_memory_query_for_snapshot(run_dir, snap0, store, family="rare_earth")
    assert (run_dir / "memory_query_result.json").exists()

    # Save snap0 to disk (as evolve_driver would)
    (run_dir / "snapshot_gen0.json").write_text(json.dumps(snap0))
    (run_dir / "steer_log.jsonl").write_text(
        json.dumps({"action": "ban", "args": {"element": "Au"}, "reason": "collapse"}) + "\n"
    )

    # Step 2 writes memory query for snap1 AND a memory entry for snap0
    write_memory_query_for_snapshot(run_dir, snap1, store, family="rare_earth")
    write_memory_entry_after_step(
        run_dir=run_dir,
        family="rare_earth",
        prev_snapshot=snap0,
        curr_snapshot=snap1,
        last_steer_action={"action": "ban", "args": {"element": "Au"}, "reason": "collapse"},
        memory_store=store,
    )
    assert len(store) == 1
    entry = store.load()[0]
    assert entry.action.startswith("ban")
    assert entry.outcome["delta_sun"] == 4.0  # 8 - 4
    # Outcome-driven confidence: delta_sun=+4 > 0 → immediate upvote
    # (newborn entry is NOT decayed in its own step; decay precedes append)
    assert entry.confidence == pytest.approx(0.55)


def test_e2e_memory_outcome_downvote(tmp_path: Path) -> None:
    """A steer whose outcome worsened must lose confidence, not gain it."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from evolve_driver import write_memory_entry_after_step
    except ImportError:
        pytest.skip("evolve_driver not importable")

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    store = MemoryStore(tmp_path / "mem" / "store.json")

    snap0 = {"generation": 0, "summary": "healthy population", "sun_count": 10,
             "ehull_median": 0.010}
    snap1 = {"generation": 1, "summary": "diversity crashed", "sun_count": 10,
             "ehull_median": 0.050}  # SUN flat, ehull median worse
    write_memory_entry_after_step(
        run_dir=run_dir, family="f", prev_snapshot=snap0, curr_snapshot=snap1,
        last_steer_action={"action": "ban", "args": {"element": "Eu"}, "reason": "dominant"},
        memory_store=store,
    )
    entry = store.load()[0]
    assert entry.confidence == pytest.approx(0.45)  # downvoted


def test_write_snapshot_has_memory_and_analysis_keys(tmp_path: Path) -> None:
    """write_snapshot must emit the keys memory hooks + pilot_analysis read."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from evolve_driver import write_snapshot
    except ImportError:
        pytest.skip("evolve_driver not importable")

    run_dir = tmp_path / "run"
    run_dir.mkdir()

    class _FakeStructure:
        # 真实 run 的种群条目持有活 pymatgen Structure（structure_obj 键），
        # JSON 不可序列化——归档路径必须经 serialize_indiv 剥离它。
        pass

    pop = [
        {"name": "a", "formula": "Rb5Na9Pb", "sg_number": 1, "is_valid": True,
         "total_violation": 0.0, "pareto_rank": 1, "fitness": 0.9,
         "ehull_eval": 0.001, "novel": 1.0, "stable": 1.0, "unique": 1.0,
         "element_set": ["Rb", "Na", "Pb"], "structure_obj": _FakeStructure()},
        {"name": "b", "formula": "CaEu3", "sg_number": 221, "is_valid": True,
         "total_violation": 0.0, "pareto_rank": 2, "fitness": 0.5,
         "ehull_eval": 0.010, "novel": 0.0, "stable": 1.0, "unique": 1.0,
         "element_set": ["Ca", "Eu"], "structure_obj": _FakeStructure()},
    ]
    config = {"max_generations": 10}
    write_snapshot(run_dir, 3, pop, {"ehull_eval": "minimize", "novel": "maximize"}, config)

    snap = json.loads((run_dir / "snapshot_gen3.json").read_text())
    assert snap["sun_count"] == 1  # only individual 'a' is SUN
    assert snap["stable_count"] == 2
    assert snap["pareto_front_size"] == 1
    assert abs(snap["ehull_median"] - 0.0055) < 1e-9
    assert isinstance(snap["summary"], str) and "gen 3" in snap["summary"]
    front = snap["top_front"][0]
    assert front["stable"] == 1.0 and front["unique"] == 1.0
    # Full population archived per generation
    pop_archived = json.loads((run_dir / "population_gen3.json").read_text())
    assert len(pop_archived) == 2


def test_write_snapshot_emits_static_rules_fields(tmp_path: Path) -> None:
    """write_snapshot must emit every field static_rules_decide reads.

    回归背景:规则表读取的 5 个字段(single_element_individuals /
    pareto_size_unchanged_gens / rmsd_median / constraint_violation_rate /
    most_violated_constraint)曾从未被写入,导致 STATIC_RULES 臂的规则
    全部退化为 noop 兜底。
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from evolve_driver import write_snapshot
    except ImportError:
        pytest.skip("evolve_driver not importable")

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "constraints.json").write_text(json.dumps(
        {"comp_valid": {"expected": True, "weight": 10},
         "num_elements": {"min": 2, "weight": 10}}))

    pop = [
        {"name": "a", "formula": "Rb5Na9Pb", "sg_number": 1, "is_valid": True,
         "total_violation": 0.0, "constraint_violation": 0.0,
         "num_elements": 3, "rmsd_relax": 0.10,
         "pareto_rank": 1, "fitness": 0.9, "ehull_eval": 0.001,
         "novel": 1.0, "stable": 1.0, "unique": 1.0,
         "element_set": ["Rb", "Na", "Pb"], "comp_valid": True},
        {"name": "b", "formula": "CaEu3", "sg_number": 221, "is_valid": True,
         "total_violation": 0.0, "constraint_violation": 0.0,
         "num_elements": 2, "rmsd_relax": 0.30,
         "pareto_rank": 2, "fitness": 0.5, "ehull_eval": 0.010,
         "novel": 0.0, "stable": 1.0, "unique": 1.0,
         "element_set": ["Ca", "Eu"], "comp_valid": True},
    ]
    config = {"max_generations": 10}
    write_snapshot(run_dir, 1, pop, {"ehull_eval": "minimize", "novel": "maximize"}, config)
    snap1 = json.loads((run_dir / "snapshot_gen1.json").read_text())

    # 5 个规则输入字段全部存在且类型正确
    assert snap1["single_element_individuals"] == []
    assert snap1["pareto_size_unchanged_gens"] == 0  # 无上一代快照
    assert abs(snap1["rmsd_median"] - 0.20) < 1e-9
    assert snap1["constraint_violation_rate"] == 0.0
    assert snap1["most_violated_constraint"] is None

    # 同一 pareto 规模再写一代 → 停滞计数应翻倍为 1
    write_snapshot(run_dir, 2, pop, {"ehull_eval": "minimize", "novel": "maximize"}, config)
    snap2 = json.loads((run_dir / "snapshot_gen2.json").read_text())
    assert snap2["pareto_size_unchanged_gens"] == 1


def test_static_rules_fires_on_real_snapshot_output(tmp_path: Path) -> None:
    """规则链路端到端:write_snapshot 的真实输出必须能触发 static_rules_decide。

    场景 1(规则 1):种群混入单元素个体 → ban 该元素。
    场景 2(规则 2):全稳定 + 前沿规模停滞 ≥2 代 → 降噪声 0.2。
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from evolve_driver import write_snapshot
        from steering_modes import static_rules_decide
    except ImportError:
        pytest.skip("evolve_driver/steering_modes not importable")

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "constraints.json").write_text(json.dumps(
        {"comp_valid": {"expected": True, "weight": 10},
         "num_elements": {"min": 2, "weight": 10}}))

    def indiv(name, rank, ehull, elements, num_elements):
        return {"name": name, "formula": name, "sg_number": 1, "is_valid": True,
                "total_violation": 0.0, "constraint_violation": 0.0,
                "num_elements": num_elements, "rmsd_relax": 0.10,
                "pareto_rank": rank, "fitness": -rank, "ehull_eval": ehull,
                "novel": 1.0, "stable": 1.0, "unique": 1.0,
                "element_set": elements, "comp_valid": True}

    objectives = {"ehull_eval": "minimize", "novel": "maximize"}
    config = {"max_generations": 10}

    # 场景 1:单元素个体 Au 混入 → 规则 1 应 ban Au
    pop_bad = [
        indiv("a", 1, 0.001, ["Rb", "Na", "Pb"], 3),
        indiv("au_cl", 2, -0.50, ["Au"], 1),  # 单元素,ehull 极低("获胜的伪解")
        indiv("b", 3, 0.010, ["Ca", "Eu"], 2),
    ]
    write_snapshot(run_dir, 1, pop_bad, objectives, config)
    snap = json.loads((run_dir / "snapshot_gen1.json").read_text())
    action = static_rules_decide(snap, "test", {})
    assert action.action == "ban" and action.args["element"] == "Au"

    # 场景 2:全稳定(pop 均满足 stable,且 num_elements>=2 合规)+ 前沿规模停滞
    pop_ok = [indiv("a", 1, 0.001, ["Rb", "Na", "Pb"], 3),
              indiv("b", 2, 0.010, ["Ca", "Eu"], 2)]
    run2 = tmp_path / "run2"
    run2.mkdir()
    (run2 / "constraints.json").write_text(json.dumps(
        {"comp_valid": {"expected": True, "weight": 10},
         "num_elements": {"min": 2, "weight": 10}}))
    for gen in (1, 2, 3):  # 同一前沿规模连续 3 代 → unchanged=2
        write_snapshot(run2, gen, pop_ok, objectives, config)
    snap3 = json.loads((run2 / "snapshot_gen3.json").read_text())
    assert snap3["pareto_size_unchanged_gens"] == 2
    action2 = static_rules_decide(snap3, "test", {})
    assert action2.action == "noise" and action2.args["level"] == 0.2


# pytest is needed by the skip() calls above; imported here to keep the
# existing tests above free of a hard pytest dependency at module top.
import pytest  # noqa: E402
