from __future__ import annotations

from app.modules.research_orchestration_resolver import build_registry_snapshot


def test_current_registry_snapshot_excludes_retired_claude_code_skills() -> None:
    snapshot = build_registry_snapshot()
    by_id = {
        row["skillId"]: row
        for row in snapshot.envelope["definitions"]["skills"]
    }

    assert "constraint_interpretation_skill" not in by_id
    assert "structured_requirement_generation" not in by_id
    assert by_id["opentrajectory_mechanism_explanation"]["runtime"] == "claude_code"
