"""Unit tests for scripts/gates/select_unit_tests.py (B4, SPEC-TEST-01).

The selection core is a pure function over file listings; these tests drive
it with synthetic listings and a real tmp_path directory tree.
"""

from __future__ import annotations

import sys
from pathlib import Path

GATES_DIR = Path(__file__).resolve().parents[1]
if str(GATES_DIR) not in sys.path:
    sys.path.insert(0, str(GATES_DIR))

import select_unit_tests  # noqa: E402


def _select(files: list[str]) -> select_unit_tests.Selection:
    return select_unit_tests.select_unit_tests(files)


def test_select_is_test_file_name_patterns() -> None:
    assert select_unit_tests.is_test_file_name("test_foo.py")
    assert select_unit_tests.is_test_file_name("foo_test.py")
    assert not select_unit_tests.is_test_file_name("foo.py")
    assert not select_unit_tests.is_test_file_name("test_foo.md")
    assert not select_unit_tests.is_test_file_name("conftest.py")


def test_select_verification_suites_including_suite_root_files() -> None:
    selection = _select(
        [
            "verification/suites/alpha/tests/test_alpha.py",
            "verification/suites/beta/test_beta.py",  # Suite-root placement
            "verification/suites/gamma/suite.yaml",
        ]
    )
    assert selection.selected["verification-suites"] == [
        "verification/suites/alpha/tests/test_alpha.py",
        "verification/suites/beta/test_beta.py",
    ]
    # one run for the whole verification domain
    assert selection.runs[0] == selection.selected["verification-suites"]


def test_select_skills_component_patterns() -> None:
    selection = _select(
        [
            # component test directory
            "skills/materials/demo-skill/tests/test_demo.py",
            # skill-root test file (SKILL.md sibling)
            "skills/materials/seed-skill/SKILL.md",
            "skills/materials/seed-skill/test_seed.py",
            # stray under a skill scripts/ dir: no tests/ ancestor, no SKILL.md parent
            "skills/materials/other-skill/scripts/test_local.py",
            # remote-MCP smoke dir: not a tests/ dir, not a skill root
            "skills/materials/test_MCPs/test_alignn.py",
            # plain production module
            "skills/materials/demo-skill/adapter/__init__.py",
        ]
    )
    assert selection.selected["skills-component"] == [
        "skills/materials/demo-skill/tests/test_demo.py",
        "skills/materials/seed-skill/test_seed.py",
    ]
    assert selection.unselected == [
        "skills/materials/other-skill/scripts/test_local.py",
        "skills/materials/test_MCPs/test_alignn.py",
    ]


def test_select_services_api_unit_excludes_integration_directory() -> None:
    selection = _select(
        [
            "services/api/tests/test_hermetic.py",
            "services/api/tests/integration/test_transactions.py",
            "services/api/tests/support/research_dispatch_transactions.py",
            "services/api/tests/__init__.py",
        ]
    )
    assert selection.selected["services-api-unit"] == [
        "services/api/tests/test_hermetic.py"
    ]
    assert selection.excluded == [
        (
            "services/api/tests/integration/test_transactions.py",
            select_unit_tests.DOMAIN_EXCLUDED_PREFIXES[0][1],
        )
    ]


def test_select_excludes_dependency_directories_everywhere() -> None:
    selection = _select(
        [
            "skills/materials/demo/tests/execution_outputs/test_runtime.py",
            "skills/materials/demo/tests/node_modules/test_vendored.py",
            "verification/suites/alpha/tests/.venv/test_cached.py",
        ]
    )
    assert selection.selected_flat() == []
    assert selection.excluded == []
    assert selection.unselected == []


def test_select_marks_out_of_domain_test_files_unselected() -> None:
    selection = _select(
        [
            "services/api/app/execution/test_worker_pool.py",
            "evals/opentrajectory/harness/tests/test_judge.py",
            "scripts/skill_registry/tests/test_stage_model_a1.py",
        ]
    )
    assert selection.selected_flat() == []
    assert selection.unselected == [
        "evals/opentrajectory/harness/tests/test_judge.py",
        "scripts/skill_registry/tests/test_stage_model_a1.py",
        "services/api/app/execution/test_worker_pool.py",
    ]


def test_select_verification_suites_respects_domain_exclusion_prefixes() -> None:
    """Harness-gated suites under verification/suites follow the boundary table.

    Sync batch: test 2596c52b moved the fail-closed postgres artifact-review
    suite into verification/suites; without this check the selector would run
    it in the unit-test job where it fails by contract (needs the isolated
    tools/testkit/postgres_harness.py provisioned postgres).
    """
    selection = _select(
        [
            "verification/suites/postgres_testkit/tests/test_postgres_harness.py",
            "verification/suites/artifact_review_postgres/tests/"
            "test_artifact_review_identity_and_durability.py",
        ]
    )
    assert selection.selected["verification-suites"] == [
        "verification/suites/postgres_testkit/tests/test_postgres_harness.py"
    ]
    assert selection.excluded == [
        (
            "verification/suites/artifact_review_postgres/tests/"
            "test_artifact_review_identity_and_durability.py",
            select_unit_tests.DOMAIN_EXCLUDED_PREFIXES[1][1],
        )
    ]


def test_select_groups_skills_by_test_root() -> None:
    selection = _select(
        [
            "skills/cs/a-skill/tests/test_one.py",
            "skills/cs/a-skill/tests/test_two.py",
            "skills/cs/b-skill/tests/test_three.py",  # different adapter root
            "skills/ot/tests/p1/test_four.py",  # domain-level tests dir
            "skills/ot/tests/p2/test_five.py",
        ]
    )
    skills_runs = [run for run in selection.runs if run and run[0].startswith("skills/")]
    assert skills_runs == [
        ["skills/cs/a-skill/tests/test_one.py", "skills/cs/a-skill/tests/test_two.py"],
        ["skills/cs/b-skill/tests/test_three.py"],
        ["skills/ot/tests/p1/test_four.py", "skills/ot/tests/p2/test_five.py"],
    ]


def test_select_deselects_nodes_with_rootdir_relative_ids() -> None:
    files = [
        "skills/materials/demo/tests/test_demo.py",
        "services/api/tests/test_hermetic.py",
        "verification/suites/alpha/tests/test_alpha.py",
    ]
    original = select_unit_tests.DESELECTED_NODES
    try:
        select_unit_tests.DESELECTED_NODES = (
            (
                "skills/materials/demo/tests/test_demo.py::test_flaky",
                "test reason",
            ),
            (
                "services/api/tests/test_hermetic.py::test_missing_dep",
                "test reason",
            ),
            (
                "skills/materials/gone/tests/test_gone.py::test_node",  # file not selected
                "test reason",
            ),
        )
        selection = _select(files)
    finally:
        select_unit_tests.DESELECTED_NODES = original

    assert selection.deselected_nodes == [
        "skills/materials/demo/tests/test_demo.py::test_flaky",
        "services/api/tests/test_hermetic.py::test_missing_dep",
    ]
    # run_deselects aligns 1:1 with runs; rootdir prefixes are stripped
    # (skills/ and services/api/), verification stays repo-relative.
    assert len(selection.run_deselects) == len(selection.runs)
    skills_run = next(run for run in selection.runs if run[0].startswith("skills/"))
    api_run = next(run for run in selection.runs if run[0].startswith("services/api/"))
    verification_run = next(
        run for run in selection.runs if run[0].startswith("verification/")
    )
    assert selection.run_deselects[selection.runs.index(skills_run)] == [
        "materials/demo/tests/test_demo.py::test_flaky"
    ]
    assert selection.run_deselects[selection.runs.index(api_run)] == [
        "tests/test_hermetic.py::test_missing_dep"
    ]
    assert selection.run_deselects[selection.runs.index(verification_run)] == []


def test_select_over_tmp_path_directory_tree(tmp_path: Path) -> None:
    """Drive the filesystem walker over a synthetic repository tree."""
    repo = tmp_path / "repo"
    for relpath in (
        "verification/suites/alpha/tests/test_alpha.py",
        "verification/suites/alpha/suite.yaml",
        "skills/materials/demo/tests/test_demo.py",
        "skills/materials/demo/SKILL.md",
        "skills/materials/seed/SKILL.md",
        "skills/materials/seed/test_seed.py",
        "skills/materials/seed/tests/execution_outputs/test_runtime.py",
        "services/api/tests/test_hermetic.py",
        "services/api/tests/integration/test_transactions.py",
        "services/api/app/modules/test_embedded.py",
    ):
        target = repo / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# synthetic\n", encoding="utf-8")

    files = select_unit_tests.walk_repository_files(repo)
    selection = select_unit_tests.select_unit_tests(files)

    assert sorted(selection.selected_flat()) == [
        "services/api/tests/test_hermetic.py",
        "skills/materials/demo/tests/test_demo.py",
        "skills/materials/seed/test_seed.py",
        "verification/suites/alpha/tests/test_alpha.py",
    ]
    assert [path for path, _reason in selection.excluded] == [
        "services/api/tests/integration/test_transactions.py"
    ]
    assert selection.unselected == ["services/api/app/modules/test_embedded.py"]
