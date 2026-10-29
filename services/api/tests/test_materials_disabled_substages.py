"""Regression: materials pack lists characterization as a placeholder
substage (enabled: false — its L3 depends on the gitignored materials-auto-xrd
skill). It must NOT appear in any runnable stageCatalog, otherwise the
executor advances into it and the task looks "in progress" with zero real
capability.

synthesis_execution is NO LONGER a placeholder: since flux_experiment was
bound as a real durable L3 (cloud experiment dispatch, S8 of the
deterministic chain) it must stay runnable in catalog and stageDetails.

Covers reviewer feedback item 1 on PR #270 (original placeholder set).
"""

from app.modules.task_store import _default_stage_catalog, _workflow_substage_instances


def _catalog_keys(pack: str) -> set[str]:
    keys: set[str] = set()
    for stage in _default_stage_catalog(pack):
        for ss in stage.get("allowedSubStages") or []:
            keys.add(ss["key"])
    return keys


def test_materials_catalog_excludes_disabled_placeholders() -> None:
    keys = _catalog_keys("materials")
    assert "characterization" not in keys, (
        f"characterization depends on the gitignored materials-auto-xrd L3 "
        f"and must not be runnable; got {sorted(keys)}"
    )


def test_materials_catalog_keeps_real_substages() -> None:
    """Sanity: the substages with real L3 skills must still be present."""
    keys = _catalog_keys("materials")
    expected = {
        "database_search",
        "candidate_generation",
        "parameter_recommendation",
        "stability_verification",
        "synthesis_execution",
    }
    assert expected.issubset(keys), f"missing real substages; got {sorted(keys)}"


def test_sdr_catalog_unaffected_by_materials_disable() -> None:
    """sdr pack has no enabled:false substages — regression guard."""
    keys = _catalog_keys("sdr")
    assert keys, "sdr catalog should be non-empty"
    # No sdr substage should be disabled under current schemas.


def test_materials_stage_details_excludes_disabled_placeholders() -> None:
    """stageDetails (built by _workflow_substage_instances) must also filter
    disabled substages — not just stageCatalog. Pre-fix: a draft plan listing
    characterization leaked it into stageDetails even though stageCatalog
    filtered it out, so the detail page rendered placeholder substages with
    no L3 skill behind them. synthesis_execution, in contrast, has a real
    durable L3 (flux_experiment) and must survive the filter.
    """
    # Simulate a draft plan that still lists the disabled substage — the
    # filter must drop it regardless of what the plan carries.
    stage_entry = {
        "substages": [
            {"substage": "stability_verification"},
            {"substage": "synthesis_execution"},
            {"substage": "characterization"},
        ],
    }
    instances = _workflow_substage_instances(
        stage_entry, stage_key="execution_validation", pack="materials", now="2026-07-10T00:00:00Z"
    )
    keys = [i["subStage"] for i in instances]
    assert "synthesis_execution" in keys, keys
    assert "characterization" not in keys, keys
    assert "stability_verification" in keys, keys


def test_sdr_stage_details_keeps_all_substages() -> None:
    """sdr pack has no disabled substages — _workflow_substage_instances must
    be a no-op filter for sdr and keep every entry from the plan."""
    stage_entry = {
        "substages": [
            {"substage": "task_orchestration"},
            {"substage": "literature_review"},
        ],
    }
    instances = _workflow_substage_instances(
        stage_entry, stage_key="problem_modeling", pack="sdr", now="2026-07-10T00:00:00Z"
    )
    keys = [i["subStage"] for i in instances]
    assert keys == ["task_orchestration", "literature_review"], keys


def test_materials_database_search_precedes_candidate_generation_across_stages() -> None:
    """database_search must run before candidate_generation.

    candidate_generation.input_skills includes database_search — the generator
    consumes the database hits as seeds. The canonical Skill definitions place
    database_search in problem_modeling and candidate_generation in
    solution_design, so the dependency order is enforced across stage order
    rather than by assigning both substages to problem_modeling.

    Locks in both canonical stage identity and dependency order.
    """
    catalog = _default_stage_catalog("materials")
    problem_modeling = next((s for s in catalog if s["stageKey"] == "problem_modeling"), None)
    solution_design = next((s for s in catalog if s["stageKey"] == "solution_design"), None)
    assert problem_modeling is not None, "materials catalog missing problem_modeling"
    assert solution_design is not None, "materials catalog missing solution_design"
    problem_keys = [ss["key"] for ss in problem_modeling.get("allowedSubStages") or []]
    solution_keys = [ss["key"] for ss in solution_design.get("allowedSubStages") or []]
    assert problem_keys[:1] == ["database_search"], problem_keys
    assert solution_keys[:1] == ["candidate_generation"], solution_keys
    assert [stage["stageKey"] for stage in catalog].index("problem_modeling") < [
        stage["stageKey"] for stage in catalog
    ].index("solution_design")
