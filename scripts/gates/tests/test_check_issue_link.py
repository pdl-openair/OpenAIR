"""Unit tests for check_issue_link (M2): fix-class PR issue-link policy.

The policy was split out of the pr-evidence job's inline python block; these
tests pin its skip conditions and blocking verdicts. ``gh`` is stubbed via
monkeypatching the module-level helper's subprocess call.
"""

import types

import check_issue_link as cil
import pytest


@pytest.fixture
def gh_env(monkeypatch):
    """PR env with a stubbed gh api; returns a recorder to script replies."""
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/openair")
    monkeypatch.setenv("PR_NUMBER", "42")
    monkeypatch.setenv("PR_TITLE", "fix(api): repair dispatch lease")
    monkeypatch.setenv("PR_BODY", "Closes #7")
    replies: dict[str, str] = {
        "repos/acme/openair/pulls/42": "",
        "repos/acme/openair/pulls/42/files": "services/api/app/x.py\n",
    }
    calls: list[tuple[str, ...]] = []

    def fake_run(argv, *args, **kwargs):
        calls.append(tuple(argv[2:]))
        # Replies key on the repos/ endpoint token; --jq/--paginate are
        # projection/pagination modifiers that do not change identity.
        endpoint = next((part for part in argv[2:] if part.startswith("repos/")), "")
        text = replies.get(endpoint, "")
        return types.SimpleNamespace(returncode=0, stdout=text, stderr="")

    monkeypatch.setattr(cil.subprocess, "run", fake_run)
    return types.SimpleNamespace(replies=replies, calls=calls)


def test_escape_label_skips(gh_env, capsys) -> None:
    gh_env.replies["repos/acme/openair/pulls/42"] = "no-issue-required\n"
    assert cil.main(["--from-env"]) == 0
    assert "escape label" in capsys.readouterr().out


def test_non_fix_title_skips(gh_env, capsys, monkeypatch) -> None:
    monkeypatch.setenv("PR_TITLE", "feat(api): new thing")
    assert cil.main(["--from-env"]) == 0
    assert "not fix-class" in capsys.readouterr().out


def test_test_only_changes_skip(gh_env, capsys) -> None:
    gh_env.replies["repos/acme/openair/pulls/42/files"] = (
        "services/api/tests/test_x.py\napps/web/src/lib/y.test.ts\n"
    )
    assert cil.main(["--from-env"]) == 0
    assert "no product code" in capsys.readouterr().out


def test_missing_issue_reference_blocks(gh_env, monkeypatch) -> None:
    monkeypatch.setenv("PR_BODY", "no references at all")
    with pytest.raises(SystemExit) as exc:
        cil.main(["--from-env"])
    assert "must reference an issue" in str(exc.value)


def test_verified_issue_passes(gh_env, capsys) -> None:
    gh_env.replies["repos/acme/openair/issues/7"] = "status/verified\n"
    assert cil.main(["--from-env"]) == 0
    assert "issue #7 carries status/verified" in capsys.readouterr().out


def test_unverified_issue_blocks(gh_env) -> None:
    gh_env.replies["repos/acme/openair/issues/7"] = "kind/bug\n"
    with pytest.raises(SystemExit) as exc:
        cil.main(["--from-env"])
    assert "status/verified" in str(exc.value) and "BLOCKED" in str(exc.value)


def test_product_file_classification() -> None:
    assert not cil.is_test_file("services/api/app/x.py")
    assert cil.is_test_file("services/api/tests/test_x.py")
    assert cil.is_test_file("apps/web/src/lib/y.test.ts")
    assert cil.is_test_file("apps/web/src/lib/y.spec.tsx")
    assert cil.is_test_file("pkg/z_test.py")
    assert cil.is_test_file("apps/web/src/anything/tests/helper.py")
