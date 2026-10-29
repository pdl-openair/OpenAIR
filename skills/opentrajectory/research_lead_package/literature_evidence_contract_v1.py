"""Production consumer for the OpenTrajectory Literature Evidence Contract v1.

This module deliberately has no retrieval dependencies.  It is the bounded
handoff from Literature Skill output to a downstream planning decision:
explicit facts are consumed; absent or ambiguous facts stay gaps.
"""
from __future__ import annotations

import re
from typing import Any


CONTRACT_ID = "opentrajectory_literature_evidence_contract_v1"
OBSERVED_OR_INFERRED = frozenset({"observed", "inferred"})
SUPPORTED_RELATIONS = frozenset({"observed_association", "supported_mechanism"})
MINIMUM_PROTOCOL_FIELDS = frozenset({
    "anode", "cathode", "voltage_window", "charge_rate_or_current_density",
})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(_text(item) for item in value if _text(item)))


def _gap(field: str, reason: str) -> dict[str, str]:
    return {"field": field, "reason": reason}


def _empty_contract() -> dict[str, Any]:
    # structured_formulation_facts is optional (Option B); omit on empty
    # legacy payloads so older consumers and schema remain compatible.
    return {
        "contract_id": CONTRACT_ID,
        "evidence": [],
        "comparators": [],
        "task_fidelity": {
            "anode_chemistry": {"value": None, "evidence_ids": []},
            "cathode_chemistry": {"value": None, "evidence_ids": []},
            "voltage_regime": {"value": None, "evidence_ids": []},
            "charge_rate_or_current_density": {"value": None, "evidence_ids": []},
            "intervention_domain": {"value": None, "evidence_ids": []},
        },
        "mechanism_evidence": [],
        "gaps": [],
    }


def _normalize_formulation_arm(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    arm_id = _text(raw.get("arm_id"))
    formulation = _text(raw.get("formulation"))
    if not arm_id or not formulation:
        return None
    arm: dict[str, Any] = {"arm_id": arm_id, "formulation": formulation}
    label = _text(raw.get("label"))
    if label:
        arm["label"] = label
    role = _text(raw.get("role"))
    if role in {"control", "candidate", "reference", "unspecified"}:
        arm["role"] = role
    shared = raw.get("shared_components")
    if isinstance(shared, dict):
        cleaned = {
            _text(key): _text(value)
            for key, value in shared.items()
            if _text(key) and _text(value)
        }
        if cleaned:
            arm["shared_components"] = cleaned
    return arm


def _normalize_formulation_relation(
    raw: Any,
    *,
    arm_ids: set[str],
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    from_arm = _text(raw.get("from_arm_id"))
    to_arm = _text(raw.get("to_arm_id"))
    relation = _text(raw.get("relation"))
    if not from_arm or not to_arm or not relation:
        return None
    if from_arm not in arm_ids or to_arm not in arm_ids:
        return None
    if from_arm == to_arm:
        return None
    return {
        "from_arm_id": from_arm,
        "to_arm_id": to_arm,
        "relation": relation,
    }


def _normalize_formulation_provenance(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    source_anchor_id = _text(raw.get("source_anchor_id"))
    evidence_quote = _text(raw.get("evidence_quote"))
    if not source_anchor_id or not evidence_quote:
        return None
    provenance: dict[str, Any] = {
        "source_anchor_id": source_anchor_id,
        "evidence_quote": evidence_quote,
    }
    paper_id = _text(raw.get("paper_id"))
    if paper_id:
        provenance["paper_id"] = paper_id
    doi = _text(raw.get("doi"))
    if doi:
        provenance["doi"] = doi
    return provenance


def normalize_structured_formulation_fact(
    raw: Any,
    *,
    evidence_by_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Admit only explicit multi-arm formulation facts; never invent protocol."""
    if not isinstance(raw, dict):
        return None
    arms_raw = raw.get("arms")
    if not isinstance(arms_raw, list):
        return None
    arms: list[dict[str, Any]] = []
    for arm_raw in arms_raw:
        arm = _normalize_formulation_arm(arm_raw)
        if arm is not None:
            arms.append(arm)
    # Deduplicate by arm_id while preserving order.
    deduped: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for arm in arms:
        if arm["arm_id"] in seen_ids:
            continue
        seen_ids.add(arm["arm_id"])
        deduped.append(arm)
    arms = deduped
    if len(arms) < 2:
        return None
    arm_ids = {arm["arm_id"] for arm in arms}
    relations: list[dict[str, Any]] = []
    for rel_raw in raw.get("relations") or []:
        rel = _normalize_formulation_relation(rel_raw, arm_ids=arm_ids)
        if rel is not None:
            relations.append(rel)
    if not relations:
        return None
    provenance = _normalize_formulation_provenance(raw.get("provenance"))
    if provenance is None:
        return None
    evidence_ids = _ids(raw.get("evidence_ids"))
    if evidence_by_id is not None:
        evidence_ids = [eid for eid in evidence_ids if eid in evidence_by_id]
    if not evidence_ids:
        return None
    fact_id = _text(raw.get("fact_id"))
    if not fact_id:
        fact_id = f"sff:{evidence_ids[0]}:{arms[0]['arm_id']}:{arms[1]['arm_id']}"
    return {
        "fact_id": fact_id,
        "evidence_ids": evidence_ids,
        "arms": arms,
        "relations": relations,
        "provenance": provenance,
    }


def collect_structured_formulation_facts(
    *,
    evidence_items: list[dict[str, Any]] | None = None,
    structured_formulation_facts: list[Any] | None = None,
    evidence_by_id: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Project typed formulation facts into Contract Option-B transport shape."""
    collected: list[dict[str, Any]] = []
    for raw in structured_formulation_facts or []:
        fact = normalize_structured_formulation_fact(raw, evidence_by_id=evidence_by_id)
        if fact is not None:
            collected.append(fact)

    for item in evidence_items or []:
        if not isinstance(item, dict):
            continue
        evidence_id = _text(item.get("evidence_id"))
        if not evidence_id:
            continue
        if evidence_by_id is not None and evidence_id not in evidence_by_id:
            continue
        typed = item.get("structured_formulation")
        if not isinstance(typed, dict):
            continue
        provenance = typed.get("provenance") if isinstance(typed.get("provenance"), dict) else {}
        candidate = {
            "fact_id": _text(typed.get("fact_id"))
            or f"sff:{evidence_id}",
            "evidence_ids": _ids(typed.get("evidence_ids")) or [evidence_id],
            "arms": typed.get("arms"),
            "relations": typed.get("relations"),
            "provenance": {
                "source_anchor_id": _text(provenance.get("source_anchor_id"))
                or _text(item.get("source_anchor_id")),
                "evidence_quote": _text(provenance.get("evidence_quote"))
                or _text(item.get("evidence_quote")),
                "paper_id": _text(provenance.get("paper_id"))
                or _text(item.get("paper_id"))
                or _text(item.get("candidate_id")),
                "doi": _text(provenance.get("doi")) or _text(item.get("doi")),
            },
        }
        if not candidate["provenance"]["doi"]:
            candidate["provenance"].pop("doi", None)
        if not candidate["provenance"]["paper_id"]:
            candidate["provenance"].pop("paper_id", None)
        fact = normalize_structured_formulation_fact(
            candidate,
            evidence_by_id=evidence_by_id,
        )
        if fact is not None:
            collected.append(fact)

    # Deduplicate by fact_id.
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for fact in collected:
        fact_id = fact["fact_id"]
        if fact_id in seen:
            continue
        seen.add(fact_id)
        unique.append(fact)
    return unique


_PROTOCOL_EVIDENCE_TYPE_TO_FIELD = {
    "anode": "anode",
    "cathode": "cathode",
    "voltage_window": "voltage_window",
    "charge_rate": "charge_rate_or_current_density",
    "current_density": "charge_rate_or_current_density",
    "temperature": "temperature",
    "loading": "loading",
    "cell_configuration": "cell_configuration",
    "electrolyte_amount_or_E_L": "electrolyte_amount_or_E_L",
}
_METRIC_EVIDENCE_TYPES = {
    "coulombic_efficiency": "coulombic_efficiency",
    "capacity_retention": "capacity_retention",
    "impedance": "impedance",
}


def _arm_formulation_text(arm: dict[str, Any]) -> str:
    text = _text(arm.get("formulation"))
    shared = arm.get("shared_components") if isinstance(arm.get("shared_components"), dict) else {}
    extras: list[str] = []
    for key in ("total_salt_concentration", "salt_ratio"):
        value = _text(shared.get(key))
        if value:
            extras.append(value)
    if extras:
        return f"{text}; shared salt: {', '.join(extras)}"
    return text


def _short_span_for_evidence(item: dict[str, Any]) -> str:
    """Return an explicit short span from typed evidence; never invent values."""
    quote = _text(item.get("evidence_quote"))
    evidence_type = _text(item.get("evidence_type"))
    if evidence_type in {"charge_rate", "current_density"}:
        match = re.search(
            r"(\d+(?:\.\d+)?\s*(?:C|c)(?:\s*-?\s*rate)?|\d+(?:\.\d+)?\s*mA\s*/?\s*cm(?:\^?2|-2)?)",
            quote,
        )
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip()
    if evidence_type == "voltage_window":
        match = re.search(
            r"(\d+(?:\.\d+)?\s*[\u2212\-~–—-]\s*\d+(?:\.\d+)?\s*V)",
            quote,
            flags=re.IGNORECASE,
        )
        if match:
            return re.sub(r"\s+", " ", match.group(1)).replace("\u2212", "-").strip()
        match = re.search(r"(\d+(?:\.\d+)?\s*V)", quote, flags=re.IGNORECASE)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip()
    if evidence_type == "anode":
        if "lithium metal" in quote.casefold():
            return "lithium metal"
        if re.search(r"\bli\s*\|\|", quote, flags=re.IGNORECASE):
            return "lithium metal"
    if evidence_type == "cathode":
        match = re.search(r"\|\|\s*([A-Za-z][A-Za-z0-9]{0,24})\b", quote)
        if match and match.group(1).casefold() not in {"li", "cu"}:
            return match.group(1).strip()
    if evidence_type == "temperature":
        match = re.search(
            r"(\-?\d+(?:\.\d+)?\s*°\s*C|\-?\d+(?:\.\d+)?\s*C\b)",
            quote,
            flags=re.IGNORECASE,
        )
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip()
    if evidence_type in _METRIC_EVIDENCE_TYPES:
        return "measured"
    if quote:
        return quote if len(quote) <= 160 else quote[:157] + "..."
    return ""


def _harvest_evidence_fields(
    *,
    paper_id: str,
    evidence: list[dict[str, Any]],
    type_to_field: dict[str, str],
) -> dict[str, Any] | None:
    fields: dict[str, str] = {}
    evidence_ids: list[str] = []
    for item in evidence:
        if _text(item.get("paper_id")) != paper_id:
            continue
        evidence_type = _text(item.get("evidence_type"))
        field = type_to_field.get(evidence_type)
        if not field or field in fields:
            continue
        value = _short_span_for_evidence(item)
        evidence_id = _text(item.get("evidence_id"))
        if not value or not evidence_id:
            continue
        fields[field] = value
        evidence_ids.append(evidence_id)
    if not fields or not evidence_ids:
        return None
    return {"fields": fields, "evidence_ids": list(dict.fromkeys(evidence_ids))}


def _resolve_control_candidate_arms(
    fact: dict[str, Any],
    relation: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    arms_by_id = {
        _text(arm.get("arm_id")): arm
        for arm in fact.get("arms") or []
        if isinstance(arm, dict) and _text(arm.get("arm_id"))
    }
    left = arms_by_id.get(_text(relation.get("from_arm_id")))
    right = arms_by_id.get(_text(relation.get("to_arm_id")))
    if left is None or right is None:
        return None
    left_role = _text(left.get("role"))
    right_role = _text(right.get("role"))
    # compared_with: candidate/from typically points at reference/control.
    if left_role == "candidate" and right_role in {"control", "reference", "unspecified"}:
        return right, left
    if right_role == "candidate" and left_role in {"control", "reference", "unspecified"}:
        return left, right
    if left_role in {"control", "reference"} and right_role == "candidate":
        return left, right
    if right_role in {"control", "reference"} and left_role == "candidate":
        return right, left
    # Fallback for explicit compared_with without roles: treat to_arm as control.
    return right, left


def assemble_comparators_from_structured_formulation_facts(
    *,
    facts: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Assemble binary Contract comparators from typed formulation facts + evidence.

    Does not invent shared_protocol / shared_metrics / single-variable deltas.
    Missing protocol or metrics are recorded as gaps and omitted from the
    comparator object (optional on v1). Partial comparators use
    ``same_paper_partial`` / ``unsupported`` and stay ineligible for canonical
    selection until minimum protocol + metrics are grounded.
    """
    evidence_rows = [item for item in evidence if isinstance(item, dict)]
    evidence_by_id = {
        _text(item.get("evidence_id")): item
        for item in evidence_rows
        if _text(item.get("evidence_id"))
    }
    comparators: list[dict[str, Any]] = []
    gaps: list[dict[str, str]] = []
    seen_keys: set[tuple[str, str, str]] = set()

    for fact in facts:
        if not isinstance(fact, dict):
            continue
        fact_ids = _ids(fact.get("evidence_ids"))
        grounded = [eid for eid in fact_ids if eid in evidence_by_id]
        if not grounded:
            gaps.append(_gap("comparator", "formulation_fact_ungrounded"))
            continue
        paper_id = _text((fact.get("provenance") or {}).get("paper_id"))
        if not paper_id:
            paper_id = _text(evidence_by_id[grounded[0]].get("paper_id"))
        if not paper_id:
            gaps.append(_gap("comparator", "missing_paper_id"))
            continue

        protocol = _harvest_evidence_fields(
            paper_id=paper_id,
            evidence=evidence_rows,
            type_to_field=_PROTOCOL_EVIDENCE_TYPE_TO_FIELD,
        )
        metrics = _harvest_evidence_fields(
            paper_id=paper_id,
            evidence=evidence_rows,
            type_to_field=_METRIC_EVIDENCE_TYPES,
        )
        if protocol is None:
            gaps.append(
                {
                    "field": "shared_protocol",
                    "reason": "missing_from_evidence",
                    "fact_id": _text(fact.get("fact_id")),
                }
            )
        if metrics is None:
            gaps.append(
                {
                    "field": "shared_metrics",
                    "reason": "missing_from_evidence",
                    "fact_id": _text(fact.get("fact_id")),
                }
            )

        protocol_fields = set(((protocol or {}).get("fields") or {}).keys())
        if protocol is not None and MINIMUM_PROTOCOL_FIELDS.issubset(protocol_fields) and metrics is not None:
            comparability = "same_paper_explicit"
        elif protocol is not None or metrics is not None:
            comparability = "same_paper_partial"
        else:
            comparability = "unsupported"

        fact_comparator_count = 0
        for relation in fact.get("relations") or []:
            if not isinstance(relation, dict):
                continue
            resolved = _resolve_control_candidate_arms(fact, relation)
            if resolved is None:
                continue
            control_arm, candidate_arm = resolved
            control_text = _arm_formulation_text(control_arm)
            candidate_text = _arm_formulation_text(candidate_arm)
            if not control_text or not candidate_text or control_text == candidate_text:
                continue
            key = (paper_id, control_text, candidate_text)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            comparator_id = (
                f"cmp:{_text(fact.get('fact_id'))}:"
                f"{_text(control_arm.get('arm_id'))}:{_text(candidate_arm.get('arm_id'))}"
            )
            comparator: dict[str, Any] = {
                "comparator_id": comparator_id,
                "paper_id": paper_id,
                "control": {
                    "formulation": control_text,
                    "evidence_ids": list(grounded),
                },
                "candidate": {
                    "formulation": candidate_text,
                    "evidence_ids": list(grounded),
                },
                "controllable_variable": {
                    # Levels are the stated formulations themselves — do not
                    # invent a narrower chemical delta (e.g. "only additive X").
                    "name": "electrolyte_formulation",
                    "control_level": control_text,
                    "candidate_level": candidate_text,
                    "evidence_ids": list(grounded),
                },
                "comparability": comparability,
            }
            if protocol is not None:
                comparator["shared_protocol"] = protocol
            if metrics is not None:
                comparator["shared_metrics"] = metrics
            comparators.append(comparator)
            fact_comparator_count += 1

        if fact_comparator_count == 0:
            gaps.append(
                {
                    "field": "comparator",
                    "reason": "no_control_candidate_pair_from_relations",
                    "fact_id": _text(fact.get("fact_id")),
                }
            )

    return comparators, gaps


def _valid_evidence(raw: Any) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """Retain existing evidence keys without rejecting model-authored values."""
    if not isinstance(raw, dict):
        return None, [_gap("evidence", "not_an_object")]
    item = dict(raw)
    required = ("evidence_id", "paper_id", "title", "source_anchor_id", "evidence_quote", "evidence_type")
    gaps = [_gap(field, "missing") for field in required if field not in item]
    if "doi" not in item and "stable_source_id" not in item:
        gaps.append(_gap("doi_or_stable_source_id", "missing"))
    if "observed_or_inferred" not in item:
        gaps.append(_gap("observed_or_inferred", "missing"))
    return (item if not gaps else None), gaps


def _mechanism_relation_from_typed_evidence(
    raw: Any,
    *,
    evidence_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Project one typed mechanism item only when its full relation is explicit.

    The literature Contract has no partial mechanism relation shape.  Keep an
    incomplete scientific link as a gap by returning ``None`` rather than
    filling it from the paper title, performance evidence, or domain priors.
    """
    if not isinstance(raw, dict):
        return None
    if _text(raw.get("evidence_type")) not in {"mechanism", "mechanism_claim"}:
        return None
    evidence_id = _text(raw.get("evidence_id"))
    observed_or_inferred = _text(raw.get("observed_or_inferred"))
    quote = _text(raw.get("evidence_quote"))
    if evidence_id not in evidence_by_id or observed_or_inferred not in OBSERVED_OR_INFERRED or not quote:
        return None

    if observed_or_inferred == "inferred":
        # An author-explicit ``outcome stems from intermediate in cause``
        # statement is the minimum complete chain for supported inference.
        match = re.search(
            r"(?P<outcome>[A-Z][^.]{2,160}?)\s+stems\s+from\s+"
            r"(?P<intermediate>[^,.]{3,160}?)\s+in\s+"
            r"(?P<cause>[^,.]{3,160}?)(?:,|\.)",
            quote,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return None
        cause = _text(match.group("cause"))
        intermediate = _text(match.group("intermediate"))
        outcome = _text(match.group("outcome"))
        relation_type = "supported_mechanism"
        support_strength = "author_explicit_mechanistic_inference_with_characterization"
    else:
        # A direct characterization statement can establish an association,
        # but it is deliberately not promoted to causal proof.
        observation = re.search(
            r"\bquantif(?:y|ies|ied)\s+(?:the\s+evolution\s+of\s+)?"
            r"(?P<outcome>[^.]{3,160}?)\s+across\s+(?P<cause>[^.]{3,160}?)(?:\.|$)",
            quote,
            flags=re.IGNORECASE | re.DOTALL,
        )
        intermediate_match = re.search(
            r"\bfor\s+(?P<intermediate>[^.]{3,160}?\bpairs)\s+in\s+",
            quote,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not observation or not intermediate_match:
            return None
        cause = _text(observation.group("cause"))
        intermediate = _text(intermediate_match.group("intermediate"))
        outcome = _text(observation.group("outcome"))
        relation_type = "observed_association"
        support_strength = "direct_characterization_association"

    if not all((cause, intermediate, outcome)):
        return None
    return {
        "cause_or_intervention": cause,
        "intermediate": intermediate,
        "outcome": outcome,
        "relation_type": relation_type,
        "support_strength": support_strength,
        "evidence_ids": [evidence_id],
    }


def collect_mechanism_relations(
    evidence_items: list[Any],
    *,
    evidence_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collect grounded Contract relations from typed mechanism evidence."""
    relations: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in evidence_items:
        relation = _mechanism_relation_from_typed_evidence(
            raw,
            evidence_by_id=evidence_by_id,
        )
        if relation is None:
            continue
        key = (relation["relation_type"], relation["evidence_ids"][0])
        if key not in seen:
            seen.add(key)
            relations.append(relation)
    return relations


def adapt_legacy_literature_output(raw: dict[str, Any]) -> dict[str, Any]:
    """Map the existing evidence-node shape without guessing missing facts.

    Legacy evidence is admitted only if every v1-required field has an
    explicit counterpart.  ``paper_packets`` may provide a title for identity
    matching, but neither formulations nor scientific conditions are inferred.
    """
    if raw.get("contract_id") == CONTRACT_ID:
        return dict(raw)

    adapted = _empty_contract()
    packets = [item for item in raw.get("paper_packets") or [] if isinstance(item, dict)]
    titles_by_identity: dict[str, str] = {}
    for packet in packets:
        title = _text(packet.get("title"))
        for key in ("paper_id", "source_id", "candidate_id", "doi", "arxiv_id"):
            identity = _text(packet.get(key))
            if identity and title:
                titles_by_identity[identity] = title

    legacy_items = raw.get("evidence") or raw.get("evidence_items") or []
    for legacy in legacy_items:
        if not isinstance(legacy, dict):
            adapted["gaps"].append(_gap("evidence", "legacy_item_not_an_object"))
            continue
        paper_id = next((_text(legacy.get(key)) for key in ("paper_id", "source_id", "candidate_id") if _text(legacy.get(key))), "")
        title = _text(legacy.get("title")) or titles_by_identity.get(paper_id, "")
        stable_source_id = _text(legacy.get("stable_source_id")) or _text(legacy.get("source_id"))
        observed_or_inferred = _text(legacy.get("observed_or_inferred"))
        candidate = {
            "evidence_id": _text(legacy.get("evidence_id")),
            "paper_id": paper_id,
            "title": title,
            "doi": _text(legacy.get("doi")) or None,
            "stable_source_id": stable_source_id or None,
            "source_anchor_id": _text(legacy.get("source_anchor_id")),
            "evidence_quote": _text(legacy.get("evidence_quote")),
            "evidence_type": _text(legacy.get("evidence_type")) or "other",
            "observed_or_inferred": observed_or_inferred,
        }
        if candidate["doi"] is None:
            candidate.pop("doi")
        if candidate["stable_source_id"] is None:
            candidate.pop("stable_source_id")
        item, gaps = _valid_evidence(candidate)
        if item is None:
            adapted["gaps"].extend(
                {**gap, "legacy_evidence_id": _text(legacy.get("evidence_id"))} for gap in gaps
            )
        else:
            adapted["evidence"].append(item)

    # Option B: transport typed multi-arm formulation facts, then assemble
    # binary comparators only from those facts + same-paper evidence fields.
    evidence_by_id = {
        _text(item.get("evidence_id")): item
        for item in adapted["evidence"]
        if _text(item.get("evidence_id"))
    }
    adapted["mechanism_evidence"] = collect_mechanism_relations(
        [item for item in legacy_items if isinstance(item, dict)],
        evidence_by_id=evidence_by_id,
    )
    facts = collect_structured_formulation_facts(
        evidence_items=[item for item in legacy_items if isinstance(item, dict)],
        structured_formulation_facts=list(raw.get("structured_formulation_facts") or []),
        evidence_by_id=evidence_by_id,
    )
    if facts:
        adapted["structured_formulation_facts"] = facts
        assembled, assembly_gaps = assemble_comparators_from_structured_formulation_facts(
            facts=facts,
            evidence=adapted["evidence"],
        )
        if assembled:
            adapted["comparators"] = assembled
        adapted["gaps"].extend(assembly_gaps)
    return adapted


def consume_literature_skill_output(literature_output: dict[str, Any]) -> dict[str, Any]:
    """Production handoff: adapt explicit fields, then consume v1 evidence."""
    return consume_literature_evidence_contract(
        adapt_legacy_literature_output(literature_output)
    )


def _evidence_by_id(contract: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    indexed: dict[str, dict[str, Any]] = {}
    gaps: list[dict[str, str]] = []
    for raw in contract.get("evidence") or []:
        item, item_gaps = _valid_evidence(raw)
        if item is None:
            gaps.extend(item_gaps)
            continue
        evidence_id = _text(item["evidence_id"])
        if evidence_id in indexed:
            gaps.append(_gap("evidence_id", f"duplicate:{evidence_id}"))
            continue
        indexed[evidence_id] = item
    return indexed, gaps


def _grounded_ids(ids: Any, evidence_by_id: dict[str, dict[str, Any]]) -> list[str]:
    return [evidence_id for evidence_id in _ids(ids) if evidence_id in evidence_by_id]


def _field_value(fields: Any, name: str) -> Any:
    if not isinstance(fields, dict):
        return None
    return fields.get(name)


def _comparator_is_eligible(
    comparator: Any,
    evidence_by_id: dict[str, dict[str, Any]],
) -> tuple[bool, list[str]]:
    if not isinstance(comparator, dict):
        return False, ["comparator_not_object"]
    missing: list[str] = []
    for field in ("comparator_id", "paper_id"):
        if not _text(comparator.get(field)):
            missing.append(field)
    if comparator.get("comparability") != "same_paper_explicit":
        missing.append("comparability_same_paper_explicit")
    for arm in ("control", "candidate"):
        value = comparator.get(arm)
        if not isinstance(value, dict) or not _text(value.get("formulation")):
            missing.append(f"{arm}_formulation")
            continue
        arm_ids = _grounded_ids(value.get("evidence_ids"), evidence_by_id)
        if not arm_ids:
            missing.append(f"{arm}_grounded_evidence")
        elif any(evidence_by_id[eid]["paper_id"] != comparator.get("paper_id") for eid in arm_ids):
            missing.append(f"{arm}_paper_mismatch")
    variable = comparator.get("controllable_variable")
    if not isinstance(variable, dict) or not _text(variable.get("name")):
        missing.append("controllable_variable_name")
    elif not _text(variable.get("control_level")) or not _text(variable.get("candidate_level")):
        missing.append("controllable_variable_levels")
    elif not _grounded_ids(variable.get("evidence_ids"), evidence_by_id):
        missing.append("controllable_variable_grounded_evidence")
    protocol = comparator.get("shared_protocol")
    protocol_fields = protocol.get("fields") if isinstance(protocol, dict) else None
    if not isinstance(protocol_fields, dict) or not MINIMUM_PROTOCOL_FIELDS.issubset(protocol_fields):
        missing.append("minimum_shared_protocol")
    elif not _grounded_ids(protocol.get("evidence_ids"), evidence_by_id):
        missing.append("shared_protocol_grounded_evidence")
    metrics = comparator.get("shared_metrics")
    metric_fields = metrics.get("fields") if isinstance(metrics, dict) else None
    if not isinstance(metric_fields, dict) or not metric_fields:
        missing.append("measurable_endpoint")
    elif not _grounded_ids(metrics.get("evidence_ids"), evidence_by_id):
        missing.append("shared_metrics_grounded_evidence")
    return not missing, missing


_EMPTY_TASK_FIDELITY: dict[str, dict[str, Any]] = {
    "anode_chemistry": {"value": None, "evidence_ids": []},
    "cathode_chemistry": {"value": None, "evidence_ids": []},
    "voltage_regime": {"value": None, "evidence_ids": []},
    "charge_rate_or_current_density": {"value": None, "evidence_ids": []},
    "intervention_domain": {"value": None, "evidence_ids": []},
}

# Comparator shared_protocol / controllable_variable → task_fidelity fields.
# Projection is deterministic and never invents values from free text.
_COMPARATOR_PROTOCOL_TO_FIDELITY = {
    "anode": "anode_chemistry",
    "cathode": "cathode_chemistry",
    "voltage_window": "voltage_regime",
    "charge_rate_or_current_density": "charge_rate_or_current_density",
}


def _empty_task_fidelity() -> dict[str, dict[str, Any]]:
    return {
        field: {"value": None, "evidence_ids": []}
        for field in _EMPTY_TASK_FIDELITY
    }


def project_task_fidelity_from_comparator(
    comparator: Any,
    evidence_by_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Project one comparator's grounded facts into task_fidelity.

    Mapping is exclusive to structured comparator fields with real evidence_ids.
    Missing fields remain null gaps. Never mixes facts across comparators.
    """
    fidelity = _empty_task_fidelity()
    if not isinstance(comparator, dict):
        return fidelity

    protocol = comparator.get("shared_protocol")
    if isinstance(protocol, dict):
        fields = protocol.get("fields") if isinstance(protocol.get("fields"), dict) else {}
        protocol_ids = (
            _grounded_ids(protocol.get("evidence_ids"), evidence_by_id)
            if evidence_by_id is not None
            else _ids(protocol.get("evidence_ids"))
        )
        for protocol_field, fidelity_field in _COMPARATOR_PROTOCOL_TO_FIDELITY.items():
            value = _text(_field_value(fields, protocol_field))
            # Value without grounded provenance stays a gap.
            if value and protocol_ids:
                fidelity[fidelity_field] = {
                    "value": value,
                    "evidence_ids": list(protocol_ids),
                }

    variable = comparator.get("controllable_variable")
    if isinstance(variable, dict):
        name = _text(variable.get("name"))
        variable_ids = (
            _grounded_ids(variable.get("evidence_ids"), evidence_by_id)
            if evidence_by_id is not None
            else _ids(variable.get("evidence_ids"))
        )
        if name and variable_ids:
            fidelity["intervention_domain"] = {
                "value": name,
                "evidence_ids": list(variable_ids),
            }
    return fidelity


def _fidelity_is_target_faithful(
    task_fidelity: Any,
    evidence_by_id: dict[str, dict[str, Any]],
) -> tuple[bool, list[str]]:
    if not isinstance(task_fidelity, dict):
        return False, ["task_fidelity_missing"]
    expected = (
        "anode_chemistry", "cathode_chemistry", "voltage_regime",
        "charge_rate_or_current_density", "intervention_domain",
    )
    missing: list[str] = []
    values: dict[str, str] = {}
    for field in expected:
        fact = task_fidelity.get(field)
        if not isinstance(fact, dict) or not _text(fact.get("value")):
            missing.append(field)
            continue
        if not _grounded_ids(fact.get("evidence_ids"), evidence_by_id):
            missing.append(f"{field}_grounded_evidence")
        values[field] = _text(fact.get("value")).casefold()
    if "anode_chemistry" in values and "lithium metal" not in values["anode_chemistry"]:
        missing.append("anode_not_lithium_metal")
    voltage = values.get("voltage_regime", "")
    voltage_values = [float(value) for value in re.findall(r"\b(\d+(?:\.\d+)?)\s*v\b", voltage)]
    if voltage and (not voltage_values or max(voltage_values) < 4.3):
        missing.append("voltage_not_high")
    rate = values.get("charge_rate_or_current_density", "")
    rate_values = [float(value) for value in re.findall(r"\b(\d+(?:\.\d+)?)\s*(?:c|ma\s*/?\s*cm(?:\^?|-?)2)\b", rate)]
    if rate and (not rate_values or max(rate_values) < 1.0):
        missing.append("rate_not_fast")
    if "intervention_domain" in values and "electrolyte" not in values["intervention_domain"]:
        missing.append("intervention_not_electrolyte_formulation")
    return not missing, missing


def _supported_mechanism(
    mechanisms: Any,
    evidence_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if not isinstance(mechanisms, list):
        return None
    for mechanism in mechanisms:
        if not isinstance(mechanism, dict):
            continue
        if mechanism.get("relation_type") not in SUPPORTED_RELATIONS:
            continue
        if not all(_text(mechanism.get(field)) for field in ("cause_or_intervention", "intermediate", "outcome", "support_strength")):
            continue
        ids = _grounded_ids(mechanism.get("evidence_ids"), evidence_by_id)
        if ids:
            return {**mechanism, "evidence_ids": ids}
    return None


def _supported_mechanisms(
    mechanisms: Any,
    evidence_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return every grounded mechanism relation, preserving its evidence class.

    A research lead needs the observed-characterization and author-supported
    inference boundaries, not just the first eligible relation used for the
    decision gate.
    """
    if not isinstance(mechanisms, list):
        return []
    selected: list[dict[str, Any]] = []
    for mechanism in mechanisms:
        if not isinstance(mechanism, dict):
            continue
        if mechanism.get("relation_type") not in SUPPORTED_RELATIONS:
            continue
        if not all(_text(mechanism.get(field)) for field in (
            "cause_or_intervention", "intermediate", "outcome", "support_strength",
        )):
            continue
        ids = _grounded_ids(mechanism.get("evidence_ids"), evidence_by_id)
        if ids:
            selected.append({**mechanism, "evidence_ids": ids})
    return selected


def _research_lead_gaps(protocol_fields: dict[str, Any]) -> list[dict[str, str]]:
    """Name decision-critical omissions; never turn their absence into facts."""
    aliases = {
        "repetitions": ("repetitions", "replicates", "n", "sample_size"),
        "temperature": ("temperature", "test_temperature"),
        "statistical_decision_threshold": (
            "statistical_decision_threshold", "decision_threshold", "threshold",
        ),
    }
    return [
        _gap(field, "not_specified_in_canonical_comparator")
        for field, names in aliases.items()
        if not any(_text(protocol_fields.get(name)) for name in names)
    ]


def _shared_salt_context(
    control: dict[str, Any], candidate: dict[str, Any], protocol_fields: dict[str, Any]
) -> str | None:
    """Project a canonical shared salt fact even when arm assembly stored it inline."""
    explicit = _text(protocol_fields.get("salt_context"))
    if explicit:
        return explicit
    contexts = []
    for arm in (control, candidate):
        match = re.search(r"shared\s+salt\s*:\s*(.+)$", _text(arm.get("formulation")), re.I)
        if match:
            contexts.append(match.group(1).strip())
    return contexts[0] if contexts and all(item == contexts[0] for item in contexts) else None


def _actionable_lead_details(
    *,
    control: dict[str, Any],
    candidate: dict[str, Any],
    variable: dict[str, Any],
    comparator: dict[str, Any],
    mechanisms: list[dict[str, Any]],
    evidence_ids: list[str],
) -> dict[str, Any]:
    """Express structured scientific truth as a planned, falsifiable decision.

    This deliberately does not inspect title, quotes, or full text.  Every
    scientific statement remains attached to the IDs supplied by the canonical
    comparator or mechanism contract.
    """
    observed = [item for item in mechanisms if item["relation_type"] == "observed_association"]
    inferred = [item for item in mechanisms if item["relation_type"] == "supported_mechanism"]
    metric_ids = _ids(comparator["shared_metrics"].get("evidence_ids"))
    measurements = [
        {
            "measurement": intermediate["intermediate"],
            "purpose": "Check the mechanism-linked characterization state in the candidate relative to the control.",
            "evidence_ids": intermediate["evidence_ids"],
        }
        for intermediate in mechanisms
    ] + [
        {
            "measurement": name,
            "purpose": "Compare the candidate with the control using the canonical comparator metric.",
            "evidence_ids": metric_ids,
        }
        for name in comparator["shared_metrics"]["fields"]
    ]
    rationale = {
        "intervention": {
            "statement": f"Change electrolyte formulation from {variable['control_level']} to {variable['candidate_level']}.",
            "evidence_ids": _ids(variable.get("evidence_ids")),
        },
        "observed_characterization": [
            {
                "statement": f"Observed characterization associates {item['cause_or_intervention']} with {item['intermediate']}.",
                "evidence_ids": item["evidence_ids"],
            }
            for item in observed
        ],
        "author_supported_mechanism": [
            {
                "statement": f"Author-supported mechanism: {item['cause_or_intervention']} acts through {item['intermediate']} toward {item['outcome']}.",
                "evidence_ids": item["evidence_ids"],
            }
            for item in inferred
        ],
        "expected_outcome": {
            "statement": "Planned validation: the candidate is expected to show the mechanism-consistent characterization pattern together with improvement in the canonical electrochemical metric(s); this is not an observed result.",
            "evidence_ids": evidence_ids,
            "observed_or_planned": "planned",
        },
    }
    return {
        "intervention": {
            "name": variable["name"],
            "control_level": variable["control_level"],
            "candidate_level": variable["candidate_level"],
            "statement": f"{variable['control_level']} → {variable['candidate_level']}",
            "evidence_ids": _ids(variable.get("evidence_ids")),
        },
        "control": {"formulation": control["formulation"], "evidence_ids": _ids(control.get("evidence_ids"))},
        "candidate": {"formulation": candidate["formulation"], "evidence_ids": _ids(candidate.get("evidence_ids"))},
        "shared_salt_context": {
            "value": _shared_salt_context(control, candidate, comparator["shared_protocol"]["fields"]),
            "evidence_ids": _ids(control.get("evidence_ids")),
        },
        "actionable_rationale_and_decision_criteria": {
            "grounded_rationale": rationale,
            "measurements": measurements,
            "support_criteria": {
                "statement": "Support the planned hypothesis only if the candidate, relative to the control, shows both a mechanism-consistent solvation/interphase characterization shift and improvement in the canonical electrochemical metric(s). No ungrounded numerical threshold is asserted.",
                "evidence_ids": evidence_ids,
                "observed_or_planned": "planned",
            },
            "falsification_or_weakening_criteria": [
                {"condition": "No improvement in the canonical electrochemical metric(s).", "decision": "weaken the hypothesis", "evidence_ids": metric_ids},
                {"condition": "Electrochemical improvement occurs but the expected mechanism-linked characterization signature is absent.", "decision": "mechanism not supported; alternative explanation required", "evidence_ids": evidence_ids},
                {"condition": "The mechanism-linked characterization signature occurs but the canonical electrochemical metric(s) do not improve.", "decision": "weaken the mechanism-to-outcome hypothesis", "evidence_ids": evidence_ids},
            ],
        },
        "gaps": _research_lead_gaps(comparator["shared_protocol"]["fields"]),
    }


def _experiment_matrix_from_actionable_lead(
    *,
    comparator_id: str,
    control: dict[str, Any],
    candidate: dict[str, Any],
    variable: dict[str, Any],
    comparator: dict[str, Any],
    lead_details: dict[str, Any],
    evidence_ids: list[str],
) -> dict[str, Any]:
    """Project Experiment Matrix validation semantics from one actionable lead.

    Preserves legacy electrochemical ``measurements`` (shared_metrics fields)
    for downstream consumers, and additively attaches the same typed
    mechanism / support / falsification / gap objects already owned by the
    Research Lead. Never re-infers scientific content.
    """
    criteria = lead_details["actionable_rationale_and_decision_criteria"]
    typed_measurements = list(criteria["measurements"])
    electrochemical_names = set(comparator["shared_metrics"]["fields"])
    electrochemical = [
        row for row in typed_measurements
        if row.get("measurement") in electrochemical_names
    ]
    mechanism_characterization = [
        row for row in typed_measurements
        if row.get("measurement") not in electrochemical_names
    ]
    return {
        "executable": True,
        "comparator_id": comparator_id,
        "arms": [
            {"role": "control", "formulation": control["formulation"], "evidence_ids": _ids(control.get("evidence_ids"))},
            {"role": "candidate", "formulation": candidate["formulation"], "evidence_ids": _ids(candidate.get("evidence_ids"))},
        ],
        "controllable_variable": variable,
        "fixed_protocol": comparator["shared_protocol"]["fields"],
        # Legacy consumer contract: electrochemical shared_metrics fields.
        "measurements": comparator["shared_metrics"]["fields"],
        # Additive enrichment projected from the same actionable lead payload.
        "electrochemical_measurements": electrochemical,
        "mechanism_characterization_measurements": mechanism_characterization,
        "support_criteria": criteria["support_criteria"],
        "falsification_or_weakening_criteria": criteria["falsification_or_weakening_criteria"],
        "gaps": list(lead_details["gaps"]),
        "provenance_evidence_ids": evidence_ids,
        "observed_or_planned": "planned",
    }


def consume_literature_evidence_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """Produce a 90-minute planning decision exclusively from v1 fields.

    ``structured_formulation_facts`` may seed ``comparators`` via prior
    assembly, but this consumer never invents shared_protocol/shared_metrics
    or lowers Task Fidelity to force canonical selection.

    Task fidelity is evaluated per eligible comparator by projecting that
    comparator's grounded protocol/controllable_variable facts — never by
    splicing fields across incompatible comparators.
    """
    evidence_by_id, validation_gaps = _evidence_by_id(contract)
    # Validate Option-B facts for grounding only.
    for raw_fact in contract.get("structured_formulation_facts") or []:
        fact = normalize_structured_formulation_fact(
            raw_fact,
            evidence_by_id=evidence_by_id,
        )
        if fact is None:
            validation_gaps.append(
                _gap("structured_formulation_facts", "ungrounded_or_invalid")
            )
    comparators = [item for item in contract.get("comparators") or [] if isinstance(item, dict)]
    comparator_gaps: list[dict[str, Any]] = []
    fidelity_gaps: list[str] = []
    faithful: dict[str, Any] | None = None
    projected_fidelity: dict[str, dict[str, Any]] | None = None
    first_eligible_fidelity: dict[str, dict[str, Any]] | None = None
    task_faithful_count = 0

    if comparators:
        for comparator in comparators:
            okay, missing = _comparator_is_eligible(comparator, evidence_by_id)
            if not okay:
                comparator_gaps.append(
                    {
                        "comparator_id": _text(comparator.get("comparator_id")),
                        "missing": missing,
                    }
                )
                continue
            projected = project_task_fidelity_from_comparator(
                comparator,
                evidence_by_id,
            )
            if first_eligible_fidelity is None:
                first_eligible_fidelity = projected
            fidelity_ok, gaps = _fidelity_is_target_faithful(
                projected,
                evidence_by_id,
            )
            if fidelity_ok:
                task_faithful_count += 1
                if faithful is None:
                    faithful = comparator
                    projected_fidelity = projected
            elif faithful is None and not fidelity_gaps:
                fidelity_gaps = gaps
    else:
        # No comparators: fall back to explicit contract task_fidelity only.
        _fidelity_ok, fidelity_gaps = _fidelity_is_target_faithful(
            contract.get("task_fidelity"),
            evidence_by_id,
        )

    mechanisms = _supported_mechanisms(contract.get("mechanism_evidence"), evidence_by_id)
    mechanism = mechanisms[0] if mechanisms else None

    if faithful is None or mechanism is None:
        return {
            "decision_status": "evidence_gap",
            "canonical_selected_comparator": None,
            "research_leads": [],
            "hypotheses": [],
            "validation": [],
            "experiment_matrix": {"executable": False},
            "task_fidelity": projected_fidelity
            or first_eligible_fidelity
            or (
                project_task_fidelity_from_comparator(comparators[0], evidence_by_id)
                if comparators
                else contract.get("task_fidelity")
            ),
            "task_faithful_comparators": task_faithful_count,
            "gaps": list(contract.get("gaps") or []) + validation_gaps + comparator_gaps + fidelity_gaps + (
                [] if mechanism is not None else ["supported_mechanism"]
            ),
        }

    comparator = faithful
    control = comparator["control"]
    candidate = comparator["candidate"]
    variable = comparator["controllable_variable"]
    endpoint = next(iter(comparator["shared_metrics"]["fields"]))
    evidence_ids = list(dict.fromkeys(
        _ids(control.get("evidence_ids"))
        + _ids(candidate.get("evidence_ids"))
        + _ids(variable.get("evidence_ids"))
        + _ids(comparator["shared_protocol"].get("evidence_ids"))
        + _ids(comparator["shared_metrics"].get("evidence_ids"))
        + [evidence_id for item in mechanisms for evidence_id in _ids(item.get("evidence_ids"))]
    ))
    comparator_id = _text(comparator["comparator_id"])
    hypothesis_id = f"hypothesis:{comparator_id}"
    lead_id = f"lead:{comparator_id}"
    validation_id = f"validation:{comparator_id}"
    canonical = {
        "comparator_id": comparator_id,
        "paper_id": comparator["paper_id"],
        "control": control,
        "candidate": candidate,
        "controllable_variable": variable,
        "shared_protocol": comparator["shared_protocol"],
        "shared_metrics": comparator["shared_metrics"],
        "evidence_ids": evidence_ids,
        "task_fidelity_status": "accepted",
        "support_status": "supported",
    }
    hypothesis = {
        "hypothesis_id": hypothesis_id,
        "comparator_id": comparator_id,
        "statement": (
            f"Changing {variable['name']} from {variable['control_level']} to "
            f"{variable['candidate_level']} is hypothesized to affect {endpoint} "
            f"through {mechanism['intermediate']}."
        ),
        "supporting_evidence_ids": evidence_ids,
        "observed_or_planned": "planned",
    }
    validation = {
        "validation_id": validation_id,
        "hypothesis_id": hypothesis_id,
        "comparator_id": comparator_id,
        "endpoint": endpoint,
        "protocol": comparator["shared_protocol"]["fields"],
        "supporting_evidence_ids": evidence_ids,
        "observed_or_planned": "planned",
    }
    # Construct the actionable validation plan once; Research Lead and
    # Experiment Matrix must share the same canonical semantics.
    lead_details = _actionable_lead_details(
        control=control,
        candidate=candidate,
        variable=variable,
        comparator=comparator,
        mechanisms=mechanisms,
        evidence_ids=evidence_ids,
    )
    matrix = _experiment_matrix_from_actionable_lead(
        comparator_id=comparator_id,
        control=control,
        candidate=candidate,
        variable=variable,
        comparator=comparator,
        lead_details=lead_details,
        evidence_ids=evidence_ids,
    )
    return {
        "decision_status": "ready_for_planned_validation",
        "canonical_selected_comparator": canonical,
        "research_leads": [{
            "research_lead_id": lead_id,
            "comparator_id": comparator_id,
            "hypothesis_id": hypothesis_id,
            "validation_id": validation_id,
            "supporting_evidence_ids": evidence_ids,
            "observed_or_planned": "planned",
            **lead_details,
        }],
        "hypotheses": [hypothesis],
        "validation": [validation],
        "experiment_matrix": matrix,
        "task_fidelity": projected_fidelity,
        "task_faithful_comparators": task_faithful_count,
        "gaps": list(contract.get("gaps") or []) + validation_gaps,
    }
