from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from app.models.research_orchestration import ResearchTaskV12
from app.modules import research_design_service as design_service
from app.modules.research_start_repository import ResearchStartRepository


CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "research_design_v1_cases.json").read_text(
        encoding="utf-8"
    )
)
SNAPSHOT = {"schemaVersion": 1, "contentHash": "sha256:" + "b" * 64}


class _DefinitionSource:
    definitions = {
        "sdr:evidence_synthesis": (1, "solution_design"),
        "sdr:source_collection": (3, "problem_modeling"),
    }

    def snapshot_ref(self):
        return SNAPSHOT

    def resolve(self, definition_id: str):
        if definition_id not in self.definitions:
            return None
        version, stage = self.definitions[definition_id]
        return design_service.ResolvedDefinition(
            definition_id=definition_id,
            definition_version=version,
            stage_key=stage,
        )

    def definition_is_eligible_for_workflow_admission(self, definition_id, workflow_admission):
        return definition_id in self.definitions


class _Repository:
    def __init__(self) -> None:
        self.task = design_service.new_research_task(
            task_id="task_reserved",
            registry_snapshot_ref=SNAPSHOT,
        )

    def load(self, **_kwargs):
        return self.task.model_copy(deep=True)

    def load_planning_workflow_admission(
        self, *, task_id: str, owner_id: str, workspace_id: str
    ):
        assert task_id == self.task.task_id
        return None

    def save(self, task, *, expected_task_revision: int, **_kwargs):
        assert self.task.revision == expected_task_revision
        payload = task.model_dump(mode="json", by_alias=True)
        payload["revision"] = expected_task_revision + 1
        self.task = ResearchTaskV12.model_validate(payload)
        return self.task.model_copy(deep=True)


class _Cursor:
    def __init__(self, task: ResearchTaskV12) -> None:
        self._task = task

    def execute(self, _sql: str, _params: object) -> None:
        pass

    def fetchone(self):
        return {"payload": self._task.model_dump(mode="json", by_alias=True)}


def _service() -> tuple[_Repository, design_service.ResearchDesignService]:
    repository = _Repository()
    service = design_service.ResearchDesignService(
        repository=repository,
        definition_source=_DefinitionSource(),
        clock=lambda: datetime(2026, 8, 7, tzinfo=timezone.utc),
    )
    return repository, service


def _plan(
    repository: _Repository,
    service: design_service.ResearchDesignService,
    *,
    request_id: str,
    provider_call_ref: str,
) -> None:
    service.plan(
        task_id="task_reserved",
        owner_id="owner",
        workspace_id="workspace",
        request_id=request_id,
        expected_task_revision=repository.task.revision,
        provider=lambda _key: design_service.PlannerProviderResult(
            proposal=deepcopy(CASES["simple"]),
            provider_call_ref=provider_call_ref,
        ),
    )


def _receipt(task: ResearchTaskV12, attempt: dict[str, object]):
    return ResearchStartRepository._committed_design_receipt(
        _Cursor(task),
        {
            "session_id": "intent_123456789abc",
            "reserved_task_id": "task_reserved",
            "owner_id": "owner",
            "workspace_id": "workspace",
        },
        {"request_id": "rsr_test"},
        attempt,
    )


@pytest.mark.parametrize("reject_latest", (False, True))
def test_receipt_selects_latest_design_before_attempt_lineage(reject_latest: bool) -> None:
    repository, service = _service()
    _plan(repository, service, request_id="rsr_test", provider_call_ref="provider:rsr_test")
    _plan(repository, service, request_id="later-revision", provider_call_ref="provider:later")

    if reject_latest:
        latest = max(repository.task.research_design_versions, key=lambda row: row.version)
        service.confirm(
            task_id="task_reserved",
            owner_id="owner",
            workspace_id="workspace",
            actor_id="owner",
            design_id=latest.research_design_id,
            version=latest.version,
            confirmation_id="confirmation-latest",
            decision="rejected",
            expected_task_revision=repository.task.revision,
        )

    assert _receipt(
        repository.task,
        {
            "phase": "research_planner",
            "ordinal": 1,
            "state": "succeeded",
            "result_payload": {"providerCallRef": "provider:rsr_test"},
        },
    ) is None


@pytest.mark.parametrize(
    "result_payload",
    (None, {}, {"providerCallRef": ""}, {"providerCallRef": "provider:attempt"}),
)
def test_succeeded_attempt_requires_matching_provider_call_ref(result_payload: object) -> None:
    repository, service = _service()
    _plan(repository, service, request_id="rsr_test", provider_call_ref="provider:task")

    assert _receipt(
        repository.task,
        {
            "phase": "research_planner",
            "ordinal": 1,
            "state": "succeeded",
            "result_payload": result_payload,
        },
    ) is None


def test_succeeded_attempt_accepts_matching_provider_call_ref() -> None:
    repository, service = _service()
    _plan(repository, service, request_id="rsr_test", provider_call_ref="provider:shared")

    receipt = _receipt(
        repository.task,
        {
            "phase": "research_planner",
            "ordinal": 1,
            "state": "succeeded",
            "result_payload": {"providerCallRef": "provider:shared"},
        },
    )

    assert receipt is not None
    assert receipt["providerCallRef"] == "provider:shared"
    assert receipt["researchDesignRef"]["version"] == 1
