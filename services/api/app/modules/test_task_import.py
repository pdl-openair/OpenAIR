from __future__ import annotations

import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[3]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.modules.task_store import TaskStore


def _importable_task(task_id: str) -> dict:
    store = TaskStore()
    created = store.create_task({"title": "Import me", "goalSummary": "goal"})
    payload = store.get_task(created["taskId"])
    assert payload is not None
    payload["taskId"] = task_id
    return payload


def test_import_persisted_task_rejects_missing_task_id() -> None:
    store = TaskStore()
    with pytest.raises(ValueError, match="missing or empty taskId"):
        store.import_persisted_task({"title": "broken"})


def test_import_persisted_task_rejects_duplicate_without_overwrite() -> None:
    store = TaskStore()
    payload = _importable_task("task_import_dup")
    store.import_persisted_task(payload)
    with pytest.raises(ValueError, match="task already exists: task_import_dup"):
        store.import_persisted_task(payload)


def test_import_persisted_task_allows_overwrite() -> None:
    store = TaskStore()
    payload = _importable_task("task_import_overwrite")
    store.import_persisted_task(payload)
    payload["title"] = "Updated title"
    imported = store.import_persisted_task(payload, overwrite=True)
    assert imported["title"] == "Updated title"


def test_import_persisted_task_rejects_invalid_taskout_without_persisting(tmp_path: Path) -> None:
    store = TaskStore()
    store.enable_persistence(tmp_path)
    broken = {"taskId": "task_invalid_shape", "status": "running"}
    with pytest.raises(ValueError, match="task payload failed TaskOut validation"):
        store.import_persisted_task(broken)
    assert not (tmp_path / "task_invalid_shape.json").exists()
    assert store.get_task("task_invalid_shape") is None
