"""Tests for the writer-side progress projection sync helpers.

These back the fix for the workbench '进度暂不可确认' bug: write paths that bump
``openair_tasks.revision`` must keep ``progressProjection`` (content + revision
stamp) in sync, else the frontend ``resolveProgressTrust`` rejects the task as
``revision_mismatch``. ``stamp_progress_projection`` is the shared hook the write
helpers call before their UPDATE; ``recompute_progress_projection`` powers it.
"""

from copy import deepcopy
import json
from pathlib import Path

from app.modules.research_progress_projection import (
    recompute_progress_projection,
    stamp_progress_projection,
)

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "shared"
    / "test-fixtures"
    / "research-orchestration-v1.2-valid.json"
)


def _payload():
    return deepcopy(json.loads(FIXTURE.read_text(encoding="utf-8"))["researchTask"])


def test_stamp_recomputes_content_and_aligns_stamp_to_new_revision():
    """A revision bump must carry the projection stamp along with fresh content."""
    payload = _payload()
    # Simulate a prior write that bumped task.revision but left the projection stamp
    # behind — exactly the desync that produced '进度暂不可确认'.
    payload["revision"] = 7
    payload["progressProjection"] = {"revision": 6}

    stamp_progress_projection(payload, next_revision=8)

    projection = payload["progressProjection"]
    assert projection["revision"] == 8  # stamp moved up to the new revision
    # Content was recomputed (not just the number), so the canonical substage
    # dimensions are present and self-consistent with the stamp.
    assert "substageProgress" in projection
    assert "taskProgress" in projection


def test_recompute_returns_full_projection_at_given_revision():
    payload = _payload()
    projection = recompute_progress_projection(payload, revision=11)
    assert projection is not None
    assert projection["revision"] == 11
    # The reducer stamps the top-level task status into the projection too.
    assert projection["taskProgress"]["status"] == payload["status"]


def test_stamp_falls_back_to_restamp_when_payload_does_not_validate():
    """A legacy/corrupt task that can't validate as ResearchTaskV12 must not crash
    the write — it falls back to re-stamping the cached snapshot's revision only
    (parity with the SSE read-side fallback in tasks.py durable_payload)."""
    payload = {"revision": 3, "status": "draft", "progressProjection": {"revision": 2, "taskProgress": {"status": "draft"}}}
    # No substageRuntimeStates / researchDesignVersions → ResearchTaskV12.model_validate fails.
    stamp_progress_projection(payload, next_revision=4)
    # No exception, and the stamp was advanced to the new revision.
    assert payload["progressProjection"]["revision"] == 4
    # Content is otherwise untouched (best-effort: we could not recompute it).
    assert payload["progressProjection"]["taskProgress"]["status"] == "draft"


def test_stamp_is_noop_when_no_projection_and_payload_invalid():
    """A legacy task with no projection at all stays projection-less rather than
    inventing one or raising."""
    payload = {"revision": 1, "status": "draft"}
    stamp_progress_projection(payload, next_revision=2)  # must not raise
    assert "progressProjection" not in payload
