"""比对 / 生成 ``skills/registry.yaml``。

- ``check`` 模式：读现有 registry，与扫描结果比对，输出差异，**不写文件**。
- ``update`` 模式：以扫描结果为准重写 registry，按 skill_id 合并保留人工字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .definition_schema import (
    PROJECTION_DEFAULTS,
    REGISTRY_FIELD_ORDER,
    REGISTRY_VERSION,
)
from .scan import SkillEntry
from .stage_model import RESEARCH_STAGE_META
from ._yaml import yaml_safe_dump, yaml_safe_load

SUBSTAGE_SKILL_MAP_REL_PATH = "schemas/sdr/substage-skill-map.yaml"


REGISTRY_REL_PATH = "skills/registry.yaml"

# registry 中每条 Skill 的字段输出顺序，对齐 skill_registry_rules.md 第 3 节示例
FIELD_ORDER = list(REGISTRY_FIELD_ORDER)


@dataclass
class RegistryDiff:
    """check 模式输出。"""

    missing_in_registry: list[str] = field(default_factory=list)   # 扫描有 / registry 无 → skill_path 列表
    extra_in_registry: list[str] = field(default_factory=list)     # registry 有 / 扫描无 → skill_id 列表
    field_drift: list[str] = field(default_factory=list)           # 字段漂移描述
    structural_warnings: list[str] = field(default_factory=list)   # 顶层结构问题（如使用 'registry:' 而非 'skills:'）

    @property
    def has_changes(self) -> bool:
        return bool(
            self.missing_in_registry
            or self.extra_in_registry
            or self.field_drift
            or self.structural_warnings
        )


def load_registry(repo_root: Path) -> tuple[dict, list[dict], list[str]]:
    """读取现有 registry。

    返回 ``(meta, skills_list, structural_warnings)``。

    - ``meta``：除 ``skills`` 外的顶层字段（用于 update 时尽量保留 ``version``）。
    - ``skills_list``：当前登记的 Skill 列表（list[dict]）。容忍历史顶层 key 'registry'。
    - ``structural_warnings``：顶层结构与规范不符的提示。
    """
    path = repo_root / REGISTRY_REL_PATH
    if not path.is_file():
        return {}, [], [f"{REGISTRY_REL_PATH} 不存在"]

    raw = path.read_text(encoding="utf-8")
    data = yaml_safe_load(raw) or {}
    if not isinstance(data, dict):
        return {}, [], [f"{REGISTRY_REL_PATH} 顶层不是 mapping"]

    warnings: list[str] = []
    skills_list: list[dict] = []

    if "skills" in data and isinstance(data["skills"], list):
        skills_list = [s for s in data["skills"] if isinstance(s, dict)]
    elif "registry" in data and isinstance(data["registry"], list):
        warnings.append(
            f"{REGISTRY_REL_PATH} 顶层使用 'registry:'，应为 'skills:'（见 skill_registry_rules.md 第 3 节）"
        )
        skills_list = [s for s in data["registry"] if isinstance(s, dict)]
    else:
        warnings.append(f"{REGISTRY_REL_PATH} 缺少 'skills:' 顶层列表")

    meta = {k: v for k, v in data.items() if k not in {"skills", "registry"}}
    return meta, skills_list, warnings


def _derived_skill_path(entry: SkillEntry) -> str:
    return f"skills/{entry.domain}/{entry.dir_name}"


def project_skill_definition(entry: SkillEntry) -> dict:
    """将扫描到的 SkillEntry 转换为 registry 行记录。

    优先采用 ``skill.yaml`` 的字段；缺失字段填默认占位（owner=TBD 等），
    与 ``skill_registry_rules.md`` 第 3 节示例对齐。
    """
    sy = entry.skill_yaml or {}
    record: dict = dict(PROJECTION_DEFAULTS)
    for key in REGISTRY_FIELD_ORDER:
        if key in sy:
            record[key] = sy[key]
    record["skill_id"] = sy.get("skill_id", "")
    record["claude_name"] = sy.get("claude_name", entry.dir_name)
    record["display_name"] = sy.get("display_name") or entry.dir_name
    record["domain"] = entry.domain
    record["skill_path"] = _derived_skill_path(entry)
    record["claude_skill_path"] = f".claude/skills/{entry.dir_name}"
    return record


_entry_to_record = project_skill_definition


def _ordered(record: dict) -> dict:
    """按 FIELD_ORDER 重排键，未列出的字段追加到末尾。"""
    out: dict = {}
    for k in FIELD_ORDER:
        if k in record:
            out[k] = record[k]
    for k, v in record.items():
        if k not in out:
            out[k] = v
    return out


def _is_current_registration(entry: SkillEntry) -> bool:
    """Keep deprecated Skill definitions as source history, not current candidates."""
    return (entry.skill_yaml or {}).get("status") != "deprecated"


def diff_registry(
    repo_root: Path, scanned: list[SkillEntry]
) -> RegistryDiff:
    """计算 ``check`` 输出。"""
    current_meta, current, struct_warnings = load_registry(repo_root)
    diff = RegistryDiff(structural_warnings=list(struct_warnings))

    current_scanned_by_id: dict[str, SkillEntry] = {}
    for e in scanned:
        sid = e.skill_yaml.get("skill_id") if isinstance(e.skill_yaml, dict) else None
        if isinstance(sid, str) and sid:
            if _is_current_registration(e):
                current_scanned_by_id[sid] = e

    current_by_id: dict[str, dict] = {}
    for rec in current:
        sid = rec.get("skill_id")
        if isinstance(sid, str) and sid:
            current_by_id[sid] = rec

    # 扫描有 / registry 无
    for e in scanned:
        if not _is_current_registration(e):
            continue
        sid = e.skill_yaml.get("skill_id") if isinstance(e.skill_yaml, dict) else None
        if not isinstance(sid, str) or not sid:
            # 没有合法 skill_id 的 Skill：单独提示，无法对账
            diff.field_drift.append(
                f"{e.path}: 缺少 skill.yaml.skill_id，无法登记到 registry"
            )
            continue
        if sid not in current_by_id:
            diff.missing_in_registry.append(str(e.path).replace("\\", "/"))

    # registry 有 / 扫描无
    for sid, rec in current_by_id.items():
        if sid not in current_scanned_by_id:
            # draft 占位：允许先登记 skill_id，目录由后续 Issue 创建（见 skill_registry_rules.md）
            if rec.get("status") == "draft":
                skill_path = rec.get("skill_path")
                if isinstance(skill_path, str) and skill_path:
                    if not (repo_root / skill_path).is_dir():
                        continue
            skill_path = rec.get("skill_path", "<unknown>")
            diff.extra_in_registry.append(f"{sid}（登记 skill_path={skill_path}）")

    generated_payload = build_registry_payload(repo_root, scanned)
    expected_meta = {key: value for key, value in generated_payload.items() if key != "skills"}
    for key in sorted(set(current_meta) | set(expected_meta)):
        if current_meta.get(key) != expected_meta.get(key) or (key in current_meta) != (key in expected_meta):
            diff.field_drift.append(
                f"registry: field '{key}' registry='{current_meta.get(key)}' actual='{expected_meta.get(key)}'"
            )

    for sid, e in current_scanned_by_id.items():
        rec = current_by_id.get(sid)
        if rec is None:
            continue
        scanned_record = project_skill_definition(e)
        for k in sorted(set(rec) | set(scanned_record)):
            cur_present = k in rec
            new_present = k in scanned_record
            cur_v = rec.get(k)
            new_v = scanned_record.get(k)
            if cur_v != new_v or cur_present != new_present:
                diff.field_drift.append(
                    f"{sid}: 字段 '{k}' registry='{cur_v}' 实际='{new_v}'"
                )

    return diff


def build_registry_payload(
    repo_root: Path,
    scanned: list[SkillEntry],
) -> dict:
    """构建 ``update`` 模式要写入的完整 registry 数据。

    - 顶层固定为 ``version / updated_at / skills``。
    Registry rows are derived only from Skill definitions. Existing Registry
    rows are never an input authority.
    """
    skills_out: list[dict] = []
    for e in scanned:
        if not _is_current_registration(e):
            continue
        rec = project_skill_definition(e)
        sid = rec.get("skill_id")
        if not isinstance(sid, str) or not sid:
            # 没有 skill_id 的条目不写入 registry
            continue
        skills_out.append(_ordered(rec))

    payload: dict = {}
    payload["version"] = REGISTRY_VERSION
    payload["skills"] = skills_out
    return payload


def write_registry(repo_root: Path, payload: dict) -> Path:
    """将 payload 写入 ``skills/registry.yaml``，返回写入路径。"""
    path = repo_root / REGISTRY_REL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml_safe_dump(payload), encoding="utf-8")
    return path


def sync_substage_skill_map(repo_root: Path, scanned: list[SkillEntry]) -> Path | None:  # deprecated
    """从扫描结果中的 substages 字段反向生成 ``schemas/sdr/substage-skill-map.yaml``。

    每个 skill.yaml 中声明的 ``substages: [key, ...]`` 被用于建立
    substage → {skill_id, display_name, description} 的映射。
    skill_type=orchestration 的 L2 skill 不写入此映射。
    """
    mapping: dict[str, list[tuple[int, str, dict]]] = {}
    for e in scanned:
        if not _is_current_registration(e):
            continue
        sy = e.skill_yaml or {}
        skill_id = sy.get("skill_id")
        if not isinstance(skill_id, str) or not skill_id:
            continue
        if sy.get("skill_type") == "orchestration":
            continue
        substages = sy.get("substages")
        if not isinstance(substages, list):
            continue
        skill_info: dict = {"skill_id": skill_id}
        if sy.get("display_name"):
            skill_info["display_name"] = sy["display_name"]
        # Use SKILL.md frontmatter description (already required by validation FM_DESC_EMPTY)
        desc = e.frontmatter.get("description")
        if isinstance(desc, str) and desc.strip():
            skill_info["description"] = desc.strip().split("\n")[0]
        for sub_key in substages:
            if isinstance(sub_key, str) and sub_key:
                mapping.setdefault(sub_key, []).append(skill_info)

    if not mapping:
        return None

    ordered: dict[str, list[dict]] = {}
    for k in sorted(mapping):
        ordered[k] = mapping[k]

    path = repo_root / SUBSTAGE_SKILL_MAP_REL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml_safe_dump(ordered), encoding="utf-8")
    return path


# ── pack 配置自动生成 ──────────────────────────────────────────────

_LEGACY_STAGE_META_UNUSED: dict[str, dict[str, str]] = {
    "problem_modeling": {
        "label": "问题建模",
        "description": "识别研究目标、定义问题边界、形成可验证假说、确定约束与成功标准。",
    },
    "solution_design": {
        "label": "方案设计",
        "description": "选择研究方法、定义变量、规划数据策略和分析计划、设计基线与消融实验、拆解任务。",
    },
    "execution_validation": {
        "label": "执行验证",
        "description": "执行实验或计算、提取证据、统计分析、验证假设、凝练发现、构建论点。",
    },
    "insight_generation": {
        "label": "洞察生成",
        "description": "提炼后续研究方向、更新假说、生成报告和图表。",
    },
}
"""stage 级元数据。所有 pack 共用。"""

STAGE_META = RESEARCH_STAGE_META

_AUTOGEN_HEADER = (
    "# AUTO-GENERATED by register_skills.py -- do not edit manually.\n"
    "# Source: skill.yaml files under skills/\n\n"
)

_SKILL_ID_TO_SUBSTAGE_HEADER = (
    "# 反向索引：L2 skill_id/claude_name → substage_key。运行时由 _resolve_substage 消费，将前端传入的 skill_id 解析到对应 substage。\n\n"
)

_SUBSTAGE_SKILL_HEADER = (
    "# 正向索引：substage_key → 可用 L3 skill_id 列表。运行时由 L2 编排 skill + 消费。\n\n"
)


def _detect_packs(scanned: list[SkillEntry]) -> list[str]:
    """从 L2 skill.yaml 的 pack 字段收集去重 pack key 列表。"""
    packs: list[str] = []
    seen: set[str] = set()
    for e in scanned:
        sy = e.skill_yaml or {}
        if sy.get("skill_type") == "orchestration":
            pack = sy.get("pack")
            if isinstance(pack, str) and pack and pack not in seen:
                packs.append(pack)
                seen.add(pack)
    return packs


def _l2_skills_for_pack(scanned: list[SkillEntry], pack: str) -> list[SkillEntry]:
    """筛选属于指定 pack 的 L2 orchestration skill。"""
    return [
        e for e in scanned
        if (e.skill_yaml or {}).get("skill_type") == "orchestration"
        and (e.skill_yaml or {}).get("pack") == pack
    ]


def _pack_substage_keys(scanned: list[SkillEntry], pack: str) -> set[str]:
    """收集指定 pack 下所有 L2 substages 的 key 集合。"""
    keys: set[str] = set()
    for e in _l2_skills_for_pack(scanned, pack):
        sy = e.skill_yaml or {}
        if sy.get("enabled") is False:
            continue
        for k in sy.get("substages") or []:
            if isinstance(k, str):
                keys.add(k)
    return keys


def _build_stage_substage_map(scanned: list[SkillEntry], pack: str) -> dict:
    """从 L2 skill.yaml 生成 stage-substage-map。

    ``sequence`` 仅用于同一 pack 内的稳定流程排序；未填写时保持原有目录扫描顺序。
    ``enabled: false`` 用于保留尚未开放执行的流程槽位。
    """
    l2_list = sorted(
        _l2_skills_for_pack(scanned, pack),
        key=lambda entry: (
            (entry.skill_yaml or {}).get("sequence", 10**9),
            str(entry.path),
        ),
    )

    # 按 stage 分组，保持 sequence / 目录扫描顺序
    stage_groups: dict[str, list[SkillEntry]] = {}
    for e in l2_list:
        sy = e.skill_yaml or {}
        for stage in sy.get("stages") or []:
            stage_groups.setdefault(stage, []).append(e)

    result: dict[str, dict] = {}
    for stage_key in STAGE_META:
        entries = stage_groups.get(stage_key, [])
        if not entries:
            continue
        meta = STAGE_META[stage_key]
        substages = []
        for e in entries:
            sy = e.skill_yaml or {}
            sub_key = (sy.get("substages") or [None])[0]
            if not sub_key:
                continue
            row = {
                "key": sub_key,
                "label": sy.get("display_name", ""),
                "description": (e.frontmatter.get("description") or "").strip(),
            }
            if sy.get("enabled") is False:
                row["enabled"] = False
            substages.append(row)
        result[stage_key] = {
            "label": meta["label"],
            "description": meta["description"],
            "substages": substages,
        }
    return result


def _resolve_l3_packs(sy: dict) -> list[str] | None:
    """Resolve explicit L3 pack membership.

    Returns ``None`` when the skill declares no ``packs`` / ``pack`` field, so callers
    can apply legacy generic-L3 fallback. Do not default to ``[sdr]`` here.
    """
    packs = sy.get("packs")
    if isinstance(packs, list):
        resolved = [p for p in packs if isinstance(p, str) and p]
        return resolved or None
    if isinstance(packs, str) and packs.strip():
        return [packs.strip()]
    pack = sy.get("pack")
    if isinstance(pack, str) and pack.strip():
        return [pack.strip()]
    return None


def _build_substage_skill_map(scanned: list[SkillEntry], pack: str, pack_substage_keys: set[str]) -> dict:
    """从 L3 skill.yaml 生成 substage-skill-map。

    Pack 专属 L3 优先覆盖同一 substage 下未声明 pack 的通用 L3；
    若某个 substage 没有 pack 专属实现，则仍允许回退复用通用 L3。
    """
    explicit_pack_substages: set[str] = set()
    for e in scanned:
        if not _is_current_registration(e):
            continue
        sy = e.skill_yaml or {}
        if sy.get("skill_type") == "orchestration":
            continue
        resolved = _resolve_l3_packs(sy)
        if resolved is None or pack not in resolved:
            continue
        for sub_key in sy.get("substages") or []:
            if isinstance(sub_key, str) and sub_key:
                explicit_pack_substages.add(sub_key)

    mapping: dict[str, list[dict]] = {}
    for e in scanned:
        if not _is_current_registration(e):
            continue
        sy = e.skill_yaml or {}
        if sy.get("skill_type") == "orchestration":
            continue
        skill_packs = _resolve_l3_packs(sy)
        if skill_packs is not None:
            if pack not in skill_packs:
                continue
        skill_id = sy.get("skill_id")
        if not isinstance(skill_id, str) or not skill_id:
            continue
        substages = sy.get("substages")
        if not isinstance(substages, list):
            continue
        skill_info: dict = {"skill_id": skill_id}
        if sy.get("display_name"):
            skill_info["display_name"] = sy["display_name"]
        desc = e.frontmatter.get("description")
        if isinstance(desc, str) and desc.strip():
            skill_info["description"] = desc.strip()
        for sub_key in substages:
            if not isinstance(sub_key, str) or not sub_key or sub_key not in pack_substage_keys:
                continue
            if skill_packs is None and sub_key in explicit_pack_substages:
                continue
            sequence = sy.get("sequence", 10**9)
            mapping.setdefault(sub_key, []).append((sequence, str(e.path), dict(skill_info)))

    # substage key 稳定排序；同一节点内的 L3 按 sequence / 路径稳定排序。
    return {
        k: [row for _sequence, _path, row in sorted(mapping[k], key=lambda item: (item[0], item[1]))]
        for k in sorted(mapping)
    }


def _build_substage_spec_map(scanned: list[SkillEntry], pack: str) -> dict[str, str]:
    """从 L2 skill.yaml 的 spec 字段生成 substage-spec-map。"""
    spec_map: dict[str, str] = {}
    for e in _l2_skills_for_pack(scanned, pack):
        sy = e.skill_yaml or {}
        spec = sy.get("spec")
        if not isinstance(spec, str) or not spec:
            continue
        sub_key = (sy.get("substages") or [None])[0]
        if sub_key:
            spec_map[sub_key] = spec
    return spec_map


def _build_skill_id_to_substage(scanned: list[SkillEntry], pack: str) -> dict[str, str]:
    """从 skill.yaml 构建 skill_id/claude_name → substage_key 的双向映射。"""
    mapping: dict[str, str] = {}
    for e in scanned:
        if not _is_current_registration(e):
            continue
        sy = e.skill_yaml or {}
        if sy.get("pack") != pack:
            continue
        substages = sy.get("substages")
        if not isinstance(substages, list) or not substages:
            continue
        sub_key = substages[0]
        skill_id = sy.get("skill_id")
        if isinstance(skill_id, str) and skill_id:
            mapping.setdefault(skill_id, sub_key)
        claude_name = sy.get("claude_name")
        if isinstance(claude_name, str) and claude_name:
            mapping.setdefault(claude_name, sub_key)
    return dict(sorted(mapping.items()))


def _existing_map_rows(path: Path) -> dict | None:
    """读取现有生成文件；不存在或解析失败时返回 None（安全回退为全量重写）。"""
    if not path.is_file():
        return None
    try:
        rows = yaml_safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return rows if isinstance(rows, dict) else None


def _preserve_hand_substages(
    generated: dict, existing: dict | None
) -> tuple[dict, list[str]]:
    """把无 L2 skill 源的手工 substage 条目从现有 stage-substage-map 合并进生成结果。

    仅保留生成结果中**不存在**的 key（held composite definition 等）；
    生成结果中已有的 key 一律以扫描结果为准，保证 L2 skill.yaml 的
    启用/禁用、描述变更始终生效。
    """
    if not existing:
        return generated, []
    preserved: list[str] = []
    for stage_key, stage_row in existing.items():
        if not isinstance(stage_row, dict):
            continue
        gen_stage = generated.get(stage_key)
        if not isinstance(gen_stage, dict):
            gen_stage = {
                "label": stage_row.get("label", ""),
                "description": stage_row.get("description", ""),
                "substages": [],
            }
            generated[stage_key] = gen_stage
        gen_keys = {
            row.get("key") for row in gen_stage.get("substages", []) if isinstance(row, dict)
        }
        for row in stage_row.get("substages") or []:
            if isinstance(row, dict) and row.get("key") not in gen_keys:
                gen_stage.setdefault("substages", []).append(row)
                preserved.append(f"{stage_key}:{row.get('key')}")
    return generated, preserved


def _preserve_hand_spec_refs(
    generated: dict, existing: dict | None
) -> tuple[dict, list[str]]:
    """把无 L2 skill 源 substage 的 spec 引用从现有 substage-spec-map 合并进生成结果。"""
    if not existing:
        return generated, []
    preserved = [
        key for key, ref in existing.items()
        if key not in generated and isinstance(ref, str) and ref
    ]
    for key in preserved:
        generated[key] = existing[key]
    return generated, preserved


def sync_pack_configs(repo_root: Path, scanned: list[SkillEntry]) -> list[Path]:
    """为每个 pack 生成 stage-substage-map、substage-skill-map、substage-spec-map、skill-id-to-substage。

    stage-substage-map 与 substage-spec-map 会保留现有文件中无 L2 skill 源的
    手工条目（如 held composite definition）；其余文件以扫描结果全量重写。

    Returns:
        写入的文件路径列表。
    """
    packs = _detect_packs(scanned)
    written: list[Path] = []

    for pack in packs:
        pack_dir = repo_root / "schemas" / pack
        pack_dir.mkdir(parents=True, exist_ok=True)

        # stage-substage-map.yaml
        ssm = _build_stage_substage_map(scanned, pack)
        ssm_path = pack_dir / "stage-substage-map.yaml"
        ssm, preserved_substages = _preserve_hand_substages(
            ssm, _existing_map_rows(ssm_path)
        )
        ssm_header = _AUTOGEN_HEADER
        if preserved_substages:
            ssm_header += (
                "# 以下条目为手工维护（无 L2 skill.yaml 源），由合并逻辑保留：\n"
                + "".join(f"#   - {key}\n" for key in sorted(preserved_substages))
                + "\n"
            )
        ssm_path.write_text(ssm_header + yaml_safe_dump(ssm), encoding="utf-8")
        written.append(ssm_path)

        # substage-skill-map.yaml
        sub_keys = _pack_substage_keys(scanned, pack)
        skm = _build_substage_skill_map(scanned, pack, sub_keys)
        skm_path = pack_dir / "substage-skill-map.yaml"
        skm_path.write_text(_AUTOGEN_HEADER + _SUBSTAGE_SKILL_HEADER + yaml_safe_dump(skm), encoding="utf-8")
        written.append(skm_path)

        # substage-spec-map.yaml
        spm = _build_substage_spec_map(scanned, pack)
        spm_path = pack_dir / "substage-spec-map.yaml"
        spm, preserved_specs = _preserve_hand_spec_refs(
            spm, _existing_map_rows(spm_path)
        )
        spm_header = _AUTOGEN_HEADER
        if preserved_specs:
            spm_header += (
                "# 以下条目为手工维护（无 L2 skill.yaml 源），由合并逻辑保留：\n"
                + "".join(f"#   - {key}\n" for key in sorted(preserved_specs))
                + "\n"
            )
        spm_path.write_text(spm_header + yaml_safe_dump(spm), encoding="utf-8")
        written.append(spm_path)

        # skill-id-to-substage.yaml
        sits = _build_skill_id_to_substage(scanned, pack)
        sits_path = pack_dir / "skill-id-to-substage.yaml"
        sits_path.write_text(_AUTOGEN_HEADER + _SKILL_ID_TO_SUBSTAGE_HEADER + yaml_safe_dump(sits), encoding="utf-8")
        written.append(sits_path)

    return written
