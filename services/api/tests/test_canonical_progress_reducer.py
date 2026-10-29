import pytest

from app.modules.canonical_progress_reducer import CanonicalProgressReducer


def _facts(
    *,
    runtime_status="planned",
    runtime_reason=None,
    unit_status="running",
    task_status="running",
    strategy="agent",
):
    return {
        "revision": 17,
        "active_design": {
            "status": "confirmed",
            "instances": [
                {
                    "substageInstanceId": "instance-1",
                    "instanceRevision": 1,
                    "stage": "problem_modeling",
                    "label": "Frame the question",
                }
            ],
        },
        "substage_runtime_states": [
            {
                "substageInstanceId": "instance-1",
                "instanceRevision": 1,
                "status": runtime_status,
                "statusReasonCode": runtime_reason,
                "workUnitIds": ["unit-1"],
            }
        ],
        "work_units": [
            {
                "workUnitId": "unit-1",
                "substageInstanceId": "instance-1",
                "instanceRevision": 1,
                "status": unit_status,
                "strategy": strategy,
            }
        ],
        "capability_calls": [],
        "artifacts": [],
        "confirmations": [],
        "task_status": task_status,
        "agent_interactions": [],
    }


@pytest.mark.parametrize(
    ("runtime_status", "unit_status"),
    [
        ("planned", "running"),
        ("ready", "failed"),
        ("completed", "blocked"),
        ("failed", "completed"),
    ],
)
def test_projection_reads_canonical_substage_status(runtime_status, unit_status):
    projection = CanonicalProgressReducer().reduce(
        **_facts(runtime_status=runtime_status, unit_status=unit_status)
    )

    assert projection["substageProgress"][0]["status"] == runtime_status


def test_lower_level_failures_cannot_reopen_completed_substage():
    facts = _facts(
        runtime_status="completed",
        unit_status="failed",
        runtime_reason=None,
    )
    facts["work_units"][0]["terminalReasonCode"] = "historical_failure"
    facts["capability_calls"] = [
        {
            "capabilityCallId": "call-1",
            "substageInstanceId": "instance-1",
            "instanceRevision": 1,
            "status": "failed",
        }
    ]

    projection = CanonicalProgressReducer().reduce(**facts)

    assert projection["substageProgress"][0]["status"] == "completed"
    assert projection["substageProgress"][0]["statusReasonCode"] is None
    assert projection["stageProgress"][0]["status"] == "completed"


@pytest.mark.parametrize(
    "task_status",
    [
        "draft",
        "not_started",
        "running",
        "awaiting_confirmation",
        "completed",
        "failed",
        "cancelled",
    ],
)
def test_projection_reads_canonical_task_status(task_status):
    projection = CanonicalProgressReducer().reduce(
        **_facts(task_status=task_status)
    )

    assert projection["taskProgress"]["status"] == task_status


def test_confirmation_refs_require_canonical_confirmation_task_status():
    facts = _facts(
        runtime_status="ready",
        runtime_reason="stage_confirmation_required",
        task_status="running",
    )
    assert (
        CanonicalProgressReducer()
        .reduce(**facts)["taskProgress"]["awaitingConfirmationRefs"]
        == []
    )

    facts["task_status"] = "awaiting_confirmation"
    assert (
        CanonicalProgressReducer()
        .reduce(**facts)["taskProgress"]["awaitingConfirmationRefs"]
        == ["instance-1"]
    )


def test_direct_skill_is_a_tool_record_not_a_collaboration_member():
    projection = CanonicalProgressReducer().reduce(
        **_facts(strategy="direct_skill")
    )

    assert projection["workbenchProjection"]["collaborationNodes"] == []
    assert projection["workbenchProjection"]["toolResourceRecords"] == [
        {"workUnitId": "unit-1", "kind": "skill"}
    ]


def test_same_canonical_facts_produce_same_projection():
    facts = _facts(runtime_status="blocked_recoverable")
    reducer = CanonicalProgressReducer()

    assert reducer.reduce(**facts) == reducer.reduce(**facts)
    assert reducer.reduce(**facts)["revision"] == 17
