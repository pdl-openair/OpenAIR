"""Unit tests for placement_guard: threshold decisions, exemptions, check-file."""

import datetime as _dt
import os

import placement_guard
from conftest import run_git


class TestEvaluatePlacement:
    def test_oversized_no_shrink_blocked(self):
        message = placement_guard.evaluate_placement(
            "big.py", base_lines=900, current_lines=950, threshold=800, exempted_paths=set()
        )
        assert message is not None
        assert "900 -> 950" in message

    def test_oversized_equal_size_blocked(self):
        message = placement_guard.evaluate_placement(
            "big.py", base_lines=900, current_lines=900, threshold=800, exempted_paths=set()
        )
        assert message is not None  # net change >= 0 counts as no shrink

    def test_oversized_net_shrink_allowed(self):
        message = placement_guard.evaluate_placement(
            "big.py", base_lines=900, current_lines=890, threshold=800, exempted_paths=set()
        )
        assert message is None

    def test_oversized_deleted_allowed(self):
        message = placement_guard.evaluate_placement(
            "big.py", base_lines=900, current_lines=0, threshold=800, exempted_paths=set()
        )
        assert message is None

    def test_small_at_base_not_blocked_even_if_grown(self):
        message = placement_guard.evaluate_placement(
            "small.py", base_lines=800, current_lines=2000, threshold=800, exempted_paths=set()
        )
        assert message is None  # threshold is "more than 800 at merge base"

    def test_new_file_not_blocked(self):
        message = placement_guard.evaluate_placement(
            "new.py", base_lines=0, current_lines=2000, threshold=800, exempted_paths=set()
        )
        assert message is None

    def test_exempted_path_allowed(self):
        message = placement_guard.evaluate_placement(
            "big.py", 900, 950, 800, exempted_paths={"big.py"}
        )
        assert message is None

    def test_only_guarded_extensions(self):
        for path in ("a.json", "b.md", "c.js"):
            message = placement_guard.evaluate_placement(
                path, 900, 950, 800, exempted_paths=set()
            )
            assert message is None
        for path in ("a.py", "b.ts", "c.tsx"):
            message = placement_guard.evaluate_placement(
                path, 900, 950, 800, exempted_paths=set()
            )
            assert message is not None


EMPTY_EXEMPTIONS = "exemptions: []\n"


class TestExemptionParsing:
    def test_empty_list(self):
        assert placement_guard.parse_exemptions(EMPTY_EXEMPTIONS) == []

    def test_full_entry_with_colon_in_reason(self):
        text = (
            "# header\n"
            "exemptions:\n"
            "  - path: services/api/app/big.py\n"
            "    reason: \"legacy: needs staged extraction\"\n"
            "    review-date: \"2026-10-01\"\n"
            "    added-by: someone\n"
        )
        entries = placement_guard.parse_exemptions(text)
        assert entries == [
            {
                "path": "services/api/app/big.py",
                "reason": "legacy: needs staged extraction",
                "review-date": "2026-10-01",
                "added-by": "someone",
            }
        ]

    def test_multiple_entries(self):
        text = (
            "exemptions:\n"
            "- path: a.py\n"
            "  reason: r1\n"
            "  review-date: \"2026-10-01\"\n"
            "  added-by: x\n"
            "- path: b.py\n"
            "  reason: r2\n"
            "  review-date: \"2026-10-02\"\n"
            "  added-by: y\n"
        )
        entries = placement_guard.parse_exemptions(text)
        assert [entry["path"] for entry in entries] == ["a.py", "b.py"]

    def test_malformed_missing_key(self):
        text = "exemptions:\n- path: a.py\n  reason: r\n"
        try:
            placement_guard.parse_exemptions(text)
        except ValueError as exc:
            assert "missing keys" in str(exc)
        else:
            raise AssertionError("expected ValueError for missing keys")

    def test_malformed_unknown_key(self):
        text = (
            "exemptions:\n- path: a.py\n  reason: r\n"
            "  review-date: \"2026-10-01\"\n  added-by: x\n  bogus: v\n"
        )
        try:
            placement_guard.parse_exemptions(text)
        except ValueError as exc:
            assert "unknown key" in str(exc)
        else:
            raise AssertionError("expected ValueError for unknown key")

    def test_malformed_header(self):
        for text in ("", "not-exemptions: []\n", "exemptions: []\n- path: a.py\n"):
            try:
                placement_guard.parse_exemptions(text)
            except ValueError:
                continue
            raise AssertionError(f"expected ValueError for {text!r}")

    def test_malformed_entry_line(self):
        text = "exemptions:\ngarbage line\n"
        try:
            placement_guard.parse_exemptions(text)
        except ValueError:
            return
        raise AssertionError("expected ValueError for garbage line")


def _write_lines(path, count):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(f"line {i}" for i in range(count)) + "\n")


class TestCheckFileMode:
    def test_check_file_blocked_and_allowed(self, git_repo, tmp_path):
        repo, _fixture_base = git_repo
        big = os.path.join(repo, "big.py")
        _write_lines(big, 900)
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-q", "-m", "big file")
        base = run_git(repo, "rev-parse", "HEAD").strip()
        exemptions = tmp_path / "placement-exemptions.yml"
        exemptions.write_text(EMPTY_EXEMPTIONS, encoding="utf-8")
        common = ["--base", base, "--root", repo, "--exemptions", str(exemptions)]
        code = placement_guard.main(
            [*common, "--check-file", "big.py", "--lines", "950"]
        )
        assert code == placement_guard.EXIT_VIOLATION
        code = placement_guard.main(
            [*common, "--check-file", "big.py", "--lines", "890"]
        )
        assert code == placement_guard.EXIT_PASS
        code = placement_guard.main([*common, "--check-file", "big.py"])
        assert code == placement_guard.EXIT_ERROR  # --lines missing

    def test_check_file_unknown_at_base_passes(self, git_repo, tmp_path):
        repo, base = git_repo
        exemptions = tmp_path / "placement-exemptions.yml"
        exemptions.write_text(EMPTY_EXEMPTIONS, encoding="utf-8")
        code = placement_guard.main(
            [
                "--base",
                base,
                "--root",
                repo,
                "--exemptions",
                str(exemptions),
                "--check-file",
                "brand_new.py",
                "--lines",
                "1200",
            ]
        )
        assert code == placement_guard.EXIT_PASS


class TestDiffMode:
    def _repo_with_big_file(self, git_repo, base_lines):
        repo, _fixture_base = git_repo
        big = os.path.join(repo, "big.py")
        _write_lines(big, base_lines)
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-q", "-m", "big file")
        base = run_git(repo, "rev-parse", "HEAD").strip()
        return repo, base, big

    def test_diff_blocks_oversized_growth(self, git_repo, tmp_path):
        repo, base, big = self._repo_with_big_file(git_repo, 900)
        _write_lines(big, 950)
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-q", "-m", "grow big file")
        exemptions = tmp_path / "placement-exemptions.yml"
        exemptions.write_text(EMPTY_EXEMPTIONS, encoding="utf-8")
        code = placement_guard.main(
            ["--base", base, "--root", repo, "--exemptions", str(exemptions)]
        )
        assert code == placement_guard.EXIT_VIOLATION

    def test_diff_allows_net_shrink(self, git_repo, tmp_path):
        repo, base, big = self._repo_with_big_file(git_repo, 900)
        _write_lines(big, 700)
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-q", "-m", "shrink big file")
        exemptions = tmp_path / "placement-exemptions.yml"
        exemptions.write_text(EMPTY_EXEMPTIONS, encoding="utf-8")
        code = placement_guard.main(
            ["--base", base, "--root", repo, "--exemptions", str(exemptions)]
        )
        assert code == placement_guard.EXIT_PASS

    def test_diff_exemption_defuses_block(self, git_repo, tmp_path):
        repo, base, big = self._repo_with_big_file(git_repo, 900)
        _write_lines(big, 950)
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-q", "-m", "grow big file")
        exemptions = tmp_path / "placement-exemptions.yml"
        exemptions.write_text(
            "exemptions:\n"
            "- path: big.py\n"
            "  reason: legacy extraction in progress\n"
            "  review-date: \"2027-01-01\"\n"
            "  added-by: gate tests\n",
            encoding="utf-8",
        )
        code = placement_guard.main(
            ["--base", base, "--root", repo, "--exemptions", str(exemptions)]
        )
        assert code == placement_guard.EXIT_PASS

    def test_malformed_exemptions_is_config_error(self, git_repo, tmp_path):
        repo, base = git_repo
        exemptions = tmp_path / "placement-exemptions.yml"
        exemptions.write_text("exemptions:\n- path: a.py\n", encoding="utf-8")
        code = placement_guard.main(
            ["--base", base, "--root", repo, "--exemptions", str(exemptions)]
        )
        assert code == placement_guard.EXIT_ERROR


class TestBaseRefFailure:
    """M6：merge_base/rev-parse 失败必须 exit 2（不与违规码 1 碰撞，无 traceback）。"""

    def test_check_file_bad_base_is_env_error(self, git_repo, tmp_path, capsys):
        repo, _base = git_repo
        exemptions = tmp_path / "placement-exemptions.yml"
        exemptions.write_text(EMPTY_EXEMPTIONS, encoding="utf-8")
        code = placement_guard.main(
            [
                "--base",
                "nonexistent-ref-xyz",
                "--root",
                repo,
                "--exemptions",
                str(exemptions),
                "--check-file",
                "big.py",
                "--lines",
                "900",
            ]
        )
        assert code == placement_guard.EXIT_ERROR
        assert "cannot resolve base" in capsys.readouterr().err

    def test_diff_mode_bad_base_is_env_error(self, git_repo, tmp_path):
        repo, _base = git_repo
        exemptions = tmp_path / "placement-exemptions.yml"
        exemptions.write_text(EMPTY_EXEMPTIONS, encoding="utf-8")
        code = placement_guard.main(
            ["--base", "nonexistent-ref-xyz", "--root", repo, "--exemptions", str(exemptions)]
        )
        assert code == placement_guard.EXIT_ERROR


class TestRenameBaseline:
    """④F3：R 记录取旧路径行数为基线（改名绕过封堵）。"""

    def test_rename_then_grow_blocked(self, git_repo, tmp_path):
        repo, _base = git_repo
        big = os.path.join(repo, "big.py")
        _write_lines(big, 900)
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-q", "-m", "big file")
        base = run_git(repo, "rev-parse", "HEAD").strip()
        run_git(repo, "mv", "big.py", "huge.py")
        huge = os.path.join(repo, "huge.py")
        _write_lines(huge, 950)
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-q", "-m", "rename and grow")
        exemptions = tmp_path / "placement-exemptions.yml"
        exemptions.write_text(EMPTY_EXEMPTIONS, encoding="utf-8")
        code = placement_guard.main(
            ["--base", base, "--root", repo, "--exemptions", str(exemptions)]
        )
        assert code == placement_guard.EXIT_VIOLATION

    def test_rename_then_shrink_allowed(self, git_repo, tmp_path):
        repo, _base = git_repo
        big = os.path.join(repo, "big.py")
        _write_lines(big, 900)
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-q", "-m", "big file")
        base = run_git(repo, "rev-parse", "HEAD").strip()
        run_git(repo, "mv", "big.py", "huge.py")
        huge = os.path.join(repo, "huge.py")
        _write_lines(huge, 700)
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-q", "-m", "rename and shrink")
        exemptions = tmp_path / "placement-exemptions.yml"
        exemptions.write_text(EMPTY_EXEMPTIONS, encoding="utf-8")
        code = placement_guard.main(
            ["--base", base, "--root", repo, "--exemptions", str(exemptions)]
        )
        assert code == placement_guard.EXIT_PASS

    def test_changed_files_reports_rename_old_path(self, git_repo):
        repo, _base = git_repo
        big = os.path.join(repo, "big.py")
        _write_lines(big, 10)
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-q", "-m", "base file")
        base = run_git(repo, "rev-parse", "HEAD").strip()
        run_git(repo, "mv", "big.py", "huge.py")
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-q", "-m", "rename")
        records = placement_guard.changed_files(base, repo)
        assert ("big.py", "huge.py") in [
            (old, new) for _status, old, new in records
        ]


class TestExemptionExpiry:
    """④F7：review-date 早于今日 → violation（fail-closed）。"""

    def test_stale_exemptions_detected(self):
        entries = [
            {"path": "a.py", "reason": "r", "review-date": "2026-01-01", "added-by": "x"},
            {"path": "b.py", "reason": "r", "review-date": "2099-01-01", "added-by": "x"},
        ]
        stale = placement_guard.stale_exemptions(entries, today=_dt.date(2026, 9, 26))
        assert len(stale) == 1
        assert "a.py" in stale[0]
        assert "续期" in stale[0]

    def test_unparsable_review_date_is_stale(self):
        entries = [{"path": "a.py", "reason": "r", "review-date": "soon", "added-by": "x"}]
        stale = placement_guard.stale_exemptions(entries, today=_dt.date(2026, 9, 26))
        assert len(stale) == 1

    def test_future_review_date_not_stale(self):
        entries = [{"path": "b.py", "reason": "r", "review-date": "2099-01-01", "added-by": "x"}]
        assert placement_guard.stale_exemptions(entries, today=_dt.date(2026, 9, 26)) == []

    def test_expired_exemption_blocks_gate(self, git_repo, tmp_path, capsys):
        repo, base = git_repo
        big = os.path.join(repo, "big.py")
        _write_lines(big, 10)
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-q", "-m", "small file")
        exemptions = tmp_path / "placement-exemptions.yml"
        exemptions.write_text(
            "exemptions:\n"
            "- path: big.py\n"
            "  reason: legacy extraction in progress\n"
            "  review-date: \"2026-01-01\"\n"
            "  added-by: gate tests\n",
            encoding="utf-8",
        )
        code = placement_guard.main(
            ["--base", base, "--root", repo, "--exemptions", str(exemptions)]
        )
        assert code == placement_guard.EXIT_VIOLATION
        assert "review-date" in capsys.readouterr().err
