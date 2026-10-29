"""Tests for the blocked-start replay expiry by task revision.

Backs the fix for the workbench "永久卡死" bug: a persisted ``blocked:`` outcome
in ``openair_execution_start_attempts`` used to be replayed forever (selected by
``idempotency_key_sha256``, ``LIMIT 1``), pinning a task after a single transient
preflight block. Blocked outcomes now record ``facts.taskRevision`` at block time;
when the task has advanced past that revision, the stale verdict is dropped and the
caller re-evaluates. Legacy blocks lacking ``taskRevision`` keep their original
always-replay behavior.
"""

from app.modules.research_orchestration_repository import _blocked_outcome_expired


def test_expired_when_task_revision_advanced_past_block():
    """A block recorded at taskRevision=5 must not replay once the task is at 6+."""
    assert _blocked_outcome_expired({"taskRevision": 5}, 6) is True


def test_persists_at_same_revision():
    """At the exact blocked revision the verdict still holds (no false release)."""
    assert _blocked_outcome_expired({"taskRevision": 5}, 5) is False


def test_persists_when_revision_behind():
    """A revision smaller than the blocked one (defensive) must not expire it."""
    assert _blocked_outcome_expired({"taskRevision": 5}, 4) is False


def test_legacy_block_without_task_revision_stays_blocked():
    """Blocks written before facts.taskRevision existed keep always-replay semantics."""
    assert _blocked_outcome_expired({}, 99) is False


def test_non_integer_task_revision_is_ignored():
    """A malformed taskRevision fact must not silently expire a real block."""
    assert _blocked_outcome_expired({"taskRevision": "five"}, 99) is False
    assert _blocked_outcome_expired({"taskRevision": None}, 99) is False
