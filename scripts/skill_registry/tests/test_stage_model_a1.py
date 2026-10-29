from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import pytest

from skill_registry.migration_report import build_migration_report, patch_yaml_metadata_text
from skill_registry.scan import SkillEntry
from skill_registry.stage_model import RESEARCH_STAGE_DISPLAY_NAMES
from skill_registry.stage_substage import build_substage_stage_index


def _entry(
    path: str,
    *,
    skill_id: str,
    stages: list[str],
    substages: list[str],
    skill_type: str = "atomic",
    pack: str | None = None,
    packs: list[str] | None = None,
) -> SkillEntry:
    skill_dir = ROOT / path
    skill_yaml = {
        "skill_id": skill_id,
        "claude_name": skill_id.replace("_", "-"),
        "stages": stages,
        "substages": substages,
        "skill_type": skill_type,
        "runtime": "claude_code",
        "status": "draft",
        "source": "manual",
    }
    if pack is not None:
        skill_yaml["pack"] = pack
    if packs is not None:
        skill_yaml["packs"] = packs
    return SkillEntry(
        path=skill_dir,
        domain="pytest",
        dir_name=skill_dir.name,
        skill_md=skill_dir / "SKILL.md",
        skill_yaml_path=skill_dir / "skill.yaml",
        frontmatter={"name": skill_yaml["claude_name"], "description": "pytest"},
        skill_yaml=skill_yaml,
        raw_skill_md="",
    )


def _catalog_entries() -> list[SkillEntry]:
    return [
        _entry(
            "skills/pytest/intent-understanding",
            skill_id="intent_understanding",
            stages=["problem_modeling"],
            substages=["intent_understanding"],
            skill_type="orchestration",
            pack="pytest",
        ),
        _entry(
            "skills/pytest/experiment-design",
            skill_id="experiment_design",
            stages=["solution_design"],
            substages=["experiment_design"],
            skill_type="orchestration",
            pack="pytest",
        ),
    ]


def test_stage_display_names_are_stable() -> None:
    assert RESEARCH_STAGE_DISPLAY_NAMES == {
        "problem_modeling": "问题建模",
        "solution_design": "方案设计",
        "execution_validation": "执行验证",
        "insight_generation": "结果总结",
    }


def test_substage_cannot_cross_stage() -> None:
    entries = _catalog_entries() + [
        _entry(
            "skills/pytest/intent-understanding-copy",
            skill_id="intent_understanding_copy",
            stages=["solution_design"],
            substages=["intent_understanding"],
            skill_type="orchestration",
            pack="pytest",
        )
    ]

    with pytest.raises(ValueError, match="intent_understanding"):
        build_substage_stage_index(entries)


def test_migration_report_is_repeatable() -> None:
    entries = _catalog_entries() + [
        _entry(
            "skills/pytest/atomic-intent",
            skill_id="atomic_intent",
            stages=["problem_modeling"],
            substages=["intent_understanding"],
            packs=["pytest"],
        )
    ]

    assert build_migration_report(entries) == build_migration_report(entries)


def test_high_confidence_item_gets_metadata_diff_plan() -> None:
    entries = _catalog_entries() + [
        _entry(
            "skills/pytest/atomic-intent",
            skill_id="atomic_intent",
            stages=["problem_modeling"],
            substages=["intent_understanding"],
            packs=["pytest"],
        )
    ]

    report = build_migration_report(entries)
    row = next(item for item in report["items"] if item["skill_name"] == "atomic_intent")
    assert row["needs_manual_review"] is False
    assert row["metadata_patch_ready"] is True
    assert row["recommended_yaml_fields"] == {
        "auto_allowed": True,
        "review_required": False,
        "risk_level": "low",
        "stage": "problem_modeling",
        "substage": "intent_understanding",
    }
    ready_names = {item["skill_name"] for item in report["items"] if item["metadata_patch_ready"]}
    assert ready_names == {"intent_understanding", "experiment_design", "atomic_intent"}
    assert report["summary"]["metadata_patch_ready"] == len(ready_names)


def test_low_confidence_item_is_not_auto_migrated() -> None:
    entries = _catalog_entries() + [
        _entry(
            "skills/pytest/unknown",
            skill_id="unknown",
            stages=["problem_modeling"],
            substages=["unknown_substage"],
            packs=["pytest"],
        )
    ]

    report = build_migration_report(entries)
    row = next(item for item in report["items"] if item["skill_name"] == "unknown")
    assert row["confidence"] < 0.85
    assert row["needs_manual_review"] is True
    assert row["metadata_patch_ready"] is False
    assert row["suggest_auto_migration"] is False


def test_patch_yaml_metadata_text_preserves_existing_body() -> None:
    text = "\n".join(
        [
            "skill_id: demo",
            "stages:",
            "  - problem_modeling",
            "risk_level: medium",
            "",
        ]
    )
    patched, changed = patch_yaml_metadata_text(
        text,
        {
            "auto_allowed": False,
            "review_required": True,
            "risk_level": "low",
            "stage": "problem_modeling",
            "substage": "intent_understanding",
        },
    )

    assert changed is True
    assert "stages:\n  - problem_modeling" in patched
    assert "risk_level: low\n" in patched
    assert patched.endswith(
        "auto_allowed: false\n"
        "review_required: true\n"
        "stage: problem_modeling\n"
        "substage: intent_understanding\n"
    )
