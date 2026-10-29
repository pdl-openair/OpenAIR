#!/usr/bin/env python3
"""Select the CI unit-test set (SPEC-TEST-01) by directory.

Transition-state test selection for the AI4SE harness campaign (batch B4).
CI runs ``python -m pytest`` over the files this selector reports, **one
pytest process per run group** (see "Run grouping" below). When the in-flight
test-governance (tg) branch lands with its ``execution-plans/`` mechanism,
whether to consume that instead is a separate decision (design doc
§13-2/§13-5); this directory-based selection stays compatible either way
because it selects whole files, not node ids.

Selection rules (evidence-backed against the repository layout at c1f15592):

1. ``verification/suites/**`` — every tracked test-named Python file.
   All 4 Suites contribute their tests, including ``test_asset_architecture``
   (guard.py self-test) and ``durable_child_policy_handoff``, whose test
   files sit at the Suite root (no ``tests/`` subdirectory). 4 files total.

2. Skills component tests — a tracked test file under ``skills/`` is selected
   when either
   a. some directory strictly below ``skills/`` on its path is named ``tests``
      (component-nearby test directory), or
   b. the file sits directly in a Skill component root (its parent directory
      contains a tracked ``SKILL.md``) — the pattern of the required-checks
      P0 seed ``skills/materials/crystal-design-structure-crossover-py/test_input_contract.py``.
   Live-network smoke tests (``test_MCPs/`` directories hitting hardcoded
   remote MCP endpoints, and ``materials-new-crystal-generation/scripts/``
   MCP HTTP suites) satisfy neither clause and land in the informational
   ``unselected`` list. They are integration smokes against external demo
   servers, not hermetic unit tests. Three further files that DO match the
   component pattern are excluded file-by-file (``SKILLS_EXCLUDED_FILES``):
   each was empirically uncollectable under the canonical invocation during
   the B4 investigation (details in the table and ledger b4.md).

3. ``services/api/tests/**`` minus the integration boundary. The integration
   boundary is the whole ``services/api/tests/integration/`` directory:
   27 test files there of which 14 fail (pytest.fail) and 12 skip without
   postgres/MinIO DSNs (``OPENAIR_T04_POSTGRES_DSN`` & friends — B4 boundary
   investigation, ledger b4.md); the one self-contained file
   (``test_task_plan_postgres_persistence.py``, fake connection objects)
   stays out with the directory for boundary uniformity. All 255 root-level
   test files were verified hermetic (no real postgres/HTTP/SDK/docker/minio).

Excluded everywhere: ``.venv``, ``node_modules``, ``dist``, ``__pycache__``,
and ``execution_outputs`` (runtime outputs under skills test directories,
already gitignored).

Known tracked test-like files NOT selected (informational ``unselected`` list;
rationale in ledger b4.md): tests embedded in the production package
``services/api/app/**`` (historical debt, disposition owned by the tg
migration), ``evals/**`` (open-quality eval domain, not unit-test CI scope),
and ``scripts/skill_registry/tests/**`` (scripts/ placement — the prospective
test-asset guard tolerates such historical files but forbids new ones).

Invocation contract for CI (batch B5): from the repository root, for each
group in ``runs``: ``PYTHONPATH=services/api python -m pytest <group files>
--deselect <node> ...`` (only the deselected nodes whose file is in the
group). The ``--deselect`` arguments come from ``deselected_nodes`` in the
``--json`` output (rationale per node: ``deselected_nodes_rationale``).

Run grouping (why the selection is not one pytest invocation):

Cross-skill collection in a single process is impossible without moving or
renaming test files (both forbidden): several skills each ship a top-level
``adapter/`` package imported as ``from adapter import ...`` after a
``sys.path.insert`` of the skill root — the first skill's ``adapter`` wins
``sys.modules`` and every other skill's import fails or silently binds the
wrong module; on top of that, duplicate test-module basenames exist
(``test_input_contract_alignment.py`` ×10, ``test_contract.py`` ×12, ...).
The selector therefore partitions the selection into co-runnable groups:

* all ``verification/suites`` files → one run;
* all ``services/api/tests`` files → one run (invoked with
  ``PYTHONPATH=services/api``; the run's rootdir resolves to
  ``services/api/pytest.ini`` so ``asyncio_mode=auto`` applies);
* each skills test root (outermost ``tests/`` directory below ``skills/``,
  or the Skill component root for root-level test files) → its own run
  (rootdir resolves to ``skills/pytest.ini``: importlib import mode,
  strict markers).

Usage:
    python scripts/gates/select_unit_tests.py --print     # flat list (human)
    python scripts/gates/select_unit_tests.py --print0    # flat list, NUL-separated
    python scripts/gates/select_unit_tests.py --json      # runs + exclusions + rationale

Pure standard library. The selection core is a pure function over a provided
file listing so unit tests can drive it with synthetic tmp_path trees.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT_DEFAULT = Path(__file__).resolve().parents[2]

TEST_FILE_PREFIX = "test_"
TEST_FILE_SUFFIX_ALT = "_test.py"
TEST_FILE_EXTENSION = ".py"

#: Directory names never selected (runtime/dependency trees, build output,
#: and skills test runtime outputs — ``skills/**/tests/execution_outputs/``
#: is already gitignored; the filter also covers filesystem-walk mode).
EXCLUDED_DIR_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "dist",
        "__pycache__",
        "execution_outputs",
    }
)

#: Prefix-based exclusions with boundary evidence, applied inside the
#: selection domains. Each entry: (path prefix, reason).
DOMAIN_EXCLUDED_PREFIXES: tuple[tuple[str, str], ...] = (
    (
        "services/api/tests/integration/",
        "postgres/MinIO/Docker-backed integration tests (need OPENAIR_T04_POSTGRES_DSN "
        "or live services); integration is outside the SPEC-TEST-01 unit scope",
    ),
    (
        "verification/suites/artifact_review_postgres/",
        "harness-gated postgres suite (test 2596c52b moved it here from the excluded "
        "integration boundary): fail-closed by contract without the isolated "
        "tools/testkit/postgres_harness.py provisioned postgres; runs in the test "
        "job's dedicated harness step instead - same boundary evidence as the "
        "integration prefix above (sync batch, ledger sync.md deviation register)",
    ),
)

#: File-level exclusions inside ``services/api/tests`` root, populated only
#: with files empirically verified to require external services. The B4
#: boundary investigation (ledger b4.md) found none: all 255 root-level test
#: files are hermetic, so this table is intentionally empty.
API_EXCLUDED_FILES: dict[str, str] = {}

#: File-level exclusions inside the skills domain. Each entry was empirically
#: shown to be uncollectable under the canonical invocation (one pytest
#: process per run group, repo-root cwd) during the B4 investigation, or (D-19
#: sync batch) absorbed from the test branch in that never-collectable state.
SKILLS_EXCLUDED_FILES: dict[str, str] = {
    "skills/cs_research/reproduction-execution-input-closure/tests/test_applicability.py": (
        "src-layout import (reproduction_execution_input_closure.*) with no sys.path bootstrap "
        "and no conftest — uncollectable under both import modes (historical dead test)"
    ),
    "skills/cs_research/environment-setup/tests/test_applicability.py": (
        "src-layout import (environment_setup.*) with no sys.path bootstrap and no conftest — "
        "drifted in from origin/test (D-19 sync batch) and never collectable under the "
        "canonical invocation on any platform; sibling test_input_contract_alignment.py "
        "stays selected. Collection ERROR has no :: node, so the D-15 known-failures "
        "channel cannot express it; this exclusion table is the designed channel"
    ),
    "skills/cs_research/resource-identity-acquisition/tests/test_applicability.py": (
        "src-layout import (resource_identity_acquisition.*) with no sys.path bootstrap and "
        "no conftest — drifted in from origin/test (D-19 sync batch) and never collectable "
        "under the canonical invocation on any platform; sibling "
        "test_input_contract_alignment.py and test_resource_identity_adapter.py stay "
        "selected. Same no-node-shape reasoning as the environment-setup entry"
    ),
    "skills/opentrajectory/source-access-readiness-resolver-skill/test_resolver.py": (
        "relies on prepend-mode basedir insertion (from resolver import ...) — incompatible "
        "with the skills-domain importlib contract (skills/pytest.ini)"
    ),
    "skills/pvd/pvd-bayesian-optimization-candidate-generation/tests/test_contract_and_domain.py": (
        "numpy absent from local gate venv (imports pvd_bo.dataset->numpy); CI parity expected "
        "via mp-api->pymatgen — flagged for B5 CI verification"
    ),
    "skills/pvd/pvd-bayesian-optimization-candidate-generation/tests/test_dataset_semantics.py": (
        "numpy absent from local gate venv (imports pvd_bo.dataset->numpy); CI parity expected "
        "via mp-api->pymatgen — flagged for B5 CI verification"
    ),
    "skills/pvd/pvd-bayesian-optimization-candidate-generation/tests/"
    "test_optimizer_regression.py": (
        "numpy absent from local gate venv (imports pvd_bo.dataset->numpy); CI parity expected "
        "via mp-api->pymatgen — flagged for B5 CI verification"
    ),
    "skills/pvd/pvd-bayesian-optimization-candidate-generation/tests/"
    "test_side_effects_and_output.py": (
        "numpy absent from local gate venv (imports pvd_bo.dataset->numpy); CI parity expected "
        "via mp-api->pymatgen — flagged for B5 CI verification"
    ),
}


#: Node-level deselections inside otherwise-selected files (pytest
#: ``--deselect`` arguments, ``path::node``). File-level selection stays intact;
#: only the listed test nodes are dropped. Every entry was empirically red (or
#: uncollectable) under the canonical invocation during the B4 investigation
#: and is classified + recorded in ledger b4.md:
#:   * env-parity (pymatgen/POSIX): expected green in the CI env (uv sync pulls
#:     pymatgen via mp-api; CI runs ubuntu) — B5 should re-admit on CI evidence;
#:   * pre-existing failures at the base commit (fail in CI too);
#:   * structurally non-hermetic (depend on untracked runtime artifacts).
DESELECTED_NODES: tuple[tuple[str, str], ...] = (
    (
        "skills/cs_research/exploratory-run/tests/test_exploratory_run_adapter.py::test_timeout_is_explicit",
        "platform-divergent: Windows taskkill tree-termination returns termination_failed locally; "
        "POSIX path (CI) expected green — B5 re-admit on CI evidence",
    ),
    (
        "skills/cs_research/exploratory-run/tests/test_exploratory_run_adapter.py::test_timeout_terminates_child_process_tree",
        "platform-divergent: Windows taskkill tree-termination returns termination_failed locally; "
        "POSIX path (CI) expected green — B5 re-admit on CI evidence",
    ),
    (
        "skills/cs_research/reproduction-experiment-design/tests/test_contract.py::"
        "test_bindings_preserve_official_source_and_reference_lineage",
        "pre-existing failure at base commit (skill-bindings.yaml content drift "
        "vs test expectations); fails in CI too — fix the test or the bindings, "
        "then re-admit",
    ),
    (
        "skills/materials/crystal-design-formula-crossover-agent/tests/test_formula_driver.py::test_cmd_init_calls_query_mp_with_correct_signature",
        "pre-existing failure at base commit (test stub lacks seed_formulas attribute that "
        "production code reads: AttributeError 'A' object has no attribute 'seed_formulas'); "
        "fails in CI too",
    ),
    (
        "skills/materials/crystal-design-formula-crossover-agent/tests/test_helpers.py::test_hhi_fe2o3_returns_tuple_pair",
        "env-parity: needs pymatgen (absent from local gate venv; CI has it via "
        "mp-api) — B5 re-admit on CI evidence",
    ),
    (
        "skills/materials/crystal-design-formula-crossover-agent/tests/test_helpers.py::test_hhi_rare_earth_higher_than_iron",
        "env-parity: needs pymatgen (absent from local gate venv; CI has it via "
        "mp-api) — B5 re-admit on CI evidence",
    ),
    (
        "skills/materials/crystal-design-formula-crossover-agent/tests/test_helpers.py::test_mag_density_basic_volume_division",
        "env-parity: needs pymatgen (absent from local gate venv; CI has it via "
        "mp-api) — B5 re-admit on CI evidence",
    ),
    (
        "skills/materials/crystal-design-formula-crossover-agent/tests/test_helpers.py::test_mag_density_zero_magmom",
        "env-parity: needs pymatgen (absent from local gate venv; CI has it via "
        "mp-api) — B5 re-admit on CI evidence",
    ),
    (
        "skills/materials/materials-magnet-design-mvp/tests/test_helpers.py::test_hhi_fe2o3_returns_tuple_pair",
        "env-parity: needs pymatgen (absent from local gate venv; CI has it via "
        "mp-api) — B5 re-admit on CI evidence",
    ),
    (
        "skills/materials/materials-magnet-design-mvp/tests/test_helpers.py::test_hhi_rare_earth_higher_than_iron",
        "env-parity: needs pymatgen (absent from local gate venv; CI has it via "
        "mp-api) — B5 re-admit on CI evidence",
    ),
    (
        "skills/materials/materials-magnet-design-mvp/tests/test_helpers.py::test_mag_density_basic_volume_division",
        "env-parity: needs pymatgen (absent from local gate venv; CI has it via "
        "mp-api) — B5 re-admit on CI evidence",
    ),
    (
        "skills/materials/materials-magnet-design-mvp/tests/test_helpers.py::test_mag_density_zero_magmom",
        "env-parity: needs pymatgen (absent from local gate venv; CI has it via "
        "mp-api) — B5 re-admit on CI evidence",
    ),
    (
        "skills/opentrajectory/tests/p08b_3n/test_gap_to_action_planner.py::test_expected_before_after_output_is_deterministic",
        "structurally non-hermetic: reads untracked runtime artifact "
        "artifacts/opentrajectory-p08b-3n-gap-to-action-harness-*/before_after_output.json "
        "(absent on any fresh checkout incl. CI)",
    ),
    (
        "skills/opentrajectory/tests/p08b_3o/test_gap_to_action_pipeline.py::test_expected_before_after_pipeline_output_is_deterministic",
        "structurally non-hermetic: reads untracked runtime artifact "
        "artifacts/opentrajectory-p08b-3n-gap-to-action-harness-*/before_after_output.json "
        "(absent on any fresh checkout incl. CI)",
    ),
)


@dataclass(frozen=True)
class Selection:
    """Result of applying the selection rules to a file listing."""

    #: selected repo-relative posix paths grouped by rule id
    selected: dict[str, list[str]] = field(default_factory=dict)
    #: co-runnable groups: each group is safe for one pytest process
    runs: list[list[str]] = field(default_factory=list)
    #: deselect arguments aligned 1:1 with ``runs`` (rootdir-relative node ids)
    run_deselects: list[list[str]] = field(default_factory=list)
    #: deselected nodes, repo-relative ``path::node`` (ledger/human view)
    deselected_nodes: list[str] = field(default_factory=list)
    #: files inside the selection domains dropped by an exclusion, with reason
    excluded: list[tuple[str, str]] = field(default_factory=list)
    #: tracked test-like files outside the selection domains (informational)
    unselected: list[str] = field(default_factory=list)

    def selected_flat(self) -> list[str]:
        """Selected paths in stable rule order, alphabetical within a rule."""
        flat: list[str] = []
        for rule_id in sorted(self.selected):
            flat.extend(self.selected[rule_id])
        return flat


def is_test_file_name(name: str) -> bool:
    """True for pytest-style test module file names (test_*.py / *_test.py)."""
    if not name.endswith(TEST_FILE_EXTENSION):
        return False
    return name.startswith(TEST_FILE_PREFIX) or name.endswith(TEST_FILE_SUFFIX_ALT)


def normalize_path(raw: str) -> str:
    """Normalize a repo-relative path to forward slashes without './' prefix."""
    normalized = raw.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def has_excluded_component(path: str) -> bool:
    return any(part in EXCLUDED_DIR_NAMES for part in path.split("/")[:-1])


def is_under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix)


def _skills_test_root(path: str) -> str:
    """Owning skills test root: outermost ``tests/`` dir, else Skill root."""
    parts = path.split("/")
    # parts[0] == "skills"; find the outermost "tests" below skills/
    for index in range(1, len(parts) - 1):
        if parts[index] == "tests":
            return "/".join(parts[: index + 1])
    return "/".join(parts[:-1])


def _skills_rule_matches(path: str, file_set: frozenset[str]) -> bool:
    """Component-nearby test under skills/: tests/ ancestor or Skill-root file."""
    parts = path.split("/")
    if "tests" in parts[1:-1]:
        return True
    parent = "/".join(parts[:-1])
    return f"{parent}/SKILL.md" in file_set


def select_unit_tests(files: Iterable[str]) -> Selection:
    """Apply the SPEC-TEST-01 directory selection rules to a file listing.

    ``files`` are repo-relative paths (posix or windows separators, with or
    without a leading ``./``). Pure function: no filesystem or git access,
    so unit tests can feed it synthetic listings.
    """
    normalized = sorted({normalize_path(path) for path in files if normalize_path(path)})
    file_set = frozenset(normalized)

    selection = Selection()
    skills_run_roots: dict[str, list[str]] = {}
    for path in normalized:
        if not is_test_file_name(path.rsplit("/", 1)[-1]):
            continue
        if has_excluded_component(path):
            continue

        if is_under(path, "verification/suites/"):
            reason = _domain_exclusion_reason(path)
            if reason is not None:
                selection.excluded.append((path, reason))
            else:
                selection.selected.setdefault("verification-suites", []).append(path)
            continue

        if is_under(path, "skills/"):
            if _skills_rule_matches(path, file_set):
                reason = _skills_exclusion_reason(path) or _domain_exclusion_reason(path)
                if reason is not None:
                    selection.excluded.append((path, reason))
                else:
                    selection.selected.setdefault("skills-component", []).append(path)
                    skills_run_roots.setdefault(_skills_test_root(path), []).append(path)
            else:
                selection.unselected.append(path)
            continue

        if is_under(path, "services/api/tests/"):
            reason = _domain_exclusion_reason(path)
            if reason is not None:
                selection.excluded.append((path, reason))
            else:
                selection.selected.setdefault("services-api-unit", []).append(path)
            continue

        # Test-like file outside all three selection domains.
        selection.unselected.append(path)

    runs: list[list[str]] = [sorted(selection.selected.get("verification-suites", []))]
    for root in sorted(skills_run_roots):
        runs.append(sorted(skills_run_roots[root]))
    runs.append(sorted(selection.selected.get("services-api-unit", [])))
    selection.runs.extend(run for run in runs if run)
    selected_flat_set = set(selection.selected_flat())
    selection.deselected_nodes.extend(
        node
        for node, _reason in DESELECTED_NODES
        if node.rsplit("::", 1)[0] in selected_flat_set
    )
    # pytest --deselect matches rootdir-relative node ids: each run resolves
    # its rootdir to its domain ini (skills/ or services/api/) or the repo
    # root, so strip the domain prefix from the repo-relative node id.
    for run in selection.runs:
        prefix = ""
        if run[0].startswith("skills/"):
            prefix = "skills/"
        elif run[0].startswith("services/api/"):
            prefix = "services/api/"
        deselects = [
            node[len(prefix):]
            for node in selection.deselected_nodes
            if node.rsplit("::", 1)[0] in run
        ]
        selection.run_deselects.append(deselects)
    return selection


def _domain_exclusion_reason(path: str) -> str | None:
    for prefix, reason in DOMAIN_EXCLUDED_PREFIXES:
        if is_under(path, prefix):
            return reason
    if path in API_EXCLUDED_FILES:
        return API_EXCLUDED_FILES[path]
    return None


def _skills_exclusion_reason(path: str) -> str | None:
    return SKILLS_EXCLUDED_FILES.get(path)


def git_tracked_files(repo_root: Path) -> list[str]:
    """Repo-relative paths of all git-tracked files (empty on git failure)."""
    try:
        listed = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "-z"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return []
    if listed.returncode != 0:
        return []
    return [
        decoded
        for raw in listed.stdout.split(b"\0")
        if raw and (decoded := raw.decode("utf-8", errors="surrogateescape"))
    ]


def walk_repository_files(repo_root: Path) -> list[str]:
    """Filesystem fallback: repo-relative files, skipping dependency trees.

    Used by unit tests over synthetic tmp_path trees; mirrors the git listing
    shape so the pure selection core cannot tell them apart.
    """
    collected: list[str] = []
    for current, dir_names, file_names in os.walk(repo_root):
        dir_names[:] = [d for d in dir_names if d not in EXCLUDED_DIR_NAMES]
        for file_name in file_names:
            absolute = Path(current) / file_name
            collected.append(absolute.relative_to(repo_root).as_posix())
    return collected


def _emit(selection: Selection, mode: str, root: Path) -> int:
    if mode == "print0":
        stream = sys.stdout.buffer
        for path in selection.selected_flat():
            stream.write(path.encode("utf-8") + b"\0")
        stream.flush()
        return 0
    if mode == "json":
        payload = {
            "root": str(root),
            "counts": {
                "selected": len(selection.selected_flat()),
                "runs": len(selection.runs),
                "deselected_nodes": len(selection.deselected_nodes),
                "excluded": len(selection.excluded),
                "unselected_test_like": len(selection.unselected),
            },
            "selected": selection.selected,
            "runs": selection.runs,
            "run_deselects": selection.run_deselects,
            "deselected_nodes": selection.deselected_nodes,
            "deselected_nodes_rationale": [
                {"node": node, "reason": reason} for node, reason in DESELECTED_NODES
            ],
            "excluded": [
                {"path": path, "reason": reason} for path, reason in selection.excluded
            ],
            "unselected_test_like": selection.unselected,
        }
        json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    for path in selection.selected_flat():
        sys.stdout.write(path + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Select the CI unit-test set (SPEC-TEST-01) by directory.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT_DEFAULT,
        help="repository root (default: autodetected from this script's location)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--print",
        dest="mode",
        action="store_const",
        const="print",
        default="print",
        help="one path per line (default)",
    )
    mode.add_argument(
        "--print0",
        dest="mode",
        action="store_const",
        const="print0",
        help="NUL-separated paths (for xargs -0)",
    )
    mode.add_argument(
        "--json",
        dest="mode",
        action="store_const",
        const="json",
        help="machine-readable JSON: runs (co-runnable groups), exclusions, rationale",
    )
    args = parser.parse_args(argv)

    repo_root = args.root.resolve()
    files = git_tracked_files(repo_root) or walk_repository_files(repo_root)
    selection = select_unit_tests(files)
    return _emit(selection, args.mode, repo_root)


if __name__ == "__main__":
    raise SystemExit(main())
