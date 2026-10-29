#!/usr/bin/env python3
"""OpenAIR Skill 自动注册框架入口（仅服务于仓库根 ``skills/``）。

子命令与详细说明见 ``scripts/skill_registry/README.md``：

    python scripts/register_skills.py check
    python scripts/register_skills.py update
    python scripts/register_skills.py sync
"""

from __future__ import annotations

import sys
from pathlib import Path

# 允许直接 `python scripts/register_skills.py` 调用，而非作为包导入。
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from skill_registry.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
