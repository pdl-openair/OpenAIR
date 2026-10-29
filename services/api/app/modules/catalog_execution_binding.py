"""Server-side authority for Resource Plaza execution pins.

Catalog release versions are audit facts; runtime candidate versions come from the
frozen Registry snapshot.  A catalog capability is bindable when exactly one
canonical Skill/Agent definition is enabled and that capability has at least one
enabled binding to an enabled Substage Definition.

Aligned with ``ProductionDefinitionSource.planner_catalog``: machine-complete
input/output/acceptance contracts remain authoritative when present
(``contractComplete``), but missing Spec contract detail must not erase an
otherwise enabled Definition from plaza bindability / planner scoping.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class CatalogExecutionBinding:
    catalog_intent_id: str
    resource_id: str
    resource_version: str
    capability_id: str
    capability_kind: str

    def to_public(self) -> dict[str, str]:
        return {
            "catalogIntentId": self.catalog_intent_id,
            "resourceId": self.resource_id,
            "resourceVersion": self.resource_version,
            "capabilityId": self.capability_id,
            "capabilityKind": self.capability_kind,
        }


def _capability_kind(resource_id: str) -> str | None:
    prefix, separator, _rest = resource_id.partition(":")
    if separator and prefix in {"skill", "agent"}:
        return prefix
    return None


def binding_from_session(session: Mapping[str, Any]) -> CatalogExecutionBinding | None:
    catalog_intent_id = session.get("catalog_intent_id")
    if catalog_intent_id is None:
        return None
    values = (
        catalog_intent_id,
        session.get("selected_resource_id"),
        session.get("selected_resource_version"),
        session.get("selected_capability_id"),
    )
    if not all(isinstance(value, str) and value.strip() for value in values):
        raise ValueError("catalog_execution_binding_incomplete")
    resource_id = str(values[1])
    capability_id = str(values[3])
    kind = _capability_kind(resource_id)
    if kind is None or resource_id != f"{kind}:{capability_id}":
        raise ValueError("catalog_execution_binding_identity_mismatch")
    return CatalogExecutionBinding(
        catalog_intent_id=str(values[0]),
        resource_id=resource_id,
        resource_version=str(values[2]),
        capability_id=capability_id,
        capability_kind=kind,
    )


def apply_catalog_session_authority(
    intent: Mapping[str, Any],
    session: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the canonical submit intent for a locked Research Start session."""
    binding = binding_from_session(session)
    normalized = dict(intent)
    if binding is None:
        return normalized
    session_id = session.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("catalog_execution_session_identity_missing")
    normalized["sessionId"] = session_id
    normalized["selectedHint"] = binding.capability_id
    return normalized


def binding_from_public(value: Mapping[str, Any] | None) -> CatalogExecutionBinding | None:
    if value is None:
        return None
    kind = value.get("capabilityKind")
    resource_id = value.get("resourceId")
    capability_id = value.get("capabilityId")
    values = (
        value.get("catalogIntentId"),
        resource_id,
        value.get("resourceVersion"),
        capability_id,
        kind,
    )
    if not all(isinstance(item, str) and item.strip() for item in values):
        raise ValueError("catalog_execution_binding_incomplete")
    if kind not in {"skill", "agent"} or resource_id != f"{kind}:{capability_id}":
        raise ValueError("catalog_execution_binding_identity_mismatch")
    return CatalogExecutionBinding(
        catalog_intent_id=str(values[0]),
        resource_id=str(resource_id),
        resource_version=str(values[2]),
        capability_id=str(capability_id),
        capability_kind=str(kind),
    )


_CONTRACT_FIELDS = (
    ("inputContract", "requiredInputRefs"),
    ("outputContract", "artifactTypes"),
    ("acceptanceContract", "criterionRefs"),
)


def definition_contract_is_complete(row: Mapping[str, Any]) -> bool:
    """Return whether frozen Spec contracts are machine-complete (planner ``contractComplete``)."""
    for contract_key, values_key in _CONTRACT_FIELDS:
        contract = row.get(contract_key)
        declared = contract.get(values_key) if isinstance(contract, Mapping) else None
        if (
            not isinstance(contract, Mapping)
            or contract.get("status") != "known"
            or not isinstance(declared, Mapping)
            or declared.get("status") != "known"
        ):
            return False
        values = tuple(
            str(value).strip()
            for value in declared.get("values") or ()
            if str(value).strip()
        )
        if not values:
            return False
    return True


def planner_eligible_definition_refs(snapshot: Mapping[str, Any]) -> frozenset[tuple[str, int]]:
    """Return enabled Substage Definition refs (planner-catalog visibility).

    Historically this required complete Spec contracts. That diverged from
    ``ProductionDefinitionSource.planner_catalog``, which keeps semantic-only
    Definitions visible and marks ``contractComplete`` separately. Plaza
    bindability and catalog-scoped planning now follow the same enabled set;
    use ``contract_complete_definition_refs`` for the stricter contract filter.
    """
    definitions = snapshot.get("definitions", {})
    rows = definitions.get("substages", []) if isinstance(definitions, Mapping) else []
    eligible: set[tuple[str, int]] = set()
    for row in rows:
        if not isinstance(row, Mapping) or row.get("enabled") is not True:
            continue
        if isinstance(row.get("definitionId"), str) and isinstance(row.get("definitionVersion"), int):
            eligible.add((str(row["definitionId"]), int(row["definitionVersion"])))
    return frozenset(eligible)


def contract_complete_definition_refs(snapshot: Mapping[str, Any]) -> frozenset[tuple[str, int]]:
    """Return enabled Definitions whose Spec input/output/acceptance contracts are complete."""
    definitions = snapshot.get("definitions", {})
    rows = definitions.get("substages", []) if isinstance(definitions, Mapping) else []
    complete: set[tuple[str, int]] = set()
    for row in rows:
        if not isinstance(row, Mapping) or row.get("enabled") is not True:
            continue
        if not isinstance(row.get("definitionId"), str) or not isinstance(row.get("definitionVersion"), int):
            continue
        if definition_contract_is_complete(row):
            complete.add((str(row["definitionId"]), int(row["definitionVersion"])))
    return frozenset(complete)


def _definition_versions(snapshot: Mapping[str, Any], binding: CatalogExecutionBinding) -> tuple[int, ...]:
    key = "skills" if binding.capability_kind == "skill" else "agents"
    id_key = "skillId" if binding.capability_kind == "skill" else "agentId"
    definitions = snapshot.get("definitions", {})
    rows = definitions.get(key, []) if isinstance(definitions, Mapping) else []
    versions = sorted({
        int(row["definitionVersion"])
        for row in rows
        if isinstance(row, Mapping)
        and row.get(id_key) == binding.capability_id
        and row.get("enabled") is True
        and isinstance(row.get("definitionVersion"), int)
    })
    return tuple(versions)


def candidate_identity(snapshot: Mapping[str, Any], binding: CatalogExecutionBinding) -> str | None:
    versions = _definition_versions(snapshot, binding)
    if len(versions) != 1:
        return None
    return f"{binding.capability_kind}:{binding.capability_id}:{versions[0]}"


def bound_definition_refs(
    snapshot: Mapping[str, Any],
    binding: CatalogExecutionBinding,
) -> frozenset[tuple[str, int]]:
    candidate = candidate_identity(snapshot, binding)
    if candidate is None:
        return frozenset()
    version = int(candidate.rsplit(":", 1)[1])
    bindings = snapshot.get("bindings", {})
    rows_key = "skills" if binding.capability_kind == "skill" else "agents"
    id_key = "skillId" if binding.capability_kind == "skill" else "agentId"
    version_key = "skillDefinitionVersion" if binding.capability_kind == "skill" else "agentDefinitionVersion"
    rows = bindings.get(rows_key, []) if isinstance(bindings, Mapping) else []
    eligible_definitions = planner_eligible_definition_refs(snapshot)
    return frozenset(
        (str(row["definitionId"]), int(row["definitionVersion"]))
        for row in rows
        if isinstance(row, Mapping)
        and row.get("enabled") is True
        and row.get(id_key) == binding.capability_id
        and row.get(version_key) == version
        and isinstance(row.get("definitionId"), str)
        and isinstance(row.get("definitionVersion"), int)
        and (str(row["definitionId"]), int(row["definitionVersion"])) in eligible_definitions
    )


def is_bindable(snapshot: Mapping[str, Any], binding: CatalogExecutionBinding) -> bool:
    return candidate_identity(snapshot, binding) is not None and bool(bound_definition_refs(snapshot, binding))


def capability_is_bindable(snapshot: Mapping[str, Any], resource_id: str, capability_id: str) -> bool:
    kind = _capability_kind(resource_id)
    if kind is None or resource_id != f"{kind}:{capability_id}":
        return False
    binding = CatalogExecutionBinding(
        catalog_intent_id="intent_000000000000",
        resource_id=resource_id,
        resource_version="catalog-projection",
        capability_id=capability_id,
        capability_kind=kind,
    )
    return is_bindable(snapshot, binding)
