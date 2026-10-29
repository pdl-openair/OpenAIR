from __future__ import annotations

from pathlib import Path
import subprocess

import pytest
import yaml

from verification.suites.test_asset_architecture import guard


def _valid_contract(suite_id: str = "verification.example") -> dict[str, object]:
    return {
        "suite_id": suite_id,
        "protected_outcome": "A protected result remains trustworthy.",
        "authority_refs": ["contract://authority"],
        "failure_class": "example_failure",
        "minimum_owner": "确定性合同与不变量",
        "production_targets": ["example"],
        "test_responsibility": "deterministic contract verification",
        "execution_profiles": ["deterministic"],
        "required_dependencies": [],
        "evidence_required": ["pytest_result"],
        "verdict_rule": (
            "PASS when assertions prove protected_outcome; "
            "FAIL when evidence proves violation; otherwise NO_VALID_EVIDENCE."
        ),
        "triggers": ["contract changes"],
    }


def _write_contract(path: Path, payload: dict[str, object] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload or _valid_contract(), sort_keys=False), encoding="utf-8")


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "guard@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Architecture Guard"], cwd=root, check=True)


def _write_file(root: Path, relative: str, content: str = "") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _commit(root: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _ci_changed_paths(root: Path, base: str, head: str = "HEAD") -> list[str]:
    output = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            "--no-renames",
            "--diff-filter=ACDMT",
            base,
            head,
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [line for line in output.splitlines() if line]


def test_guard_suite_contract_matches_authority() -> None:
    repository = Path(__file__).resolve().parents[3]
    contract = repository / "verification" / "suites" / "test_asset_architecture" / "suite.yaml"
    payload, errors = guard._load_suite(contract)
    assert errors == []
    assert isinstance(payload, dict)
    for field in guard.REQUIRED_SUITE_FIELDS:
        assert field in payload
        if field != "required_dependencies":
            assert guard._nonempty(payload[field])
    assert payload["minimum_owner"] == "确定性合同与不变量"
    assert "verdict_rule" in payload and guard._nonempty(payload["verdict_rule"])
    protected = str(payload["protected_outcome"]).lower()
    responsibility = str(payload["test_responsibility"]).lower()
    failure_class = str(payload["failure_class"]).lower()
    assert "execution boundary" not in protected
    assert "workflow" not in protected
    assert "workflow" not in responsibility
    assert "execution_boundary" not in failure_class
    assert "git-effective" in protected or "git-effective" in responsibility
    suite_id, contract_errors = guard.validate_suite_contract(
        contract,
        repository,
        require_complete_fields=True,
    )
    assert suite_id == "verification.test-asset-architecture"
    assert contract_errors == []


@pytest.mark.parametrize(
    "minimum_owner",
    [
        "确定性合同与不变量",
        "单个组件行为",
        "跨组件与基础设施",
        "真实运行与韧性",
        "完整产品链验证",
        "科研 / Agent 能力评测",
    ],
)
def test_canonical_minimum_owner_passes(tmp_path: Path, minimum_owner: str) -> None:
    contract = tmp_path / "verification" / "suites" / "example" / "suite.yaml"
    payload = _valid_contract()
    payload["minimum_owner"] = minimum_owner
    _write_contract(contract, payload)
    assert guard.validate_repository(tmp_path) == []


@pytest.mark.parametrize(
    "minimum_owner",
    [
        "component",
        "single Web API client contract",
        "single Artifact Library page behavior",
        "unknown owner",
    ],
)
def test_noncanonical_minimum_owner_fails_even_when_untouched(
    tmp_path: Path, minimum_owner: str
) -> None:
    contract = tmp_path / "verification" / "suites" / "example" / "suite.yaml"
    payload = _valid_contract()
    payload["minimum_owner"] = minimum_owner
    _write_contract(contract, payload)
    errors = guard.validate_repository(tmp_path)
    assert any("invalid minimum_owner" in error for error in errors)


def test_new_complete_component_test_passes(tmp_path: Path) -> None:
    test_path = tmp_path / "services" / "api" / "app" / "modules" / "tests" / "example" / "test_behavior.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text("", encoding="utf-8")
    suite_path = test_path.parent / "suite.yaml"
    _write_contract(suite_path, _valid_contract("component.modules.example"))
    rel = test_path.relative_to(tmp_path).as_posix()
    suite_rel = suite_path.relative_to(tmp_path).as_posix()
    assert guard.validate_repository(tmp_path, [rel, suite_rel]) == []


def test_new_stray_production_test_fails(tmp_path: Path) -> None:
    rel = "services/api/app/modules/test_hidden.py"
    _write_file(tmp_path, rel)
    errors = guard.validate_repository(tmp_path, [rel])
    assert any("test asset is stray" in error for error in errors)


def test_new_unowned_component_test_fails(tmp_path: Path) -> None:
    test_path = tmp_path / "services" / "api" / "app" / "modules" / "tests" / "example" / "test_behavior.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text("", encoding="utf-8")
    missing = guard.validate_repository(tmp_path, [test_path.relative_to(tmp_path).as_posix()])
    assert any("component test has no owning suite.yaml" in error for error in missing)


def test_historical_unowned_skill_tests_do_not_block_when_untouched(tmp_path: Path) -> None:
    test_dir = tmp_path / "skills" / "opentrajectory" / "legacy-skill" / "tests"
    test_dir.mkdir(parents=True)
    (test_dir / "replay.md").write_text("# historical replay", encoding="utf-8")
    assert guard.validate_repository(tmp_path) == []
    assert guard.validate_repository(tmp_path, ["README.md"]) == []


def test_new_unowned_skill_test_fails(tmp_path: Path) -> None:
    rel = "skills/opentrajectory/legacy-skill/tests/test_new_behavior.py"
    _write_file(tmp_path, rel)
    errors = guard.validate_repository(tmp_path, [rel])
    assert any("component test has no owning suite.yaml" in error for error in errors)


def test_changed_incomplete_suite_contract_missing_verdict_rule_fails(tmp_path: Path) -> None:
    contract = tmp_path / "verification" / "suites" / "example" / "suite.yaml"
    incomplete = {
        "suite_id": "verification.example",
        "protected_outcome": "A protected result remains trustworthy.",
        "authority_refs": ["contract://authority"],
        "failure_class": "example_failure",
        "minimum_owner": "确定性合同与不变量",
        "production_targets": ["example"],
        "test_responsibility": "deterministic contract verification",
        "execution_profiles": ["deterministic"],
        "required_dependencies": [],
        "evidence_required": ["pytest_result"],
        "triggers": ["contract changes"],
    }
    _write_contract(contract, incomplete)
    rel = contract.relative_to(tmp_path).as_posix()
    errors = guard.validate_repository(tmp_path, [rel])
    assert any("missing required field 'verdict_rule'" in error for error in errors)


def test_untouched_incomplete_suite_contract_does_not_block(tmp_path: Path) -> None:
    contract = tmp_path / "verification" / "suites" / "legacy" / "suite.yaml"
    incomplete = {
        "suite_id": "verification.legacy-incomplete",
        "protected_outcome": "Legacy debt remains until migration.",
        "authority_refs": ["contract://authority"],
        "failure_class": "example_failure",
        "minimum_owner": "确定性合同与不变量",
        "production_targets": ["example"],
        "test_responsibility": "legacy",
        "execution_profiles": ["deterministic"],
        "required_dependencies": [],
        "evidence_required": ["pytest_result"],
        "triggers": ["legacy"],
    }
    _write_contract(contract, incomplete)
    assert guard.validate_repository(tmp_path) == []
    # Not legitimized: changing the same incomplete contract still fails.
    rel = contract.relative_to(tmp_path).as_posix()
    errors = guard.validate_repository(tmp_path, [rel])
    assert any("missing required field 'verdict_rule'" in error for error in errors)


def test_live_suite_cannot_share_fake_or_replay_identity(tmp_path: Path) -> None:
    payload = _valid_contract()
    payload["execution_profiles"] = ["live", "replay"]
    payload["required_dependencies"] = ["REAL_PROVIDER_ENDPOINT"]
    payload["evidence_required"] = ["provider_identity"]
    contract = tmp_path / "verification" / "suites" / "example" / "suite.yaml"
    _write_contract(contract, payload)
    assert any("cannot also identify as fake, replay" in error for error in guard.validate_repository(tmp_path))


def test_live_suite_requires_runtime_identity_evidence(tmp_path: Path) -> None:
    payload = _valid_contract()
    payload["execution_profiles"] = ["live"]
    payload["required_dependencies"] = []
    contract = tmp_path / "verification" / "suites" / "example" / "suite.yaml"
    _write_contract(contract, payload)
    errors = guard.validate_repository(tmp_path)
    assert any("requires explicit runtime/provider dependencies" in error for error in errors)
    assert any("requires runtime/provider identity evidence" in error for error in errors)


def test_new_test_under_scripts_is_rejected(tmp_path: Path) -> None:
    rel = "scripts/readiness/tests/test_smoke.py"
    _write_file(tmp_path, rel)
    errors = guard.validate_repository(tmp_path, [rel])
    assert errors == ["scripts/readiness/tests/test_smoke.py: new long-term test assets are forbidden under scripts/"]


def test_gate_component_test_with_owning_contract_passes(tmp_path: Path) -> None:
    test_path = tmp_path / "scripts" / "gates" / "tests" / "test_gate_behavior.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text("", encoding="utf-8")
    suite_path = test_path.parent / "suite.yaml"
    _write_contract(suite_path, _valid_contract("component.gates.example"))
    rel = test_path.relative_to(tmp_path).as_posix()
    suite_rel = suite_path.relative_to(tmp_path).as_posix()
    assert guard.validate_repository(tmp_path, [rel, suite_rel]) == []


def test_gate_component_test_without_owning_contract_fails(tmp_path: Path) -> None:
    rel = "scripts/gates/tests/test_unowned.py"
    _write_file(tmp_path, rel)
    errors = guard.validate_repository(tmp_path, [rel])
    assert any("component test has no owning suite.yaml" in error for error in errors)


def test_stray_test_in_gate_production_root_fails(tmp_path: Path) -> None:
    rel = "scripts/gates/test_hidden.py"
    _write_file(tmp_path, rel)
    errors = guard.validate_repository(tmp_path, [rel])
    assert any("test asset is stray" in error for error in errors)


def test_skill_registry_component_test_with_owning_contract_passes(tmp_path: Path) -> None:
    test_path = tmp_path / "scripts" / "skill_registry" / "tests" / "test_registry_behavior.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text("", encoding="utf-8")
    suite_path = test_path.parent / "suite.yaml"
    _write_contract(suite_path, _valid_contract("component.skill-registry.example"))
    rel = test_path.relative_to(tmp_path).as_posix()
    suite_rel = suite_path.relative_to(tmp_path).as_posix()
    assert guard.validate_repository(tmp_path, [rel, suite_rel]) == []


def test_skill_registry_component_test_without_owning_contract_fails(tmp_path: Path) -> None:
    rel = "scripts/skill_registry/tests/test_unowned.py"
    _write_file(tmp_path, rel)
    errors = guard.validate_repository(tmp_path, [rel])
    assert any("component test has no owning suite.yaml" in error for error in errors)


def test_stray_test_in_skill_registry_production_root_fails(tmp_path: Path) -> None:
    rel = "scripts/skill_registry/test_hidden.py"
    _write_file(tmp_path, rel)
    errors = guard.validate_repository(tmp_path, [rel])
    assert any("test asset is stray" in error for error in errors)


def test_scripts_outside_component_roots_stay_forbidden(tmp_path: Path) -> None:
    rel = "scripts/other_tooling/tests/test_still_forbidden.py"
    _write_file(tmp_path, rel)
    errors = guard.validate_repository(tmp_path, [rel])
    assert any(
        "new long-term test assets are forbidden under scripts/" in error for error in errors
    )


def test_new_skill_local_eval_asset_is_rejected(tmp_path: Path) -> None:
    rel = "skills/opentrajectory/example/evals/cases.json"
    _write_file(tmp_path, rel, "{}")
    errors = guard.validate_repository(tmp_path, [rel])
    assert errors == [
        "skills/opentrajectory/example/evals/cases.json: new formal Eval assets must use the repository evals/ authority, not a Skill-local evals/ directory"
    ]


def test_guard_in_changed_set_does_not_bypass_other_illegal_paths(tmp_path: Path) -> None:
    _write_file(tmp_path, "scripts/legacy/tests/test_bad.py")
    errors = guard.validate_repository(
        tmp_path,
        [
            "verification/suites/test_asset_architecture/guard.py",
            "scripts/legacy/tests/test_bad.py",
        ],
    )
    assert any("forbidden under scripts/" in error for error in errors)


def test_parallel_suite_identity_manifest_is_rejected(tmp_path: Path) -> None:
    suite_dir = tmp_path / "verification" / "suites" / "example"
    _write_contract(suite_dir / "suite.yaml")
    (suite_dir / "execution-profiles.json").write_text(
        '{"suite_id":"verification.example","profiles":{"deterministic":["tests/example.spec.ts"]}}',
        encoding="utf-8",
    )
    errors = guard.validate_repository(tmp_path)
    assert any("parallel Suite identity/member manifest is forbidden" in error for error in errors)


def test_repository_scan_prunes_dependency_and_git_trees(tmp_path: Path) -> None:
    for ignored_root in (".git", ".venv", "node_modules", "__pycache__"):
        hidden_contract = tmp_path / ignored_root / "nested" / "suite.yaml"
        hidden_contract.parent.mkdir(parents=True)
        hidden_contract.write_text("not: a-valid-contract\n", encoding="utf-8")
    assert guard.validate_repository(tmp_path) == []


def test_repository_git_scan_ignores_ignored_runtime_outputs(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / ".gitignore").write_text(
        "skills/opentrajectory/tests/execution_outputs/\n", encoding="utf-8"
    )
    runtime = tmp_path / "skills" / "opentrajectory" / "tests" / "execution_outputs" / "local.json"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("{}", encoding="utf-8")
    # Ignored runtime output must not invent ownership problems.
    assert guard.validate_repository(tmp_path) == []


def test_repository_git_scan_sees_nonignored_untracked_new_test(tmp_path: Path) -> None:
    _git_init(tmp_path)
    test_asset = tmp_path / "skills" / "opentrajectory" / "legacy" / "tests" / "test_new.py"
    test_asset.parent.mkdir(parents=True)
    test_asset.write_text("# new formal test", encoding="utf-8")
    rel = test_asset.relative_to(tmp_path).as_posix()
    # Prospective check via added path must see and reject the unowned new test.
    errors = guard.validate_repository(tmp_path, [rel])
    assert any("component test has no owning suite.yaml" in error for error in errors)
    # Untouched historical presence alone does not fail.
    assert guard.validate_repository(tmp_path) == []


def test_ignored_suite_yaml_cannot_own_new_test(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / ".gitignore").write_text(
        "services/api/app/modules/tests/example/suite.yaml\n", encoding="utf-8"
    )
    test_dir = tmp_path / "services" / "api" / "app" / "modules" / "tests" / "example"
    test_dir.mkdir(parents=True)
    _write_contract(test_dir / "suite.yaml", _valid_contract("component.modules.ignored-owner"))
    test_path = test_dir / "test_behavior.py"
    test_path.write_text("", encoding="utf-8")
    rel = test_path.relative_to(tmp_path).as_posix()
    errors = guard.validate_repository(tmp_path, [rel])
    assert any("component test has no owning suite.yaml" in error for error in errors)


def test_ignored_bad_suite_assets_do_not_participate_in_formal_scan(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / ".gitignore").write_text("verification/suites/local-junk/\n", encoding="utf-8")
    junk = tmp_path / "verification" / "suites" / "local-junk"
    junk.mkdir(parents=True)
    (junk / "suite.yaml").write_text("suite_id: verification.local-junk\n", encoding="utf-8")
    (junk / "execution-profiles.json").write_text(
        '{"suite_id":"verification.local-junk","profiles":{"deterministic":["x"]}}',
        encoding="utf-8",
    )
    assert guard.validate_repository(tmp_path) == []
    assert guard.validate_repository(
        tmp_path,
        ["verification/suites/local-junk/suite.yaml"],
    ) == []


def test_changed_paths_file_utf8_bom_illegal_path_still_fails(tmp_path: Path) -> None:
    paths_file = tmp_path / "added-paths.txt"
    # First line carries a UTF-8 BOM and names an illegal long-term test under scripts/.
    paths_file.write_bytes(
        "\ufeffscripts/readiness/tests/test_smoke.py\n".encode("utf-8")
    )
    illegal = tmp_path / "scripts" / "readiness" / "tests" / "test_smoke.py"
    illegal.parent.mkdir(parents=True)
    illegal.write_text("", encoding="utf-8")
    errors = guard.validate_repository(tmp_path, guard._read_changed_paths(paths_file))
    assert any("forbidden under scripts/" in error for error in errors)


def test_skill_production_target_in_suite_must_exist(tmp_path: Path) -> None:
    contract = _valid_contract("eval.example")
    contract["production_targets"] = ["skills/opentrajectory/missing-skill"]
    path = tmp_path / "evals" / "example" / "suite.yaml"
    _write_contract(path, contract)
    rel = path.relative_to(tmp_path).as_posix()
    errors = guard.validate_repository(tmp_path, [rel])
    assert any("Skill production_target does not exist" in error for error in errors)


def test_skill_production_target_ignored_local_directory_does_not_count(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / ".gitignore").write_text("skills/opentrajectory/local-only/\n", encoding="utf-8")
    local_only = tmp_path / "skills" / "opentrajectory" / "local-only" / "cache.json"
    local_only.parent.mkdir(parents=True)
    local_only.write_text("{}", encoding="utf-8")
    contract = _valid_contract("eval.example.ignored-target")
    contract["production_targets"] = ["skills/opentrajectory/local-only"]
    path = tmp_path / "evals" / "example" / "suite.yaml"
    _write_contract(path, contract)
    rel = path.relative_to(tmp_path).as_posix()
    errors = guard.validate_repository(tmp_path, [rel])
    assert any("Skill production_target does not exist" in error for error in errors)


def test_repository_local_authority_ref_must_exist(tmp_path: Path) -> None:
    payload = _valid_contract("verification.missing-authority")
    payload["authority_refs"] = ["docs/missing-authority.md"]
    path = tmp_path / "verification" / "suites" / "example" / "suite.yaml"
    _write_contract(path, payload)
    rel = path.relative_to(tmp_path).as_posix()
    errors = guard.validate_repository(tmp_path, [rel])
    assert any("authority_ref does not exist" in error for error in errors)


def test_repository_local_authority_ref_existing_file_passes(tmp_path: Path) -> None:
    authority = tmp_path / "docs" / "authority.md"
    authority.parent.mkdir(parents=True)
    authority.write_text("# Authority\n", encoding="utf-8")
    payload = _valid_contract("verification.existing-authority")
    payload["authority_refs"] = ["docs/authority.md"]
    path = tmp_path / "verification" / "suites" / "example" / "suite.yaml"
    _write_contract(path, payload)
    rel = path.relative_to(tmp_path).as_posix()
    assert guard.validate_repository(tmp_path, [rel]) == []


def test_current_repository_safe_mode_passes() -> None:
    repository = Path(__file__).resolve().parents[3]
    # Historical unowned Skill tests and untouched incomplete Suite Contracts must not block.
    assert guard.validate_repository(repository) == []


def test_current_object_rejects_new_stray_when_declared(tmp_path: Path) -> None:
    rel = "services/api/app/modules/invented_for_guard/test_stray.py"
    _write_file(tmp_path, rel)
    errors = guard.validate_repository(
        tmp_path,
        [rel],
    )
    assert any("test asset is stray" in error for error in errors)


def test_new_api_component_tests_root_requires_suite_contract(tmp_path: Path) -> None:
    rel = "services/api/tests/test_new_guard_case.py"
    _write_file(tmp_path, rel)
    errors = guard.validate_repository(tmp_path, [rel])
    assert any("component test has no owning suite.yaml" in error for error in errors)


def test_historical_api_tests_untouched_do_not_block(tmp_path: Path) -> None:
    historical = tmp_path / "services" / "api" / "tests" / "test_legacy_api.py"
    historical.parent.mkdir(parents=True)
    historical.write_text("", encoding="utf-8")
    assert guard.validate_repository(tmp_path) == []
    assert guard.validate_repository(tmp_path, ["README.md"]) == []


def test_new_eval_asset_without_suite_fails(tmp_path: Path) -> None:
    rel = "evals/example-capability/eval-spec.md"
    _write_file(tmp_path, rel, "# Eval")
    errors = guard.validate_repository(tmp_path, [rel])
    assert any("formal Eval asset has no owning suite.yaml" in error for error in errors)


def test_new_eval_asset_with_suite_passes(tmp_path: Path) -> None:
    suite_path = tmp_path / "evals" / "example-capability" / "suite.yaml"
    asset_path = tmp_path / "evals" / "example-capability" / "eval-spec.md"
    _write_contract(suite_path, _valid_contract("eval.example-capability"))
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_text("# Eval", encoding="utf-8")
    assert (
        guard.validate_repository(
            tmp_path,
            [
                asset_path.relative_to(tmp_path).as_posix(),
                suite_path.relative_to(tmp_path).as_posix(),
            ],
        )
        == []
    )


def test_evals_root_index_is_not_rejected(tmp_path: Path) -> None:
    assert guard.validate_repository(tmp_path, ["evals/README.md"]) == []


def test_deleting_suite_yaml_leaves_owned_skill_tests_fails(tmp_path: Path) -> None:
    _git_init(tmp_path)
    test_dir = tmp_path / "skills" / "opentrajectory" / "active-skill" / "tests"
    test_dir.mkdir(parents=True)
    (test_dir / "test_behavior.py").write_text("", encoding="utf-8")
    # Changed-path lists a suite.yaml that is no longer present (deleted/moved away).
    errors = guard.validate_repository(
        tmp_path,
        ["skills/opentrajectory/active-skill/tests/suite.yaml"],
    )
    assert any("removing Suite Contract leaves" in error for error in errors)
    assert any("without a Git-effective owner" in error for error in errors)


def test_historical_unowned_skill_tests_untouched_still_pass_with_other_changes(
    tmp_path: Path,
) -> None:
    test_dir = tmp_path / "skills" / "opentrajectory" / "legacy-skill" / "tests"
    test_dir.mkdir(parents=True)
    (test_dir / "test_legacy.py").write_text("", encoding="utf-8")
    # No owner deletion in the changed set — historical debt remains non-blocking.
    assert guard.validate_repository(tmp_path, ["docs/note.md"]) == []


def test_ci_delete_old_test_and_add_formal_replacement_passes(tmp_path: Path) -> None:
    _git_init(tmp_path)
    old = "apps/web/src/api/client.legacy-review.test.ts"
    _write_file(tmp_path, old, "legacy")
    base = _commit(tmp_path, "baseline")

    (tmp_path / old).unlink()
    suite = "apps/web/src/api/tests/review/suite.yaml"
    replacement = "apps/web/src/api/tests/review/test_client_review.test.ts"
    _write_contract(tmp_path / suite, _valid_contract("web.api.review"))
    _write_file(tmp_path, replacement, "formal")
    _commit(tmp_path, "formalize")

    changed = _ci_changed_paths(tmp_path, base)
    assert old in changed
    assert replacement in changed
    assert guard.validate_repository(tmp_path, changed) == []


def test_ci_added_stray_test_still_fails(tmp_path: Path) -> None:
    _git_init(tmp_path)
    _write_file(tmp_path, "README.md", "baseline")
    base = _commit(tmp_path, "baseline")
    stray = "apps/web/src/api/client.unowned.test.ts"
    _write_file(tmp_path, stray, "bad")
    _commit(tmp_path, "add stray")

    errors = guard.validate_repository(tmp_path, _ci_changed_paths(tmp_path, base))
    assert any("test asset is stray" in error for error in errors)


def test_ci_deleted_old_test_is_not_checked_as_current(tmp_path: Path) -> None:
    _git_init(tmp_path)
    old = "scripts/legacy/tests/test_retired.py"
    _write_file(tmp_path, old, "legacy")
    base = _commit(tmp_path, "baseline")
    (tmp_path / old).unlink()
    _commit(tmp_path, "retire")

    changed = _ci_changed_paths(tmp_path, base)
    assert changed == [old]
    assert guard.validate_repository(tmp_path, changed) == []


def test_ci_deleted_suite_contract_still_fails_repository_integrity(tmp_path: Path) -> None:
    _git_init(tmp_path)
    suite = "skills/opentrajectory/example/tests/suite.yaml"
    member = "skills/opentrajectory/example/tests/test_behavior.py"
    _write_contract(tmp_path / suite, _valid_contract("skill.example"))
    _write_file(tmp_path, member, "formal")
    base = _commit(tmp_path, "baseline")
    (tmp_path / suite).unlink()
    _commit(tmp_path, "break suite")

    errors = guard.validate_repository(tmp_path, _ci_changed_paths(tmp_path, base))
    assert any("removing Suite Contract leaves" in error for error in errors)


def test_ci_test_migration_from_old_path_to_formal_path_passes(tmp_path: Path) -> None:
    _git_init(tmp_path)
    old = "services/api/app/modules/test_legacy_owner.py"
    _write_file(tmp_path, old, "legacy")
    base = _commit(tmp_path, "baseline")
    (tmp_path / old).unlink()
    suite = "services/api/app/modules/tests/owner/suite.yaml"
    new = "services/api/app/modules/tests/owner/test_behavior.py"
    _write_contract(tmp_path / suite, _valid_contract("api.modules.owner"))
    _write_file(tmp_path, new, "formal")
    _commit(tmp_path, "migrate")

    assert guard.validate_repository(tmp_path, _ci_changed_paths(tmp_path, base)) == []


def test_ci_no_renames_reports_rename_as_delete_add_and_guard_passes(tmp_path: Path) -> None:
    _git_init(tmp_path)
    old = "scripts/legacy/tests/test_rename.py"
    _write_file(tmp_path, old, "same content")
    base = _commit(tmp_path, "baseline")
    (tmp_path / old).unlink()
    suite = "apps/web/src/lib/tests/renamed/suite.yaml"
    new = "apps/web/src/lib/tests/renamed/test_rename.test.ts"
    _write_contract(tmp_path / suite, _valid_contract("web.lib.renamed"))
    _write_file(tmp_path, new, "same content")
    _commit(tmp_path, "rename into formal owner")

    changed = _ci_changed_paths(tmp_path, base)
    status = subprocess.run(
        ["git", "diff", "--name-status", "--no-renames", base, "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert f"D\t{old}" in status
    assert f"A\t{new}" in status
    assert guard.validate_repository(tmp_path, changed) == []


def test_ci_deleted_old_test_does_not_mask_added_stray_test(tmp_path: Path) -> None:
    _git_init(tmp_path)
    old = "scripts/legacy/tests/test_old.py"
    _write_file(tmp_path, old, "old")
    base = _commit(tmp_path, "baseline")
    (tmp_path / old).unlink()
    bad = "services/api/app/modules/test_new_stray.py"
    _write_file(tmp_path, bad, "bad")
    _commit(tmp_path, "replace badly")

    errors = guard.validate_repository(tmp_path, _ci_changed_paths(tmp_path, base))
    assert any(bad in error and "test asset is stray" in error for error in errors)


def test_normalize_repo_path_preserves_dot_github_prefix() -> None:
    assert (
        guard._normalize_repo_path("./.github/workflows/required-checks.yml")
        == ".github/workflows/required-checks.yml"
    )
    assert (
        guard._normalize_repo_path(".github/workflows/required-checks.yml")
        == ".github/workflows/required-checks.yml"
    )
