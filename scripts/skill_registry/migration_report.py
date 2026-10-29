"""Read-only skill migration report for stage/substage model hardening."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .scan import SkillEntry
from .stage_model import RESEARCH_STAGE_DISPLAY_NAMES, RESEARCH_STAGE_ORDER
from .stage_substage import (
    build_stage_substage_bindings,
    build_substage_stage_index,
    resolve_entry_packs,
)


REPORT_SCHEMA_VERSION = "research-stage-model-a1.v1"


@dataclass(frozen=True)
class MetadataApplyOutputs:
    changed_paths: list[Path]
    skipped_rows: list[dict[str, Any]]
    dry_run: bool


def _rel(path: Path) -> str:
    return str(path).replace("\\", "/")


def _skill_path(entry: SkillEntry) -> str:
    rel = _rel(entry.path)
    if "/skills/" in rel:
        return "skills" + rel.split("/skills", 1)[1]
    return rel


_PATCH_FIELD_ORDER = ["auto_allowed", "review_required", "risk_level", "stage", "substage"]


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


def patch_yaml_metadata_text(text: str, fields: dict[str, Any]) -> tuple[str, bool]:
    """Append/update top-level scalar metadata fields without reformatting YAML."""
    lines = text.splitlines()
    had_trailing_newline = text.endswith("\n")
    changed = False

    for field in _PATCH_FIELD_ORDER:
        if field not in fields:
            continue
        rendered = f"{field}: {_yaml_scalar(fields[field])}"
        pattern = re.compile(rf"^{re.escape(field)}\s*:")
        hit = False
        for idx, line in enumerate(lines):
            if pattern.match(line):
                hit = True
                if line != rendered:
                    lines[idx] = rendered
                    changed = True
                break
        if not hit:
            lines.append(rendered)
            changed = True

    patched = "\n".join(lines)
    if had_trailing_newline or patched:
        patched += "\n"
    return patched, changed


def _is_list_of_strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _infer_for_entry(
    entry: SkillEntry,
    pack_stage_index: dict[str, dict[str, str]],
) -> dict[str, Any]:
    sy = entry.skill_yaml or {}
    skill_id = sy.get("skill_id")
    skill_name = str(skill_id or sy.get("claude_name") or entry.dir_name)
    stages = sy.get("stages") if _is_list_of_strings(sy.get("stages")) else []
    substages = sy.get("substages") if _is_list_of_strings(sy.get("substages")) else []
    declared_packs = resolve_entry_packs(entry)
    skill_type = sy.get("skill_type", "atomic")

    candidate_packs = declared_packs
    pack_evidence: list[dict[str, str]] = []
    missing_substages: list[str] = []

    if candidate_packs is None:
        all_matching_packs = sorted(
            pack
            for pack, substage_to_stage in pack_stage_index.items()
            if any(substage in substage_to_stage for substage in substages)
        )
        candidate_packs = all_matching_packs

    for substage in substages:
        matched = False
        for pack in candidate_packs or []:
            stage = pack_stage_index.get(pack, {}).get(substage)
            if stage:
                matched = True
                pack_evidence.append({"pack": pack, "substage": substage, "stage": stage})
        if not matched:
            missing_substages.append(substage)

    inferred_stages = sorted({item["stage"] for item in pack_evidence}, key=RESEARCH_STAGE_ORDER.index)
    inferred_substages = sorted({item["substage"] for item in pack_evidence})

    questions: list[str] = []
    evidence: list[str] = []
    confidence = 0.0

    if not substages:
        questions.append("skill.yaml substages 为空或缺失，无法通过 substage 反推 stage。")
        evidence.append("未找到可用于反推的 substages 字段。")
    elif missing_substages:
        questions.append(f"确认这些 substage 是否仍有效：{', '.join(sorted(missing_substages))}。")
        evidence.append(f"substage 未出现在任何 L2 pack catalog：{', '.join(sorted(missing_substages))}。")
    elif len(inferred_stages) != 1:
        questions.append("确认该 skill 是否确实需要跨多个 stage；当前模型不允许 substage 跨 stage。")
        evidence.append(f"substage 反推得到多个 stage：{', '.join(inferred_stages) or '<none>'}。")
    else:
        inferred_stage = inferred_stages[0]
        evidence.append(
            f"通过 L2 pack catalog 将 {', '.join(inferred_substages)} 反推到 {inferred_stage}。"
        )
        if not stages:
            questions.append("确认是否补齐 skill.yaml stages 字段。")
            confidence = 0.86
        elif len(stages) == 1 and stages[0] == inferred_stage:
            confidence = 0.97
        elif inferred_stage in stages:
            questions.append("确认是否收敛为单一 stage；当前 skill.yaml stages 声明了多个 stage。")
            confidence = 0.76
        else:
            questions.append(f"确认 stages 是否应改为 {inferred_stage}。")
            confidence = 0.82

    if candidate_packs is None:
        evidence.append("skill 未声明 pack/packs，使用所有 pack catalog 进行 legacy 推断。")
    elif not candidate_packs:
        evidence.append("未从 pack/packs 或 substage catalog 得到候选 pack。")
    else:
        evidence.append(f"候选 pack：{', '.join(candidate_packs)}。")

    current_stage_set = set(stages)
    inferred_stage = inferred_stages[0] if len(inferred_stages) == 1 else None
    fields_aligned = (
        inferred_stage is not None
        and len(stages) == 1
        and stages[0] == inferred_stage
        and not missing_substages
    )
    legacy_pack_field = skill_type == "atomic" and isinstance(sy.get("pack"), str) and "packs" not in sy
    stage_needs_change = inferred_stage is not None and current_stage_set != {inferred_stage}
    high_confidence = confidence >= 0.85

    suggest_auto_migration = bool(
        high_confidence
        and not missing_substages
        and inferred_stage is not None
        and (stage_needs_change or legacy_pack_field)
        and skill_type != "orchestration"
    )
    needs_manual_review = not high_confidence or len(inferred_stages) != 1 or bool(missing_substages)
    if needs_manual_review:
        risk_level = "high" if missing_substages or len(inferred_stages) != 1 else "medium"
    elif stage_needs_change:
        risk_level = "medium"
    else:
        risk_level = "low"

    review_required = needs_manual_review or stage_needs_change
    auto_allowed = bool(high_confidence and not review_required and risk_level == "low")

    recommended_yaml_fields = {
        "auto_allowed": auto_allowed,
        "review_required": review_required,
        "risk_level": risk_level,
        "stage": inferred_stage,
        "substage": inferred_substages[0] if len(inferred_substages) == 1 else None,
    }
    metadata_patch_ready = bool(
        high_confidence
        and not needs_manual_review
        and inferred_stage is not None
        and len(inferred_substages) == 1
        and any(sy.get(key) != value for key, value in recommended_yaml_fields.items())
    )

    if suggest_auto_migration:
        category = "suggested_auto_change"
    elif needs_manual_review or review_required:
        category = "needs_manual_confirmation"
    else:
        category = "no_action"

    if fields_aligned and not legacy_pack_field:
        questions = []

    return {
        "skill_name": skill_name,
        "file_path": _skill_path(entry),
        "current_fields": {
            "stages": stages,
            "substages": substages,
            "pack": sy.get("pack"),
            "packs": sy.get("packs"),
            "skill_type": skill_type,
            "enabled": sy.get("enabled", True),
            "status": sy.get("status"),
            "source": sy.get("source"),
        },
        "current_auto_fields": {
            "candidate_packs": candidate_packs or [],
            "pack_catalog_matches": pack_evidence,
            "stage_display_names": RESEARCH_STAGE_DISPLAY_NAMES,
        },
        "inferred_stage": inferred_stage,
        "inferred_substage": inferred_substages[0] if len(inferred_substages) == 1 else inferred_substages,
        "recommended_auto_allowed": auto_allowed,
        "recommended_review_required": review_required,
        "recommended_risk_level": risk_level,
        "recommended_yaml_fields": recommended_yaml_fields,
        "metadata_patch_ready": metadata_patch_ready,
        "confidence": round(confidence, 2),
        "suggest_auto_migration": suggest_auto_migration,
        "needs_manual_review": needs_manual_review,
        "inference_evidence": evidence,
        "user_confirmation_questions": questions,
        "category": category,
    }


def build_migration_report(entries: list[SkillEntry]) -> dict[str, Any]:
    validation = build_stage_substage_bindings(entries)
    pack_stage_index = build_substage_stage_index(entries)
    rows = [_infer_for_entry(entry, pack_stage_index) for entry in entries]
    rows = sorted(rows, key=lambda row: (row["category"], row["file_path"], row["skill_name"]))

    categories = {
        "suggested_auto_change": [row for row in rows if row["category"] == "suggested_auto_change"],
        "needs_manual_confirmation": [row for row in rows if row["category"] == "needs_manual_confirmation"],
        "no_action": [row for row in rows if row["category"] == "no_action"],
    }

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "stage_display_names": RESEARCH_STAGE_DISPLAY_NAMES,
        "stage_order": list(RESEARCH_STAGE_ORDER),
        "substage_stage_conflicts": validation.conflicts,
        "summary": {
            "total_skills": len(rows),
            "suggested_auto_change": len(categories["suggested_auto_change"]),
            "needs_manual_confirmation": len(categories["needs_manual_confirmation"]),
            "no_action": len(categories["no_action"]),
            "metadata_patch_ready": len([row for row in rows if row["metadata_patch_ready"]]),
        },
        "categories": categories,
        "items": rows,
    }


def apply_metadata_patch_plan(repo_root: Path, entries: list[SkillEntry], *, dry_run: bool = False) -> MetadataApplyOutputs:
    report = build_migration_report(entries)
    changed_paths: list[Path] = []
    skipped_rows: list[dict[str, Any]] = []
    for row in report["items"]:
        if not row.get("metadata_patch_ready"):
            skipped_rows.append(row)
            continue
        rel_skill_dir = row["file_path"]
        yaml_path = repo_root / rel_skill_dir / "skill.yaml"
        if not yaml_path.is_file():
            skipped_rows.append({**row, "skip_reason": "missing skill.yaml"})
            continue
        current = yaml_path.read_text(encoding="utf-8")
        patched, changed = patch_yaml_metadata_text(current, row["recommended_yaml_fields"])
        if changed:
            changed_paths.append(yaml_path)
            if not dry_run:
                yaml_path.write_text(patched, encoding="utf-8")
    return MetadataApplyOutputs(
        changed_paths=changed_paths,
        skipped_rows=skipped_rows,
        dry_run=dry_run,
    )
