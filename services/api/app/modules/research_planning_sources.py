"""Traceable prior-research sources for ResearchDesign planning.

This module projects immutable research assets into provider-local planning
choices and deterministically maps the Provider's scientific adoption decision
back to formal source identity. It never promotes source content into Evidence,
ScientificState, ResearchDesign authority, or runtime resource-use facts.
"""

from __future__ import annotations

from app.execution.research_start_processing import PlannerSemanticInputError

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.research_orchestration import PlanningSourceUsage
from app.modules.research_package_asset import ResearchPackageAssetService


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class PlanningSourceUseDecision(BaseModel):
    """Provider-local scientific decision; all formal source identity stays server-owned."""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
        strict=True,
    )

    source_choice: Annotated[str, Field(min_length=1, max_length=64)]
    adopted_unit_numbers: list[Annotated[int, Field(ge=1, strict=True)]]
    not_adopted_unit_numbers: list[Annotated[int, Field(ge=1, strict=True)]]
    revalidation_unit_numbers: list[Annotated[int, Field(ge=1, strict=True)]]

    @model_validator(mode="after")
    def validate_local_partition(self) -> "PlanningSourceUseDecision":
        groups = (
            self.adopted_unit_numbers,
            self.not_adopted_unit_numbers,
            self.revalidation_unit_numbers,
        )
        for values in groups:
            if len(values) != len(set(values)):
                raise ValueError("planning source decision contains duplicate unit numbers")
        flattened = [number for values in groups for number in values]
        if len(flattened) != len(set(flattened)):
            raise ValueError("planning source decision categories must be disjoint")
        return self


@dataclass(frozen=True)
class _Unit:
    path: str
    category: str
    content: str
    supporting_sources: tuple[dict[str, str], ...] = ()


def _source_catalog(document: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    catalog: dict[str, dict[str, str]] = {}
    for raw in document.get("sources") or ():
        if not isinstance(raw, Mapping):
            continue
        source_id = str(raw.get("id") or "").strip()
        if not source_id:
            continue
        catalog[source_id] = {
            "id": source_id,
            "name": str(raw.get("name") or "").strip(),
            "ref": str(raw.get("ref") or "").strip(),
        }
    return catalog


def _supporting_sources(
    source_ids: Sequence[Any],
    catalog: Mapping[str, dict[str, str]],
) -> tuple[dict[str, str], ...]:
    rows: list[dict[str, str]] = []
    for raw in source_ids:
        source = catalog.get(str(raw))
        if source is not None:
            rows.append(dict(source))
    return tuple(rows)


def _json_pointer_segment(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _package_units(document: Mapping[str, Any]) -> list[_Unit]:
    """Project protocol-defined scientific content into stable source paths."""
    catalog = _source_catalog(document)
    units: list[_Unit] = []

    def add(path: str, category: str, value: Any, source_ids: Sequence[Any] = ()) -> None:
        text = _json_text(value)
        if text:
            units.append(
                _Unit(
                    path=path,
                    category=category,
                    content=text,
                    supporting_sources=_supporting_sources(source_ids, catalog),
                )
            )

    add("/summary", "summary", document.get("summary"))

    method = document.get("method")
    if isinstance(method, Mapping):
        refs = method.get("source_refs") if isinstance(method.get("source_refs"), list) else []
        add("/method/goal", "method_goal", method.get("goal"), refs)
        for field, category in (
            ("inputs", "method_input"),
            ("logic", "method_logic"),
            ("outputs", "method_output"),
            ("validation_criteria", "method_validation"),
        ):
            values = method.get(field)
            if isinstance(values, list):
                for index, value in enumerate(values):
                    add(f"/method/{field}/{index}", category, value, refs)

    cases = document.get("reference_cases")
    if isinstance(cases, list):
        for case_index, raw_case in enumerate(cases):
            if not isinstance(raw_case, Mapping):
                continue
            refs = raw_case.get("source_refs") if isinstance(raw_case.get("source_refs"), list) else []
            base = f"/reference_cases/{case_index}"
            add(f"{base}/target", "reference_target", raw_case.get("target"), refs)
            conditions = raw_case.get("conditions")
            if isinstance(conditions, list):
                for index, value in enumerate(conditions):
                    add(f"{base}/conditions/{index}", "reference_condition", value, refs)
            results = raw_case.get("results")
            if isinstance(results, list):
                for index, value in enumerate(results):
                    add(f"{base}/results/{index}", "reference_result", value, refs)
            resource_refs = raw_case.get("resource_refs")
            if isinstance(resource_refs, list):
                for index, value in enumerate(resource_refs):
                    add(f"{base}/resource_refs/{index}", "reference_resource", value, refs)

    resources = document.get("resources")
    if isinstance(resources, list):
        for index, value in enumerate(resources):
            add(f"/resources/{index}", "resource_reference", value)

    applicability = document.get("applicability")
    if isinstance(applicability, Mapping):
        refs = (
            applicability.get("source_refs")
            if isinstance(applicability.get("source_refs"), list)
            else []
        )
        for field, category in (
            ("use_when", "applicability_use_when"),
            ("preserve", "applicability_preserve"),
            ("adaptable", "applicability_adaptable"),
            ("limits", "applicability_limit"),
        ):
            values = applicability.get(field)
            if isinstance(values, list):
                for index, value in enumerate(values):
                    add(f"/applicability/{field}/{index}", category, value, refs)

    capabilities = document.get("capabilities")
    if isinstance(capabilities, list):
        for index, value in enumerate(capabilities):
            add(f"/capabilities/{index}", "capability", value)

    # Standard protocol fields above own v0.2 semantics. Preserve top-level
    # extensions as opaque planning units without letting them redefine the
    # standard fields or become machine authority.
    standard_fields = {
        "spec_version",
        "id",
        "name",
        "version",
        "summary",
        "sources",
        "method",
        "reference_cases",
        "resources",
        "applicability",
        "capabilities",
    }
    for key in sorted(set(document) - standard_fields):
        add(f"/{_json_pointer_segment(key)}", "extension", document[key])

    if not units:
        raise ValueError("planning source contains no projectable research content")
    return units


def resolve_research_package_planning_sources(
    refs: Sequence[Mapping[str, Any]] | None,
    *,
    owner_id: str,
    workspace_id: str,
    asset_service: ResearchPackageAssetService | None = None,
) -> list[dict[str, Any]]:
    """Resolve exact package versions into provider-local planning source choices."""
    if not refs:
        return []
    service = asset_service or ResearchPackageAssetService()
    projected: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, raw_ref in enumerate(refs, start=1):
        if not isinstance(raw_ref, Mapping):
            raise ValueError("planning source reference must be an object")
        kind = str(raw_ref.get("kind") or "").strip()
        source_id = str(raw_ref.get("id") or "").strip()
        source_version = str(raw_ref.get("version") or "").strip()
        identity = (kind, source_id, source_version)
        if kind != "research_package" or not source_id or not source_version or identity in seen:
            raise ValueError("planning source reference is invalid")
        seen.add(identity)
        asset = service.read(
            owner_id=owner_id,
            workspace_id=workspace_id,
            package_id=source_id,
            version=source_version,
        )
        units = _package_units(asset.package.document)
        projected.append(
            {
                "sourceChoice": f"planning_source_{index}",
                "sourceKind": kind,
                "sourceId": asset.package.package_id,
                "sourceVersion": asset.package.version,
                "sourceArtifactRef": asset.artifact_id,
                "sourceContentHash": f"sha256:{asset.package.content_sha256}",
                "name": str(asset.package.document.get("name") or "").strip(),
                "units": [
                    {
                        "unitNumber": unit_index,
                        "sourcePath": unit.path,
                        "category": unit.category,
                        "content": unit.content,
                        "supportingSources": [dict(row) for row in unit.supporting_sources],
                    }
                    for unit_index, unit in enumerate(units, start=1)
                ],
            }
        )
    return projected


def planning_sources_for_provider(
    planning_sources: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Expose only local selectors and scientific content to the Provider.

    Formal package identity, Artifact identity, content hashes, and source paths
    remain server-owned provenance and are joined back after the Provider returns
    local sourceChoice/unitNumber decisions.
    """
    projected: list[dict[str, Any]] = []
    for source in planning_sources:
        units = source.get("units")
        if not isinstance(units, list):
            raise ValueError("planning source context has invalid units")
        provider_units: list[dict[str, Any]] = []
        for unit in units:
            if not isinstance(unit, Mapping):
                raise ValueError("planning source unit is invalid")
            provider_units.append(
                {
                    "unitNumber": unit.get("unitNumber"),
                    "category": unit.get("category"),
                    "content": unit.get("content"),
                    "supportingSources": [
                        dict(row) for row in unit.get("supportingSources") or ()
                    ],
                }
            )
        projected.append(
            {
                "sourceChoice": source.get("sourceChoice"),
                "name": source.get("name"),
                "units": provider_units,
            }
        )
    return projected


def planning_source_prompt_rules(planning_sources: Sequence[Mapping[str, Any]]) -> str:
    if not planning_sources:
        return ""
    return """
Prior-research planning sources are supplied in planningSources. They are inputs to current planning, not current-project Evidence, ScientificState, execution results, or resource-use facts.
For every planningSources item, return exactly one planningSourceUses item using only its sourceChoice and unitNumber values. Classify every supplied unit exactly once:
- adoptedUnitNumbers: scientifically useful as a planning premise, method reference, comparison point, boundary, or research lead for THIS project;
- notAdoptedUnitNumbers: intentionally not used in THIS project plan;
- revalidationUnitNumbers: potentially useful but context-dependent, conflicting, insufficiently established for the current project, or requiring current-project verification before reliance.
Current user needs, current conditions, selected Workflow responsibilities, and current formal facts take precedence over prior sources when they differ.
Adoption means only that the source content informed this plan. It never means the current project has accepted it as Evidence, reproduced its result, obtained/bound a referenced resource, or actually used that resource at runtime.
A resource_reference unit is only a source/availability lead unless later current-project mechanisms establish actual acquisition and use. Method validation criteria from a source do not automatically become current-project completion requirements.
The server owns formal source identity and paths; do not invent source IDs, versions, artifact refs, source paths, or unit numbers.
""".strip()


def materialize_planning_source_usages(
    decisions: Sequence[PlanningSourceUseDecision],
    planning_sources: Sequence[Mapping[str, Any]] | None,
) -> tuple[PlanningSourceUsage, ...]:
    """Map provider-local complete partitions back to formal planning provenance."""
    sources = list(planning_sources or ())
    if not sources:
        if decisions:
            raise PlannerSemanticInputError('planning source decisions were returned without planning sources', error_code='planning_source_choice_invalid', field_paths=('/planningSourceUses',))
        return ()

    by_choice = {str(row.get("sourceChoice") or ""): row for row in sources}
    if len(by_choice) != len(sources) or "" in by_choice:
        raise ValueError("planning source context contains invalid choices")
    if len(decisions) != len(sources):
        raise PlannerSemanticInputError('every planning source must have exactly one usage decision', error_code='planning_source_choice_invalid', field_paths=('/planningSourceUses',))

    usages: list[PlanningSourceUsage] = []
    seen_choices: set[str] = set()
    for decision in decisions:
        choice = decision.source_choice
        if choice in seen_choices or choice not in by_choice:
            raise PlannerSemanticInputError('planning source decision references an unknown or duplicate source choice', error_code='planning_source_choice_invalid', field_paths=('/planningSourceUses',))
        seen_choices.add(choice)
        source = by_choice[choice]
        units = source.get("units")
        if not isinstance(units, list) or not units:
            raise ValueError("planning source context has no units")
        path_by_number: dict[int, str] = {}
        for row in units:
            if not isinstance(row, Mapping):
                raise ValueError("planning source unit is invalid")
            number = row.get("unitNumber")
            path = row.get("sourcePath")
            if isinstance(number, bool) or not isinstance(number, int) or number < 1:
                raise ValueError("planning source unit number is invalid")
            if not isinstance(path, str) or not path.strip() or number in path_by_number:
                raise ValueError("planning source unit path is invalid")
            path_by_number[number] = path.strip()
        all_numbers = set(path_by_number)
        classified = (
            set(decision.adopted_unit_numbers)
            | set(decision.not_adopted_unit_numbers)
            | set(decision.revalidation_unit_numbers)
        )
        if classified != all_numbers:
            raise PlannerSemanticInputError('planning source decision must classify every supplied unit exactly once', error_code='planning_source_partition_invalid', field_paths=('/planningSourceUses',))

        def paths(numbers: Sequence[int]) -> list[str]:
            return [path_by_number[number] for number in numbers]

        usages.append(
            PlanningSourceUsage(
                sourceKind=str(source.get("sourceKind") or ""),
                sourceId=str(source.get("sourceId") or ""),
                sourceVersion=str(source.get("sourceVersion") or ""),
                sourceArtifactRef=str(source.get("sourceArtifactRef") or ""),
                sourceContentHash=str(source.get("sourceContentHash") or ""),
                adoptedSourcePaths=paths(decision.adopted_unit_numbers),
                notAdoptedSourcePaths=paths(decision.not_adopted_unit_numbers),
                revalidationSourcePaths=paths(decision.revalidation_unit_numbers),
            )
        )
    return tuple(usages)
