"""Regression: skillPack normalization must propagate from draft.plan back to
payload, otherwise task.skillPack and creationPlan.skillPack diverge and the
cross-pack bleed guard blocks the task on first execution.

Covers reviewer feedback item 2 on PR #270.
"""

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_unknown_skillpack_is_normalized_to_sdr_and_propagated() -> None:
    """An unknown skillPack must be normalized to "sdr" by the draft builder,
    and the normalized value must be written back to BOTH task.skillPack and
    task.creationPlan.skillPack. Pre-fix: task.skillPack stayed as the raw
    user input ("badpack") while plan used "sdr" → cross-pack guard blocked."""
    resp = client.post(
        "/api/tasks/draft",
        json={"goalSummary": "demo goal", "skillPack": "badpack"},
    )
    assert resp.status_code == 201, resp.text
    task = resp.json()

    assert task["skillPack"] == "sdr", task.get("skillPack")
    plan = task.get("creationPlan") or {}
    assert plan.get("skillPack") == "sdr", plan.get("skillPack")


def test_known_skillpack_passes_through_unchanged() -> None:
    """Sanity: legitimate packs (sdr/materials/ope/opentrajectory) must not be
    rewritten."""
    resp = client.post(
        "/api/tasks/draft",
        json={"goalSummary": "materials demo", "skillPack": "materials"},
    )
    assert resp.status_code == 201, resp.text
    task = resp.json()
    assert task["skillPack"] == "materials"
    assert (task.get("creationPlan") or {}).get("skillPack") == "materials"


def test_missing_skillpack_defaults_to_sdr() -> None:
    """No skillPack supplied → defaults to sdr everywhere."""
    resp = client.post(
        "/api/tasks/draft",
        json={"goalSummary": "minimal"},
    )
    assert resp.status_code == 201, resp.text
    task = resp.json()
    assert task["skillPack"] == "sdr"
    assert (task.get("creationPlan") or {}).get("skillPack") == "sdr"
