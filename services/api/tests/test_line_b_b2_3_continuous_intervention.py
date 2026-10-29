from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CONTROLLER_REPOSITORY = ROOT / "services/api/app/modules/controller_command_repository.py"
ORCHESTRATION_REPOSITORY = ROOT / "services/api/app/modules/research_orchestration_repository.py"
CONTROLLER_ROUTES = ROOT / "services/api/app/routes/controller_commands.py"


def _load_controller_repository_module():
    psycopg = types.ModuleType("psycopg")
    psycopg.connect = lambda *args, **kwargs: None
    psycopg_rows = types.ModuleType("psycopg.rows")
    psycopg_rows.dict_row = object()
    sys.modules.setdefault("psycopg", psycopg)
    sys.modules.setdefault("psycopg.rows", psycopg_rows)

    config = types.ModuleType("app.config")
    config.settings = types.SimpleNamespace(
        postgres_host="localhost",
        postgres_port=5432,
        postgres_database="openair",
        postgres_user="openair",
        postgres_password="openair",
    )
    sys.modules.setdefault("app.config", config)

    durable = types.ModuleType("app.modules.durable_execution")

    class DurableExecutionError(Exception):
        pass

    durable.DurableExecutionError = DurableExecutionError
    sys.modules.setdefault("app.modules.durable_execution", durable)

    spec = importlib.util.spec_from_file_location("line_b_controller_command_repository", CONTROLLER_REPOSITORY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Cursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.rowcount = 0
        self.executed = []

    def execute(self, query, params=()):
        text = " ".join(str(query).split())
        self.executed.append((text, params))
        if text.startswith("UPDATE openair_controller_commands"):
            command_ids = list(params[-1])
            self.rowcount = len(command_ids)
        else:
            self.rowcount = len(self.rows)

    def fetchall(self):
        return list(self.rows)


def _task_payload():
    return {
        "taskId": "task-1",
        "status": "running",
        "revision": 7,
        "researchDesignVersions": [],
        "executionDecisions": [
            {
                "executionDecisionId": "decision-1",
                "taskId": "task-1",
                "status": "dispatched",
                "decisionSequence": 1,
                "revisionReasonCode": None,
                "assignments": [
                    {
                        "assignmentId": "assignment-primary",
                        "role": "primary",
                        "executorRef": {"executorType": "agent", "executorId": "research-agent"},
                    },
                    {
                        "assignmentId": "assignment-assembler",
                        "role": "assembler",
                        "executorRef": {"executorType": "agent", "executorId": "openair_summary_agent"},
                    },
                ],
                "resultAssemblyPolicy": {"assemblerAssignmentId": "assignment-assembler"},
            }
        ],
        "workUnits": [
            {
                "workUnitId": "work-1",
                "taskId": "task-1",
                "executionDecisionId": "decision-1",
                "assignmentId": "assignment-primary",
                "sourceMode": "live",
                "dispatchable": True,
                "status": "queued",
                "executorRef": {"executorType": "agent", "executorId": "research-agent"},
                "instruction": "Base research instruction.",
            }
        ],
        "capabilityCalls": [],
    }


def _received(command_id: str, text: str, revision: int):
    return {
        "command_id": command_id,
        "payload": {"text": text},
        "user_message": text,
        "submitted_task_revision": revision,
        "target_boundary": {
            "canonicalExecutionDecisionId": "decision-1",
            "canonicalWorkUnitId": "work-1",
        },
    }


def test_running_interventions_enter_real_authorized_execution_context_in_order_once():
    module = _load_controller_repository_module()
    rows = [
        _received("cmd-a", "只关注科研文献问答。", 5),
        _received("cmd-b", "不要企业知识库。", 6),
    ]
    cursor = _Cursor(rows)

    payload, command_ids = module.ControllerCommandRepository.prepare_canonical_at_authorization(
        cursor,
        task_payload=_task_payload(),
        work_unit_id="work-1",
        owner_id="owner-1",
        workspace_id="workspace-1",
    )

    assert command_ids == ["cmd-a", "cmd-b"]
    instruction = payload["instruction"]
    assert instruction.index("[controllerCommandId=cmd-a]") < instruction.index(
        "[controllerCommandId=cmd-b]"
    )
    assert instruction.count("[controllerCommandId=cmd-a]") == 1
    assert instruction.count("[controllerCommandId=cmd-b]") == 1
    assert "只关注科研文献问答。" in instruction
    assert "不要企业知识库。" in instruction

    module.ControllerCommandRepository.mark_canonical_applied_at_action_start(
        cursor,
        task_id="task-1",
        owner_id="owner-1",
        workspace_id="workspace-1",
        execution_decision_id="decision-1",
        work_unit_id="work-1",
        command_ids=command_ids,
    )
    update = next(
        query
        for query, _ in cursor.executed
        if query.startswith("UPDATE openair_controller_commands")
    )
    assert "AND state='received'" in update
    assert "SET state='applied'" in update


def test_recovery_restores_applied_context_without_duplicate_consumption():
    module = _load_controller_repository_module()
    applied_rows = [
        _received("cmd-a", "只关注科研文献问答。", 5),
        _received("cmd-b", "不要企业知识库。", 6),
    ]
    cursor = _Cursor(applied_rows)
    original = {
        "workUnitId": "work-1",
        "instruction": (
            "Base research instruction.\n\n用户在下一安全执行边界补充的要求：\n"
            "- [controllerCommandId=cmd-a] 只关注科研文献问答。\n"
            "- [controllerCommandId=cmd-b] 不要企业知识库。"
        ),
    }

    restored = module.ControllerCommandRepository.restore_canonical_execution_context(
        cursor,
        task_id="task-1",
        owner_id="owner-1",
        workspace_id="workspace-1",
        work_unit_payload=original,
    )

    assert restored["instruction"].count("[controllerCommandId=cmd-a]") == 1
    assert restored["instruction"].count("[controllerCommandId=cmd-b]") == 1
    select = cursor.executed[0][0]
    assert "state='applied'" in select
    assert "ORDER BY submitted_task_revision, created_at, command_id" in select


def test_received_is_not_marked_applied_at_materialization_or_http_receipt():
    repository_source = CONTROLLER_REPOSITORY.read_text(encoding="utf-8")
    routes_source = CONTROLLER_ROUTES.read_text(encoding="utf-8")
    materialization = repository_source.split("def apply_canonical_at_materialization", 1)[1].split(
        "def _append_canonical_supplements", 1
    )[0]
    reconcile = routes_source.split("def _reconcile_canonical_supplement", 1)[1].split(
        "@router.post", 1
    )[0]
    assert "SET state='applied'" not in materialization
    assert 'context.get("taskStatus") == "running"' in reconcile
    assert "return row" in reconcile
    create_block = repository_source.split("def create(", 1)[1].split("def list_for_task", 1)[0]
    assert "status IN ('claimed', 'submission_authorized')" in create_block
    assert "external_action_started_at IS NULL" in create_block


def test_dispatch_authorization_freezes_payload_but_action_start_is_applied_truth():
    source = ORCHESTRATION_REPOSITORY.read_text(encoding="utf-8")
    authorization = source.split("def authorize_dispatch_submission", 1)[1].split(
        "def mark_external_action_started", 1
    )[0]
    action_start = source.split("def mark_external_action_started", 1)[1].split(
        "def record_dispatch_outcome", 1
    )[0]
    assert 'if self._execution_stop_active(raw_payload):' in authorization
    assert "ControllerCommandRepository.prepare_canonical_at_authorization(" in authorization
    assert "work_unit_payload=work_unit_payload" in authorization
    assert "mark_canonical_applied_at_action_start" not in authorization
    assert "external_action_started_at" in action_start
    assert "if cursor.rowcount != 1:" in action_start
    assert "ControllerCommandRepository.mark_canonical_applied_at_action_start(" in action_start
    assert action_start.index("if cursor.rowcount != 1:") < action_start.index(
        "ControllerCommandRepository.mark_canonical_applied_at_action_start("
    )
