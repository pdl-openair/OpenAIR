from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from app.execution.research_dispatch_provider import DispatchSubmissionRequest
from app.execution.skill_dependency_runtime import (
    execute_frozen_skill_dependency_chain,
    resolve_frozen_skill_dependency_chain,
)
from app.modules.research_orchestration_resolver import build_registry_snapshot


ROOT = Path(__file__).resolve().parents[3]

OT_RETRIEVAL_MATRIX_BINDING = (
    "bind:opentrajectory:retrieval_strategy_design:skill:retrieval_matrix_generation",
    1,
)


def _request() -> DispatchSubmissionRequest:
    return DispatchSubmissionRequest(
        dispatch_id="dispatch-dependency-chain",
        task_id="task-dependency-chain",
        execution_decision_id="decision-dependency-chain",
        work_unit_id="workunit-dependency-chain",
        idempotency_key="workunit:dependency-chain",
        owner_id="owner",
        workspace_id="workspace",
        payload={},
        capability_call_id="call-dependency-chain",
        attempt_number=1,
    )


def _result_for_bytes(raw: bytes) -> dict:
    digest = hashlib.sha256(raw).hexdigest()
    return {
        "state": "succeeded",
        "artifactPayload": {
            "contentBase64": base64.b64encode(raw).decode("ascii"),
            "contentType": "application/json",
            "presentation": {
                "title": "dependency result",
                "mimeType": "application/json",
                "contentSha256": digest,
                "contentSize": len(raw),
            },
        },
        "externalActionEvidence": [],
    }


def _example(skill_path: str) -> bytes:
    return (ROOT / skill_path / "examples" / "response.json").read_bytes()


def _runtime(skill_id: str, **_kwargs):
    external = skill_id == "academic_source_retrieval_skill"
    return {
        "artifactContract": f"{skill_id}_artifact",
        "artifactSchemaDigest": "a" * 64,
        "artifactMimeType": "application/json",
        "logicalProviderId": None if external else "openai_primary",
        "modelProfile": None if external else "terra",
        "runtimeKind": "external_adapter" if external else "llm_reasoning",
        "requiresExternalAction": external,
        "adapterId": (
            "python_adapter:academic_source_retrieval_skill"
            if external
            else "huiyan_chat_completions"
        ),
        "evidenceContractVersion": "external-action-evidence/v1" if external else "none",
    }


def test_production_execution_has_no_raw_durable_skill_bypass():
    app_root = ROOT / "services" / "api" / "app"
    raw_offenders = []
    production_entry_callers = []
    allowed_production_entry_callers = {
        "services/api/app/execution/research_dispatch_skill_provider.py",
        "services/api/app/execution/skill_dependency_runtime.py",
    }
    for path in app_root.rglob("*.py"):
        relative = str(path.relative_to(ROOT)).replace("\\", "/")
        text = path.read_text(encoding="utf-8")
        if path.name != "durable_skill_runtime.py" and "execute_durable_skill(" in text:
            raw_offenders.append(relative)
        if (
            path.name != "durable_skill_runtime.py"
            and "execute_production_durable_skill" in text
        ):
            production_entry_callers.append(relative)
    assert raw_offenders == []
    assert set(production_entry_callers) == allowed_production_entry_callers


def test_failed_prerequisite_stops_generic_retrieval_matrix_chain():
    snapshot = build_registry_snapshot().envelope
    calls: list[str] = []

    def execute(request: dict) -> dict:
        skill_id = request["skillId"]
        calls.append(skill_id)
        if skill_id == "structured_requirement_generation":
            return _result_for_bytes(
                _example("skills/opentrajectory/structured-requirement-generation")
            )
        return {
            "state": "failed",
            "failureClassification": "provider_response_contract_invalid",
            "summary": "dependency output rejected",
        }

    result = execute_frozen_skill_dependency_chain(
        snapshot=snapshot,
        target_binding_ref=OT_RETRIEVAL_MATRIX_BINDING,
        canonical_user_query="中文研究目标",
        request=_request(),
        requesting_actor_id="key_literature_positioning",
        execute=execute,
        runtime_resolver=_runtime,
    )

    assert result["state"] == "failed"
    assert result["failedStage"] == "dependency:constraint_interpretation_skill"
    assert calls == [
        "structured_requirement_generation",
        "constraint_interpretation_skill",
    ]


def test_external_action_dependency_cannot_hide_inside_parent_dispatch():
    snapshot = build_registry_snapshot().envelope

    def runtime(skill_id: str, **kwargs):
        row = _runtime(skill_id, **kwargs)
        if skill_id == "constraint_interpretation_skill":
            return {
                **row,
                "runtimeKind": "external_adapter",
                "requiresExternalAction": True,
                "adapterId": "python_adapter:synthetic_external_dependency",
                "evidenceContractVersion": "external-action-evidence/v1",
            }
        return row

    from app.execution.skill_dependency_runtime import SkillDependencyContractError

    calls: list[str] = []

    def execute(request: dict) -> dict:
        skill_id = request["skillId"]
        calls.append(skill_id)
        if skill_id == "structured_requirement_generation":
            return _result_for_bytes(
                _example("skills/opentrajectory/structured-requirement-generation")
            )
        raise AssertionError("external dependency must fail before its execution")

    try:
        execute_frozen_skill_dependency_chain(
            snapshot=snapshot,
            target_binding_ref=OT_RETRIEVAL_MATRIX_BINDING,
            canonical_user_query="中文研究目标",
            request=_request(),
            requesting_actor_id="key_literature_positioning",
            execute=execute,
            runtime_resolver=runtime,
        )
    except SkillDependencyContractError as exc:
        assert exc.code == "skill_dependency_external_action_requires_dispatch"
    else:
        raise AssertionError("external dependency must require its own dispatch")
    assert calls == ["structured_requirement_generation"]


def test_legacy_execution_runtime_rejects_dependency_bearing_selected_skill(monkeypatch):
    from app.execution import runtime_boundary

    monkeypatch.setattr(runtime_boundary.settings, "skill_exec_mode", "live")
    request = runtime_boundary.ExecutionRequest(
        task_id="task-dependency-chain",
        message="run selected skill",
        mode="execution",
        requested_skill_id="retrieval_matrix_generation",
    )

    try:
        runtime_boundary.require_materialized_skill_dependency(request)
    except runtime_boundary.SkillDependencyPreconditionError as exc:
        assert str(exc) == "skill_binding_precondition_missing"
    else:
        raise AssertionError("legacy runtime must not execute a dependency-bearing selected Skill")


def test_legacy_execution_runtime_allows_dependency_free_selected_skill(monkeypatch):
    from app.execution import runtime_boundary

    monkeypatch.setattr(runtime_boundary.settings, "skill_exec_mode", "live")
    request = runtime_boundary.ExecutionRequest(
        task_id="task-dependency-chain",
        message="run selected skill",
        mode="execution",
        requested_skill_id="literature_search",
    )

    runtime_boundary.require_materialized_skill_dependency(request)
