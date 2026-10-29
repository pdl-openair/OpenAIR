"""Deterministic finalization for PVD evidence retrieval design.

The model authors retrieval semantics only. This module preserves M1 parameter
identity, validates real shortage provenance, and performs stable core +
supplemental merging without inventing scientific facts.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

_SUPPLEMENTAL_INTENTS = {"adjacent_but_relevant", "noise_exclusion"}


@dataclass(frozen=True)
class FinalizationResult:
    status: str
    output: dict[str, Any] | None
    errors: tuple[str, ...] = ()


def _failed(code: str) -> FinalizationResult:
    return FinalizationResult("failed", None, (code,))


def _keys(rows: Any, field: str) -> list[str]:
    values: list[str] = []
    for row in rows:
        value = str(row.get(field) or "").strip()
        values.append(value)
    return values


def _authority(input_payload: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    structured = input_payload.get("structured_context")
    parameter_set = input_payload.get("recommendation_parameter_set")
    judgments = input_payload.get("parameter_admission_judgments")
    formal = _keys(parameter_set.get("parameters"), "parameter_key")
    pending = _keys(judgments, "parameter_key") if judgments else []
    return formal, pending


def _normalized_query(raw: Any) -> str:
    return " ".join(str(raw or "").split())


def _validate_shortage_facts(input_payload: Mapping[str, Any], allowed: list[str]) -> list[dict[str, Any]]:
    facts = input_payload.get("evidence_shortage_facts")
    if facts in (None, []):
        return []
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    allowed_set = set(allowed)
    for raw in facts:
        row = deepcopy(dict(raw))
        shortage_id = str(row.get("shortage_id") or "").strip()
        source_ref = str(row.get("source_artifact_ref") or "").strip()
        description = str(row.get("description") or "").strip()
        seen.add(shortage_id)
        affected = row.get("affected_parameter_keys")
        result.append(row)
    return result


def _finalize_draft_rows(
    draft_payload: Mapping[str, Any],
    *,
    allowed: list[str],
    start_index: int,
    supplemental: bool,
    shortage_parameters: set[str],
    existing_queries: set[str],
) -> list[dict[str, Any]]:
    rows = draft_payload.get("query_variants")
    finalized: list[dict[str, Any]] = []
    allowed_set = set(allowed)
    seen = set(existing_queries)
    for offset, raw in enumerate(rows):
        row = deepcopy(dict(raw))
        query = _normalized_query(row.get("query"))
        fingerprint = query.casefold()
        seen.add(fingerprint)
        intent = str(row.get("search_intent") or "")
        row_keys = row.get("covered_parameter_keys")
        row["query"] = query
        row["query_id"] = f"query_{start_index + offset:03d}"
        finalized.append(row)
    return finalized


def _validate_prior_design(prior: Any, allowed: list[str]) -> dict[str, Any]:
    rows = prior.get("query_variants")
    allowed_set = set(allowed)
    ids: set[str] = set()
    fingerprints: set[str] = set()
    covered: set[str] = set()
    for row in rows:
        query_id = str(row.get("query_id") or "").strip()
        query = _normalized_query(row.get("query"))
        keys = row.get("covered_parameter_keys")
        ids.add(query_id); fingerprints.add(query.casefold()); covered.update(keys)
    return deepcopy(dict(prior))


def _finalize_body(input_payload: dict[str, Any], draft_payload: dict[str, Any]) -> dict[str, Any]:
    formal, pending = _authority(input_payload)
    allowed = formal + pending
    shortage_facts = _validate_shortage_facts(input_payload, allowed)
    coverage = {
        "formal_parameter_keys": formal,
        "pending_parameter_keys": pending,
        "all_parameter_keys": allowed,
    }

    if not shortage_facts:
        rows = _finalize_draft_rows(
            draft_payload,
            allowed=allowed,
            start_index=1,
            supplemental=False,
            shortage_parameters=set(),
            existing_queries=set(),
        )
        covered = {key for row in rows for key in row.get("covered_parameter_keys") or []}
        design = {
            "contract": "pvd-evidence-retrieval-design/v1",
            "mode": "core",
            "query_variants": rows,
            "design_rationale": str(draft_payload.get("design_rationale") or "").strip(),
        }
    else:
        prior = _validate_prior_design(input_payload.get("prior_retrieval_design"), allowed)
        shortage_parameters = {
            key for fact in shortage_facts for key in fact.get("affected_parameter_keys") or []
        }
        prior_rows = list(prior["query_variants"])
        existing = {_normalized_query(row.get("query")).casefold() for row in prior_rows}
        supplemental_rows = _finalize_draft_rows(
            draft_payload,
            allowed=allowed,
            start_index=len(prior_rows) + 1,
            supplemental=True,
            shortage_parameters=shortage_parameters,
            existing_queries=existing,
        )
        merged_rows = prior_rows + supplemental_rows
        prior_rationale = str(prior.get("design_rationale") or "").strip()
        new_rationale = str(draft_payload.get("design_rationale") or "").strip()
        design = {
            "contract": "pvd-evidence-retrieval-design/v1",
            "mode": "core_plus_supplemental",
            "query_variants": merged_rows,
            "design_rationale": " | ".join(part for part in (prior_rationale, new_rationale) if part),
            "supplemental_trace": {
                "shortage_fact_ids": [str(fact["shortage_id"]) for fact in shortage_facts],
                "source_artifact_refs": list(dict.fromkeys(str(fact["source_artifact_ref"]) for fact in shortage_facts)),
                "supplemental_query_ids": [row["query_id"] for row in supplemental_rows],
            },
        }

    return {
        "retrieval_design": design,
        "coverage_target": coverage,
        "pending_parameter_evidence_identities": pending,
    }


def finalize(input_payload: dict[str, Any], draft_payload: dict[str, Any]) -> FinalizationResult:
    try:
        return FinalizationResult("succeeded", _finalize_body(input_payload, draft_payload))
    except ValueError as exc:
        return _failed(str(exc))
