"""Preserve historical planning facts through current aggregate validation."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from app.models.research_orchestration import (
    READ_ONLY_LEGACY_RESEARCH_DESIGN,
    ResearchTaskV12,
    ResearchUnderstanding,
)
from pydantic import ValidationError

FIXTURE = (
    Path(__file__).resolve().parents[6]
    / "packages/shared/test-fixtures/research-orchestration-v1.2-valid.json"
)


def _understanding() -> dict:
    task = json.loads(FIXTURE.read_text(encoding="utf-8"))["researchTask"]
    return task["researchDesignVersions"][0]["understanding"]


@pytest.mark.parametrize(
    "field", ["planningRationale", "planning_rationale", "routeRationale", "route_rationale"]
)
def test_exact_input_names_preserve_fact_and_emit_only_current_name(field: str) -> None:
    source = _understanding()
    original = source.pop("planningRationale")
    source[field] = original
    before = deepcopy(source)
    model = ResearchUnderstanding.model_validate(source)
    dumped = model.model_dump(mode="json", by_alias=True)
    assert source == before
    assert model.planning_rationale == original
    assert dumped["planningRationale"] == original
    assert "routeRationale" not in dumped
    assert "route_rationale" not in dumped
    assert ResearchUnderstanding.model_validate(dumped) == model


@pytest.mark.parametrize("context", [None, {READ_ONLY_LEGACY_RESEARCH_DESIGN: True}])
def test_historical_aggregate_load_and_round_trip(context: dict | None) -> None:
    source = json.loads(FIXTURE.read_text(encoding="utf-8"))["researchTask"]
    understanding = source["researchDesignVersions"][0]["understanding"]
    original = understanding.pop("planningRationale")
    understanding["routeRationale"] = original
    before = deepcopy(source)
    model = ResearchTaskV12.model_validate(source, context=context)
    current = model.model_dump(mode="json", by_alias=True)
    assert source == before
    assert current["researchDesignVersions"][0]["understanding"]["planningRationale"] == original
    assert "routeRationale" not in current["researchDesignVersions"][0]["understanding"]
    assert ResearchTaskV12.model_validate(current) == model


@pytest.mark.parametrize("value", [None, "", 42, True, [], {}])
def test_legacy_input_still_rejects_invalid_values(value: object) -> None:
    source = _understanding()
    source.pop("planningRationale")
    source["routeRationale"] = value
    with pytest.raises(ValidationError):
        ResearchUnderstanding.model_validate(source)


@pytest.mark.parametrize("value", ["conflicting old rationale", "same"])
def test_two_field_names_are_ambiguous_even_when_equal(value: str) -> None:
    source = _understanding()
    source["planningRationale"] = "same"
    source["routeRationale"] = value
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ResearchUnderstanding.model_validate(source)


def test_missing_rationale_and_unknown_fields_remain_invalid() -> None:
    source = _understanding()
    source.pop("planningRationale")
    with pytest.raises(ValidationError, match="Field required"):
        ResearchUnderstanding.model_validate(source)
    source["routeRationale"] = "retained original value"
    source["guessedRationale"] = "must not be accepted"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ResearchUnderstanding.model_validate(source)


def test_schema_only_advertises_current_rationale_name() -> None:
    for mode in ("validation", "serialization"):
        schema = ResearchUnderstanding.model_json_schema(mode=mode)
        assert "planningRationale" in schema["properties"]
        assert "planningRationale" in schema["required"]
        assert "routeRationale" not in schema["properties"]


def test_legacy_unknown_semantics_remain_read_only() -> None:
    source = _understanding()
    source["routeRationale"] = source.pop("planningRationale")
    source.pop("primaryResearchQuestions")
    source.pop("auxiliaryResearchDimensions")
    with pytest.raises(ValidationError):
        ResearchUnderstanding.model_validate(source)
    model = ResearchUnderstanding.model_validate(
        source, context={READ_ONLY_LEGACY_RESEARCH_DESIGN: True}
    )
    assert model.primary_research_questions is None
    assert model.auxiliary_research_dimensions is None
    with pytest.raises(ValidationError):
        ResearchUnderstanding.model_validate(model.model_dump(mode="json", by_alias=True))
