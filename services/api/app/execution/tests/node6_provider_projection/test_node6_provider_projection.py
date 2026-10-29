from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from app.execution.provider_visible_skill_input import project_provider_parameters


REPO = Path(__file__).resolve().parents[6]
SKILL = REPO / "skills/opentrajectory/opentrajectory-source-screening-selection"
EXPECTED_FIELDS = [
    "candidate_id",
    "title",
    "abstract",
    "authors",
    "year",
    "venue",
    "doi",
]


def _metadata() -> dict:
    metadata = yaml.safe_load((SKILL / "skill.yaml").read_text(encoding="utf-8"))
    assert isinstance(metadata, dict)
    return metadata


def test_node6_declares_exact_provider_visible_candidate_fields() -> None:
    projection = _metadata()["llm"]["provider_input_projection"]
    assert projection["array_fields"]["candidates"]["fields"] == EXPECTED_FIELDS


def test_node6_projects_scientific_candidate_without_mutating_canonical_input() -> None:
    parameters = {
        "task_id": "task-t12",
        "research_context": {"research_object": "AlScN d33 optimization"},
        "candidates": [{
            "candidate_id": "c1",
            "title": "AlScN sputtering study",
            "abstract": "Scientific abstract",
            "authors": ["A. Researcher"],
            "year": 2025,
            "venue": "Journal",
            "doi": "10.1000/example",
            "provenance": [{"source_name": "openalex"}],
            "access_routes": [{"url": "https://example.test"}],
            "citation_count": 999,
            "raw_metadata": {"provider_payload": "internal"},
            "retry": {"attempts": 1},
            "warnings": ["transport warning"],
        }],
    }
    canonical = deepcopy(parameters)

    projected = project_provider_parameters(_metadata(), parameters)

    assert parameters == canonical
    assert projected is not parameters
    assert list(projected["candidates"][0]) == EXPECTED_FIELDS
    assert projected["candidates"][0] == {
        field: canonical["candidates"][0][field] for field in EXPECTED_FIELDS
    }
    assert projected["research_context"] == canonical["research_context"]
