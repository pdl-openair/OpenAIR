"""``register_skills.py`` 的子命令分发与控制台输出。

子命令：

- ``check``：扫描 + 校验 + 与 registry 比对，只读。问题非空时退出码非 0。
- ``update``：以扫描结果为准重写 ``skills/registry.yaml``。
- ``sync-pack-configs``：显式同步各 pack 的派生配置。
- ``sync``：转发到 ``scripts/sync_claude_skills.py``。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .registry import (
    REGISTRY_REL_PATH,
    build_registry_payload,
    diff_registry,
    load_registry,
    sync_pack_configs,
    write_registry,
)
from .migration_report import apply_metadata_patch_plan
from .scan import ScanResult, scan_skills
from .validate import ValidationIssue, validate_entries


def _repo_root() -> Path:
    """``scripts/skill_registry/cli.py`` → 仓库根。"""
    root = Path(__file__).resolve().parents[2]
    git_marker = root / ".git"
    if not git_marker.exists():
        raise RuntimeError(f"无法定位仓库根：{root} 下未找到 .git 标记")
    return root


def _print_scan_overview(result: ScanResult) -> None:
    print(f"扫描发现 {len(result.entries)} 个 Skill：")
    for e in result.entries:
        rel = str(e.path).replace("\\", "/")
        if "/skills/" in rel:
            rel = "skills" + rel.split("/skills", 1)[1]
        sid = ""
        if isinstance(e.skill_yaml, dict):
            sid = e.skill_yaml.get("skill_id") or ""
        print(f"  - [{e.domain}] {e.dir_name}  ({rel})  skill_id={sid or '<missing>'}")

    if result.empty_domains:
        print("以下域目录无任何 Skill（仅含 README 等占位文件）：")
        for d in result.empty_domains:
            print(f"  - skills/{d.name}/")


def _print_issues(issues: list[ValidationIssue], verbose: bool) -> tuple[int, int]:
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]

    if not issues:
        print("校验：未发现问题。")
        return 0, 0

    print(f"校验：发现 {len(errors)} 个错误，{len(warnings)} 个警告。")
    if verbose or errors:
        for issue in issues:
            tag = "ERROR" if issue.severity == "error" else "WARN "
            print(f"  [{tag}] {issue.skill_path} :: {issue.code} :: {issue.message}")
    else:
        print("（加 --verbose 查看每条详情）")
    return len(errors), len(warnings)


def _print_diff(diff) -> bool:
    """打印 registry 差异，返回是否存在差异。"""
    if not diff.has_changes:
        print("registry 比对：与扫描结果一致。")
        return False

    print("registry 比对：发现差异。")
    if diff.structural_warnings:
        print("  顶层结构问题：")
        for w in diff.structural_warnings:
            print(f"    - {w}")
    if diff.missing_in_registry:
        print("  缺登记（扫描有 / registry 无）：")
        for p in diff.missing_in_registry:
            print(f"    - {p}")
    if diff.extra_in_registry:
        print("  多登记（registry 有 / 扫描无）：")
        for s in diff.extra_in_registry:
            print(f"    - {s}")
    if diff.field_drift:
        print("  字段漂移：")
        for f in diff.field_drift:
            print(f"    - {f}")
    return True


# === 子命令实现 ===
def _cmd_check(args: argparse.Namespace) -> int:
    repo = _repo_root()
    result = scan_skills(repo)
    _print_scan_overview(result)
    print()

    issues = validate_entries(result.entries)
    error_count, _warn_count = _print_issues(issues, verbose=args.verbose)
    print()

    diff = diff_registry(repo, result.entries)
    has_diff = _print_diff(diff)

    # 退出码：发现错误或 registry 有差异即非 0
    return 0 if (error_count == 0 and not has_diff) else 1


def _cmd_update(args: argparse.Namespace) -> int:
    repo = _repo_root()
    result = scan_skills(repo)

    issues = validate_entries(result.entries)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        print(f"update 已中止：扫描结果存在 {len(errors)} 个错误。")
        print("先解决错误。可用 'check --verbose' 查看详情。")
        return 1

    payload = build_registry_payload(repo, result.entries)
    path = write_registry(repo, payload)
    print(f"已写入 {path}")
    print(f"包含 {len(payload['skills'])} 条 Skill 登记。")

    return 0


def _cmd_sync_pack_configs(args: argparse.Namespace) -> int:
    repo = _repo_root()
    result = scan_skills(repo)
    errors = [issue for issue in validate_entries(result.entries) if issue.severity == "error"]
    if errors:
        print(f"sync-pack-configs 已中止：扫描结果存在 {len(errors)} 个错误。")
        return 1
    pack_paths = sync_pack_configs(repo, result.entries)
    for p in pack_paths:
        print(f"已同步 {p}")
    return 0


def _cmd_sync(args: argparse.Namespace) -> int:
    repo = _repo_root()
    target_script = repo / "scripts" / "sync_claude_skills.py"
    if not target_script.is_file():
        print(f"error: 未找到 {target_script}", file=sys.stderr)
        return 2
    print(f"转发到 {target_script}")
    completed = subprocess.run(
        [sys.executable, str(target_script)],
        cwd=str(repo),
    )
    return completed.returncode


def _cmd_migration_apply_metadata(args: argparse.Namespace) -> int:
    repo = _repo_root()
    result = scan_skills(repo)
    outputs = apply_metadata_patch_plan(repo, result.entries, dry_run=args.dry_run)
    action = "would update" if args.dry_run else "updated"
    print(f"{action} {len(outputs.changed_paths)} skill.yaml file(s)")
    print(f"left unchanged {len(outputs.skipped_rows)} manual-review or already-aligned item(s)")
    if args.verbose:
        for path in outputs.changed_paths:
            print(f"  - {path.relative_to(repo)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="register_skills.py",
        description="OpenAIR Skill 自动注册框架（仅服务于 skills/）。",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser(
        "check", help="扫描 skills/ + 校验 + 与 registry 比对（只读）。"
    )
    p_check.add_argument("--verbose", action="store_true", help="输出每条问题详情。")
    p_check.set_defaults(func=_cmd_check)

    p_update = sub.add_parser(
        "update", help="按正式 SkillDefinition 原子重写 skills/registry.yaml。"
    )
    p_update.set_defaults(func=_cmd_update)

    p_pack = sub.add_parser(
        "sync-pack-configs",
        help="显式同步由 SkillDefinition 派生的 pack 配置；不更新 registry。",
    )
    p_pack.set_defaults(func=_cmd_sync_pack_configs)

    p_sync = sub.add_parser(
        "sync", help="转发到 scripts/sync_claude_skills.py。"
    )
    p_sync.set_defaults(func=_cmd_sync)

    p_apply_metadata = sub.add_parser(
        "migration-apply-metadata",
        help="Apply high-confidence stage/substage metadata fields to skill.yaml; skips manual items.",
    )
    p_apply_metadata.add_argument("--dry-run", action="store_true", help="Only print the number of files that would change.")
    p_apply_metadata.add_argument("--verbose", action="store_true", help="Print changed file paths.")
    p_apply_metadata.set_defaults(func=_cmd_migration_apply_metadata)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:  # pragma: no cover - argparse 已强制 required
        parser.print_help()
        return 2
    # 检查 registry 文件路径在 update 模式下提示
    if args.cmd == "update":
        repo = _repo_root()
        path = repo / REGISTRY_REL_PATH
        if not path.parent.is_dir():
            print(f"error: 缺少目录 {path.parent}", file=sys.stderr)
            return 2
        # 先用 load_registry 给一个轻量提示（不影响流程）
        _meta, _existing, warnings = load_registry(repo)
        for w in warnings:
            print(f"warning: {w}")
    return int(func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
