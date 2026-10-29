"""Accept formal record outputs or use the existing grounded singleton closure."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

# The grouping contract was validated at small scale; a single call covering every
# paper is known to degrade into identity-only record groups.  Keep each grouping
# call within this evidence budget.  Papers are never split across calls because
# record identity is intra-source.
GROUPING_EVIDENCE_BATCH_LIMIT = 8


def assert_formal_child_result(result: Mapping[str, Any]) -> None:
    artifact = result.get("artifactPayload")
    presentation = artifact.get("presentation") if isinstance(artifact, Mapping) else None
    metadata = presentation.get("metadata") if isinstance(presentation, Mapping) else None
    protocol = metadata.get("resultProtocol") if isinstance(metadata, Mapping) else None
    if isinstance(protocol, Mapping) and any(
        item.get("path") == "$"
        for item in protocol.get("quarantined_items") or []
        if isinstance(item, Mapping)
    ):
        raise RuntimeError("child_skill_output_not_finalized")


def _accept_records(output: Any, record_input: Mapping[str, Any], finalizer: Any) -> dict[str, Any]:
    if not isinstance(output, Mapping):
        raise RuntimeError("research_record_formal_output_invalid")
    required_errors = [
        error for error in finalizer.OUTPUT_VALIDATOR.iter_errors(output)
        if error.validator == "required"
    ]
    if required_errors:
        raise RuntimeError("research_record_formal_output_invalid")
    return dict(output)


def _known_fact_count(output: Mapping[str, Any]) -> int:
    """Count informative fields a grouping result actually carried."""
    count = 0
    for record in output.get("evidence_records") or []:
        for section in ("material", "process"):
            for fact in (record.get(section) or {}).values():
                if isinstance(fact, Mapping) and fact.get("fact_status") == "known":
                    count += 1
        count += len(record.get("outcomes") or [])
        count += len(record.get("other_context") or [])
    return count


def _grouping_batches(record_input: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Split the record input into per-source grouping batches within the scale limit."""
    evidence_packet = record_input.get("evidence_packet")
    source_material = record_input.get("source_material")
    if not isinstance(evidence_packet, Mapping) or not isinstance(source_material, Mapping):
        return [dict(record_input)]
    items = [
        row for row in evidence_packet.get("evidence_items") or []
        if isinstance(row, Mapping)
    ]
    packets = [
        row for row in source_material.get("paper_packets") or []
        if isinstance(row, Mapping)
    ]
    if not items or not packets:
        return [dict(record_input)]
    by_source: dict[str, list[Mapping[str, Any]]] = {}
    for row in items:
        by_source.setdefault(str(row.get("candidate_id") or ""), []).append(row)
    ordered = [row for row in packets if str(row.get("candidate_id") or "") in by_source]
    covered = sum(len(by_source[str(row.get("candidate_id") or "")]) for row in ordered)
    # Evidence without a readable source packet cannot be batched safely; keep the
    # single-call path so such inputs behave exactly as before.
    if not ordered or covered != len(items):
        return [dict(record_input)]
    batches: list[dict[str, Any]] = []
    current_packets: list[Mapping[str, Any]] = []
    current_items: list[Mapping[str, Any]] = []
    for packet in ordered:
        paper_items = by_source[str(packet.get("candidate_id") or "")]
        if current_items and len(current_items) + len(paper_items) > GROUPING_EVIDENCE_BATCH_LIMIT:
            batches.append(_batch_view(record_input, current_packets, current_items))
            current_packets, current_items = [], []
        current_packets.append(packet)
        current_items.extend(paper_items)
    if current_items:
        batches.append(_batch_view(record_input, current_packets, current_items))
    return batches


def _batch_view(
    record_input: Mapping[str, Any],
    packets: list[Mapping[str, Any]],
    items: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Project one grouping batch from the canonical record input."""
    evidence_ids = {str(row.get("evidence_id") or "") for row in items}
    candidate_ids = {str(row.get("candidate_id") or "") for row in packets}
    evidence_packet = dict(record_input["evidence_packet"])
    evidence_packet["evidence_items"] = [dict(row) for row in items]
    source_material = dict(record_input["source_material"])
    source_material["paper_packets"] = [dict(row) for row in packets]
    batch = dict(record_input)
    batch["evidence_packet"] = evidence_packet
    batch["source_material"] = source_material
    index = record_input.get("evidence_index")
    if isinstance(index, Mapping):
        filtered = dict(index)
        filtered["evidence_items"] = [
            dict(row) for row in index.get("evidence_items") or []
            if isinstance(row, Mapping) and str(row.get("evidence_id") or "") in evidence_ids
        ]
        batch["evidence_index"] = filtered
    catalog = record_input.get("atomic_grounding_catalog")
    if isinstance(catalog, list):
        batch["atomic_grounding_catalog"] = [
            dict(row) for row in catalog
            if isinstance(row, Mapping) and str(row.get("evidence_id") or "") in evidence_ids
        ]
    source_index = record_input.get("source_index")
    if isinstance(source_index, Mapping):
        filtered = dict(source_index)
        filtered["paper_packets"] = [
            dict(row) for row in source_index.get("paper_packets") or []
            if isinstance(row, Mapping) and str(row.get("candidate_id") or "") in candidate_ids
        ]
        batch["source_index"] = filtered
    return batch


def _merge_outputs(outputs: list[dict[str, Any]]) -> dict[str, Any]:
    records: list[Mapping[str, Any]] = []
    provenance: list[Mapping[str, Any]] = []
    seen_sources: set[str] = set()
    for output in outputs:
        records.extend(output.get("evidence_records") or [])
        for row in output.get("source_provenance") or []:
            source_id = str(row.get("source_id") or "")
            if source_id and source_id not in seen_sources:
                seen_sources.add(source_id)
                provenance.append(row)
    records.sort(key=lambda row: str(row.get("record_id") or ""))
    return {"evidence_records": records, "source_provenance": provenance}


def close_research_records(
    record_input: dict[str, Any],
    *,
    llm_child: Callable[..., dict[str, Any]],
    finalizer: Any,
    warnings: list[str],
    singleton_materializer: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    batches = _grouping_batches(record_input)
    outputs: list[dict[str, Any]] = []
    for ordinal, batch in enumerate(batches, 1):
        adapter_name = (
            "pvd_t04_research_record_grouping"
            if len(batches) == 1
            else f"pvd_t04_research_record_grouping_batch_{ordinal:02d}"
        )
        accepted: dict[str, Any] | None = None
        try:
            records = llm_child(
                "pvd_research_record_structuring",
                batch,
                adapter_name=adapter_name,
            )
            accepted = _accept_records(records, batch, finalizer)
            # A schema-valid result whose every record carries only identity is
            # less informative than the grounded singleton degradation the
            # contract already defines; take that path instead of hollow groups.
            if (accepted.get("evidence_records") or []) and _known_fact_count(accepted) == 0:
                warnings.append("research_record_grouping_uninformative:singleton_closure_used")
                accepted = None
        except RuntimeError:
            warnings.append("research_record_grouping_unavailable:singleton_closure_used")
        if accepted is None:
            materializer = singleton_materializer or finalizer.materialize_evidence_singletons
            fallback = materializer(batch)
            if getattr(fallback, "output", None) is None:
                raise RuntimeError("deterministic_singleton_closure_failed") from None
            accepted = _accept_records(fallback.output, batch, finalizer)
        outputs.append(accepted)
    if len(outputs) == 1:
        return outputs[0]
    return _merge_outputs(outputs)
