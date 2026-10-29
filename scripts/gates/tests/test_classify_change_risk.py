"""classify_change_risk 单测：判定表各级别 + 删除探测 + 优先级（B3 规格 §2.2-4）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import classify_change_risk as ccr  # noqa: E402

# ---------- high ----------


def test_high_auth_paths():
    assert ccr.classify_path("services/api/app/routes/auth_login.py")[0] == "high"
    assert ccr.classify_path("apps/web/src/utils/authGuard.ts")[0] == "high"


def test_high_token_paths():
    assert ccr.classify_path("services/api/app/core/token_store.py")[0] == "high"
    assert ccr.classify_path("packages/shared/src/tokenTypes.ts")[0] == "high"


def test_high_schemas_and_registry():
    assert ccr.classify_path("schemas/task.schema.json")[0] == "high"
    assert ccr.classify_path("services/api/app/schemas/x.py")[0] == "high"
    assert ccr.classify_path("skills/registry.yaml")[0] == "high"
    assert ccr.classify_path("skills/skill-bindings.yaml")[0] == "high"


def test_high_spec_enforcement():
    assert ccr.classify_path("spec/enforcement/enforcement-matrix.md")[0] == "high"


def test_deletion_forces_high():
    final, reasons = ccr.classify(["docs/README.md"], deleted=["docs/old_guide.md"])
    assert final == "high"
    assert any("删除" in r for r in reasons)


# ---------- medium ----------


def test_medium_product_code():
    assert ccr.classify_path("services/api/app/main.py")[0] == "medium"
    assert ccr.classify_path("apps/web/src/pages/TaskBoardPage.tsx")[0] == "medium"
    assert ccr.classify_path("packages/shared/src/task.ts")[0] == "medium"


def test_medium_is_conservative_default():
    assert ccr.classify_path("unknown_root_dir/binary.bin")[0] == "medium"


# ---------- low ----------


def test_low_docs_spec():
    assert ccr.classify_path("docs/README.md")[0] == "low"
    assert ccr.classify_path("spec/workflows/pr-workflow.md")[0] == "low"


def test_low_scripts_gates():
    assert ccr.classify_path("scripts/gates/hook_guard.py")[0] == "low"


def test_low_tests_and_fixtures():
    assert ccr.classify_path("services/api/tests/test_api.py")[0] == "low"
    assert ccr.classify_path("scripts/gates/tests/test_hook_guard.py")[0] == "low"
    assert ccr.classify_path("apps/web/src/foo.test.tsx")[0] == "low"
    assert ccr.classify_path("fixtures/runs/sample.json")[0] == "low"
    assert ccr.classify_path("verification/suites/guard.py")[0] == "low"


def test_low_root_config():
    assert ccr.classify_path("ruff.toml")[0] == "low"
    assert ccr.classify_path(".pre-commit-config.yaml")[0] == "low"


# ---------- 优先级与输出 ----------


def test_priority_high_over_low():
    final, reasons = ccr.classify(["docs/README.md", "skills/registry.yaml"])
    assert final == "high"


def test_priority_medium_over_low():
    final, _ = ccr.classify(["docs/README.md", "services/api/app/main.py"])
    assert final == "medium"


def test_cli_files_mode(capsys):
    assert ccr.main(["--files", "docs/README.md", "services/api/app/main.py"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("risk: medium")
    assert "services/api/app/main.py" in out


def test_cli_from_diff_mode(monkeypatch, capsys):
    monkeypatch.setattr(
        ccr, "_git_diff_name_status", lambda base: "M\tdocs/README.md\nD\told_module.py\n"
    )
    assert ccr.main(["--from-diff", "origin/test"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("risk: high")
    assert "删除" in out


def test_cli_from_diff_no_deletion(monkeypatch, capsys):
    monkeypatch.setattr(
        ccr, "_git_diff_name_status", lambda base: "A\tservices/api/app/x.py\n"
    )
    assert ccr.main(["--from-diff", "origin/test"]) == 0
    assert capsys.readouterr().out.startswith("risk: medium")


def test_parse_name_status_rename_takes_new_path():
    paths, deleted = ccr.parse_name_status("R100\told.py\tnew.py\nD\tgone.py\nM\tmod.py\n")
    assert paths == ["new.py", "mod.py"]
    assert deleted == ["gone.py"]


def test_pattern_tables_are_module_constants():
    """规则表=模块级常量（pytest 可断言）。"""
    assert len(ccr.HIGH_PATTERNS) == 6
    assert len(ccr.MEDIUM_PATTERNS) == 3
    assert len(ccr.LOW_PATTERNS) == 8
