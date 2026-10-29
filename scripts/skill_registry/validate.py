"""SkillEntry 校验。

约束与当前 ``skills/**/skill.yaml`` 及 ``skills/registry.yaml`` 保持兼容。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .definition_schema import validate_skill_yaml_field_types
from .scan import SkillEntry


# === 枚举值（来自 skill_registry_rules.md 第 6 节） ===
ALLOWED_STATUS = {"draft", "converting", "ready", "deprecated"}
ALLOWED_STAGES = {
    "problem_modeling",
    "solution_design",
    "execution_validation",
    "insight_generation",
}
ALLOWED_SKILL_TYPE = {"atomic", "workflow", "management", "orchestration"}
ALLOWED_SOURCE = {
    "manual",
    "old_openair",
    "opentrajectory",
    "generated",
    "external_reference",
}
ALLOWED_FIXTURE_STATUS = {
    "missing",
    "example_only",
    "recorded_run",
    "replay_passed",
    "none",  # 现仓库 registry 已使用 none，先纳入容忍
}
ALLOWED_RUNTIME = {
    "claude_code",
    "claude_agent_sdk",  # deprecated, 仅兼容旧记录
    "python_adapter",    # deterministic Python adapter runtime
    "llm_structured",    # structured-output LLM runtime with deterministic guards
}

# Claude Code skill name 命名规则
NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
DOUBLE_HYPHEN = re.compile(r"--")


@dataclass
class ValidationIssue:
    """单条问题。``severity`` 取 ``error`` 或 ``warning``。"""

    severity: str
    code: str
    message: str
    skill_path: str  # 相对仓库根的路径片段


def _err(entry: SkillEntry, code: str, message: str) -> ValidationIssue:
    return ValidationIssue(
        severity="error",
        code=code,
        message=message,
        skill_path=str(entry.path).replace("\\", "/"),
    )


def _warn(entry: SkillEntry, code: str, message: str) -> ValidationIssue:
    return ValidationIssue(
        severity="warning",
        code=code,
        message=message,
        skill_path=str(entry.path).replace("\\", "/"),
    )


def _check_name_format(name: str) -> str | None:
    """返回错误描述；合法返回 None。"""
    if not name:
        return "name 为空"
    if not NAME_PATTERN.match(name):
        return f"name '{name}' 不符合 [a-z0-9-] 规则（不允许大写、下划线、空格、中文）"
    if name.startswith("-") or name.endswith("-"):
        return f"name '{name}' 不能以 '-' 开头或结尾"
    if DOUBLE_HYPHEN.search(name):
        return f"name '{name}' 不允许连续 '--'"
    return None


def validate_entry(entry: SkillEntry) -> list[ValidationIssue]:
    """对单个 SkillEntry 跑全部规则。"""
    issues: list[ValidationIssue] = []

    # --- frontmatter ---
    fm = entry.frontmatter or {}
    fm_name = fm.get("name")
    fm_desc = fm.get("description")

    if not fm:
        issues.append(_err(entry, "FM_MISSING", "SKILL.md 缺少 YAML frontmatter"))
    if not isinstance(fm_name, str) or not fm_name:
        issues.append(_err(entry, "FM_NAME_EMPTY", "frontmatter.name 为空"))
    else:
        problem = _check_name_format(fm_name)
        if problem:
            issues.append(_err(entry, "FM_NAME_FORMAT", problem))
        if fm_name != entry.dir_name:
            issues.append(
                _err(
                    entry,
                    "NAME_DIR_MISMATCH",
                    f"frontmatter.name '{fm_name}' 与父目录名 '{entry.dir_name}' 不一致",
                )
            )

    if not isinstance(fm_desc, str) or not fm_desc.strip():
        issues.append(
            _err(entry, "FM_DESC_EMPTY", "frontmatter.description 为空")
        )
    elif len(fm_desc) > 1024:
        issues.append(
            _err(
                entry,
                "FM_DESC_TOO_LONG",
                f"frontmatter.description 长度 {len(fm_desc)} 超过 1024",
            )
        )

    # --- skill.yaml ---
    sy = entry.skill_yaml or {}
    if entry.skill_yaml_path is None:
        issues.append(_err(entry, "YAML_MISSING", "缺少 skill.yaml"))
        return issues  # 后续依赖 skill.yaml 的检查无法继续
    for message in validate_skill_yaml_field_types(sy):
        issues.append(_err(entry, "YAML_SCHEMA", message))

    expected_skill_path = f"skills/{entry.domain}/{entry.dir_name}"
    expected_claude_path = f".claude/skills/{entry.dir_name}"
    for key, expected in (
        ("domain", entry.domain),
        ("skill_path", expected_skill_path),
        ("claude_skill_path", expected_claude_path),
    ):
        declared = sy.get(key)
        if declared is not None and declared != expected:
            issues.append(
                _err(
                    entry,
                    "YAML_DERIVED_FIELD_MISMATCH",
                    f"skill.yaml.{key} must equal directory-derived value '{expected}'",
                )
            )

    # claude_name 一致性
    claude_name = sy.get("claude_name")
    if not isinstance(claude_name, str) or not claude_name:
        issues.append(_err(entry, "YAML_CLAUDE_NAME_EMPTY", "skill.yaml.claude_name 为空"))
    else:
        if isinstance(fm_name, str) and fm_name and claude_name != fm_name:
            issues.append(
                _err(
                    entry,
                    "CLAUDE_NAME_MISMATCH",
                    f"skill.yaml.claude_name '{claude_name}' 与 frontmatter.name '{fm_name}' 不一致",
                )
            )
        if claude_name != entry.dir_name:
            issues.append(
                _err(
                    entry,
                    "CLAUDE_NAME_DIR_MISMATCH",
                    f"skill.yaml.claude_name '{claude_name}' 与父目录名 '{entry.dir_name}' 不一致",
                )
            )

    # skill_id
    skill_id = sy.get("skill_id")
    if not isinstance(skill_id, str) or not skill_id:
        issues.append(_err(entry, "YAML_SKILL_ID_EMPTY", "skill.yaml.skill_id 为空"))
    elif not re.match(r"^[a-z0-9_]+$", skill_id):
        issues.append(
            _err(
                entry,
                "YAML_SKILL_ID_FORMAT",
                f"skill.yaml.skill_id '{skill_id}' 仅允许小写英文 / 数字 / 下划线",
            )
        )

    # runtime
    runtime = sy.get("runtime")
    if runtime not in ALLOWED_RUNTIME:
        issues.append(
            _err(
                entry,
                "YAML_RUNTIME",
                f"skill.yaml.runtime '{runtime}' 非法，应为 claude_code / python_adapter / llm_structured（claude_agent_sdk 仅兼容旧记录）",
            )
        )

    # pack / packs
    _skill_type = sy.get("skill_type")
    if _skill_type == "orchestration":
        pack = sy.get("pack")
        if not isinstance(pack, str) or not pack:
            issues.append(
                _err(entry, "YAML_PACK_MISSING", "skill.yaml.pack 为空（orchestration 类型必填）")
            )

    if _skill_type == "atomic":
        legacy_pack = sy.get("pack")
        packs = sy.get("packs")
        if isinstance(legacy_pack, str) and legacy_pack and packs is not None:
            issues.append(
                _err(
                    entry,
                    "YAML_PACK_PACKS_CONFLICT",
                    "L3 atomic 不可同时声明 pack 与 packs；请只保留 packs",
                )
            )
        elif isinstance(legacy_pack, str) and legacy_pack:
            issues.append(
                _warn(
                    entry,
                    "YAML_L3_LEGACY_PACK",
                    "L3 atomic 使用了 legacy pack 单数字段；请迁移为 packs: [<pack>]",
                )
            )
        elif packs is not None:
            if not isinstance(packs, list) or not packs:
                issues.append(
                    _err(entry, "YAML_PACKS_TYPE", "L3 atomic 的 skill.yaml.packs 必须为非空 list")
                )
            else:
                for item in packs:
                    if not isinstance(item, str) or not item:
                        issues.append(
                            _err(entry, "YAML_PACKS_ELEM", "skill.yaml.packs 元素必须为非空 string")
                        )
                        break

    # spec — orchestration 类型必填
    if _skill_type == "orchestration":
        spec = sy.get("spec")
        if not isinstance(spec, str) or not spec:
            issues.append(
                _err(entry, "YAML_SPEC_MISSING", "skill.yaml.spec 为空（orchestration 类型必填）")
            )

    # input_skills — 可选，若填则必须是 list
    input_skills = sy.get("input_skills")
    if input_skills is not None and not isinstance(input_skills, list):
        issues.append(
            _err(entry, "YAML_INPUT_SKILLS_TYPE", "skill.yaml.input_skills 必须为 list")
        )

    # sequence / enabled — orchestration 流程槽位可选元数据
    sequence = sy.get("sequence")
    if sequence is not None and (not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0):
        issues.append(
            _err(entry, "YAML_SEQUENCE_TYPE", "skill.yaml.sequence 必须为非负整数")
        )

    enabled = sy.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        issues.append(
            _err(entry, "YAML_ENABLED_TYPE", "skill.yaml.enabled 必须为 boolean")
        )

    status = sy.get("status")
    if status is not None and status not in ALLOWED_STATUS:
        issues.append(
            _err(entry, "YAML_STATUS", f"skill.yaml.status '{status}' 非法")
        )

    skill_type = sy.get("skill_type")
    if skill_type is not None and skill_type not in ALLOWED_SKILL_TYPE:
        issues.append(
            _err(entry, "YAML_SKILL_TYPE", f"skill.yaml.skill_type '{skill_type}' 非法")
        )

    fixture_status = sy.get("fixture_status")
    if fixture_status is not None and fixture_status not in ALLOWED_FIXTURE_STATUS:
        issues.append(
            _err(
                entry,
                "YAML_FIXTURE_STATUS",
                f"skill.yaml.fixture_status '{fixture_status}' 非法",
            )
        )

    source = sy.get("source")
    if source is not None and source not in ALLOWED_SOURCE:
        issues.append(
            _err(entry, "YAML_SOURCE", f"skill.yaml.source '{source}' 非法")
        )

    stages = sy.get("stages")
    if stages is not None:
        if not isinstance(stages, list) or not stages:
            issues.append(
                _err(entry, "YAML_STAGES_EMPTY", "skill.yaml.stages 应为非空列表")
            )
        else:
            for s in stages:
                if s not in ALLOWED_STAGES:
                    issues.append(
                        _err(entry, "YAML_STAGES", f"skill.yaml.stages 出现非法值 '{s}'")
                    )

    return issues


def validate_entries(entries: list[SkillEntry]) -> list[ValidationIssue]:
    """对一批条目跑校验，并附加 skill_id / claude_name 全局唯一检查。"""
    issues: list[ValidationIssue] = []
    for e in entries:
        issues.extend(validate_entry(e))

    # 全局唯一性
    skill_id_to_paths: dict[str, list[SkillEntry]] = {}
    claude_name_to_paths: dict[str, list[SkillEntry]] = {}
    for e in entries:
        sid = e.skill_yaml.get("skill_id") if isinstance(e.skill_yaml, dict) else None
        cname = e.skill_yaml.get("claude_name") if isinstance(e.skill_yaml, dict) else None
        if isinstance(sid, str) and sid:
            skill_id_to_paths.setdefault(sid, []).append(e)
        if isinstance(cname, str) and cname:
            claude_name_to_paths.setdefault(cname, []).append(e)

    for sid, group in skill_id_to_paths.items():
        if len(group) > 1:
            paths = ", ".join(str(g.path).replace("\\", "/") for g in group)
            for g in group:
                issues.append(
                    _err(
                        g,
                        "SKILL_ID_DUPLICATE",
                        f"skill_id '{sid}' 在多个 Skill 重复：{paths}",
                    )
                )
    for cname, group in claude_name_to_paths.items():
        if len(group) > 1:
            paths = ", ".join(str(g.path).replace("\\", "/") for g in group)
            for g in group:
                issues.append(
                    _err(
                        g,
                        "CLAUDE_NAME_DUPLICATE",
                        f"claude_name '{cname}' 在多个 Skill 重复：{paths}",
                    )
                )

    return issues
