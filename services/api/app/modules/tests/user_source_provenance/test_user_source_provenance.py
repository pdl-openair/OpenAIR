import pytest

from app.modules.cs_reproduction_scientific_state_materialization import (
    _validate_source_consistency,
)


REPOSITORY = "https://example.org/actual.git"
INVENTED_REPOSITORY = "https://example.org/only-in-planner-goal.git"
INVENTED_REVISION = "commit-from-planner-goal"
RAW_REQUEST = f"官方代码仓库：{REPOSITORY}。代码 revision 尚未提供，必须保持未知。"
RAW_REVISION = "只缩小比较范围；仓库保持，代码 revision 仍未知，不得补造。"
CONFIRMED_USER_INPUT = f"{RAW_REQUEST}\n\n{RAW_REVISION}"


def test_planner_summary_cannot_be_used_as_user_supplied_code_identity():
    with pytest.raises(ValueError, match="source_mismatch"):
        _validate_source_consistency(
            {
                "reproduction_repository": INVENTED_REPOSITORY,
                "reproduction_revision": INVENTED_REVISION,
                "reproduction_unresolved_inputs": [],
            },
            confirmed_goal=CONFIRMED_USER_INPUT,
        )


def test_request_identity_cannot_be_used_as_user_supplied_git_revision():
    source_ref = "rsr_real_request_123"
    with pytest.raises(ValueError, match="revision_source_mismatch"):
        _validate_source_consistency(
            {
                "reproduction_repository": REPOSITORY,
                "reproduction_revision": source_ref,
                "reproduction_unresolved_inputs": [],
            },
            confirmed_goal=CONFIRMED_USER_INPUT,
        )


def test_valid_git_revision_with_quote_survives_multisource_rendering():
    revision = 'topic"v2'
    accepted = _validate_source_consistency(
        {
            "reproduction_repository": REPOSITORY,
            "reproduction_revision": revision,
            "reproduction_unresolved_inputs": [],
        },
        confirmed_goal=f"官方代码仓库：{REPOSITORY}；明确代码 revision：{revision}。",
    )
    assert accepted["reproduction_revision"] == revision
