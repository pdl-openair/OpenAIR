"""Sync skills from source tree to .claude/skills/ layout (Docker build helper)."""
import shutil
from pathlib import Path

src = Path("/app/_skills_src")
dst = Path("/app/.claude/skills")
dst.mkdir(parents=True, exist_ok=True)

count = 0
for domain in sorted(src.iterdir()):
    if not domain.is_dir() or domain.name.startswith(("_", ".")):
        continue
    for skill_dir in sorted(domain.iterdir()):
        if not skill_dir.is_dir():
            continue
        if not (skill_dir / "SKILL.md").is_file():
            continue
        target = dst / skill_dir.name
        if target.exists():
            continue
        shutil.copytree(skill_dir, target)
        count += 1

print(f"Synced {count} system skills to /app/.claude/skills/")
