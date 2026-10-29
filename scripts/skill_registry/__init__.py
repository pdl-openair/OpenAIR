"""OpenAIR Skill 自动注册工具包。

仅服务于仓库根 ``skills/`` 下的 OpenAIR 科研 / 业务 Skill。
不扫描 ``docs/teamwork_skills/``、``docs/dev_skills/``、``.claude/`` 等目录。

详见 ``scripts/skill_registry/README.md``。
"""

from .scan import SkillEntry, scan_skills
from .validate import ValidationIssue, validate_entry, validate_entries
from .registry import (
    RegistryDiff,
    load_registry,
    diff_registry,
    build_registry_payload,
    write_registry,
    sync_pack_configs,
)

__all__ = [
    "SkillEntry",
    "scan_skills",
    "ValidationIssue",
    "validate_entry",
    "validate_entries",
    "RegistryDiff",
    "load_registry",
    "diff_registry",
    "build_registry_payload",
    "write_registry",
    "sync_pack_configs",
]
