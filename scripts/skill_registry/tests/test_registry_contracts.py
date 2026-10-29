from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from skill_registry.registry import build_registry_payload, diff_registry, load_registry
from skill_registry.scan import SkillEntry, scan_skills
from skill_registry.workflow_validation import validate_workflow_pack


def _entry(tmp_path: Path, *, skill_yaml: dict) -> SkillEntry:
    skill_dir = tmp_path / "skills" / "demo" / "demo-skill"
    skill_dir.mkdir(parents=True)
    return SkillEntry(
        path=skill_dir,
        domain="demo",
        dir_name="demo-skill",
        skill_md=skill_dir / "SKILL.md",
        skill_yaml_path=skill_dir / "skill.yaml",
        frontmatter={"name": "demo-skill", "description": "demo"},
        skill_yaml=skill_yaml,
        raw_skill_md="",
    )


def _write_registry(tmp_path: Path, row: dict) -> None:
    registry = tmp_path / "skills" / "registry.yaml"
    registry.parent.mkdir(parents=True, exist_ok=True)
    import yaml

    registry.write_text(yaml.safe_dump({"version": "0.1.0", "skills": [row]}, sort_keys=False), encoding="utf-8")


def test_update_does_not_preserve_registry_only_execution_contracts(tmp_path: Path) -> None:
    skill = {
        "skill_id": "demo_skill",
        "claude_name": "demo-skill",
        "stages": ["problem_modeling"],
        "substages": ["demo"],
        "packs": ["demo"],
        "skill_type": "atomic",
        "runtime": "claude_code",
        "status": "ready",
        "source": "manual",
    }
    old_contract = {"kind": "skill_script", "requires_external_action": True, "adapter_id": "skill_runtime:demo", "evidence_contract_version": "skill-execution-evidence/v1"}
    old_handoff = {"contract_version": 1, "predecessor_skill_id": "upstream"}
    _write_registry(tmp_path, {**skill, "runtime_contract": old_contract, "input_handoff": old_handoff})

    payload = build_registry_payload(tmp_path, [_entry(tmp_path, skill_yaml=skill)])
    row = payload["skills"][0]
    assert "runtime_contract" not in row
    assert "input_handoff" not in row


def test_skill_yaml_cannot_publish_legacy_input_handoff_authority(tmp_path: Path) -> None:
    skill = {
        "skill_id": "demo_skill",
        "claude_name": "demo-skill",
        "stages": ["problem_modeling"],
        "substages": ["demo"],
        "packs": ["demo"],
        "skill_type": "atomic",
        "runtime": "claude_code",
        "status": "ready",
        "source": "manual",
        "runtime_contract": {"kind": "llm_reasoning", "requires_external_action": False, "adapter_id": "huiyan_chat_completions", "evidence_contract_version": "none"},
        "input_handoff": {"contract_version": 1, "predecessor_skill_id": None},
    }
    _write_registry(tmp_path, {**skill, "runtime_contract": {"kind": "old"}, "input_handoff": {"contract_version": 1, "predecessor_skill_id": "old"}})

    row = build_registry_payload(tmp_path, [_entry(tmp_path, skill_yaml=skill)])["skills"][0]
    assert row["runtime_contract"] == skill["runtime_contract"]
    assert "input_handoff" not in row


def test_skill_yaml_can_explicitly_remove_an_existing_contract(tmp_path: Path) -> None:
    skill = {
        "skill_id": "demo_skill",
        "claude_name": "demo-skill",
        "stages": ["problem_modeling"],
        "substages": ["demo"],
        "packs": ["demo"],
        "skill_type": "atomic",
        "runtime": "claude_code",
        "status": "ready",
        "source": "manual",
        "runtime_contract": None,
    }
    _write_registry(
        tmp_path,
        {
            **skill,
            "runtime_contract": {
                "kind": "skill_script",
                "requires_external_action": True,
                "adapter_id": "skill_runtime:old",
                "evidence_contract_version": "skill-execution-evidence/v1",
            },
        },
    )

    row = build_registry_payload(tmp_path, [_entry(tmp_path, skill_yaml=skill)])["skills"][0]
    assert row["runtime_contract"] is None


def test_deprecated_skill_is_not_a_current_registry_candidate(tmp_path: Path) -> None:
    skill = {
        "skill_id": "historical_skill",
        "claude_name": "demo-skill",
        "stages": ["problem_modeling"],
        "substages": ["demo"],
        "packs": ["demo"],
        "skill_type": "atomic",
        "runtime": "claude_code",
        "status": "deprecated",
        "source": "manual",
    }
    entry = _entry(tmp_path, skill_yaml=skill)
    _write_registry(tmp_path, {**skill, "skill_path": "skills/demo/demo-skill"})

    assert build_registry_payload(tmp_path, [entry])["skills"] == []
    diff = diff_registry(tmp_path, [entry])
    assert any("historical_skill" in row for row in diff.extra_in_registry)


def test_check_rejects_generated_pack_drift(tmp_path: Path) -> None:
    skill = {
        "skill_id": "demo_skill",
        "claude_name": "demo-skill",
        "stages": ["execution_validation"],
        "substages": ["source_acquisition"],
        "packs": ["opentrajectory"],
        "skill_type": "atomic",
        "runtime": "claude_code",
        "status": "ready",
        "source": "manual",
    }
    entry = _entry(tmp_path, skill_yaml=skill)
    _write_registry(tmp_path, {**skill, "packs": ["opentrajectory", "sdr"]})

    diff = diff_registry(tmp_path, [entry])

    assert any("字段 'packs'" in row for row in diff.field_drift)


def test_academic_retrieval_registry_row_is_generated_from_skill_yaml() -> None:
    entries = scan_skills(ROOT).entries
    generated = next(
        row
        for row in build_registry_payload(ROOT, entries)["skills"]
        if row["skill_id"] == "academic_source_retrieval_skill"
    )
    current = next(
        row
        for row in load_registry(ROOT)[1]
        if row["skill_id"] == "academic_source_retrieval_skill"
    )

    assert current == generated


def test_ot_split_skill_status_and_enabled_are_projected_from_skill_yaml() -> None:
    entries = scan_skills(ROOT).entries
    generated = {
        row["skill_id"]: row
        for row in build_registry_payload(ROOT, entries)["skills"]
    }
    current = {row["skill_id"]: row for row in load_registry(ROOT)[1]}
    expected_ids = {
        "ot_evidence_supported_findings_synthesis",
        "ot_falsifiable_hypothesis_generation",
        "ot_research_lead_generation",
    }

    for skill_id in expected_ids:
        assert generated[skill_id]["status"] == "draft"
        assert generated[skill_id]["enabled"] is False
        assert current[skill_id]["status"] == generated[skill_id]["status"]
        assert current[skill_id]["enabled"] is generated[skill_id]["enabled"]


def test_workflow_validation_rejects_an_unregistered_composition_skill(tmp_path: Path) -> None:
    composition = tmp_path / "composition.yaml"
    composition.write_text("database_search:\n  - missing_skill\n", encoding="utf-8")

    issues = validate_workflow_pack(
        ROOT,
        pack="materials",
        composition_map=composition,
    )

    assert any(issue.code == "SKILL_UNREGISTERED" for issue in issues)


def test_skill_yaml_publishes_durable_model_profile_into_registry(tmp_path: Path) -> None:
    skill = {
        "skill_id": "demo_skill",
        "claude_name": "demo-skill",
        "stages": ["problem_modeling"],
        "substages": ["demo"],
        "packs": ["demo"],
        "skill_type": "atomic",
        "runtime": "llm_structured",
        "status": "ready",
        "source": "manual",
        "runtime_contract": {
            "kind": "llm_reasoning",
            "requires_external_action": False,
            "adapter_id": "huiyan_chat_completions",
            "evidence_contract_version": "none",
        },
        "durable_model_profile": "terra",
    }

    row = build_registry_payload(tmp_path, [_entry(tmp_path, skill_yaml=skill)])["skills"][0]

    assert row["durable_model_profile"] == "terra"


def test_skill_yaml_publishes_durable_llm_execution_policy_into_registry(tmp_path: Path) -> None:
    skill = {
        "skill_id": "demo_skill",
        "claude_name": "demo-skill",
        "stages": ["problem_modeling"],
        "substages": ["demo"],
        "packs": ["demo"],
        "skill_type": "atomic",
        "runtime": "llm_reasoning",
        "status": "ready",
        "source": "manual",
        "runtime_contract": {
            "kind": "llm_reasoning",
            "requires_external_action": False,
            "adapter_id": "huiyan_chat_completions",
            "evidence_contract_version": "none",
        },
        "durable_llm_execution_policy": {
            "contract_version": 1,
            "model_profile": "terra",
            "reasoning_effort": "none",
            "max_output_tokens": 6000,
            "read_timeout_seconds": 60,
            "response_mode": "json_object",
            "load_references": False,
            "structured_output_recovery_attempts": 1,
        },
    }

    row = build_registry_payload(tmp_path, [_entry(tmp_path, skill_yaml=skill)])["skills"][0]

    assert row["durable_llm_execution_policy"] == skill["durable_llm_execution_policy"]


def test_skill_yaml_publishes_finalization_into_registry(tmp_path: Path) -> None:
    skill = {
        "skill_id": "demo_skill",
        "claude_name": "demo-skill",
        "stages": ["execution_validation"],
        "substages": ["demo"],
        "packs": ["demo"],
        "skill_type": "atomic",
        "runtime": "llm_structured",
        "status": "ready",
        "source": "manual",
        "finalization": {
            "contract_version": 1,
            "entrypoint": "scripts.finalize:finalize",
            "draft_schema": "model_output.schema.json",
        },
    }

    row = build_registry_payload(tmp_path, [_entry(tmp_path, skill_yaml=skill)])["skills"][0]

    assert row["finalization"] == skill["finalization"]


def test_skill_yaml_publishes_durable_llm_batch_policy_into_registry(tmp_path: Path) -> None:
    skill = {
        "skill_id": "demo_skill",
        "claude_name": "demo-skill",
        "stages": ["execution_validation"],
        "substages": ["demo"],
        "packs": ["demo"],
        "skill_type": "atomic",
        "runtime": "llm_structured",
        "status": "ready",
        "source": "manual",
        "durable_llm_batch_policy": {
            "contract_version": 1,
            "input_array_field": "candidates",
            "output_array_field": "judgments",
            "identity_field": "candidate_id",
            "max_items_per_batch": 30,
            "max_parallel": 4,
            "partial_failure_mode": "preserve_successes",
        },
    }

    row = build_registry_payload(tmp_path, [_entry(tmp_path, skill_yaml=skill)])["skills"][0]

    assert row["durable_llm_batch_policy"] == skill["durable_llm_batch_policy"]


def test_skill_yaml_publishes_scientific_state_projection_into_registry(tmp_path: Path) -> None:
    projection = {
        "contract_version": 2,
        "materializer_ref": "research:declared-scientific-facts:v1",
        "facts": [
            {"fact_key": "demo_result", "source_field": "result"},
        ],
    }
    skill = {
        "skill_id": "demo_skill",
        "claude_name": "demo-skill",
        "stages": ["execution_validation"],
        "substages": ["demo"],
        "packs": ["demo"],
        "skill_type": "atomic",
        "runtime": "python_adapter",
        "status": "ready",
        "source": "manual",
        "scientific_state_projection": projection,
    }

    row = build_registry_payload(tmp_path, [_entry(tmp_path, skill_yaml=skill)])["skills"][0]

    assert row["scientific_state_projection"] == projection


def test_check_rejects_scientific_state_projection_drift(tmp_path: Path) -> None:
    projection = {
        "contract_version": 2,
        "materializer_ref": "research:declared-scientific-facts:v1",
        "facts": [{"fact_key": "demo_result", "source_field": "result"}],
    }
    skill = {
        "skill_id": "demo_skill",
        "claude_name": "demo-skill",
        "stages": ["execution_validation"],
        "substages": ["demo"],
        "packs": ["demo"],
        "skill_type": "atomic",
        "runtime": "python_adapter",
        "status": "ready",
        "source": "manual",
        "scientific_state_projection": projection,
    }
    entry = _entry(tmp_path, skill_yaml=skill)
    drifted = {
        **skill,
        "scientific_state_projection": {
            **projection,
            "facts": [{"fact_key": "wrong_result", "source_field": "result"}],
        },
    }
    _write_registry(tmp_path, drifted)

    diff = diff_registry(tmp_path, [entry])

    assert any("字段 'scientific_state_projection'" in row for row in diff.field_drift)


def test_repo_scientific_state_projections_have_skill_yaml_source_truth_and_required_output_fields() -> None:
    entries = scan_skills(ROOT).entries
    generated = {
        row["skill_id"]: row
        for row in build_registry_payload(ROOT, entries)["skills"]
    }
    current = {row["skill_id"]: row for row in load_registry(ROOT)[1]}
    projected_ids = sorted(
        skill_id
        for skill_id in set(generated) | set(current)
        if generated.get(skill_id, {}).get("scientific_state_projection") is not None
        or current.get(skill_id, {}).get("scientific_state_projection") is not None
    )

    assert projected_ids
    by_id = {
        entry.skill_yaml.get("skill_id"): entry
        for entry in entries
        if isinstance(entry.skill_yaml, dict) and entry.skill_yaml.get("skill_id")
    }
    for skill_id in projected_ids:
        generated_projection = generated.get(skill_id, {}).get("scientific_state_projection")
        assert generated_projection is not None, f"{skill_id}: projection exists only in registry"
        assert current.get(skill_id, {}).get("scientific_state_projection") == generated_projection

        entry = by_id[skill_id]
        skill_yaml = entry.skill_yaml
        assert skill_yaml.get("scientific_state_projection") == generated_projection
        output_schema_name = skill_yaml.get("output_schema") or "output.schema.json"
        output_schema_path = entry.path / output_schema_name
        assert output_schema_path.is_file(), f"{skill_id}: projection requires an output schema"
        output_schema = json.loads(output_schema_path.read_text(encoding="utf-8"))
        properties = output_schema.get("properties") or {}
        required = set(output_schema.get("required") or [])
        for fact in generated_projection["facts"]:
            source_field = fact["source_field"]
            assert source_field in properties, f"{skill_id}: unknown projected source field {source_field}"
            assert source_field in required, f"{skill_id}: projected source field must be required: {source_field}"


def test_check_rejects_registry_only_scientific_state_projection(tmp_path: Path) -> None:
    projection = {
        "contract_version": 2,
        "materializer_ref": "research:declared-scientific-facts:v1",
        "facts": [{"fact_key": "demo_result", "source_field": "result"}],
    }
    skill = {
        "skill_id": "demo_skill",
        "claude_name": "demo-skill",
        "stages": ["execution_validation"],
        "substages": ["demo"],
        "packs": ["demo"],
        "skill_type": "atomic",
        "runtime": "python_adapter",
        "status": "ready",
        "source": "manual",
    }
    _write_registry(tmp_path, {**skill, "scientific_state_projection": projection})

    diff = diff_registry(tmp_path, [_entry(tmp_path, skill_yaml=skill)])

    assert any("scientific_state_projection" in row for row in diff.field_drift)
