"""Unit tests for run_local: subcommand dispatch and graceful degradation."""

import json
import subprocess

import pytest
import run_local


class FakeRunner:
    """Records delegated commands; returns a canned exit code per call."""

    def __init__(self, returncode=0):
        self.calls = []
        self.returncode = returncode

    def __call__(self, argv, cwd=None):
        self.calls.append((list(argv), cwd))
        return subprocess.CompletedProcess(args=argv, returncode=self.returncode)


def _always_true(_python, _module):
    return True


def _always_false(_python, _module):
    return False


class TestDispatch:
    def test_ruff_delegates_to_diff_gate(self):
        runner = FakeRunner(returncode=0)
        code = run_local.main(
            ["ruff"],
            runner=runner,
            which=lambda _name: None,
            prober=_always_true,
            python="PYEXE",
        )
        assert code == 0
        assert len(runner.calls) == 1
        argv = runner.calls[0][0]
        assert argv[0] == "PYEXE"
        assert argv[1].endswith("diff_gate.py")
        assert argv[2:] == ["--ruff", "--base", "origin/test"]

    def test_mypy_delegates_to_mypy_gate(self):
        runner = FakeRunner(returncode=0)
        code = run_local.main(
            ["mypy"],
            runner=runner,
            which=lambda _name: None,
            prober=_always_true,
            python="PYEXE",
        )
        assert code == 0
        argv = runner.calls[0][0]
        assert argv[1].endswith("mypy_gate.py")
        assert "--python" in argv and "PYEXE" in argv

    def test_placement_delegates_without_probe(self):
        runner = FakeRunner(returncode=0)
        code = run_local.main(
            ["placement"],
            runner=runner,
            which=lambda _name: None,
            prober=_always_false,  # placement needs no external tool
            python="PYEXE",
        )
        assert code == 0
        assert runner.calls[0][0][1].endswith("placement_guard.py")

    def test_returncode_propagated(self):
        runner = FakeRunner(returncode=1)
        code = run_local.main(
            ["ruff"], runner=runner, which=lambda _n: None, prober=_always_true, python="P"
        )
        assert code == 1

    def test_missing_subcommand_is_usage_error(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            run_local.main([])
        assert excinfo.value.code == 2

    def test_unknown_subcommand_is_usage_error(self):
        with pytest.raises(SystemExit) as excinfo:
            run_local.main(["nope"])
        assert excinfo.value.code == 2


class TestGitleaks:
    def test_missing_binary_degrades_to_zero(self, capsys):
        runner = FakeRunner(returncode=0)
        code = run_local.main(
            ["gitleaks"],
            runner=runner,
            which=lambda _name: None,
            prober=_always_true,
            python="P",
        )
        assert code == 0
        assert runner.calls == []  # nothing delegated
        assert "degraded" in capsys.readouterr().err

    def test_missing_binary_strict_is_error(self):
        code = run_local.main(
            ["--strict", "gitleaks"],
            runner=FakeRunner(),
            which=lambda _name: None,
            prober=_always_true,
            python="P",
        )
        assert code == 2

    def test_present_binary_delegates_protect_staged(self):
        runner = FakeRunner(returncode=1)  # leaks found
        code = run_local.main(
            ["gitleaks"],
            runner=runner,
            which=lambda name: f"/usr/bin/{name}" if name == "gitleaks" else None,
            prober=_always_true,
            python="P",
        )
        assert code == 1
        argv = runner.calls[0][0]
        assert argv[0].endswith("gitleaks")
        assert argv[1:] == ["protect", "--staged", "--redact"]


class TestMissingToolDegradation:
    def test_ruff_not_installed_degrades(self):
        code = run_local.main(
            ["ruff"],
            runner=FakeRunner(),
            which=lambda _name: None,
            prober=_always_false,
            python="P",
        )
        assert code == 0

    def test_mypy_not_installed_degrades(self):
        code = run_local.main(
            ["mypy"],
            runner=FakeRunner(),
            which=lambda _name: None,
            prober=_always_false,
            python="P",
        )
        assert code == 0

    def test_ruff_not_installed_strict_fails(self):
        code = run_local.main(
            ["--strict", "ruff"],
            runner=FakeRunner(),
            which=lambda _name: None,
            prober=_always_false,
            python="P",
        )
        assert code == 2


class TestFutureGates:
    def test_pointer_missing_script_degrades(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            run_local, "FUTURE_GATES", {"pointer": str(tmp_path / "nope.py")}
        )
        code = run_local.main(
            ["pointer"],
            runner=FakeRunner(),
            which=lambda _name: None,
            prober=_always_true,
            python="P",
        )
        assert code == 0

    def test_pointer_present_script_delegates(self, monkeypatch, tmp_path):
        script = tmp_path / "check_pointer_integrity.py"
        script.write_text("# placeholder\n", encoding="utf-8")
        monkeypatch.setattr(run_local, "FUTURE_GATES", {"pointer": str(script)})
        runner = FakeRunner(returncode=0)
        code = run_local.main(
            ["pointer"], runner=runner, which=lambda _n: None, prober=_always_true, python="P"
        )
        assert code == 0
        assert runner.calls[0][0] == ["P", str(script)]

    def test_commit_msg_forwards_message_file(self, monkeypatch, tmp_path):
        script = tmp_path / "check_commit_msg.py"
        script.write_text("# placeholder\n", encoding="utf-8")
        monkeypatch.setattr(
            run_local,
            "FUTURE_GATES",
            {"pointer": str(tmp_path / "nope.py"), "commit-msg": str(script)},
        )
        runner = FakeRunner(returncode=0)
        code = run_local.main(
            ["commit-msg", ".git/COMMIT_EDITMSG"],
            runner=runner,
            which=lambda _n: None,
            prober=_always_true,
            python="P",
        )
        assert code == 0
        assert runner.calls[0][0] == ["P", str(script), ".git/COMMIT_EDITMSG"]


def _proc(stdout="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class FakeGit:
    """Dispatches the ④F4 two-point git calls with canned outputs."""

    def __init__(self, merge_base="MB", point1="", point2="", diff="", fail=False):
        self.merge_base = merge_base
        self.point1 = point1
        self.point2 = point2
        self.diff = diff
        self.fail = fail
        self.calls = []

    def __call__(self, argv, cwd=None):
        self.calls.append(list(argv))
        joined = " ".join(argv)
        if self.fail:
            return _proc(returncode=128)
        if argv[1] == "merge-base":
            return _proc(stdout=self.merge_base + "\n")
        if joined == f"git diff --cached --name-only {self.merge_base}":
            return _proc(stdout=self.point1)
        if joined == "git diff --cached --name-only":
            return _proc(stdout=self.point2)
        if joined == f"git diff --no-color --unified=0 --cached {self.merge_base}":
            return _proc(stdout=self.diff)
        raise AssertionError(f"unexpected git call: {joined}")


class TestStagedScope:
    """④F4 两点式 staged 检测：merge-base↔index 与 HEAD↔index 共同判定。"""

    def test_two_point_intersection(self, tmp_path):
        git = FakeGit(point1="a.py\nb.py\n", point2="a.py\nc.py\n")
        result = run_local.staged_scope(str(tmp_path), "origin/test", git=git)
        assert result == (["a.py"], "MB")

    def test_staged_back_to_base_state_excluded(self, tmp_path):
        # c.py staged 但内容已还原到 merge-base 态 → 不在点1清单 → 无需门禁
        git = FakeGit(point1="a.py\n", point2="a.py\nc.py\n")
        assert run_local.staged_scope(str(tmp_path), "origin/test", git=git) == (["a.py"], "MB")

    def test_no_staged_delta(self, tmp_path):
        git = FakeGit(point1="a.py\n", point2="")
        assert run_local.staged_scope(str(tmp_path), "origin/test", git=git) == ([], "MB")

    def test_git_failure_returns_none(self, tmp_path):
        assert run_local.staged_scope(str(tmp_path), "origin/test", git=FakeGit(fail=True)) is None


class TestStagedRuffGate:
    DIFF = (
        "diff --git a/a.py b/a.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/a.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+import os\n"
        "+x = 1  # type: ignore[explicit]\n"
    )

    def _ruff(self, payload, returncode=1):
        def fake_sp(argv, **_kwargs):
            return _proc(stdout=payload, returncode=returncode)

        return fake_sp

    def test_violation_on_staged_added_line_blocks(self, tmp_path, capsys):
        git = FakeGit(diff=self.DIFF)
        ruff_json = json.dumps(
            [
                {
                    "filename": str(tmp_path / "a.py"),
                    "location": {"row": 2},
                    "code": "RUF100",
                    "message": "unused type ignore",
                }
            ]
        )
        code = run_local.staged_ruff_gate(
            "P", ["a.py"], "MB", str(tmp_path), git=git, sp=self._ruff(ruff_json)
        )
        assert code == 1
        out = capsys.readouterr().out
        assert "staged-ruff" in out and "a.py:2" in out

    def test_violation_on_unstaged_file_ignored(self, tmp_path):
        git = FakeGit(diff=self.DIFF)
        ruff_json = json.dumps(
            [{"filename": "other.py", "location": {"row": 1}, "code": "E501", "message": "long"}]
        )
        code = run_local.staged_ruff_gate(
            "P", ["a.py"], "MB", str(tmp_path), git=git, sp=self._ruff(ruff_json)
        )
        assert code == 0

    def test_ruff_crash_degrades_to_pass(self, tmp_path, capsys):
        git = FakeGit(diff=self.DIFF)
        code = run_local.staged_ruff_gate(
            "P", ["a.py"], "MB", str(tmp_path), git=git, sp=self._ruff("[]", returncode=2)
        )
        assert code == 0
        assert "WARNING" in capsys.readouterr().err


class TestCmdRuffStagedPass:
    def _main(self, monkeypatch, staged_scope, staged_gate):
        monkeypatch.setattr(run_local, "staged_scope", staged_scope)
        monkeypatch.setattr(run_local, "staged_ruff_gate", staged_gate)
        return run_local.main(
            ["ruff"],
            runner=FakeRunner(returncode=0),
            which=lambda _n: None,
            prober=_always_true,
            python="P",
        )

    def test_staged_detection_unavailable_degrades(self, monkeypatch, capsys):
        code = self._main(monkeypatch, lambda *a, **k: None, lambda *a, **k: 1)
        assert code == 0
        assert "degraded" in capsys.readouterr().err

    def test_no_staged_delta_passes_with_note(self, monkeypatch, capsys):
        code = self._main(monkeypatch, lambda *a, **k: ([], "MB"), lambda *a, **k: 1)
        assert code == 0
        assert "no staged delta" in capsys.readouterr().out

    def test_staged_violation_propagates(self, monkeypatch):
        gate = lambda *a, **k: 1  # noqa: E731
        code = self._main(monkeypatch, lambda *a, **k: (["a.py"], "MB"), gate)
        assert code == 1

    def test_committed_range_failure_short_circuits(self, monkeypatch):
        monkeypatch.setattr(run_local, "staged_scope", lambda *a, **k: (["a.py"], "MB"))
        monkeypatch.setattr(run_local, "staged_ruff_gate", lambda *a, **k: 0)
        code = run_local.main(
            ["ruff"],
            runner=FakeRunner(returncode=1),  # committed-range gate red
            which=lambda _n: None,
            prober=_always_true,
            python="P",
        )
        assert code == 1
