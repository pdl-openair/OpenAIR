"""Unit tests for check_known_failures_ratchet (M3): nodes[] may only shrink."""

import json
from pathlib import Path

import check_known_failures_ratchet as ratchet
from conftest import run_git

KNOWN_PATH = ".github/unit-test-known-failures.json"


def _doc(*ids: str) -> str:
    return json.dumps(
        {
            "version": 1,
            "nodes": [
                {"id": node_id, "status": "known-fail", "exc": "fixture"} for node_id in ids
            ],
        }
    )


def _commit_known(repo: str, *ids: str) -> str:
    """Write the known-failures file, commit it, return the commit sha."""
    known = Path(repo) / ".github" / "unit-test-known-failures.json"
    known.parent.mkdir(parents=True, exist_ok=True)
    known.write_text(_doc(*ids), encoding="utf-8")
    run_git(repo, "add", KNOWN_PATH)
    run_git(repo, "commit", "-q", "-m", "known-failures snapshot")
    return run_git(repo, "rev-parse", "HEAD").strip()


def _write_known(repo: str, text: str) -> None:
    known = Path(repo) / ".github" / "unit-test-known-failures.json"
    known.parent.mkdir(parents=True, exist_ok=True)
    known.write_text(text, encoding="utf-8")


class TestLoadNodeIds:
    def test_well_formed(self):
        assert ratchet.load_node_ids(_doc("a", "b"), origin="x") == {"a", "b"}

    def test_empty_nodes_list(self):
        assert ratchet.load_node_ids('{"version": 1, "nodes": []}', origin="x") == set()

    def test_invalid_json_raises(self):
        for text in ("", "not json", "{"):
            try:
                ratchet.load_node_ids(text, origin="x")
            except ValueError:
                continue
            raise AssertionError(f"expected ValueError for {text!r}")

    def test_wrong_shapes_raise(self):
        for text in ("[]", '"str"', '{"nodes": {}}', '{"other": 1}'):
            try:
                ratchet.load_node_ids(text, origin="x")
            except ValueError:
                continue
            raise AssertionError(f"expected ValueError for {text!r}")

    def test_node_without_id_raises(self):
        for text in ('{"nodes": [{}]}', '{"nodes": [{"id": ""}]}', '{"nodes": [{"id": 3}]}'):
            try:
                ratchet.load_node_ids(text, origin="x")
            except ValueError:
                continue
            raise AssertionError(f"expected ValueError for {text!r}")


class TestTriStateDoD6:
    """DoD D6 三态：增 → 1；删 → 0；无基线 → 0。"""

    def test_added_node_is_violation(self, git_repo, capsys):
        repo, _base = git_repo
        base = _commit_known(repo, "a.py::test_a")
        _write_known(repo, _doc("a.py::test_a", "b.py::test_b"))
        code = ratchet.main(["--base", base, "--root", repo])
        assert code == ratchet.EXIT_VIOLATION
        err = capsys.readouterr().err
        assert "b.py::test_b" in err and "grew" in err

    def test_removed_node_passes_with_notice(self, git_repo, capsys):
        repo, _base = git_repo
        base = _commit_known(repo, "a.py::test_a", "b.py::test_b")
        _write_known(repo, _doc("a.py::test_a"))
        code = ratchet.main(["--base", base, "--root", repo])
        assert code == ratchet.EXIT_PASS
        out = capsys.readouterr().out
        assert "- b.py::test_b" in out and "did not grow" in out

    def test_no_baseline_file_is_bootstrap_pass(self, git_repo, capsys):
        repo, base = git_repo  # base commit has no known-failures file at all
        _write_known(repo, _doc("a.py::test_a"))
        code = ratchet.main(["--base", base, "--root", repo])
        assert code == ratchet.EXIT_PASS
        assert "bootstrap" in capsys.readouterr().out

    def test_identical_set_passes(self, git_repo):
        repo, _base = git_repo
        base = _commit_known(repo, "a.py::test_a")
        _write_known(repo, _doc("a.py::test_a"))
        assert ratchet.main(["--base", base, "--root", repo]) == ratchet.EXIT_PASS

    def test_same_id_changed_status_not_growth(self, git_repo):
        repo, _base = git_repo
        base = _commit_known(repo, "a.py::test_a")
        _write_known(
            repo,
            json.dumps(
                {"version": 1, "nodes": [{"id": "a.py::test_a", "status": "excused", "exc": "x"}]}
            ),
        )
        assert ratchet.main(["--base", base, "--root", repo]) == ratchet.EXIT_PASS


class TestAllowGrowth:
    def test_allow_growth_flag_permits_addition_with_notice(self, git_repo, capsys):
        repo, _base = git_repo
        base = _commit_known(repo, "a.py::test_a")
        _write_known(repo, _doc("a.py::test_a", "b.py::test_b"))
        code = ratchet.main(["--base", base, "--root", repo, "--allow-growth"])
        assert code == ratchet.EXIT_PASS
        assert "--allow-growth" in capsys.readouterr().out


class TestFailClosed:
    def test_invalid_base_ref_is_env_error(self, git_repo, capsys):
        repo, _base = git_repo
        code = ratchet.main(["--base", "nonexistent-ref-xyz", "--root", repo])
        assert code == ratchet.EXIT_ERROR
        assert "invalid base ref" in capsys.readouterr().err

    def test_malformed_working_tree_json_is_config_error(self, git_repo, capsys):
        repo, base = git_repo
        _write_known(repo, "not json at all")
        code = ratchet.main(["--base", base, "--root", repo])
        assert code == ratchet.EXIT_ERROR
        assert "working tree" in capsys.readouterr().err

    def test_malformed_base_json_is_config_error(self, git_repo):
        repo, _base = git_repo
        _write_known(repo, "garbage {")
        run_git(repo, "add", KNOWN_PATH)
        run_git(repo, "commit", "-q", "-m", "bad baseline")
        base = run_git(repo, "rev-parse", "HEAD").strip()
        _write_known(repo, _doc("a.py::test_a"))
        assert ratchet.main(["--base", base, "--root", repo]) == ratchet.EXIT_ERROR

    def test_deleted_working_tree_file_is_maximal_shrink(self, git_repo, capsys):
        repo, _base = git_repo
        base = _commit_known(repo, "a.py::test_a")
        known = Path(repo) / ".github" / "unit-test-known-failures.json"
        known.unlink()
        code = ratchet.main(["--base", base, "--root", repo])
        assert code == ratchet.EXIT_PASS
        assert "maximal shrink" in capsys.readouterr().out
