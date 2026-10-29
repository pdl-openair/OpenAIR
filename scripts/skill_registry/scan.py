"""扫描 ``skills/<domain>/<name>/`` 两层结构，返回 SkillEntry 列表。

仅识别 ``skills/`` 下的 OpenAIR Skill；不纳入 ``skills/_template/``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from ._yaml import yaml_safe_load


@dataclass
class SkillEntry:
    """扫描到的单个 Skill 目录。

    字段保持原始读取值，校验工作交给 ``validate.py``。
    """

    path: Path                       # skills/<domain>/<name>
    domain: str                      # 父目录名（即 <domain>）
    dir_name: str                    # 自身目录名（即 <name>，期望等于 frontmatter.name）
    skill_md: Path                   # SKILL.md 绝对路径
    skill_yaml_path: Path | None     # skill.yaml 绝对路径或 None
    frontmatter: dict = field(default_factory=dict)
    skill_yaml: dict = field(default_factory=dict)
    raw_skill_md: str = ""

    @property
    def rel_path(self) -> Path:
        """相对仓库根的 POSIX 风格路径片段（用于报告输出）。"""
        return self.path


@dataclass
class DomainStat:
    """域目录统计。"""

    name: str
    path: Path
    skill_count: int


@dataclass
class ScanResult:
    entries: list[SkillEntry]
    empty_domains: list[DomainStat]   # 仅含 README 等占位文件、无任何 Skill 子目录


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """从 SKILL.md 文本切出 YAML frontmatter 与正文。

    缺失 frontmatter 时返回空 dict 与原文。
    """
    if not text.startswith("---"):
        return {}, text
    # 在第一行 --- 之后查找下一处 ---（行首）
    lines = text.splitlines()
    end_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx == -1:
        return {}, text
    fm_text = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1 :])
    try:
        parsed = yaml_safe_load(fm_text) or {}
    except Exception as exc:
        import sys
        print(f"warning: SKILL.md frontmatter YAML 解析失败: {exc}", file=sys.stderr)
        return {}, body
    if not isinstance(parsed, dict):
        return {}, body
    return parsed, body


def _iter_skill_dirs(skills_root: Path) -> Iterator[tuple[str, Path]]:
    """枚举 ``skills/<domain>/<name>/`` 候选目录。

    返回 ``(domain, skill_dir)``。会过滤无 SKILL.md 的目录。
    """
    for domain_dir in sorted(p for p in skills_root.iterdir() if p.is_dir()):
        if domain_dir.name.startswith("."):
            continue
        # _template 不是可注册 Skill 域，始终跳过
        if domain_dir.name == "_template":
            continue
        for child in sorted(p for p in domain_dir.iterdir() if p.is_dir()):
            if child.name.startswith("."):
                continue
            if (child / "SKILL.md").is_file():
                yield domain_dir.name, child


def _build_entry(domain: str, skill_dir: Path) -> SkillEntry:
    skill_md = skill_dir / "SKILL.md"
    raw = skill_md.read_text(encoding="utf-8")
    frontmatter, _body = _split_frontmatter(raw)

    skill_yaml_path: Path | None = skill_dir / "skill.yaml"
    skill_yaml: dict = {}
    if skill_yaml_path.is_file():
        loaded = yaml_safe_load(skill_yaml_path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            skill_yaml = loaded
    else:
        skill_yaml_path = None

    return SkillEntry(
        path=skill_dir,
        domain=domain,
        dir_name=skill_dir.name,
        skill_md=skill_md,
        skill_yaml_path=skill_yaml_path,
        frontmatter=frontmatter,
        skill_yaml=skill_yaml,
        raw_skill_md=raw,
    )


def scan_skills(
    repo_root: Path, *, include_template: bool = False
) -> ScanResult:
    """扫描 ``<repo_root>/skills/``。

    ``include_template`` 参数保留仅为兼容旧调用，当前实现会忽略该参数。
    """
    _ = include_template
    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return ScanResult(entries=[], empty_domains=[])

    entries: list[SkillEntry] = []
    for domain, skill_dir in _iter_skill_dirs(skills_root):
        entries.append(_build_entry(domain=domain, skill_dir=skill_dir))

    # 统计无 Skill 的域目录（只含 README.md 之类占位文件）
    empty_domains: list[DomainStat] = []
    for domain_dir in sorted(p for p in skills_root.iterdir() if p.is_dir()):
        if domain_dir.name.startswith(".") or domain_dir.name == "_template":
            continue
        skill_count = sum(1 for e in entries if e.domain == domain_dir.name)
        if skill_count == 0:
            empty_domains.append(
                DomainStat(name=domain_dir.name, path=domain_dir, skill_count=0)
            )

    return ScanResult(
        entries=entries,
        empty_domains=empty_domains,
    )
