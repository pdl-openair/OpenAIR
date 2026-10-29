from __future__ import annotations

import base64
import hashlib
import importlib
import importlib.util
import json
import os
import sys
from collections import Counter
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping

INITIAL_WAVE = 8
EXPANSION_WAVE = 4
MAX_PAPERS = INITIAL_WAVE + EXPANSION_WAVE
MAX_EVIDENCE_ITEMS = 60
MODEL_CONTEXT_MAX_CHARS_PER_PAPER = 8_000
MODEL_CONTEXT_PROJECTION_VERSION = "407b-anchored-sections-v1"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@lru_cache(maxsize=None)
def _child_callable(
    domain: str,
    skill_dir_name: str,
    module_name: str,
    callable_name: str,
) -> Callable[..., dict[str, Any]]:
    adapter_dir = _repo_root() / "skills" / domain / skill_dir_name / "adapter"
    init_py = adapter_dir / "__init__.py"
    if not init_py.is_file():
        raise RuntimeError(f"child adapter package missing: {adapter_dir}")
    package_name = "_pvd_t04_" + domain.replace("-", "_") + "_" + skill_dir_name.replace("-", "_")
    if package_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            package_name,
            init_py,
            submodule_search_locations=[str(adapter_dir)],
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"child adapter package cannot be loaded: {adapter_dir}")
        package = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = package
        spec.loader.exec_module(package)
    module = importlib.import_module(f"{package_name}.{module_name}")
    fn = getattr(module, callable_name, None)
    if not callable(fn):
        raise RuntimeError(f"child adapter callable missing: {skill_dir_name}:{callable_name}")
    return fn


def _source_acquisition(payload: dict[str, Any]) -> dict[str, Any]:
    return _child_callable(
        "opentrajectory",
        "opentrajectory-source-acquisition",
        "source_acquisition_adapter",
        "run_source_acquisition",
    )(payload)


def _progressive_reading(payload: dict[str, Any]) -> dict[str, Any]:
    return _child_callable(
        "opentrajectory",
        "deepxiv-progressive-reading-skill",
        "progressive_reading_adapter",
        "progressive_read_candidates",
    )(payload)


def _load_record_finalizer() -> Any:
    skill_root = _repo_root() / "skills" / "pvd" / "pvd-research-record-structuring"
    scripts_dir = skill_root / "scripts"
    init_py = scripts_dir / "__init__.py"
    package_name = "_pvd_t04_record_finalizer"
    if package_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            package_name, init_py, submodule_search_locations=[str(scripts_dir)]
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("research-record finalizer package unavailable")
        package = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = package
        spec.loader.exec_module(package)
    return importlib.import_module(f"{package_name}.finalize")


def _api_imports() -> Any:
    api_root = _repo_root() / 'services' / 'api'
    if str(api_root) not in sys.path:
        sys.path.insert(0, str(api_root))
    from app.execution import durable_skill_runtime
    return durable_skill_runtime


class ChildSkillFailure(RuntimeError):
    """Preserve the runtime's failure facts, including bounded diagnostics."""

    def __init__(self, result: Mapping[str, Any]) -> None:
        self.classification = str(result.get('failureClassification') or 'skill_execution_failed')
        self.summary = str(result.get('summary') or 'child Skill failed')
        self.failed_stage = str(result.get('failedStage') or '')
        self.first_mismatch = str(result.get('firstMismatch') or '')
        self.diagnostic = {key: deepcopy(result[key]) for key in ('state', 'failureClassification', 'summary', 'failedStage', 'firstMismatch', 'diagnostics', 'externalResultUnknown') if key in result}
        super().__init__(f'{self.classification}:{self.summary}')


def _decode_artifact(result: Mapping[str, Any]) -> dict[str, Any]:
    if result.get("state") != "succeeded":
        raise ChildSkillFailure(result)
    artifact = result.get("artifactPayload")
    _child_callable(
        "pvd", "pvd-literature-evidence-acquisition-closure", "record_closure", "assert_formal_child_result",
    )(result)
    encoded = artifact.get("contentBase64") if isinstance(artifact, Mapping) else None
    if not isinstance(encoded, str) or not encoded:
        raise RuntimeError("child_skill_artifact_missing")
    value = json.loads(base64.b64decode(encoded).decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("child_skill_artifact_invalid")
    return value


def _llm_child(skill_id: str, parameters: dict[str, Any], *, adapter_name: str) -> dict[str, Any]:
    runtime = _api_imports()
    result = runtime.execute_new_durable_llm_child(skill_id=skill_id, parameters=parameters, parent_capability_call_id=str(os.environ.get('OPENAIR_PARENT_CAPABILITY_CALL_ID') or ''), adapter_name=adapter_name)
    return _decode_artifact(result)


def _model_paper_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    """407b-authoritative bounded model view for one finalized paper packet.

    The canonical packet remains the finalizer/provenance authority.  This view
    contains only stable paper identity plus anchored text prefixes visible to
    the Provider, with a hard 8k-character budget per paper.
    """
    candidate_id = str(packet.get("candidate_id") or "").strip()
    sections = {
        (str(row.get("section_name") or ""), str(row.get("content_hash") or "")): row
        for row in (packet.get("sections") or [])
        if isinstance(row, Mapping)
    }
    resolved: list[tuple[str, str, str]] = []
    for raw_anchor in packet.get("source_anchors") or []:
        if not isinstance(raw_anchor, Mapping):
            continue
        anchor_id = str(raw_anchor.get("anchor_id") or "").strip()
        section_name = str(raw_anchor.get("section_name") or "")
        content_hash = str(raw_anchor.get("content_hash") or "")
        section = sections.get((section_name, content_hash))
        if anchor_id and section is not None:
            resolved.append((anchor_id, section_name, str(section.get("content") or "")))

    remaining = MODEL_CONTEXT_MAX_CHARS_PER_PAPER
    anchored_sections: list[dict[str, Any]] = []
    for index, (anchor_id, section_name, content) in enumerate(resolved):
        section_budget = max(1, remaining // (len(resolved) - index))
        selected = content[:section_budget]
        anchored_sections.append({
            "source_anchor_id": anchor_id,
            "section_name": section_name,
            "content": selected,
        })
        remaining -= len(selected)

    return {
        "candidate_id": candidate_id,
        "title": str(packet.get("title") or ""),
        "doi": str(packet.get("doi") or "") or None,
        "arxiv_id": str(packet.get("arxiv_id") or "") or None,
        "anchored_sections": anchored_sections,
    }

def _evidence_skill_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Attach the bounded Provider view without replacing canonical source material."""
    canonical = deepcopy(dict(packet))
    model_view = _model_paper_packet(packet)
    canonical["anchored_sections"] = deepcopy(model_view["anchored_sections"])
    return canonical


def _reader_evidence_schema_view(
    evidence_schema: Mapping[str, Any], *, user_query: str
) -> dict[str, Any]:
    """Mechanical transport view for the existing generic reader; no new scientific semantics."""
    targets: list[dict[str, Any]] = []
    for raw in evidence_schema.get("evidence_targets") or []:
        if not isinstance(raw, Mapping):
            continue
        targets.append(
            {
                "target_id": raw.get("target_id"),
                "target_type": raw.get("target_type"),
                "scientific_role": raw.get("scientific_role"),
                "description": raw.get("description"),
                "requiredness": raw.get("requiredness"),
                "completion_criteria": raw.get("completion_criteria"),
                "allowed_evidence_forms": [],
                "condition_dimensions": [],
                "comparison_requirements": list(raw.get("comparison_dimensions") or []),
                "provenance_requirements": list(raw.get("provenance_requirements") or []),
            }
        )
    return {
        "schema_id": "pvd-t04:" + _fingerprint(evidence_schema)[:24],
        "schema_version": "0.1.0",
        "task_context": {
            "research_question": user_query,
            "task_scope": "PVD literature evidence acquisition and closure",
            "domain_hints": [],
            "design_assumptions": [],
        },
        "evidence_targets": targets,
        "gap_taxonomy": list(evidence_schema.get("gap_taxonomy") or []),
        "uncertainty_rules": list(evidence_schema.get("uncertainty_rules") or []),
        "optional_extensions": [],
    }


def _record_model_source_index(source_material: Mapping[str, Any]) -> dict[str, Any]:
    """407b-authoritative source identity view for CALL_07."""
    return {
        "paper_packets": [
            {
                "candidate_id": str(packet.get("candidate_id") or ""),
                "title": str(packet.get("title") or ""),
                "doi": str(packet.get("doi") or "") or None,
                "arxiv_id": str(packet.get("arxiv_id") or "") or None,
            }
            for packet in (source_material.get("paper_packets") or [])
            if isinstance(packet, Mapping)
        ]
    }


def _record_model_evidence_index(evidence_packet: Mapping[str, Any]) -> dict[str, Any]:
    """Give CALL_07 only grouping identity/role metadata, not the full Evidence packet."""
    return {
        "evidence_items": [
            {
                "evidence_id": str(row.get("evidence_id") or ""),
                "candidate_id": str(row.get("candidate_id") or ""),
                "evidence_type": str(row.get("evidence_type") or ""),
                "section_name": str(row.get("section_name") or ""),
                "matched_target_ids": list(row.get("matched_target_ids") or []),
            }
            for row in (evidence_packet.get("evidence_items") or [])
            if isinstance(row, Mapping)
        ]
    }


def _record_atomic_grounding_catalog(evidence_packet: Mapping[str, Any]) -> list[dict[str, str]]:
    """Bind every model-visible scientific string to exactly one Evidence Node."""
    scientific_fields = (
        "evidence_type",
        "assertion_mode",
        "claim",
        "evidence_quote",
        "conditions",
        "metric",
        "mechanism",
        "comparison",
        "qualifiers",
    )
    catalog: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def collect(value: Any, evidence_id: str) -> None:
        if isinstance(value, str):
            if not value.strip():
                return
            identity = (evidence_id, value)
            if identity in seen:
                return
            seen.add(identity)
            catalog.append({
                "grounded_ref": f"ground_{len(catalog) + 1:04d}",
                "text": value,
                "evidence_id": evidence_id,
            })
        elif isinstance(value, Mapping):
            for child in value.values():
                collect(child, evidence_id)
        elif isinstance(value, list):
            for child in value:
                collect(child, evidence_id)

    for index, row in enumerate(evidence_packet.get("evidence_items") or []):
        if not isinstance(row, Mapping):
            raise ValueError(f"evidence_packet.evidence_items[{index}] must be an object")
        evidence_id = str(row.get("evidence_id") or "").strip()
        for field in scientific_fields:
            if field in row:
                collect(row[field], evidence_id)
    return catalog


def _fallback_selection(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected = [deepcopy(row) for row in candidates[:MAX_PAPERS]]
    return selected, {
        "mode": "retrieval_order_fallback",
        "selected_candidate_ids": [str(row.get("candidate_id") or "") for row in selected],
        "degraded": True,
        "reason": "scientific_screening_unavailable",
    }


def _screened_selection(
    screening: Mapping[str, Any], candidates: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Use scientific screening to order reading without turning it into a hard gate.

    This mirrors the validated 407b wartime rule: model judgments improve order,
    while unused capacity is filled from the real retrieval ranking. Explicit
    exclusions are respected unless every reviewed item was excluded, in which
    case retrieval order remains available for the stronger full-text judgment.
    """
    topk = screening.get("topk_selection")
    if not isinstance(topk, Mapping):
        return _fallback_selection(candidates)
    rows = topk.get("selected_for_p03")
    if not isinstance(rows, list):
        rows = []

    candidate_by_id = {
        str(row.get("candidate_id") or ""): row
        for row in candidates
        if isinstance(row, Mapping) and str(row.get("candidate_id") or "")
    }
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        candidate_id = str(raw.get("candidate_id") or "").strip()
        if not candidate_id or candidate_id in seen or candidate_id not in candidate_by_id:
            continue
        ordered.append(deepcopy(dict(raw)))
        seen.add(candidate_id)

    explicit_excluded = {
        str(row.get("candidate_id") or "").strip()
        for row in (topk.get("excluded_examples") or [])
        if isinstance(row, Mapping) and str(row.get("candidate_id") or "").strip()
    } if isinstance(topk, Mapping) else set()

    fallback_used = False
    for raw in candidates:
        candidate_id = str(raw.get("candidate_id") or "").strip()
        if not candidate_id or candidate_id in seen or candidate_id in explicit_excluded:
            continue
        ordered.append(deepcopy(raw))
        seen.add(candidate_id)
        fallback_used = True
        if len(ordered) >= MAX_PAPERS:
            break

    if not ordered and candidates:
        selected, meta = _fallback_selection(candidates)
        meta["reason"] = "all_screened_candidates_excluded_fulltext_fallback"
        return selected, meta

    selected = ordered[:MAX_PAPERS]
    return selected, {
        "mode": "scientific_screening_with_retrieval_fill" if fallback_used else "scientific_screening",
        "selected_candidate_ids": [str(row.get("candidate_id") or "") for row in selected],
        "degraded": (
            bool(topk.get("degraded")) if isinstance(topk, Mapping) else False
        ) or fallback_used,
        "screening_summary": deepcopy(screening.get("summary") or {}),
        "retrieval_fill_used": fallback_used,
    }


def _merge_reading_outputs(waves: list[Mapping[str, Any]]) -> dict[str, Any]:
    if not waves:
        return {
            "status": "degraded",
            "paper_packets": [],
            "material_attempts": [],
            "summary": {
                "input_candidate_count": 0,
                "attempted_candidate_count": 0,
                "truncated_candidate_count": 0,
                "paper_packet_count": 0,
                "ready_packet_count": 0,
                "abstract_only_packet_count": 0,
                "failed_candidate_count": 0,
                "material_attempt_count": 0,
                "section_count": 0,
                "source_anchor_count": 0,
                "partial_success": False,
                "degraded": True,
            },
            "audit": {
                "skill_id": "deepxiv_progressive_reading",
                "reader_mode": "deepxiv_sdk_reader",
                "raw_default_enabled": False,
                "raw_call_count": 0,
                "max_papers": 1,
                "max_sections_per_paper": 3,
                "network_timeout_seconds": 20.0,
                "max_provider_retries": 1,
                "max_total_chars": 300000,
                "output_content_chars": 0,
                "section_priority": ["method", "results", "discussion"],
                "input_fingerprint": _fingerprint({"waves": []}),
            },
        }
    packets: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    summaries = [row.get("summary") for row in waves if isinstance(row.get("summary"), Mapping)]
    audits = [row.get("audit") for row in waves if isinstance(row.get("audit"), Mapping)]
    for row in waves:
        packets.extend(deepcopy([x for x in row.get("paper_packets") or [] if isinstance(x, Mapping)]))
        attempts.extend(deepcopy([x for x in row.get("material_attempts") or [] if isinstance(x, Mapping)]))
    sum_keys = (
        "input_candidate_count", "attempted_candidate_count", "truncated_candidate_count",
        "paper_packet_count", "ready_packet_count", "abstract_only_packet_count",
        "failed_candidate_count", "material_attempt_count", "section_count", "source_anchor_count",
    )
    summary = {key: sum(int(s.get(key) or 0) for s in summaries) for key in sum_keys}
    summary["partial_success"] = any(bool(s.get("partial_success")) for s in summaries)
    summary["degraded"] = any(bool(s.get("degraded")) for s in summaries)
    first = audits[0] if audits else {}
    audit = {
        "skill_id": "deepxiv_progressive_reading",
        "reader_mode": "mixed_deepxiv_and_fulltext" if any(a.get("reader_mode") == "mixed_deepxiv_and_fulltext" for a in audits) else "deepxiv_sdk_reader",
        "raw_default_enabled": any(bool(a.get("raw_default_enabled")) for a in audits),
        "raw_call_count": sum(int(a.get("raw_call_count") or 0) for a in audits),
        "max_papers": max(1, min(MAX_PAPERS, summary["input_candidate_count"] or len(packets) or 1)),
        "max_sections_per_paper": int(first.get("max_sections_per_paper") or 3),
        "network_timeout_seconds": float(first.get("network_timeout_seconds") or 20.0),
        "max_provider_retries": int(first.get("max_provider_retries") or 1),
        "max_total_chars": int(first.get("max_total_chars") or 300000),
        "output_content_chars": sum(int(a.get("output_content_chars") or 0) for a in audits),
        "section_priority": list(first.get("section_priority") or ["method", "results", "discussion"]),
        "input_fingerprint": _fingerprint({"wave_fingerprints": [a.get("input_fingerprint") for a in audits]}),
    }
    status = "degraded" if summary["degraded"] or summary["partial_success"] else "success"
    return {"status": status, "paper_packets": packets, "material_attempts": attempts, "summary": summary, "audit": audit}




def _validate_identity_scope(evidence_schema, coverage_target, pending_parameter_evidence_identities):
    return (
        list(coverage_target.get("formal_parameter_keys") or []),
        list(coverage_target.get("pending_parameter_keys") or []),
        list(coverage_target.get("all_parameter_keys") or []),
    )


def _required_target_ids(evidence_schema: Mapping[str, Any]) -> list[str]:
    return [
        str(row.get("target_id"))
        for row in evidence_schema.get("evidence_targets") or []
        if isinstance(row, Mapping) and row.get("requiredness") == "required" and row.get("target_id")
    ]


def _merge_evidence_packets(
    packets: list[Mapping[str, Any]], evidence_schema: Mapping[str, Any], warnings: list[str]
) -> dict[str, Any]:
    by_id: dict[str, dict[str, Any]] = {}
    extraction_warnings = list(warnings)
    dropped_invalid = 0
    dropped_duplicate = 0
    for packet in packets:
        summary = packet.get("summary") if isinstance(packet.get("summary"), Mapping) else {}
        dropped_invalid += int(summary.get("dropped_invalid_count") or 0)
        dropped_duplicate += int(summary.get("dropped_duplicate_count") or 0)
        extraction_warnings.extend(str(v) for v in packet.get("extraction_warnings") or [])
        for raw in packet.get("evidence_items") or []:
            if not isinstance(raw, Mapping):
                continue
            eid = str(raw.get("evidence_id") or "").strip()
            if not eid:
                continue
            if eid in by_id:
                dropped_duplicate += 1
                continue
            by_id[eid] = deepcopy(dict(raw))
    items = list(by_id.values())
    covered = {
        str(tid)
        for row in items
        for tid in row.get("matched_target_ids") or []
        if str(tid)
    }
    required = _required_target_ids(evidence_schema)
    uncovered = [tid for tid in required if tid not in covered]
    counts = Counter(str(row.get("evidence_type") or "") for row in items)
    degraded = bool(uncovered or extraction_warnings)
    return {
        "evidence_items": items,
        "schema_id": evidence_schema.get("schema_id"),
        "summary": {
            "status": "degraded" if degraded else "succeeded",
            "input_paper_packet_count": sum(int((p.get("summary") or {}).get("input_paper_packet_count") or 0) for p in packets),
            "input_source_anchor_count": sum(int((p.get("summary") or {}).get("input_source_anchor_count") or 0) for p in packets),
            "evidence_item_count": len(items),
            "evidence_type_counts": dict(sorted(counts.items())),
            "dropped_duplicate_count": dropped_duplicate,
            "dropped_invalid_count": dropped_invalid,
            "uncovered_required_target_ids": uncovered,
            "degraded": degraded,
        },
        "audit": {
            "skill_id": "pvd_evidence_node_extraction",
            "traceability_mode": "source_anchor_quote_finalizer",
            "finalizer_version": "t04-composite-merge-v1",
            "input_fingerprint": _fingerprint({"evidence_ids": sorted(by_id), "schema": evidence_schema}),
            "supported_evidence_types": [
                "material_composition", "experimental_condition", "quantitative_result",
                "mechanism_claim", "characterization_observation", "computation_result",
                "comparison", "limitation",
            ],
        },
        "extraction_warnings": sorted(set(extraction_warnings)),
    }


def _coverage(evidence_schema: Mapping[str, Any], evidence_packet: Mapping[str, Any]) -> dict[str, Any]:
    targets = [dict(row) for row in evidence_schema.get("evidence_targets") or [] if isinstance(row, Mapping)]
    target_ids_by_key: dict[str, set[str]] = {}
    target_keys_by_id: dict[str, list[str]] = {}
    required_target_ids: list[str] = []
    for row in targets:
        target_id = str(row.get("target_id") or "").strip()
        if not target_id:
            continue
        keys = [str(k) for k in row.get("covered_parameter_keys") or [] if str(k)]
        target_keys_by_id[target_id] = keys
        for key in keys:
            target_ids_by_key.setdefault(key, set()).add(target_id)
        if row.get("requiredness") == "required":
            required_target_ids.append(target_id)
    refs_by_key = {key: [] for key in target_ids_by_key}
    refs_by_required = {tid: [] for tid in required_target_ids}
    for row in evidence_packet.get("evidence_items") or []:
        if not isinstance(row, Mapping):
            continue
        eid = str(row.get("evidence_id") or "").strip()
        status = str(row.get("target_match_status") or "").strip()
        matched = {str(v) for v in row.get("matched_target_ids") or []}
        if not eid or status == "not_comparable":
            continue
        for key, ids in target_ids_by_key.items():
            if matched.intersection(ids): refs_by_key[key].append(eid)
        if status == "matched":
            for tid in required_target_ids:
                if tid in matched: refs_by_required[tid].append(eid)
    covered_keys = [key for key, refs in refs_by_key.items() if refs]
    missing_keys = [key for key, refs in refs_by_key.items() if not refs]
    missing_required = [tid for tid in required_target_ids if not refs_by_required[tid]]
    missing_required_keys: list[str] = []
    for tid in missing_required:
        for key in target_keys_by_id.get(tid, []):
            if key not in missing_required_keys: missing_required_keys.append(key)
    return {
        "coverage_sufficient": not missing_keys,
        "covered_parameter_keys": covered_keys,
        "missing_parameter_keys": missing_keys,
        "evidence_refs_by_parameter": refs_by_key,
        "usable_evidence_count": len([
            x for x in evidence_packet.get("evidence_items") or []
            if isinstance(x, Mapping) and str(x.get("target_match_status") or "") != "not_comparable"
        ]),
        "decision_coverage_sufficient": not missing_required,
        "required_target_ids": required_target_ids,
        "missing_required_target_ids": missing_required,
        "missing_required_parameter_keys": missing_required_keys,
        "evidence_refs_by_required_target": refs_by_required,
        "decision_coverage_basis": "all_required_evidence_targets_with_full_match",
    }


def _unresolved_gaps(evidence_schema: Mapping[str, Any], coverage: Mapping[str, Any]) -> list[dict[str, Any]]:
    missing = set(coverage.get("missing_required_target_ids") or [])
    return [
        {
            "target_id": str(row.get("target_id") or ""),
            "covered_parameter_keys": list(row.get("covered_parameter_keys") or []),
            "description": str(row.get("description") or ""),
        }
        for row in evidence_schema.get("evidence_targets") or []
        if isinstance(row, Mapping) and str(row.get("target_id") or "") in missing
    ]


def _conditional_links(
    evidence_schema: Mapping[str, Any], evidence_packet: Mapping[str, Any], pending_keys: list[str]
) -> list[dict[str, Any]]:
    target_ids_by_key = {str(key): [] for key in pending_keys}
    for row in evidence_schema.get("evidence_targets") or []:
        if not isinstance(row, Mapping): continue
        tid = str(row.get("target_id") or "")
        covered = {str(v) for v in row.get("covered_parameter_keys") or []}
        for key in target_ids_by_key:
            if key in covered: target_ids_by_key[key].append(tid)
    refs = {key: [] for key in target_ids_by_key}
    for row in evidence_packet.get("evidence_items") or []:
        if not isinstance(row, Mapping): continue
        eid = str(row.get("evidence_id") or "")
        matched = {str(v) for v in row.get("matched_target_ids") or []}
        for key, tids in target_ids_by_key.items():
            if eid and matched.intersection(tids): refs[key].append(eid)
    return [{"parameter_key": key, "target_ids": tids, "evidence_refs": refs[key]} for key, tids in target_ids_by_key.items()]


def _empty_evidence(evidence_schema: Mapping[str, Any], warnings: list[str]) -> dict[str, Any]:
    return _merge_evidence_packets([], evidence_schema, warnings)


def _select_scientific_sources(
    candidates: list[dict[str, Any]],
    *,
    task_id: str,
    user_query: str,
    retrieval_design: Mapping[str, Any],
    evidence_schema: Mapping[str, Any],
    llm_child: Callable[..., dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    """Run only screening; preserve its failure facts without redoing retrieval/read."""
    if not candidates:
        return [], {"mode": "no_candidates", "selected_candidate_ids": [], "degraded": True}, [
            "retrieval_returned_no_candidates"
        ]
    try:
        screening = llm_child(
            "opentrajectory_source_screening_selection", #NOTE: 通过这里实现跨技能嵌套调用，调用opentrajectory_source_screening_selection技能
            {
                "task_id": task_id,
                "user_query": user_query,
                "research_context": {
                    "retrieval_design": deepcopy(dict(retrieval_design)),
                    "formal_evidence_targets": deepcopy(list(evidence_schema.get("evidence_targets") or [])),
                },
                "evidence_schema": deepcopy(dict(evidence_schema)),
                "candidates": deepcopy(candidates),
            },
            adapter_name="pvd_t04_scientific_screening",
        )
        selected, selection = _screened_selection(screening, candidates)
        return selected, selection, []
    except RuntimeError as exc:
        selected, selection = _fallback_selection(candidates)
        if isinstance(exc, ChildSkillFailure):
            selection["diagnostics"] = deepcopy(exc.diagnostic)
        else:
            # Non-runtime exceptions have no structured classification to invent.
            selection["diagnostics"] = {"exceptionType": type(exc).__name__}
        return selected, selection, [
            "scientific_screening_unavailable:retrieval_order_preserved"
        ]


def run_literature_evidence_closure(
    payload: dict[str, Any],
    *,
    source_acquisition: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    progressive_reading: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    llm_child: Callable[..., dict[str, Any]] | None = None,
    singleton_materializer: Callable[[Mapping[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    task_id = str(payload.get("task_id") or "").strip()
    user_query = str(payload.get("user_query") or "").strip()
    retrieval_design = payload.get("retrieval_design")
    evidence_schema = payload.get("evidence_schema")
    coverage_target = payload.get("coverage_target")
    pending_raw = payload.get("pending_parameter_evidence_identities")
    if (
        not task_id
        or not user_query
        or not isinstance(retrieval_design, Mapping)
        or not isinstance(evidence_schema, Mapping)
        or not isinstance(coverage_target, Mapping)
        or not isinstance(pending_raw, list)
    ):
        raise ValueError("T04 canonical inputs are incomplete")
    _formal_keys, pending_keys, _all_keys = _validate_identity_scope(
        evidence_schema, coverage_target, pending_raw
    )

    source_acquisition = source_acquisition or _source_acquisition
    progressive_reading = progressive_reading or _progressive_reading
    llm_child = llm_child or _llm_child
    warnings: list[str] = []

    query_variants = [deepcopy(dict(v)) for v in retrieval_design.get("query_variants") or [] if isinstance(v, Mapping)]
    acquisition = source_acquisition({
        "task_id": task_id,
        "user_query": user_query,
        "search_matrix": [],
        "expanded_query_variants": query_variants,
        "retrieval_config": deepcopy(payload.get("retrieval_config") or {}),
    })
    candidates = [dict(row) for row in acquisition.get("candidates") or [] if isinstance(row, Mapping)]
    retrieval_report = deepcopy(acquisition.get("retrieval_report") or {})

    selected, selection, screening_warnings = _select_scientific_sources(
        candidates,
        task_id=task_id,
        user_query=user_query,
        retrieval_design=retrieval_design,
        evidence_schema=evidence_schema,
        llm_child=llm_child,
    )
    warnings.extend(screening_warnings)

    reader_schema = _reader_evidence_schema_view(evidence_schema, user_query=user_query)
    reading_waves: list[dict[str, Any]] = []
    evidence_packets: list[dict[str, Any]] = []
    waves_completed = 0

    def execute_wave(rows: list[dict[str, Any]], wave_number: int) -> None:
        nonlocal waves_completed
        if not rows:
            return
        reading = progressive_reading({
            "topk_selection": {"selected_for_p03": deepcopy(rows)},
            "evidence_schema": deepcopy(reader_schema),
            "reading_config": {"max_papers": len(rows)},
        })
        reading_waves.append(reading)
        waves_completed += 1
        paper_packets = [dict(row) for row in reading.get("paper_packets") or [] if isinstance(row, Mapping)]
        if not paper_packets:
            warnings.append(f"wave_{wave_number}:no_readable_paper_packets")
            return

        evidence_skill_packets: list[dict[str, Any]] = []
        for source_packet in paper_packets:
            try:
                evidence_skill_packets.append(_evidence_skill_packet(source_packet))
            except ValueError as exc:
                candidate_id = str(source_packet.get("candidate_id") or "")
                warnings.append(
                    f"wave_{wave_number}:paper_evidence_input_unavailable:"
                    f"candidate_id={candidate_id}:reason={exc}"
                )
        if not evidence_skill_packets:
            warnings.append(f"wave_{wave_number}:no_model_eligible_paper_packets")
            return

        try:
            packet = llm_child(
                "pvd_evidence_node_extraction",
                {
                    "task_id": task_id,
                    "paper_packets": evidence_skill_packets,
                    "evidence_schema": deepcopy(dict(evidence_schema)),
                    "item_budget": MAX_EVIDENCE_ITEMS,
                },
                adapter_name=f"pvd_t04_evidence_wave_{wave_number}",
            )
            evidence_packets.append(packet)
        except ChildSkillFailure as exc:
            warnings.append(
                f"wave_{wave_number}:evidence_extraction_unavailable:"
                f"classification={exc.classification}:summary={exc.summary}"
            )
        except RuntimeError as exc:
            warnings.append(
                f"wave_{wave_number}:evidence_extraction_unavailable:"
                f"classification=runtime_error:summary={exc}"
            )

    execute_wave(selected[:INITIAL_WAVE], 1)
    evidence_packet = _merge_evidence_packets(evidence_packets, evidence_schema, warnings)
    coverage = _coverage(evidence_schema, evidence_packet)
    early_stop_reason = "required_evidence_coverage_satisfied" if coverage["decision_coverage_sufficient"] else "required_evidence_gaps_remain"
    if not coverage["decision_coverage_sufficient"] and len(selected) > INITIAL_WAVE:
        execute_wave(selected[INITIAL_WAVE:MAX_PAPERS], 2)
        evidence_packet = _merge_evidence_packets(evidence_packets, evidence_schema, warnings)
        coverage = _coverage(evidence_schema, evidence_packet)
        early_stop_reason = "required_evidence_coverage_satisfied_after_expansion" if coverage["decision_coverage_sufficient"] else "reading_budget_exhausted_with_required_gaps"
    elif not selected:
        early_stop_reason = "retrieval_exhausted_without_selected_candidates"
    elif not coverage["decision_coverage_sufficient"] and len(selected) <= INITIAL_WAVE:
        early_stop_reason = "selected_material_exhausted_with_required_gaps"

    source_material = _merge_reading_outputs(reading_waves)
    records = {"evidence_records": [], "source_provenance": []}
    if evidence_packet.get("evidence_items"):
        record_input = {
            "evidence_packet": evidence_packet,
            "source_material": source_material,
            "evidence_index": _record_model_evidence_index(evidence_packet),
            "atomic_grounding_catalog": _record_atomic_grounding_catalog(evidence_packet),
            "source_index": _record_model_source_index(source_material),
        }
        records = _child_callable(
            "pvd", "pvd-literature-evidence-acquisition-closure", "record_closure", "close_research_records",
        )(
            record_input, llm_child=llm_child, finalizer=_load_record_finalizer(),
            warnings=warnings, singleton_materializer=singleton_materializer,
        )

    coverage = dict(coverage)
    coverage.update({
        "waves_completed": waves_completed,
        "papers_read": len(source_material.get("paper_packets") or []),
        "early_stop_reason": early_stop_reason,
    })
    return {
        "task_id": task_id,
        "skill_id": "pvd_literature_evidence_acquisition_closure",
        "evidence_packet": evidence_packet,
        "research_records": list(records.get("evidence_records") or []),
        "source_provenance": list(records.get("source_provenance") or []),
        "coverage": coverage,
        "unresolved_gaps": _unresolved_gaps(evidence_schema, coverage),
        "conditional_evidence_linkage": _conditional_links(evidence_schema, evidence_packet, pending_keys),
        "retrieval_report": retrieval_report,
        "selection": selection,
        "reading_evidence": source_material,
        "warnings": sorted(set(warnings + [str(v) for v in evidence_packet.get("extraction_warnings") or []])),
    }

def _pvd_shortage_safe_identity(value):
    text = str(value or "coverage")
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-")
    text = "_".join(filter(None, ("".join(ch if ch in allowed else "_" for ch in text)).split("_")))
    if not text or not text[0].isalnum():
        text = "coverage_" + text
    return text[:100]

def _build_evidence_shortage_facts(result):
    if not isinstance(result, dict): return []
    coverage=result.get("coverage")
    if not isinstance(coverage, dict) or coverage.get("decision_coverage_sufficient") is True: return []
    missing_targets=sorted({str(v) for v in (coverage.get("missing_required_target_ids") or []) if str(v)})
    missing_parameters=sorted({str(v) for v in (coverage.get("missing_required_parameter_keys") or coverage.get("missing_parameter_keys") or []) if str(v)})
    gaps=result.get("unresolved_gaps"); gaps=gaps if isinstance(gaps,list) else []
    by_target={str(g.get("target_id")):g for g in gaps if isinstance(g,dict) and g.get("target_id") is not None}
    task_id=str(result.get("task_id") or "unknown_task")
    source_ref=f"pvd_literature_evidence_acquisition_closure:{task_id}:coverage"
    facts=[]
    for target_id in missing_targets:
        gap=by_target.get(target_id) or {}
        keys=gap.get("covered_parameter_keys"); keys=keys if isinstance(keys,list) else []
        keys=sorted({str(v) for v in keys if str(v) and (not missing_parameters or str(v) in missing_parameters)}) or missing_parameters
        facts.append({"shortage_id":"coverage_gap:"+_pvd_shortage_safe_identity(target_id),"source_artifact_type":"coverage_report","source_artifact_ref":source_ref,"reason_code":"coverage_gap","description":str(gap.get("description") or f"Required evidence target {target_id} remains uncovered after literature evidence closure.")[:1000],"affected_parameter_keys":keys,"affected_target_ids":[target_id]})
    if not facts:
        facts.append({"shortage_id":"coverage_gap:required_evidence","source_artifact_type":"coverage_report","source_artifact_ref":source_ref,"reason_code":"coverage_gap","description":"Required evidence remains insufficient after literature evidence closure.","affected_parameter_keys":missing_parameters,"affected_target_ids":[]})
    return facts

_run_literature_evidence_closure_core = run_literature_evidence_closure


def run_literature_evidence_closure(
    payload: dict[str, Any],
    *,
    source_acquisition: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    progressive_reading: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    llm_child: Callable[..., dict[str, Any]] | None = None,
    singleton_materializer: Callable[[Mapping[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    result = _run_literature_evidence_closure_core(
        payload,
        source_acquisition=source_acquisition,
        progressive_reading=progressive_reading,
        llm_child=llm_child,
        singleton_materializer=singleton_materializer,
    )
    if not isinstance(result, dict):
        return result
    result = dict(result)
    retrieval_design = payload.get("retrieval_design") if isinstance(payload, dict) else None
    if not isinstance(retrieval_design, dict):
        raise RuntimeError("PVD_LITERATURE_CLOSURE_RETRIEVAL_DESIGN_MISSING")
    result["prior_retrieval_design"] = dict(retrieval_design)
    result["evidence_shortage_facts"] = _build_evidence_shortage_facts(result)
    return result
