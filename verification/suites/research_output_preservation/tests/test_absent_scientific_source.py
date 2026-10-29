from __future__ import annotations

import pytest
from app.execution.research_input_contract_gate import (
    InputContractGateFailure,
    resolve_registered_skill_input,
    validate_registered_skill_input_snapshot,
)
from app.execution.skill_contract_policy import validate_action_input
from app.execution.tests.research_input_contract_gate.test_research_input_contract_gate import (
    _artifact,
    _request,
    _snapshot,
    _target_binding,
)


@pytest.mark.parametrize("presence", ["required", "conditional"])
@pytest.mark.parametrize("cardinality", ["one", "many"])
@pytest.mark.parametrize("source_field", ["$", "value"])
def test_absent_scientific_source_reaches_consumer_as_unknown(
    presence: str,
    cardinality: str,
    source_field: str,
) -> None:
    schema = {
        "type": "object",
        "required": ["source_payload"],
        "properties": {"source_payload": {"type": "object"}},
    }
    snapshot = _snapshot(target_input=schema, source_output={"type": "object"})
    binding = _target_binding(presence=presence)
    field = binding["inputSources"]["fields"][0]
    field["source"].update(cardinality=cardinality, field=source_field)
    field["defaultValue"] = {"status": "ready"}
    resolved = resolve_registered_skill_input(
        snapshot=snapshot,
        request=_request(),
        input_context={"artifacts": []},
        skill_binding=binding,
    )
    assert resolved == {"source_payload": None}
    assert (
        validate_registered_skill_input_snapshot(
            snapshot=snapshot,
            request=_request(),
            resolved_input=resolved,
            skill_binding=binding,
        )
        == resolved
    )
    with pytest.raises(ValueError, match="input_contract_invalid"):
        validate_action_input(snapshot["definitions"]["skills"][0], resolved)


def test_absence_does_not_weaken_source_binding_identity() -> None:
    snapshot = _snapshot(target_input={"type": "object"}, source_output={"type": "object"})
    with pytest.raises(InputContractGateFailure, match="source_lineage_mismatch"):
        resolve_registered_skill_input(
            snapshot=snapshot,
            request=_request(),
            skill_binding=_target_binding(),
            input_context={"artifacts": [_artifact({"status": "failed"}, binding_id="other")]},
        )


def test_missing_runtime_input_stays_a_contract_failure() -> None:
    snapshot = _snapshot(target_input={"type": "object"}, source_output={"type": "object"})
    binding = _target_binding(
        runtime_fields=[
            {
                "targetField": "executable",
                "source": {"kind": "invocationContext", "field": "executable"},
            }
        ]
    )
    with pytest.raises(InputContractGateFailure):
        resolve_registered_skill_input(
            snapshot=snapshot,
            request=_request(),
            skill_binding=binding,
            input_context={"artifacts": [], "invocationContext": {}},
        )
