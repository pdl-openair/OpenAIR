"""Unit tests for dev fixture ownership rebind."""

from __future__ import annotations

import copy

from app.modules.task_store import DEFAULT_OWNER_ID, DEFAULT_WORKSPACE_ID, TaskStore


def test_rebind_legacy_fixture_ownership_rewrites_task_and_children() -> None:
    store = TaskStore()
    store.seed_tasks(
        {
            "task_fixture_001": {
                "taskId": "task_fixture_001",
                "ownerId": DEFAULT_OWNER_ID,
                "workspaceId": DEFAULT_WORKSPACE_ID,
                "artifacts": [
                    {
                        "id": "art-001",
                        "ownerId": DEFAULT_OWNER_ID,
                        "workspaceId": DEFAULT_WORKSPACE_ID,
                    }
                ],
                "capabilityCalls": [
                    {
                        "id": "call-001",
                        "ownerId": DEFAULT_OWNER_ID,
                        "workspaceId": DEFAULT_WORKSPACE_ID,
                    }
                ],
            },
            "task_user_owned": {
                "taskId": "task_user_owned",
                "ownerId": "user_real",
                "workspaceId": "ws_real",
            },
        }
    )

    rebinded = store.rebind_legacy_fixture_ownership("user_dev", "ws_dev")
    assert rebinded == 1

    task = store.get_task("task_fixture_001")
    assert task is not None
    assert task["ownerId"] == "user_dev"
    assert task["workspaceId"] == "ws_dev"
    assert task["artifacts"][0]["ownerId"] == "user_dev"
    assert task["capabilityCalls"][0]["workspaceId"] == "ws_dev"

    untouched = store.get_task("task_user_owned")
    assert untouched is not None
    assert untouched["ownerId"] == "user_real"


def test_rebind_legacy_fixture_ownership_noop_on_empty_ids() -> None:
    store = TaskStore()
    store.seed_tasks(
        {
            "task_fixture_002": {
                "taskId": "task_fixture_002",
                "ownerId": DEFAULT_OWNER_ID,
                "workspaceId": DEFAULT_WORKSPACE_ID,
            }
        }
    )
    assert store.rebind_legacy_fixture_ownership("", "ws_dev") == 0
    assert store.get_task("task_fixture_002")["ownerId"] == DEFAULT_OWNER_ID


def test_rebind_after_persistence_overwrite_restores_dev_ownership() -> None:
    """Simulate postgres/json load overwriting seeded fixtures, then rebind."""
    store = TaskStore()
    store.seed_tasks(
        {
            "task_fixture_001": {
                "taskId": "task_fixture_001",
                "ownerId": DEFAULT_OWNER_ID,
                "workspaceId": DEFAULT_WORKSPACE_ID,
                "artifacts": [{"id": "art-001", "ownerId": DEFAULT_OWNER_ID, "workspaceId": DEFAULT_WORKSPACE_ID}],
            },
        }
    )
    assert store.rebind_legacy_fixture_ownership("user_dev", "ws_dev") == 1

    # Postgres load_persisted_tasks overwrites by taskId without merge.
    legacy_copy = copy.deepcopy(store.get_task("task_fixture_001"))
    assert legacy_copy is not None
    legacy_copy["ownerId"] = DEFAULT_OWNER_ID
    legacy_copy["workspaceId"] = DEFAULT_WORKSPACE_ID
    legacy_copy["artifacts"][0]["ownerId"] = DEFAULT_OWNER_ID
    legacy_copy["artifacts"][0]["workspaceId"] = DEFAULT_WORKSPACE_ID
    store._tasks["task_fixture_001"] = legacy_copy

    assert store.get_task("task_fixture_001")["ownerId"] == DEFAULT_OWNER_ID

    rebinded = store.rebind_legacy_fixture_ownership("user_dev", "ws_dev")
    assert rebinded == 1
    task = store.get_task("task_fixture_001")
    assert task is not None
    assert task["ownerId"] == "user_dev"
    assert task["workspaceId"] == "ws_dev"
    assert task["artifacts"][0]["ownerId"] == "user_dev"


def test_rebind_fixture_seed_task_id_even_when_overwrite_not_legacy_defaults() -> None:
    """Fixture IDs should still rebind after persistence overwrite with arbitrary ownership."""
    store = TaskStore()
    store.register_fixture_seed_task_ids({"task_fixture_003"})
    store.seed_tasks(
        {
            "task_fixture_003": {
                "taskId": "task_fixture_003",
                "ownerId": DEFAULT_OWNER_ID,
                "workspaceId": DEFAULT_WORKSPACE_ID,
                "artifacts": [{"id": "art-001", "ownerId": DEFAULT_OWNER_ID, "workspaceId": DEFAULT_WORKSPACE_ID}],
                "capabilityCalls": [{"id": "call-001", "ownerId": DEFAULT_OWNER_ID, "workspaceId": DEFAULT_WORKSPACE_ID}],
            },
        }
    )

    overwritten = copy.deepcopy(store.get_task("task_fixture_003"))
    assert overwritten is not None
    overwritten["ownerId"] = "user_persisted_other"
    overwritten["workspaceId"] = "ws_persisted_other"
    overwritten["artifacts"][0]["ownerId"] = "user_persisted_other"
    overwritten["artifacts"][0]["workspaceId"] = "ws_persisted_other"
    overwritten["capabilityCalls"][0]["ownerId"] = "user_persisted_other"
    overwritten["capabilityCalls"][0]["workspaceId"] = "ws_persisted_other"
    store._tasks["task_fixture_003"] = overwritten

    assert store.rebind_legacy_fixture_ownership("user_dev", "ws_dev") == 1
    task = store.get_task("task_fixture_003")
    assert task is not None
    assert task["ownerId"] == "user_dev"
    assert task["workspaceId"] == "ws_dev"
    assert task["artifacts"][0]["ownerId"] == "user_dev"
    assert task["artifacts"][0]["workspaceId"] == "ws_dev"
    assert task["capabilityCalls"][0]["ownerId"] == "user_dev"
    assert task["capabilityCalls"][0]["workspaceId"] == "ws_dev"
