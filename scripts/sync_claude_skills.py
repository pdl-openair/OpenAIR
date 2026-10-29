#!/usr/bin/env python3
"""将标准 Skill 目录同步到各 agent 的镜像目录。

同步源仅识别：
    skills/<domain>/<skill-name>/SKILL.md

同步目标（同一份内容写两处，2026-10-27 起）：
- ``.claude/skills/``  —— [CC] 与 zcode 读取
- ``.agents/skills/``  —— DSH、zcode、[CC] 均可读取的跨客户端标准位置

行为约束：
- 仅文件拷贝，不调用 Claude，不做校验与代码生成；
- 不删除目标目录下的 ``README.md``（人工说明）；
- 目标路径不保留 domain（``skills/general/foo`` -> ``<out>/foo``）；
- 非标准结构（如多级嵌套、``skills/_template``）不会被同步。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _iter_standard_skill_dirs(skills_root: Path):
    """枚举标准 Skill 目录：``skills/<domain>/<skill-name>/``。"""
    for domain_dir in sorted(p for p in skills_root.iterdir() if p.is_dir()):
        if domain_dir.name.startswith(".") or domain_dir.name == "_template":
            continue
        for skill_dir in sorted(p for p in domain_dir.iterdir() if p.is_dir()):
            if skill_dir.name.startswith("."):
                continue
            if (skill_dir / "SKILL.md").is_file():
                yield domain_dir.name, skill_dir


def main() -> int:
    root = repo_root()
    skills = root / "skills"
    outs = [root / ".claude" / "skills", root / ".agents" / "skills"]

    if not skills.is_dir():
        print(f"error: missing directory {skills}", file=sys.stderr)
        return 1

    for out in outs:
        out.mkdir(parents=True, exist_ok=True)

    candidates = list(_iter_standard_skill_dirs(skills))
    # .claude/skills/ 以 skill-name 作为目录名；若跨 domain 重名，必须人工先消歧。
    by_name: dict[str, list[str]] = {}
    for domain, skill_dir in candidates:
        rel = f"skills/{domain}/{skill_dir.name}"
        by_name.setdefault(skill_dir.name, []).append(rel)
    duplicates = {name: rels for name, rels in by_name.items() if len(rels) > 1}
    if duplicates:
        print("error: 检测到跨 domain 同名 Skill，停止同步：", file=sys.stderr)
        for name, rels in sorted(duplicates.items()):
            print(f"  - {name}: {', '.join(sorted(rels))}", file=sys.stderr)
        print("请先确保 skill-name 全局唯一，再执行同步。", file=sys.stderr)
        return 1

    synced: list[str] = []
    for domain, skill_dir in candidates:
        rel = f"skills/{domain}/{skill_dir.name}"
        for out in outs:
            dest = out / skill_dir.name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(skill_dir, dest)
        synced.append(rel)

    if not synced:
        print("warning: no skill directories with SKILL.md found under skills/", file=sys.stderr)
    else:
        print("synced:", ", ".join(synced))
        print("targets:", ", ".join(str(o.relative_to(root)) for o in outs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
