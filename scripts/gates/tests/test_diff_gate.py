"""Unit tests for diff_gate: line-level filtering and coverage evaluation."""

import os

import diff_gate
from conftest import run_git

SAMPLE_DIFF = """\
diff --git a/mod.py b/mod.py
index 1111111..2222222 100644
--- a/mod.py
+++ b/mod.py
@@ -2,2 +2,3 @@
 context line
-deleted line
+added line one
+added line two
diff --git a/brand_new.py b/brand_new.py
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/brand_new.py
@@ -0,0 +1,2 @@
+first
+second
"""


def _violation(file, line, code="F401", message="unused import"):
    return diff_gate.Violation(file=file, line=line, code=code, message=message)


def test_default_root_points_at_repo_root():
    root = diff_gate.default_root()
    assert os.path.isfile(os.path.join(root, "ruff.toml"))
    assert os.path.isfile(os.path.join(root, "scripts", "gates", "diff_gate.py"))


class TestParseDiff:
    def test_added_lines_mapped_per_file(self):
        parsed = diff_gate.parse_diff(SAMPLE_DIFF)
        assert parsed.added["mod.py"] == {3, 4}
        assert parsed.added["brand_new.py"] == {1, 2}

    def test_deleted_lines_not_counted(self):
        parsed = diff_gate.parse_diff(SAMPLE_DIFF)
        # old line 3 ("deleted line") must not appear as an added line
        assert 3 in parsed.added["mod.py"]  # new side line 3 is "added line one"
        # deletion only shows up on the old side; new-side count is 3 lines
        assert max(parsed.added["mod.py"]) == 4
        assert min(parsed.added["mod.py"]) == 3

    def test_new_file_flagged(self):
        parsed = diff_gate.parse_diff(SAMPLE_DIFF)
        assert "brand_new.py" in parsed.new_files
        assert "mod.py" not in parsed.new_files

    def test_renamed_file_without_hunks_adds_nothing(self):
        diff_text = (
            "diff --git a/old_name.py b/new_name.py\n"
            "similarity index 100%\n"
            "rename from old_name.py\n"
            "rename to new_name.py\n"
        )
        parsed = diff_gate.parse_diff(diff_text)
        # a pure rename has no +++ header and no hunks: nothing recorded
        assert "new_name.py" not in parsed.added
        assert "new_name.py" not in parsed.new_files

    def test_quoted_path_with_spaces_unquoted(self):
        diff_text = (
            'diff --git "a/odd name.py" "b/odd name.py"\n'
            "index 1111111..2222222 100644\n"
            "--- a/odd name.py\n"
            '+++ b/odd name.py\n'
            "@@ -1 +1,1 @@\n"
            "+hello\n"
        )
        parsed = diff_gate.parse_diff(diff_text)
        assert parsed.added == {"odd name.py": {1}}


class TestFilterViolations:
    def test_violation_on_added_line_hits(self):
        parsed = diff_gate.parse_diff(SAMPLE_DIFF)
        kept = diff_gate.filter_violations([_violation("mod.py", 3)], parsed)
        assert kept == [_violation("mod.py", 3)]

    def test_violation_on_unchanged_line_filtered(self):
        parsed = diff_gate.parse_diff(SAMPLE_DIFF)
        kept = diff_gate.filter_violations(
            [_violation("mod.py", 1), _violation("mod.py", 2)], parsed
        )
        assert kept == []

    def test_violation_on_deleted_line_filtered(self):
        # the file is untouched entirely -> everything filtered
        parsed = diff_gate.parse_diff(SAMPLE_DIFF)
        kept = diff_gate.filter_violations([_violation("other.py", 1)], parsed)
        assert kept == []

    def test_file_level_violation_only_for_new_files(self):
        parsed = diff_gate.parse_diff(SAMPLE_DIFF)
        kept = diff_gate.filter_violations(
            [_violation("brand_new.py", None), _violation("mod.py", None)], parsed
        )
        assert [v.file for v in kept] == ["brand_new.py"]


class TestRealGitDiff:
    def test_read_diff_against_base(self, git_repo):
        repo, _fixture_base = git_repo
        module = "src/thing.py"
        os.makedirs(os.path.join(repo, "src"))
        path = os.path.join(repo, "src", "thing.py")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("one\ntwo\nthree\n")
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-q", "-m", "add thing")
        base = run_git(repo, "rev-parse", "HEAD").strip()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("one\ntwo changed\nthree\nfour\nfive\n")
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-q", "-m", "grow thing")
        parsed = diff_gate.parse_diff(diff_gate.read_diff(base, repo))
        assert parsed.added[module] == {2, 4, 5}

    def test_unborn_branch_falls_back_to_plain_diff(self, tmp_path):
        repo = tmp_path / "empty"
        repo.mkdir()
        run_git(str(repo), "init", "-q", "-b", "main")
        run_git(str(repo), "config", "user.email", "gates@example.invalid")
        run_git(str(repo), "config", "user.name", "Gate Tests")
        (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
        run_git(str(repo), "add", "seed.txt")
        run_git(str(repo), "commit", "-q", "-m", "seed")
        base = run_git(str(repo), "rev-parse", "HEAD").strip()
        # unborn branch: HEAD does not exist, so read_diff degrades to a
        # plain diff of base vs the (staged) working tree
        run_git(str(repo), "switch", "-q", "--orphan", "fresh")
        (repo / "seed.txt").unlink(missing_ok=True)
        (repo / "seed.txt").write_text("seed changed\n", encoding="utf-8")
        run_git(str(repo), "add", "seed.txt")
        diff_text = diff_gate.read_diff(base, str(repo))
        parsed = diff_gate.parse_diff(diff_text)
        assert parsed.added["seed.txt"] == {1}


class TestToolParsers:
    def test_parse_ruff_json(self, tmp_path):
        filename = str(tmp_path / "a.py").replace("\\", "/")
        payload = (
            '[{"code": "F401", "filename": "'
            + filename
            + '", "location": {"row": 3}, "message": "unused import"}]'
        )
        violations = diff_gate.parse_ruff_json(payload, str(tmp_path))
        assert violations == [
            diff_gate.Violation(file="a.py", line=3, code="F401", message="unused import")
        ]

    def test_parse_eslint_json(self):
        payload = (
            '[{"filePath": "src/a.ts", "messages": '
            '[{"line": 7, "ruleId": "no-console", "message": "no console"}]}]'
        )
        violations = diff_gate.parse_eslint_json(payload, ".")
        assert violations[0].file == "src/a.ts"
        assert violations[0].line == 7
        assert violations[0].code == "no-console"

    def test_parse_prettier_output(self):
        payload = (
            "Checking formatting...\n"
            "[warn] src/a.ts\n"
            "[warn] Code style issues found in 1 file.\n"
        )
        violations = diff_gate.parse_prettier_output(payload, "apps/web")
        assert violations == [
            diff_gate.Violation(
                file="apps/web/src/a.ts", line=None, code="prettier", message="file not formatted"
            )
        ]


class TestCoverage:
    def test_product_scope_matching(self):
        assert diff_gate.is_product_code("services/api/app/main.py")
        assert diff_gate.is_product_code("services/worker/worker/runner.py")
        assert diff_gate.is_product_code("packages/llm_core/llm_core/token_usage.py")
        assert not diff_gate.is_product_code("services/api/app/tests/test_main.py")
        assert not diff_gate.is_product_code("services/api/tests/test_x.py")
        assert not diff_gate.is_product_code("scripts/gates/diff_gate.py")
        assert not diff_gate.is_product_code("apps/web/src/a.ts")

    def test_coverage_ratio_and_missing_file(self):
        parsed = diff_gate.DiffLines(
            added={
                "services/api/app/a.py": {10, 11, 12},
                "services/api/app/b.py": {1, 2},
            },
            new_files={"services/api/app/b.py"},
        )
        coverage = {
            "files": {
                "app/a.py": {"executed_lines": [10], "missing_lines": [11, 12]},
            }
        }
        stats = diff_gate.compute_diff_coverage(parsed, coverage)
        assert stats.executable == 5
        assert stats.covered == 1
        assert stats.ratio == 0.2
        # b.py has no coverage entry at all: both added lines count as missed

    def test_coverage_gate_threshold(self, git_repo, tmp_path):
        repo, base = git_repo
        os_dirs = os.path.join(repo, "services", "api", "app")
        os.makedirs(os_dirs)
        with open(f"{os_dirs}/a.py", "w", encoding="utf-8") as handle:
            handle.write("x = 1\ny = 2\nz = 3\n")
        run_git(repo, "add", ".")
        run_git(repo, "commit", "-q", "-m", "product file")
        coverage_file = tmp_path / "coverage.json"
        coverage_file.write_text(
            '{"files": {"app/a.py": {"executed_lines": [1], "missing_lines": [2, 3]}}}',
            encoding="utf-8",
        )
        # 1/3 = 33% -> below 50% threshold
        code = diff_gate.run_coverage_gate(base, repo, str(coverage_file), 50.0)
        assert code == diff_gate.EXIT_VIOLATION
        # threshold 20% -> passes
        code = diff_gate.run_coverage_gate(base, repo, str(coverage_file), 20.0)
        assert code == diff_gate.EXIT_PASS
