"""Regression test for the postgres legacy-taskstore mutation guard (D5).

The JSON TestClient can't exercise the postgres guard, which is how the
clone/reuse and artifact-edit 503 regressions slipped through (a new repo-backed
write route under /api/tasks or /api/artifacts that isn't in the allowlist
returns 503 in postgres mode). This test pins every repo-backed write route.
"""
from __future__ import annotations

from app.main import _LEGACY_TASKSTORE_PREFIXES, _postgres_legacy_mutation_allowed


def _blocked(method: str, path: str) -> bool:
    """Mirror the middleware's block decision without HTTP."""
    return not _postgres_legacy_mutation_allowed(method, path) and path.startswith(
        _LEGACY_TASKSTORE_PREFIXES
    )


# Routes that write through postgres repositories and MUST be allowed.
ALLOWED_WRITES = [
    ("POST", "/api/tasks"),
    ("POST", "/api/tasks/draft"),
    ("POST", "/api/tasks/task_x/confirm-plan"),
    ("POST", "/api/tasks/task_x/update-plan"),
    ("POST", "/api/tasks/task_x/research-design/agent-revision"),  # canonical RD version append
    ("POST", "/api/tasks/task_x/start"),
    ("POST", "/api/tasks/task_x/controller-commands"),
    ("POST", "/api/tasks/task_x/stop-run"),
    ("POST", "/api/tasks/task_x/resume-run"),
    ("PATCH", "/api/tasks/task_x"),
    ("DELETE", "/api/tasks/task_x"),
    ("POST", "/api/tasks/task_x/revise-plan"),
    ("POST", "/api/tasks/task_x/cancel-draft"),
    ("POST", "/api/tasks/task_x/clone"),  # A1
    ("POST", "/api/tasks/task_x/pvd/experiment-feedback"),  # PVD round-2 feedback
    ("POST", "/api/tasks/task_x/collaboration/decisions/dec_y"),
    ("POST", "/api/tasks/task_x/agent/chat/stream"),
    ("PATCH", "/api/tasks/task_x/execution-mode"),
    ("PATCH", "/api/tasks/task_x/execution-policy"),
    ("POST", "/api/artifacts/upload"),
    ("DELETE", "/api/artifacts/art_x"),
    ("POST", "/api/artifacts/art_x/review"),  # P2b
    ("POST", "/api/artifacts/art_x/confirm-scope-expansion"),
    ("POST", "/api/artifacts/art_x/reuse"),
    ("PATCH", "/api/artifacts/art_x/content"),  # B1
    ("POST", "/api/artifacts/art_x/versions/v_abc/restore"),  # B1
]

# Unknown/legacy mutations under the legacy prefixes MUST be blocked.
BLOCKED_WRITES = [
    ("POST", "/api/tasks/task_x/recovery-requests"),
    ("POST", "/api/tasks/task_x/recovery-requests/recovery_x/confirm"),
    ("POST", "/api/tasks/drafts"),  # only the exact repository-backed draft route
    ("POST", "/api/tasks/task_x/unknown-mutation"),
    ("POST", "/api/tasks/nested/task_x/stop-run"),
    ("POST", "/api/tasks/nested/task_x/resume-run"),
    ("POST", "/api/tasks/task_x/agent/chat"),  # non-stream chat variant
    ("PATCH", "/api/artifacts/art_x/unknown"),
    ("POST", "/api/artifacts/art_x/regenerate"),  # deliberately gated (durable gate)
    ("POST", "/api/starters/starter_x/use"),  # starters write through TaskStore
]


def test_allowed_write_routes_are_not_blocked():
    for method, path in ALLOWED_WRITES:
        assert not _blocked(method, path), f"{method} {path} should be allowed in postgres mode"


def test_unknown_legacy_mutations_are_blocked():
    for method, path in BLOCKED_WRITES:
        assert _blocked(method, path), f"{method} {path} should be blocked (503) in postgres mode"


def test_community_routes_pass_regardless_of_allowlist():
    # /api/community/* is not under the legacy prefixes, so it is never blocked
    # by the taskstore guard even though it isn't in the allowlist.
    for method, path in [
        ("POST", "/api/community/publications"),
        ("POST", "/api/community/publications/pub_x/fork"),
        ("POST", "/api/community/publications/pub_x/comments"),
    ]:
        assert not _blocked(method, path), f"{method} {path} should not be blocked"


def test_guard_is_bypassed_outside_legacy_prefixes():
    # An unknown POST elsewhere is not blocked (the guard only covers legacy prefixes).
    assert not _blocked("POST", "/api/something-else/unknown")
