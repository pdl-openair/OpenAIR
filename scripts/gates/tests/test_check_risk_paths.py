"""Unit tests for check_risk_paths (M5): auth/token paths need 风险分级 >= medium."""

import check_risk_paths as risk


class TestIsHighRisk:
    def test_auth_prefix_hits(self):
        for path in (
            "services/api/app/auth/router.py",
            "services/api/app/auth/deps/jwt.py",
            "./services/api/app/auth/x.py",
            "services\\api\\app\\auth\\y.py",
        ):
            assert risk.is_high_risk(path), path

    def test_auth_prefix_overmatches_documented(self):
        # 前缀规则字面匹配：authorize 也命中（保守过匹配是有意行为，规格字面）
        assert risk.is_high_risk("services/api/app/authorize.py")

    def test_keyword_py_hits(self):
        for path in (
            "services/api/app/token_store.py",
            "packages/shared/credentials.py",
            "apps/web/src/jwtHelper.py",
            "scripts/rotate_secret.py",
        ):
            assert risk.is_high_risk(path), path

    def test_non_py_or_clean_paths_pass(self):
        for path in (
            "services/api/app/main.py",
            "docs/token.md",
            "apps/web/src/secrets.tsx",
            "packages/shared/types.py",
            ".github/workflows/required-checks.yml",
        ):
            assert not risk.is_high_risk(path), path


class TestParseRiskLevel:
    def test_levels(self):
        assert risk.parse_risk_level("风险分级: low") == 0
        assert risk.parse_risk_level("风险分级: medium") == 1
        assert risk.parse_risk_level("风险分级: High") == 2

    def test_chinese_levels_and_fullwidth_colon(self):
        assert risk.parse_risk_level("风险分级：低") == 0
        assert risk.parse_risk_level("风险分级：中") == 1
        assert risk.parse_risk_level("风险分级：高") == 2

    def test_missing_or_unknown_is_none(self):
        assert risk.parse_risk_level("") is None
        assert risk.parse_risk_level("risk: low") is None
        assert risk.parse_risk_level("风险分级: catastrophic") is None


class TestSplitFiles:
    def test_comma_and_newline_and_trailing(self):
        assert risk.split_files("a.py,b.py\nc.py,") == ["a.py", "b.py", "c.py"]

    def test_empty(self):
        assert risk.split_files("") == []
        assert risk.split_files(",,\n") == []


AUTH_FILE = "services/api/app/auth/router.py"


class TestCliFilesBody:
    def test_auth_path_low_report_blocked(self, capsys):
        code = risk.main(["--files", AUTH_FILE, "--body", "风险分级: low"])
        assert code == risk.EXIT_VIOLATION
        assert AUTH_FILE in capsys.readouterr().err

    def test_auth_path_missing_level_blocked(self, capsys):
        code = risk.main(["--files", AUTH_FILE, "--body", "ordinary PR description"])
        assert code == risk.EXIT_VIOLATION
        assert "风险分级" in capsys.readouterr().err

    def test_auth_path_unknown_level_blocked(self, capsys):
        code = risk.main(["--files", AUTH_FILE, "--body", "风险分级: critical"])
        assert code == risk.EXIT_VIOLATION

    def test_auth_path_medium_passes_with_banner(self, capsys):
        code = risk.main(["--files", AUTH_FILE, "--body", "风险分级: medium"])
        assert code == risk.EXIT_PASS
        captured = capsys.readouterr()
        assert "必人审" in captured.out and AUTH_FILE in captured.out

    def test_high_level_also_passes(self):
        assert risk.main(["--files", AUTH_FILE, "--body", "风险分级：高"]) == risk.EXIT_PASS

    def test_no_risk_path_passes_without_banner(self, capsys):
        code = risk.main(["--files", "docs/readme.md,services/api/app/main.py", "--body", ""])
        assert code == risk.EXIT_PASS
        assert "必人审" not in capsys.readouterr().out

    def test_mixed_list_single_hit_blocks(self, capsys):
        code = risk.main(
            ["--files", "docs/a.md,services/api/app/jwt_helper.py", "--body", "风险分级: low"]
        )
        assert code == risk.EXIT_VIOLATION
        assert "jwt_helper.py" in capsys.readouterr().err


class TestCliFromEnv:
    def test_from_env_missing_pr_files_is_env_error(self, monkeypatch, capsys):
        monkeypatch.delenv("PR_FILES", raising=False)
        code = risk.main(["--from-env"])
        assert code == risk.EXIT_ERROR
        assert "PR_FILES" in capsys.readouterr().err

    def test_from_env_pipeline_form(self, monkeypatch, capsys):
        monkeypatch.setenv("PR_FILES", "docs/x.md,services/api/app/auth/deps.py,")
        monkeypatch.setenv("PR_BODY", "风险分级: high")
        assert risk.main(["--from-env"]) == risk.EXIT_PASS
        assert "deps.py" in capsys.readouterr().out

    def test_from_env_low_report_blocked(self, monkeypatch):
        monkeypatch.setenv("PR_FILES", "services/api/app/auth/deps.py")
        monkeypatch.setenv("PR_BODY", "风险分级: low")
        assert risk.main(["--from-env"]) == risk.EXIT_VIOLATION

    def test_from_env_body_unset_blocks_not_crashes(self, monkeypatch):
        monkeypatch.setenv("PR_FILES", "services/api/app/auth/deps.py")
        monkeypatch.delenv("PR_BODY", raising=False)
        assert risk.main(["--from-env"]) == risk.EXIT_VIOLATION


class TestCliUsage:
    def test_no_input_form_is_usage_error(self, capsys):
        assert risk.main([]) == risk.EXIT_ERROR
        assert "nothing to check" in capsys.readouterr().err
