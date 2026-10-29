from __future__ import annotations

import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from skill_registry.registry import build_registry_payload
from skill_registry.scan import scan_skills


HISTORICAL_ONLY = {
    "code_identity_acquisition",
    "execution_plan_acquisition",
    "reference_metric_acquisition",
    "reproduction_input_structuring",
    "constraint_interpretation_skill",
    "structured_requirement_generation",
    "opentrajectory_constraints_criteria",
    "opentrajectory_intent_understanding",
    "opentrajectory_problem_framing",
    "opentrajectory_retrieval_strategy_design",
    "opentrajectory_evidence_schema_design",
    "opentrajectory_progressive_reading",
    "opentrajectory_evidence_extraction",
    "opentrajectory_findings_synthesis",
}

STALE_ONLY = {
    "opentrajectory_hypothesis_update",
    "opentrajectory_report_generation",
    "opentrajectory_research_lead_generation",
    "opentrajectory_validation_plan_generation",
    "trajectory_planning_eval",
}

ACTIVE_CLAUDE_CODE = {"opentrajectory_mechanism_explanation"}
RETIRED = HISTORICAL_ONLY | STALE_ONLY


def _source_entries():
    return scan_skills(ROOT).entries


def test_retired_sources_stay_historical_and_leave_current_registry() -> None:
    entries = _source_entries()
    source_by_id = {
        entry.skill_yaml.get("skill_id"): entry.skill_yaml
        for entry in entries
        if entry.skill_yaml.get("skill_id")
    }
    generated = {
        row["skill_id"]: row
        for row in build_registry_payload(ROOT, entries)["skills"]
    }

    assert {
        skill_id
        for skill_id in RETIRED
        if source_by_id[skill_id]["status"] == "deprecated"
    } == RETIRED
    assert RETIRED.isdisjoint(generated)
    assert generated["opentrajectory_mechanism_explanation"]["runtime"] == "claude_code"
    assert generated["opentrajectory_mechanism_explanation"]["status"] == "ready"


def test_retired_skills_are_absent_from_current_bindings_and_selection_maps() -> None:
    bindings = yaml.safe_load(
        (ROOT / "skills" / "skill-bindings.yaml").read_text(encoding="utf-8")
    )
    rows = bindings["bindings"] if isinstance(bindings, dict) else bindings
    assert RETIRED.isdisjoint({row["skillId"] for row in rows})

    selected_ids: set[str] = set()
    for pack in ("cs_reproduction", "opentrajectory"):
        pack_root = ROOT / "schemas" / pack
        forward = yaml.safe_load(
            (pack_root / "substage-skill-map.yaml").read_text(encoding="utf-8")
        ) or {}
        selected_ids.update(
            item["skill_id"] if isinstance(item, dict) else item
            for items in forward.values()
            for item in items
        )
        reverse = yaml.safe_load(
            (pack_root / "skill-id-to-substage.yaml").read_text(encoding="utf-8")
        ) or {}
        selected_ids.update(reverse)

    assert RETIRED.isdisjoint(selected_ids)
    assert "opentrajectory_mechanism_explanation" in selected_ids


def test_current_registry_keeps_only_the_proven_scoped_claude_code_authority() -> None:
    registry = yaml.safe_load(
        (ROOT / "skills" / "registry.yaml").read_text(encoding="utf-8")
    )
    scoped_ids = RETIRED | ACTIVE_CLAUDE_CODE
    registered = {
        row["skill_id"]
        for row in registry["skills"]
        if row["skill_id"] in scoped_ids and row.get("runtime") == "claude_code"
    }
    assert registered == ACTIVE_CLAUDE_CODE
