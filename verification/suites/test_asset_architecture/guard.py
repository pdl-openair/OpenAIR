from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Iterable

import yaml


REQUIRED_SUITE_FIELDS = (
    "suite_id",
    "protected_outcome",
    "authority_refs",
    "failure_class",
    "minimum_owner",
    "production_targets",
    "test_responsibility",
    "execution_profiles",
    "required_dependencies",
    "evidence_required",
    "verdict_rule",
    "triggers",
)
CANONICAL_MINIMUM_OWNERS = (
    "确定性合同与不变量",
    "单个组件行为",
    "跨组件与基础设施",
    "真实运行与韧性",
    "完整产品链验证",
    "科研 / Agent 能力评测",
)
LIST_FIELDS = {
    "authority_refs",
    "production_targets",
    "execution_profiles",
    "required_dependencies",
    "evidence_required",
    "triggers",
}
TEST_FILE_PATTERNS = (
    re.compile(r"^test_.+\.py$"),
    re.compile(r"^.+_test\.py$"),
    re.compile(r"^.+\.(?:test|spec)\.[^.]+$"),
)
PRODUCTION_COMPONENT_ROOTS = (
    PurePosixPath("services/api/app"),
    PurePosixPath("apps/web/src"),
    PurePosixPath("skills"),
    # CI gate implementations are production components (debt ① ruling, sync
    # batch): their tests live in scripts/gates/tests/ under the same
    # component-pairing contract as any other production component.
    PurePosixPath("scripts/gates"),
    # The skill registry tooling is a maintained production component
    # (D-19 ruling, sync batch): same pairing contract, tests under
    # scripts/skill_registry/tests/ with an owning suite.yaml.
    PurePosixPath("scripts/skill_registry"),
)
# Authority: <production component>/tests/ — matched to current repository layout.
COMPONENT_TEST_ROOTS = (
    PurePosixPath("services/api/tests"),
    PurePosixPath("apps/web/tests"),
)
IGNORED_DIRECTORY_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _normalize_repo_path(raw_path: str) -> str:
    """Normalize separators and strip only a true './' relative prefix.

    Must preserve legitimate dot-prefixed paths such as ``.github/...``.
    """
    normalized = raw_path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _walk_files(root: Path, *, name: str | None = None) -> Iterable[Path]:
    """Yield files without descending into repository/runtime dependency trees."""
    for current, directory_names, file_names in os.walk(root):
        directory_names[:] = [
            directory_name
            for directory_name in directory_names
            if directory_name not in IGNORED_DIRECTORY_NAMES
        ]
        for file_name in file_names:
            if name is None or file_name == name:
                yield Path(current) / file_name


def _repository_asset_files(root: Path) -> set[PurePosixPath] | None:
    """Return tracked + non-ignored untracked repository assets when *root* is a Git root.

    Architecture ownership must not be created by ignored local caches, execution outputs,
    provider artifacts, or other machine-local files. During an Apply, newly created formal
    assets are still visible because --others --exclude-standard includes non-ignored files.
    Non-Git temporary trees fall back to filesystem scanning so unit tests remain simple.
    """
    try:
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None
    if top.returncode != 0:
        return None
    try:
        git_root = Path(top.stdout.strip()).resolve()
    except OSError:
        return None
    if git_root != root.resolve():
        return None
    listed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if listed.returncode != 0:
        return None
    assets: set[PurePosixPath] = set()
    for raw in listed.stdout.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        asset = PurePosixPath(rel)
        if (root / Path(*asset.parts)).is_file():
            assets.add(asset)
    return assets


def _formal_paths(
    root: Path,
    repository_assets: set[PurePosixPath] | None,
    *,
    name: str | None = None,
    under: PurePosixPath | None = None,
) -> Iterable[Path]:
    """Yield formal assets from the Git-effective view, or filesystem fallback outside Git."""
    if repository_assets is None:
        scan_root = root / under.as_posix() if under is not None else root
        if under is not None and not scan_root.is_dir():
            return
        yield from _walk_files(scan_root, name=name)
        return
    for asset in sorted(repository_assets):
        if under is not None and not _is_under(asset, under):
            continue
        if name is not None and asset.name != name:
            continue
        path = root / Path(*asset.parts)
        if path.is_file():
            yield path


def _nonempty(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return value is not None


def _load_suite(path: Path) -> tuple[dict[str, object] | None, list[str]]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        return None, [f"{path}: invalid Suite Contract YAML: {exc}"]
    if not isinstance(payload, dict):
        return None, [f"{path}: Suite Contract must be a YAML mapping"]
    return payload, []


def _validate_live_identity(display: str, payload: dict[str, object]) -> list[str]:
    errors: list[str] = []
    profiles = (
        {
            str(profile).strip().lower().replace("_", "-")
            for profile in payload.get("execution_profiles", [])
        }
        if isinstance(payload.get("execution_profiles"), list)
        else set()
    )
    if not any(profile == "live" or profile.startswith("live-") or profile.endswith("-live") for profile in profiles):
        return errors
    if profiles.intersection({"fake", "replay", "synthetic", "offline"}):
        errors.append(
            f"{display}: a Suite declaring the live profile cannot also identify as fake, replay, synthetic, or offline"
        )
    dependencies = payload.get("required_dependencies", [])
    evidence = " ".join(str(item).lower() for item in payload.get("evidence_required", []))
    if not dependencies:
        errors.append(f"{display}: live profile requires explicit runtime/provider dependencies")
    if not any(marker in evidence for marker in ("runtime", "provider", "endpoint", "deployment")):
        errors.append(f"{display}: live profile requires runtime/provider identity evidence")
    return errors


def validate_suite_contract(
    path: Path,
    root: Path,
    repository_assets: set[PurePosixPath] | None = None,
    *,
    require_complete_fields: bool = True,
) -> tuple[str | None, list[str]]:
    payload, errors = _load_suite(path)
    display = _relative(path, root)
    if payload is None:
        return None, [error.replace(str(path), display) for error in errors]

    minimum_owner = payload.get("minimum_owner")
    if not isinstance(minimum_owner, str) or minimum_owner not in CANONICAL_MINIMUM_OWNERS:
        errors.append(f"{display}: invalid minimum_owner {minimum_owner!r}")

    if require_complete_fields:
        for field in REQUIRED_SUITE_FIELDS:
            if field not in payload:
                errors.append(f"{display}: missing required field {field!r}")
            elif field not in {"required_dependencies"} and not _nonempty(payload[field]):
                errors.append(f"{display}: required field {field!r} must not be empty")
        for field in LIST_FIELDS:
            if field in payload and not isinstance(payload[field], list):
                errors.append(f"{display}: field {field!r} must be a list")

        authority_refs = payload.get("authority_refs", [])
        if isinstance(authority_refs, list):
            for authority_ref in authority_refs:
                if not isinstance(authority_ref, str):
                    continue
                normalized_ref = authority_ref.strip().replace("\\", "/")
                if not normalized_ref.startswith(("docs/", "evals/", "skills/", "verification/")):
                    continue
                ref_path = PurePosixPath(normalized_ref)
                if repository_assets is None:
                    authority_exists = (root / normalized_ref).exists()
                else:
                    authority_exists = (
                        ref_path in repository_assets
                        or any(_is_under(asset, ref_path) for asset in repository_assets)
                    )
                if not authority_exists:
                    errors.append(f"{display}: authority_ref does not exist: {normalized_ref}")

        production_targets = payload.get("production_targets", [])
        if isinstance(production_targets, list):
            for target in production_targets:
                if not isinstance(target, str):
                    continue
                normalized_target = target.strip().replace("\\", "/")
                if normalized_target.startswith("skills/"):
                    target_path = PurePosixPath(normalized_target)
                    if repository_assets is None:
                        target_exists = (root / normalized_target).is_dir()
                    else:
                        target_exists = any(_is_under(asset, target_path) for asset in repository_assets)
                    if not target_exists:
                        errors.append(
                            f"{display}: Skill production_target does not exist: {normalized_target}"
                        )

    errors.extend(_validate_live_identity(display, payload))

    suite_id = payload.get("suite_id")
    return str(suite_id) if isinstance(suite_id, str) and suite_id.strip() else None, errors


def _nearest_suite_contract(
    start: Path,
    stop: Path,
    root: Path,
    repository_assets: set[PurePosixPath] | None,
) -> Path | None:
    current = start
    while True:
        candidate = current / "suite.yaml"
        if candidate.is_file():
            if repository_assets is None:
                return candidate
            rel = PurePosixPath(_relative(candidate, root))
            if rel in repository_assets:
                return candidate
        if current == stop or stop not in current.parents:
            return None
        current = current.parent


def _is_test_file(path: PurePosixPath) -> bool:
    return any(pattern.fullmatch(path.name) for pattern in TEST_FILE_PATTERNS)


def _is_under(path: PurePosixPath, parent: PurePosixPath) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _is_evals_root_index(path: PurePosixPath) -> bool:
    """Repository-level evals/ index or notes (not a suite member)."""
    return len(path.parts) == 2 and path.parts[0] == "evals" and path.name != "suite.yaml"


def _is_formal_eval_member(path: PurePosixPath) -> bool:
    if not _is_under(path, PurePosixPath("evals")) or _is_evals_root_index(path):
        return False
    if path.name == "suite.yaml":
        return False
    return len(path.parts) >= 3


def _is_formal_owned_member(path: PurePosixPath) -> bool:
    """Assets that must retain a Git-effective Suite owner when one is removed."""
    if path.name == "suite.yaml":
        return False
    if _is_test_file(path):
        return True
    if path.name == "eval-spec.md" or path.name.startswith("playwright.config."):
        return True
    if _is_formal_eval_member(path):
        return True
    if "tests" in path.parts and path.suffix.lower() in {
        ".py",
        ".md",
        ".ts",
        ".js",
        ".json",
        ".yaml",
        ".yml",
    }:
        return True
    return False


def _ownership_stop_for(path: PurePosixPath) -> PurePosixPath:
    for test_root in COMPONENT_TEST_ROOTS:
        if _is_under(path, test_root):
            return test_root
    for component_root in PRODUCTION_COMPONENT_ROOTS:
        if _is_under(path, component_root):
            return component_root
    if _is_under(path, PurePosixPath("evals")):
        return PurePosixPath("evals")
    if _is_under(path, PurePosixPath("verification/suites")):
        return PurePosixPath("verification/suites")
    return PurePosixPath(*path.parts[:1]) if path.parts else PurePosixPath(".")


def _repository_path_present(
    path: PurePosixPath,
    root: Path,
    repository_assets: set[PurePosixPath] | None,
) -> bool:
    if repository_assets is not None:
        return path in repository_assets
    return (root / Path(*path.parts)).is_file()


def _assets_under(
    directory: PurePosixPath,
    root: Path,
    repository_assets: set[PurePosixPath] | None,
) -> list[PurePosixPath]:
    if repository_assets is not None:
        return sorted(
            asset for asset in repository_assets if _is_under(asset, directory) and asset != directory
        )
    abs_dir = root / directory.as_posix()
    if not abs_dir.is_dir():
        return []
    return [PurePosixPath(_relative(path, root)) for path in sorted(_walk_files(abs_dir))]


def validate_changed_path(
    root: Path,
    raw_path: str,
    repository_assets: set[PurePosixPath] | None = None,
) -> list[str]:
    """Validate a changed object only when it exists in the current repository view."""
    normalized = _normalize_repo_path(raw_path)
    if not normalized:
        return []
    path = PurePosixPath(normalized)
    if not _repository_path_present(path, root, repository_assets):
        return []
    if _is_under(path, PurePosixPath("skills")) and "evals" in path.parts:
        return [
            f"{normalized}: new formal Eval assets must use the repository evals/ authority, "
            "not a Skill-local evals/ directory"
        ]

    if _is_test_file(path) and _is_under(path, PurePosixPath("scripts")) and not any(
        _is_under(path, component_root) for component_root in PRODUCTION_COMPONENT_ROOTS
    ):
        return [f"{normalized}: new long-term test assets are forbidden under scripts/"]

    if _is_test_file(path):
        for component_root in PRODUCTION_COMPONENT_ROOTS:
            if not _is_under(path, component_root):
                continue
            relative_parts = path.relative_to(component_root).parts
            if "tests" not in relative_parts:
                return [
                    f"{normalized}: test asset is stray in a production source directory; "
                    "place it under a contracted tests/ Suite"
                ]
            absolute = root / normalized
            contract = _nearest_suite_contract(
                absolute.parent,
                root / component_root.as_posix(),
                root,
                repository_assets,
            )
            if contract is None:
                return [f"{normalized}: component test has no owning suite.yaml"]

        for test_root in COMPONENT_TEST_ROOTS:
            if not _is_under(path, test_root):
                continue
            absolute = root / normalized
            contract = _nearest_suite_contract(
                absolute.parent,
                root / test_root.as_posix(),
                root,
                repository_assets,
            )
            if contract is None:
                return [f"{normalized}: component test has no owning suite.yaml"]

    if _is_formal_eval_member(path):
        absolute = root / normalized
        contract = _nearest_suite_contract(
            absolute.parent,
            root / "evals",
            root,
            repository_assets,
        )
        if contract is None:
            return [f"{normalized}: formal Eval asset has no owning suite.yaml"]
    return []


def _validate_removed_suite_contracts(
    root: Path,
    changed_paths: list[str],
    repository_assets: set[PurePosixPath] | None,
) -> list[str]:
    """Fail when a changed-path suite.yaml is gone but formal members remain unowned."""
    errors: list[str] = []
    for raw_path in changed_paths:
        path = PurePosixPath(_normalize_repo_path(raw_path))
        if path.name != "suite.yaml":
            continue
        if _repository_path_present(path, root, repository_assets):
            continue
        owner_dir = path.parent
        for member in _assets_under(owner_dir, root, repository_assets):
            if not _is_formal_owned_member(member):
                continue
            stop = _ownership_stop_for(member)
            contract = _nearest_suite_contract(
                root / Path(*member.parts[:-1]),
                root / stop.as_posix(),
                root,
                repository_assets,
            )
            if contract is None:
                errors.append(
                    f"{path.as_posix()}: removing Suite Contract leaves "
                    f"{owner_dir.as_posix()} formal test assets without a Git-effective owner"
                )
                break
    return errors


def validate_repository(root: Path, changed_paths: Iterable[str] = ()) -> list[str]:
    """Validate repository architecture with prospective drift protection.

    Historical Skill/Eval ownership debt and untouched incomplete Suite Contracts are
    treated as migration facts: they do not fail the run by themselves. New or changed
    Suite Contracts and changed formal test paths that still exist are checked
    strictly against the current Authority, including ``verdict_rule``.

    Formal scans and ownership lookups use the Git-effective asset view when available.
    """
    root = root.resolve()
    errors: list[str] = []
    suite_ids: dict[str, str] = {}
    repository_assets = _repository_asset_files(root)
    normalized_changed_paths = [
        _normalize_repo_path(path) for path in changed_paths if _normalize_repo_path(path)
    ]
    changed_set = set(normalized_changed_paths)

    for contract in _formal_paths(root, repository_assets, name="suite.yaml"):
        display = _relative(contract, root)
        require_complete = display in changed_set
        suite_id, contract_errors = validate_suite_contract(
            contract,
            root,
            repository_assets,
            require_complete_fields=require_complete,
        )
        errors.extend(contract_errors)
        if suite_id:
            if suite_id in suite_ids:
                errors.append(
                    f"{display}: duplicate suite_id {suite_id!r}; first declared by {suite_ids[suite_id]}"
                )
            else:
                suite_ids[suite_id] = display

    for authority_root_name in (PurePosixPath("verification/suites"), PurePosixPath("evals")):
        for candidate in _formal_paths(root, repository_assets, under=authority_root_name):
            if (
                candidate.name == "suite.yaml"
                or candidate.suffix.lower() not in {".yaml", ".yml", ".json"}
            ):
                continue
            try:
                text = candidate.read_text(encoding="utf-8")
                payload = json.loads(text) if candidate.suffix.lower() == ".json" else yaml.safe_load(text)
            except (OSError, UnicodeError, json.JSONDecodeError, yaml.YAMLError):
                continue
            if not isinstance(payload, dict) or not isinstance(payload.get("suite_id"), str):
                continue
            if any(key in payload for key in ("execution_profiles", "profiles", "specs", "tests")):
                errors.append(
                    f"{_relative(candidate, root)}: parallel Suite identity/member manifest is forbidden; "
                    "suite.yaml and the Suite directory are the only authorities"
                )

    verification_suites = root / "verification" / "suites"
    if verification_suites.is_dir():
        for suite_dir in sorted(path for path in verification_suites.iterdir() if path.is_dir()):
            suite_rel = PurePosixPath(_relative(suite_dir, root))
            if repository_assets is None:
                has_content = next(_walk_files(suite_dir), None) is not None
                has_contract = (suite_dir / "suite.yaml").is_file()
            else:
                has_content = any(_is_under(asset, suite_rel) for asset in repository_assets)
                has_contract = (suite_rel / "suite.yaml") in repository_assets
            if has_content and not has_contract:
                errors.append(f"{suite_rel.as_posix()}: formal Verification Suite has no suite.yaml")

    for changed_path in normalized_changed_paths:
        errors.extend(validate_changed_path(root, changed_path, repository_assets))
    errors.extend(
        _validate_removed_suite_contracts(root, normalized_changed_paths, repository_assets)
    )
    return errors


def _read_changed_paths(path: Path | None) -> list[str]:
    if path is None or not path.exists():
        return []
    # utf-8-sig strips a leading Windows UTF-8 BOM when present.
    return [
        _normalize_repo_path(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if _normalize_repo_path(line)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the OpenAIR test-asset architecture.")
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument(
        "--changed-paths-file",
        "--added-paths-file",
        dest="changed_paths_file",
        type=Path,
        help="newline-delimited CI changed paths; --added-paths-file is retained for compatibility",
    )
    args = parser.parse_args(argv)
    errors = validate_repository(args.repository, _read_changed_paths(args.changed_paths_file))
    if errors:
        print("Test-asset architecture guard failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Test-asset architecture guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
