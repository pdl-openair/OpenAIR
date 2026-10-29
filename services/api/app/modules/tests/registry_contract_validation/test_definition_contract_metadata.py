import pytest

from app.modules.registry_snapshot_repository import (
    RegistrySnapshotIntegrityError,
    parse_spec_document,
)


def _spec(metadata: str) -> str:
    return f"""# Contract test

## Input
- `input_a`

## Output
- `artifact_a`

## Acceptance
- `quality_rule_a`
- `quality_rule_b`

## Contract Metadata
```yaml
{metadata}
```
"""


def test_v2_separates_quality_rules_from_evaluator_authority():
    parsed = parse_spec_document(
        "demo",
        "demo.md",
        _spec(
            """schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
evaluatorRefs: [domain_reviewer]
deterministicValidatorRefs: [shape_validator]"""
        ),
    )

    assert parsed["specSource"]["parsed"]["parserVersion"] == "registry-spec-parser.v2"
    acceptance = parsed["acceptanceContract"]
    assert acceptance["qualityRuleRefs"] == {
        "status": "known",
        "values": ["quality_rule_a", "quality_rule_b"],
    }
    assert acceptance["judgmentRequirement"] == {
        "status": "known",
        "value": "domain_professional",
    }
    assert acceptance["evaluatorRefs"] == {
        "status": "known",
        "values": ["domain_reviewer"],
    }
    assert acceptance["deterministicValidatorRefs"] == {
        "status": "known",
        "values": ["shape_validator"],
    }
    assert "criterionRefs" not in acceptance

def test_v2_preserves_known_empty_vs_unknown():
    known_empty = parse_spec_document(
        "demo",
        "known-empty.md",
        _spec(
            """schemaVersion: definition-contract-metadata.v1
judgmentRequirement: deterministic
evaluatorRefs: []
deterministicValidatorRefs: [shape_validator]"""
        ),
    )
    assert known_empty["acceptanceContract"]["evaluatorRefs"] == {
        "status": "known",
        "values": [],
    }

    unknown = parse_spec_document(
        "demo",
        "unknown.md",
        _spec(
            """schemaVersion: definition-contract-metadata.v1
judgmentRequirement: deterministic
deterministicValidatorRefs: [shape_validator]"""
        ),
    )
    assert unknown["acceptanceContract"]["evaluatorRefs"] == {
        "status": "unknown",
        "values": [],
    }

def test_v1_remains_unchanged_without_contract_metadata():
    content = """# Legacy

## Input
- `input_a`

## Output
- `artifact_a`

## Acceptance
- `legacy_evaluator`
"""
    parsed = parse_spec_document("demo", "legacy.md", content)
    assert parsed["specSource"]["parsed"]["parserVersion"] == "registry-spec-parser.v1"
    assert parsed["acceptanceContract"]["criterionRefs"]["values"] == ["legacy_evaluator"]

def test_v2_rejects_invalid_metadata():
    with pytest.raises(RegistrySnapshotIntegrityError):
        parse_spec_document(
            "demo",
            "invalid.md",
            _spec(
                """schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
evaluatorRefs: [reviewer, reviewer]"""
            ),
        )
