#!/usr/bin/env python3
"""
将 docs/dev_skills/ 下的开发者 skill 同步安装到 .claude/、.cursor/、.trae/、.qoder/。

- 统一以 skill 形态安装（<tool>/skills/<name>/SKILL.md），不做格式转换。
- 不删除目标目录中已有的人工文件。
- 支持 --target claude|cursor|trae|qoder|all（默认 all）和 --dry-run。

运行方式：
python docs\dev_skills\sync_dev_skills.py
python docs\dev_skills\sync_dev_skills.py --dry-run
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

TARGETS = {
    "claude": ".claude/skills",
    "cursor": ".cursor/skills",
    "trae": ".trae/skills",
    "qoder": ".qoder/skills",
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def sync_skill(skill_dir: Path, dest_dir: Path, dry_run: bool) -> str:
    """将 skill 目录原样复制到目标路径。"""
    dest = dest_dir / skill_dir.name
    rel = dest.relative_to(repo_root()) if not dry_run else dest
    if dry_run:
        return f"[dry-run] would sync → {rel}"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(skill_dir, dest)
    return f"synced → {rel}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync dev_skills to .claude/ .cursor/ .trae/")
    parser.add_argument("--target", choices=["claude", "cursor", "trae", "qoder", "all"], default="all")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing files")
    args = parser.parse_args()

    root = repo_root()
    dev_skills = root / "docs" / "dev_skills"

    if not dev_skills.is_dir():
        print(f"error: missing directory {dev_skills}", file=sys.stderr)
        return 1

    targets = list(TARGETS.keys()) if args.target == "all" else [args.target]

    skill_dirs = sorted(
        d for d in dev_skills.iterdir()
        if d.is_dir() and not d.name.startswith(".") and (d / "SKILL.md").exists()
    )

    if not skill_dirs:
        print("warning: no skill directories with SKILL.md found under dev_skills/", file=sys.stderr)
        return 0

    for skill_dir in skill_dirs:
        print(f"\n# {skill_dir.name}")
        for t in targets:
            dest_dir = root / TARGETS[t]
            if not args.dry_run:
                dest_dir.mkdir(parents=True, exist_ok=True)
            msg = sync_skill(skill_dir, dest_dir, args.dry_run)
            print(f"  {t}: {msg}")

    print(f"\ndone: {len(skill_dirs)} skills × {len(targets)} targets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
