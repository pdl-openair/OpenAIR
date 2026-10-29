"""Unit tests for run_unit_suite (M2): group driver, ratchet set-diff, probe.

The module was split out of the unit-test job's inline python blocks; these
tests pin the decision rules that used to live only inside the workflow:
marker attribution, fail-closed semantics, new/known/healed set logic and
the probe's re-admission classification.
"""

import json
from pathlib import Path

import run_unit_suite as rus


def _write(path: Path, text: str) -> str:
    path.write_text(text, encoding="utf-8", newline="\n")
    return str(path)


def _selection(tmp_path: Path, *, runs=None, deselects=None, excluded=(),
               rationale=()) -> str:
    payload = {
        "runs": runs or [],
        "run_deselects": deselects or [],
        "deselected_nodes_rationale": [
            {"node": node, "reason": reason} for node, reason in rationale
        ],
        "excluded": [{"path": path, "reason": reason} for path, reason in excluded],
    }
    return _write(tmp_path / "selection.json", json.dumps(payload))


# --- ratchet: marker attribution -------------------------------------------


def test_parse_group_buckets_attributes_lines_to_group_starts(tmp_path: Path) -> None:
    log = tmp_path / "unit-test-output.log"
    _write(
        log,
        "[group-start] index=1 files=2\n"
        "FAILED a/test_x.py::test_one - assert\n"
        "some noise line\n"
        "[group-boundary] index=1 files=2 exit=1\n"
        "outside any group: FAILED b/test_y.py::test_must_not_count\n"
        "[group-start] index=2 files=1\n"
        "ERROR sub/test_z.py::test_two\n"
        "[group-boundary] index=2 files=1 exit=1\n",
    )
    buckets = rus.parse_group_buckets(str(log))
    assert set(buckets) == {1, 2}
    assert buckets[1] == ["FAILED a/test_x.py::test_one - assert", "some noise line"]
    assert buckets[2] == ["ERROR sub/test_z.py::test_two"]


def test_collect_actual_nodes_requires_node_lines_for_failed_group() -> None:
    groups = [{"index": 1, "files": 2, "exit": 1, "seconds": 1.0}]
    actual, fail_closed = rus.collect_actual_nodes(groups, {1: ["no node lines here"]})
    assert actual == set()
    assert fail_closed is not None and "failing closed" in fail_closed


def test_collect_actual_nodes_extracts_failed_and_error_ids() -> None:
    groups = [{"index": 1, "files": 2, "exit": 1, "seconds": 1.0}]
    buckets = {1: ["FAILED tests/test_a.py::test_one - assert", "ERROR tests/test_b.py::test_two"]}
    actual, fail_closed = rus.collect_actual_nodes(groups, buckets)
    assert fail_closed is None
    assert actual == {
        "tests/test_a.py::test_one",
        "tests/test_b.py::test_two",
    }


def test_normalize_node_strips_cwd_and_backslashes(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    raw = str(tmp_path / "tests" / "test_a.py").replace("/", "\\") + "::test_one"
    node = rus.normalize_node(raw)
    assert node == "tests/test_a.py::test_one"


# --- ratchet: verdict semantics --------------------------------------------


def _groups_doc(tmp_path: Path, groups) -> str:
    return _write(tmp_path / "unit-test-groups.json", json.dumps(groups))


def _known_doc(tmp_path: Path, *ids: str) -> str:
    return _write(
        tmp_path / "known.json",
        json.dumps(
            {
                "version": 1,
                "nodes": [{"id": i, "status": "known-fail", "exc": "fixture"} for i in ids],
            }
        ),
    )


def test_ratchet_green_when_all_failures_known(tmp_path, capsys) -> None:
    groups_doc = _groups_doc(tmp_path, [{"index": 1, "files": 1, "exit": 1, "seconds": 1.0}])
    known_doc = _known_doc(tmp_path, "tests/test_a.py::test_one")
    log = tmp_path / "unit-test-output.log"
    _write(
        log,
        "[group-start] index=1 files=1\n"
        "FAILED tests/test_a.py::test_one - assert\n"
        "[group-boundary] index=1 files=1 exit=1\n",
    )
    code = rus.main([
        "ratchet", "--groups-json", groups_doc,
        "--output-log", str(log), "--known-failures", known_doc,
    ])
    assert code == 0
    assert "zero new failures" in capsys.readouterr().out


def test_ratchet_red_on_new_failing_node(tmp_path, capsys) -> None:
    groups_doc = _groups_doc(tmp_path, [{"index": 1, "files": 1, "exit": 1, "seconds": 1.0}])
    known_doc = _known_doc(tmp_path, "tests/test_old.py::test_known")
    log = tmp_path / "unit-test-output.log"
    _write(
        log,
        "[group-start] index=1 files=1\n"
        "FAILED tests/test_new.py::test_fresh - assert\n"
        "[group-boundary] index=1 files=1 exit=1\n",
    )
    code = rus.main([
        "ratchet", "--groups-json", groups_doc,
        "--output-log", str(log), "--known-failures", known_doc,
    ])
    assert code == 1
    out = capsys.readouterr().out
    assert "NEW failing nodes" in out and "tests/test_new.py::test_fresh" in out


def test_ratchet_fail_closed_when_groups_json_missing(tmp_path, capsys) -> None:
    log = tmp_path / "unit-test-output.log"
    _write(log, "")
    code = rus.main([
        "ratchet", "--groups-json", str(tmp_path / "missing.json"),
        "--output-log", str(log), "--known-failures", _known_doc(tmp_path),
    ])
    assert code == 1
    assert "driver evidence incomplete" in capsys.readouterr().out


def test_ratchet_reports_healed_known_nodes(tmp_path, capsys) -> None:
    groups_doc = _groups_doc(tmp_path, [{"index": 1, "files": 1, "exit": 0, "seconds": 1.0}])
    known_doc = _known_doc(tmp_path, "tests/test_healed.py::test_gone")
    log = tmp_path / "unit-test-output.log"
    _write(
        log,
        "[group-start] index=1 files=1\n"
        "1 passed in 0.01s\n"
        "[group-boundary] index=1 files=1 exit=0\n",
    )
    code = rus.main([
        "ratchet", "--groups-json", groups_doc,
        "--output-log", str(log), "--known-failures", known_doc,
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "healed: tests/test_healed.py::test_gone" in out


# --- probe ------------------------------------------------------------------


def test_probe_reports_nothing_marked(tmp_path, capsys) -> None:
    selection = _selection(tmp_path)
    code = rus.main(["probe", "--selection", selection])
    assert code == 0
    assert "nothing marked" in capsys.readouterr().out


def test_probe_collects_marked_files_and_nodes(tmp_path, monkeypatch) -> None:
    seen: list[list[str]] = []

    def fake_run(argv, *args, **kwargs):
        seen.append(argv)
        import types
        return types.SimpleNamespace(returncode=1)

    monkeypatch.setattr(rus.subprocess, "run", fake_run)
    selection = _selection(
        tmp_path,
        rationale=[("skills/a/tests/test_a.py::test_x", "B5 re-admit on CI evidence: env parity")],
        excluded=[("skills/b/tests/test_b.py", "numpy absent ... flagged for B5 CI verification")],
    )
    code = rus.main(["probe", "--selection", selection])
    assert code == 0  # probe is non-blocking by design
    probed = {argv[-1] for argv in seen}
    assert probed == {"skills/a/tests/test_a.py", "skills/b/tests/test_b.py"}


# --- run: driver smoke against real pytest ---------------------------------


def test_run_driver_writes_markers_groups_and_exit(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    passing = tmp_path / "pkg_a" / "tests" / "test_ok.py"
    passing.parent.mkdir(parents=True)
    _write(passing, "def test_ok():\n    assert True\n")
    failing = tmp_path / "pkg_b" / "tests" / "test_bad.py"
    failing.parent.mkdir(parents=True)
    _write(failing, "def test_bad():\n    assert False\n")
    selection = _selection(
        tmp_path,
        runs=[[str(passing)], [str(failing)]],
        deselects=[[], []],
    )
    code = rus.main([
        "run", "--selection", selection,
        "--output-log", str(tmp_path / "unit-test-output.log"),
        "--groups-json", str(tmp_path / "unit-test-groups.json"),
    ])
    assert code == 1  # group 2 failed
    groups = json.loads((tmp_path / "unit-test-groups.json").read_text(encoding="utf-8"))
    assert [g["exit"] for g in groups] == [0, 1]
    log_text = (tmp_path / "unit-test-output.log").read_text(encoding="utf-8")
    assert "[group-start] index=1 files=1" in log_text
    assert "[group-boundary] index=1 files=1 exit=0" in log_text
    assert "[group-boundary] index=2 files=1 exit=1" in log_text
    out = capsys.readouterr().out
    assert "[unit-test] group 1/2" in out and "failed groups: [2]" in out


# --- sharding (debt-ledger ⑧): deterministic plan, global numbering, merge ---


def test_plan_shards_balanced_complete_and_deterministic() -> None:
    runs = [[f"pkg_a/tests/test_{i}.py" for i in range(24)]] + [
        [f"pkg_{chr(98 + g)}/tests/test_one.py", f"pkg_{chr(98 + g)}/tests/test_two.py"]
        for g in range(6)
    ]
    units = rus.build_shard_units(runs, [[] for _ in runs], 3)
    plan_once = rus.plan_shards(units, 3)
    plan_again = rus.plan_shards(rus.build_shard_units(runs, [[] for _ in runs], 3), 3)
    assert plan_once == plan_again  # pure function of (selection, shards)
    assert len(plan_once) == 3
    planned_files = [f for shard in plan_once for unit in shard for f in unit["files"]]
    assert sorted(planned_files) == sorted(f for group in runs for f in group)
    loads = [sum(len(unit["files"]) for unit in shard) for shard in plan_once]
    biggest = max(len(unit["files"]) for unit in units)
    assert max(loads) <= (36 + biggest - 1) // 3 + biggest  # LPT bound vs fair share


def test_build_shard_units_splits_only_big_groups_whole_file() -> None:
    big = [f"services/tests/test_{i:03d}.py" for i in range(30)]
    runs = [big, ["skills/a/tests/test_x.py"], ["skills/b/tests/test_y.py"]]
    units = rus.build_shard_units(runs, [[] for _ in runs], 6)
    # fair share of 32 files over 6 shards = 6 -> the 30-file group splits,
    # both single-file groups stay intact
    chunked = [unit for unit in units if unit["files"][0].startswith("services/")]
    assert len(chunked) == 6  # ceil(30/6)-file chunks
    assert all(len(unit["files"]) <= 5 for unit in chunked)
    singles = [unit for unit in units if not unit["files"][0].startswith("services/")]
    assert [len(unit["files"]) for unit in singles] == [1, 1]


def test_build_shard_units_single_shard_preserves_legacy_order() -> None:
    runs = [["a/test_1.py", "a/test_2.py"], ["b/test_3.py"], ["c/test_4.py", "c/test_5.py"]]
    deselects = [["a/test_1.py::test_x"], [], []]
    units = rus.build_shard_units(runs, deselects, 1)
    assert [unit["files"] for unit in units] == runs  # order and grouping intact
    assert units[0]["deselects"] == ["a/test_1.py::test_x"]


def test_build_shard_units_routes_deselects_to_owning_chunk() -> None:
    big = [f"services/tests/test_{i:03d}.py" for i in range(12)]
    runs = [big, ["skills/a/tests/test_x.py"]]
    deselects = [["services/tests/test_007.py::test_slow"], []]
    units = rus.build_shard_units(runs, deselects, 4)
    owning = [u for u in units if "services/tests/test_007.py" in u["files"]]
    drop = [u for u in units if u["deselects"]]
    assert len(owning) == 1 and drop == owning  # landed on exactly its chunk
    # review finding 2: selector deselect ids are ROOTDIR-relative for
    # skills/services groups (domain prefix stripped) while chunk files are
    # repo-relative - routing must bridge the prefix, not just match exactly
    units = rus.build_shard_units(runs, [["tests/test_007.py::test_slow"], []], 4)
    owning = [u for u in units if "services/tests/test_007.py" in u["files"]]
    assert owning and owning[0]["deselects"] == ["tests/test_007.py::test_slow"]
    # in a SPLIT group, a deselect node whose file has no home chunk must
    # fail closed rather than be dropped silently (intact groups keep the
    # legacy per-group trust: the selector vouches for those pairings)
    try:
        rus.build_shard_units([big], [["b/other.py::test_gone"]], 4)
        raised = False
    except SystemExit:
        raised = True
    assert raised


def test_run_shard_cycle_merges_to_green_ratchet(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    passing = tmp_path / "pkg_a" / "tests" / "test_ok.py"
    passing.parent.mkdir(parents=True)
    _write(passing, "def test_ok():\n    assert True\n")
    failing = tmp_path / "pkg_b" / "tests" / "test_bad.py"
    failing.parent.mkdir(parents=True)
    _write(failing, "def test_bad():\n    assert False\n")
    selection = _selection(
        tmp_path,
        runs=[[str(passing)], [str(failing)]],
        deselects=[[], []],
    )
    known_doc = _known_doc(
        tmp_path, str(failing).replace("\\", "/") + "::test_bad"
    )
    for shard in (1, 2):
        shard_dir = tmp_path / "dist" / f"shard-{shard}"
        shard_dir.mkdir(parents=True)
        rus.main([
            "run", "--selection", selection, "--shard", f"{shard}/2",
            "--output-log", str(shard_dir / "unit-test-output.log"),
            "--groups-json", str(shard_dir / "unit-test-groups.json"),
        ])
    merged_groups = tmp_path / "unit-test-groups.json"
    merged_log = tmp_path / "unit-test-output.log"
    code = rus.main([
        "merge", "--shards-dir", str(tmp_path / "dist"), "--expect", "2",
        "--output-log", str(merged_log), "--groups-json", str(merged_groups),
    ])
    assert code == 0
    capsys.readouterr()
    code = rus.main([
        "ratchet", "--groups-json", str(merged_groups),
        "--output-log", str(merged_log), "--known-failures", known_doc,
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "zero new failures" in out
    # global numbering: one group per shard, dense across the merge
    groups = json.loads(merged_groups.read_text(encoding="utf-8"))
    assert [g["index"] for g in groups] == [1, 2]
    assert sorted(g["shard"] for g in groups) == [1, 2]


def test_merge_fail_closed_on_missing_shard(tmp_path, capsys) -> None:
    only = tmp_path / "dist" / "shard-1"
    only.mkdir(parents=True)
    (only / "unit-test-groups.json").write_text("[]", encoding="utf-8")
    (only / "unit-test-output.log").write_text("", encoding="utf-8")
    code = rus.main([
        "merge", "--shards-dir", str(tmp_path / "dist"), "--expect", "2",
        "--output-log", str(tmp_path / "m.log"),
        "--groups-json", str(tmp_path / "m.json"),
    ])
    assert code == 1
    assert "incomplete shard evidence" in capsys.readouterr().out


def test_merge_fail_closed_on_numbering_collision(tmp_path, capsys) -> None:
    for shard in (1, 2):
        shard_dir = tmp_path / "dist" / f"shard-{shard}"
        shard_dir.mkdir(parents=True)
        # both shards claim global index 1 -> not dense 1..N
        (shard_dir / "unit-test-groups.json").write_text(
            json.dumps([{"index": 1, "files": 1, "exit": 0, "seconds": 0.1}]),
            encoding="utf-8",
        )
        (shard_dir / "unit-test-output.log").write_text("", encoding="utf-8")
    code = rus.main([
        "merge", "--shards-dir", str(tmp_path / "dist"), "--expect", "2",
        "--output-log", str(tmp_path / "m.log"),
        "--groups-json", str(tmp_path / "m.json"),
    ])
    assert code == 1
    assert "not dense" in capsys.readouterr().out


def test_merge_fail_closed_on_all_empty_shards(tmp_path, capsys) -> None:
    # review finding 1: density check is vacuous at N=0 (range(1,1)==[]) -
    # an all-empty merge must still read RED, not GREEN on zero evidence
    for shard in (1, 2):
        shard_dir = tmp_path / "dist" / f"shard-{shard}"
        shard_dir.mkdir(parents=True)
        (shard_dir / "unit-test-groups.json").write_text("[]", encoding="utf-8")
        (shard_dir / "unit-test-output.log").write_text("", encoding="utf-8")
    code = rus.main([
        "merge", "--shards-dir", str(tmp_path / "dist"), "--expect", "2",
        "--output-log", str(tmp_path / "m.log"),
        "--groups-json", str(tmp_path / "m.json"),
    ])
    assert code == 1
    assert "zero groups" in capsys.readouterr().out


def test_merge_fail_closed_on_malformed_json_syntax(tmp_path, capsys) -> None:
    # review nit 3: syntactically broken json must die with the tidy
    # [merge] message, not an uncaught traceback (exit is 1 either way)
    for shard in (1, 2):
        shard_dir = tmp_path / "dist" / f"shard-{shard}"
        shard_dir.mkdir(parents=True)
    (tmp_path / "dist" / "shard-1" / "unit-test-groups.json").write_text(
        "{not json", encoding="utf-8"
    )
    (tmp_path / "dist" / "shard-1" / "unit-test-output.log").write_text("", encoding="utf-8")
    (tmp_path / "dist" / "shard-2" / "unit-test-groups.json").write_text("[]", encoding="utf-8")
    (tmp_path / "dist" / "shard-2" / "unit-test-output.log").write_text("", encoding="utf-8")
    code = rus.main([
        "merge", "--shards-dir", str(tmp_path / "dist"), "--expect", "2",
        "--output-log", str(tmp_path / "m.log"),
        "--groups-json", str(tmp_path / "m.json"),
    ])
    assert code == 1
    assert "unreadable or malformed JSON" in capsys.readouterr().out
