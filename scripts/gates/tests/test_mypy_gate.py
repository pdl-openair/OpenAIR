"""Unit tests for mypy_gate: scope parsing, ratchet, zombies, gate loop."""

import os
import subprocess
from pathlib import Path

import mypy_gate
from conftest import run_git


def _scope_path(repo: str) -> Path:
    """Repo-relative scope file path with parent dirs created."""
    path = Path(repo) / "spec" / "enforcement" / "mypy-scope.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


class TestScopeParsing:
    def test_parses_entries_with_comments_and_blanks(self):
        text = (
            "# header comment\n"
            "\n"
            "services/api/app/main.py  # trailing comment\n"
            "#comment-only line\n"
            "packages/llm_core/llm_core/token_usage.py\n"
        )
        assert mypy_gate.parse_scope_text(text) == [
            "services/api/app/main.py",
            "packages/llm_core/llm_core/token_usage.py",
        ]

    def test_rejects_entries_with_whitespace(self):
        try:
            mypy_gate.parse_scope_text("two tokens.py\n")
        except ValueError as exc:
            assert "whitespace" in str(exc)
        else:
            raise AssertionError("expected ValueError for whitespace entry")

    def test_added_entries_from_diff_ignores_deletions(self):
        diff_text = (
            "--- a/spec/enforcement/mypy-scope.txt\n"
            "+++ b/spec/enforcement/mypy-scope.txt\n"
            "@@ -1,3 +1,3 @@\n"
            "-services/api/app/gone.py\n"
            "+services/api/app/new.py\n"
            "+# comment line is not an entry\n"
            " context\n"
        )
        assert mypy_gate.added_entries_from_diff(diff_text) == {"services/api/app/new.py"}


class TestZombiesAndSplit:
    def test_zombie_entries_detected(self, tmp_path):
        (tmp_path / "alive.py").write_text("x = 1\n", encoding="utf-8")
        entries = ["alive.py", "deleted.py"]
        assert mypy_gate.find_zombie_entries(entries, str(tmp_path)) == ["deleted.py"]

    def test_split_checked_separates_exempted(self):
        all_files = {"services/api": ["app/keep.py", "app/exempt.py"]}
        checked, exempt_count = mypy_gate.split_checked(
            all_files, {"services/api/app/exempt.py"}
        )
        assert checked == {"services/api": ["app/keep.py"]}
        assert exempt_count == 1


class TestRatchet:
    def test_addition_to_existing_list_is_blocked(self, git_repo):
        repo, _fixture_base = git_repo
        scope = "spec/enforcement/mypy-scope.txt"
        scope_path = _scope_path(repo)
        scope_path.write_text("# baseline\nservices/api/app/a.py\n", encoding="utf-8")
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-q", "-m", "freeze scope")
        base = run_git(repo, "rev-parse", "HEAD").strip()
        scope_path.write_text(
            "# baseline\nservices/api/app/a.py\nservices/api/app/b.py\n", encoding="utf-8"
        )
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-q", "-m", "grow scope")
        problems, mode = mypy_gate.check_ratchet(base, repo, scope)
        assert problems == [
            "ratchet violation: scope entry ADDED in this diff: services/api/app/b.py"
        ]
        assert mode == "ratchet (file existed at base)"

    def test_removal_only_passes(self, git_repo):
        repo, _fixture_base = git_repo
        scope = "spec/enforcement/mypy-scope.txt"
        scope_path = _scope_path(repo)
        scope_path.write_text(
            "# baseline\nservices/api/app/a.py\nservices/api/app/b.py\n", encoding="utf-8"
        )
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-q", "-m", "freeze scope")
        base = run_git(repo, "rev-parse", "HEAD").strip()
        scope_path.write_text("# baseline\nservices/api/app/a.py\n", encoding="utf-8")
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-q", "-m", "shrink scope")
        problems, _mode = mypy_gate.check_ratchet(base, repo, scope)
        assert problems == []

    def test_initial_freeze_is_bootstrap_not_violation(self, git_repo):
        repo, base = git_repo
        scope = "spec/enforcement/mypy-scope.txt"
        scope_path = _scope_path(repo)
        scope_path.write_text("# baseline\nservices/api/app/a.py\n", encoding="utf-8")
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-q", "-m", "create scope")
        problems, mode = mypy_gate.check_ratchet(base, repo, scope)
        assert problems == []
        assert "bootstrap" in mode


class TestMypyInvocation:
    def test_error_line_grouped_per_file(self):
        output = (
            "app/main.py:12: error: Missing return type annotation  [no-untyped-def]\n"
            "app/main.py:30: error: Incompatible types in assignment  [assignment]\n"
            "app/other.py:3: error: Cannot find implementation for 'x'  [import]\n"
            "note: ...not an error line...\n"
        )
        errors = mypy_gate.parse_mypy_errors(output, "services/api")
        assert set(errors) == {
            "services/api/app/main.py",
            "services/api/app/other.py",
        }
        assert len(errors["services/api/app/main.py"]) == 2

    def test_check_out_of_scope_reports_errors(self, monkeypatch, tmp_path):
        def fake_run_mypy(_python, _root, run_root, _files, _config):
            return subprocess.CompletedProcess(
                args=["mypy"],
                returncode=1,
                stdout="app/keep.py:7: error: bad types  [assignment]\n",
                stderr="",
            )

        monkeypatch.setattr(mypy_gate, "run_mypy", fake_run_mypy)
        problems = mypy_gate.check_out_of_scope(
            "python", str(tmp_path), {"services/api": ["app/keep.py"]}, "mypy.ini"
        )
        assert problems == [
            "out-of-scope mypy error: app/keep.py:7: error: bad types  [assignment]"
        ]

    def test_check_out_of_scope_clean(self, monkeypatch, tmp_path):
        def fake_run_mypy(_python, _root, _run_root, _files, _config):
            return subprocess.CompletedProcess(args=["mypy"], returncode=0, stdout="", stderr="")

        monkeypatch.setattr(mypy_gate, "run_mypy", fake_run_mypy)
        problems = mypy_gate.check_out_of_scope(
            "python", str(tmp_path), {"services/api": ["app/keep.py"]}, "mypy.ini"
        )
        assert problems == []


class TestBaseRefValidation:
    """H3：无效 base ref 必须 fail-closed（exit 2），绝不静默落 bootstrap。"""

    def test_invalid_base_ref_is_env_error_not_bootstrap(self, git_repo, capsys):
        repo, _base = git_repo
        scope_rel = "spec/enforcement/mypy-scope.txt"
        _scope_path(repo).write_text("# baseline\nservices/api/app/a.py\n", encoding="utf-8")
        code = mypy_gate.main(
            ["--base", "nonexistent-ref-xyz", "--root", repo, "--scope-file", scope_rel]
        )
        assert code == mypy_gate.EXIT_ERROR
        assert "invalid base ref" in capsys.readouterr().err

    def test_invalid_base_ref_in_freeze_mode_is_env_error(self, git_repo):
        repo, _base = git_repo
        code = mypy_gate.main(
            [
                "--base",
                "nonexistent-ref-xyz",
                "--root",
                repo,
                "--scope-file",
                "spec/enforcement/mypy-scope.txt",
                "--freeze",
            ]
        )
        assert code == mypy_gate.EXIT_ERROR

    def test_ref_exists_helper(self, git_repo):
        repo, base = git_repo
        assert mypy_gate.ref_exists(base, repo) is True
        assert mypy_gate.ref_exists("nonexistent-ref-xyz", repo) is False


class TestPathAnchoring:
    """⑤：脚本自身路径自锚定（root/config/python 不依赖调用方 cwd）。"""

    def test_default_root_is_file_anchored(self):
        root = mypy_gate.default_root()
        assert os.path.isfile(os.path.join(root, "scripts", "gates", "mypy_gate.py"))

    def test_run_mypy_absolutizes_relative_python(self, monkeypatch, tmp_path):
        captured = {}

        def fake_run(argv, **_kwargs):
            captured["argv"] = argv
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(mypy_gate.subprocess, "run", fake_run)
        mypy_gate.run_mypy(
            "rel/venv/bin/python", str(tmp_path), "services/api", ["app/x.py"], "mypy.ini"
        )
        # ⑤ 根治：--python 相对路径在进入子进程前绝对化（run_root 换 cwd 不再失效）
        assert os.path.isabs(captured["argv"][0])
        assert captured["argv"][0].endswith("rel/venv/bin/python") or captured["argv"][0].endswith(
            os.path.join("rel", "venv", "bin", "python")
        )

    def test_mypy_config_resolved_against_root(self, git_repo, monkeypatch):
        repo, base = git_repo
        scope_rel = "spec/enforcement/mypy-scope.txt"
        _scope_path(repo).write_text("# baseline\n", encoding="utf-8")
        app_dir = os.path.join(repo, "services", "api", "app")
        os.makedirs(app_dir)
        with open(os.path.join(app_dir, "x.py"), "w", encoding="utf-8") as handle:
            handle.write("y: int = 1\n")
        captured = {}

        def fake_run_mypy(_python, _root, _run_root, _files, config_file):
            captured["config"] = config_file
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        monkeypatch.setattr(mypy_gate, "run_mypy", fake_run_mypy)
        code = mypy_gate.main(
            [
                "--base",
                base,
                "--root",
                repo,
                "--scope-file",
                scope_rel,
                "--mypy-config",
                "custom.ini",
            ]
        )
        assert code == mypy_gate.EXIT_PASS
        assert captured["config"] == os.path.join(repo, "custom.ini")


class TestFreezeAndGateLoop:
    def test_freeze_then_gate_passes(self, git_repo, monkeypatch):
        repo, base = git_repo
        app_dir = os.path.join(repo, "services", "api", "app")
        os.makedirs(app_dir)
        with open(os.path.join(app_dir, "clean.py"), "w", encoding="utf-8") as handle:
            handle.write("x: int = 1\n")
        with open(os.path.join(app_dir, "dirty.py"), "w", encoding="utf-8") as handle:
            handle.write("x = 1  # type: ignore[explicit]\n")
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-q", "-m", "sources")

        def fake_run_mypy(_python, _root, _run_root, files, _config):
            stdout = ""
            for rel in files:
                if rel == "app/dirty.py":
                    stdout += "app/dirty.py:1: error: Untyped definition  [no-untyped-def]\n"
            return subprocess.CompletedProcess(
                args=["mypy"],
                returncode=1 if stdout else 0,
                stdout=stdout,
                stderr="",
            )

        monkeypatch.setattr(mypy_gate, "run_mypy", fake_run_mypy)
        scope_rel = "spec/enforcement/mypy-scope.txt"
        code = mypy_gate.main(
            ["--base", base, "--root", repo, "--scope-file", scope_rel, "--freeze"]
        )
        assert code == mypy_gate.EXIT_PASS
        scope_path = os.path.join(repo, *scope_rel.split("/"))
        with open(scope_path, encoding="utf-8") as handle:
            content = handle.read()
        assert "Baseline commit:" in content
        assert "services/api/app/dirty.py" in content
        assert "services/api/app/clean.py" not in content

        # gate: everything dirty is exempt, clean file passes -> PASS
        code = mypy_gate.main(["--base", base, "--root", repo, "--scope-file", scope_rel])
        assert code == mypy_gate.EXIT_PASS

    def test_gate_fails_on_out_of_scope_error(self, git_repo, monkeypatch):
        repo, base = git_repo
        app_dir = os.path.join(repo, "services", "api", "app")
        os.makedirs(app_dir)
        with open(os.path.join(app_dir, "dirty.py"), "w", encoding="utf-8") as handle:
            handle.write("x = 1\n")
        scope_rel = "spec/enforcement/mypy-scope.txt"
        scope_path = _scope_path(repo)
        scope_path.write_text("# baseline (empty): no exemptions\n", encoding="utf-8")
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-q", "-m", "sources and empty scope")

        def fake_run_mypy(_python, _root, _run_root, files, _config):
            stdout = "app/dirty.py:1: error: Untyped definition  [no-untyped-def]\n"
            return subprocess.CompletedProcess(
                args=["mypy"], returncode=1, stdout=stdout, stderr=""
            )

        monkeypatch.setattr(mypy_gate, "run_mypy", fake_run_mypy)
        code = mypy_gate.main(["--base", base, "--root", repo, "--scope-file", scope_rel])
        assert code == mypy_gate.EXIT_VIOLATION

    def test_gate_fails_on_zombie_entry(self, git_repo, monkeypatch):
        repo, base = git_repo
        app_dir = os.path.join(repo, "services", "api", "app")
        os.makedirs(app_dir)
        with open(os.path.join(app_dir, "alive.py"), "w", encoding="utf-8") as handle:
            handle.write("x: int = 1\n")
        scope_rel = "spec/enforcement/mypy-scope.txt"
        scope_path = _scope_path(repo)
        scope_path.write_text(
            "# baseline\nservices/api/app/alive.py\nservices/api/app/deleted.py\n",
            encoding="utf-8",
        )
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-q", "-m", "scope with zombie")

        def fake_run_mypy(_python, _root, _run_root, files, _config):
            return subprocess.CompletedProcess(args=["mypy"], returncode=0, stdout="", stderr="")

        monkeypatch.setattr(mypy_gate, "run_mypy", fake_run_mypy)
        code = mypy_gate.main(["--base", base, "--root", repo, "--scope-file", scope_rel])
        assert code == mypy_gate.EXIT_VIOLATION

    def test_missing_scope_file_is_env_error(self, git_repo):
        repo, base = git_repo
        code = mypy_gate.main(["--base", base, "--root", repo, "--scope-file", "nope.txt"])
        assert code == mypy_gate.EXIT_ERROR
