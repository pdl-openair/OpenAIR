"""Shared material and task-memory helpers for the research agent MVP.

The store remains embedded in the task payload for this MVP. The helpers keep
classification, provenance, and context-pack shaping deterministic so later
specialist-agent work can consume material IDs instead of raw upload blobs.
"""

from __future__ import annotations

from hashlib import sha256
import re
from typing import Any


MATERIAL_LIKE_KINDS = {
    "user_upload",
    "material",
    "materials_probe_seed_note",
    "source_fragment",
    "evidence_row",
    "research_note",
    "seed_note",
}


# This contract is deliberately small and serializable.  A context envelope is
# constructed from the run's immutable snapshot, never from mutable task state.
CONTEXT_ENVELOPE_VERSION = "memory-context-v1"
MAX_REQUIRED_FACTS = 12
MAX_REQUIRED_FACT_CHARS = 1200
MAX_CATALOG_ENTRIES = 32
MAX_CATALOG_SUMMARY_CHARS = 240
MAX_RETRIEVAL_RECORDS = 3
MAX_RETRIEVAL_CHARS = 6000


class MemoryAccessDenied(ValueError):
    """Fail closed without revealing whether a memory record exists."""


def build_specialist_context_envelope(*, frozen_run: dict[str, Any], work_unit: dict[str, Any]) -> dict[str, Any]:
    """Build the bounded memory context for one authorized specialist unit.

    ``inputMemoryRefs`` is the complete allowlist.  Missing or malformed
    references reject the request before any provider callback can run.
    """

    snapshot = frozen_run.get("input_snapshot")
    if not isinstance(snapshot, dict):
        raise MemoryAccessDenied("memory_access_denied")
    allowed_ids = _memory_refs(work_unit.get("input_memory_refs", work_unit.get("inputMemoryRefs")))
    records = _snapshot_memory_by_id(snapshot)
    if any(memory_id not in records for memory_id in allowed_ids):
        raise MemoryAccessDenied("memory_access_denied")

    required: list[dict[str, Any]] = []
    catalog: list[dict[str, Any]] = []
    for memory_id in allowed_ids:
        record = records[memory_id]
        if record["kind"] in {"user_fact", "decision"} and len(required) < MAX_REQUIRED_FACTS:
            content, truncated = _bounded_text(record["content"], MAX_REQUIRED_FACT_CHARS)
            required.append({
                **_memory_identity(record),
                "content": content,
                "truncated": truncated,
            })
        elif len(catalog) < MAX_CATALOG_ENTRIES:
            summary, truncated = _bounded_text(record["content"], MAX_CATALOG_SUMMARY_CHARS)
            catalog.append({
                "memoryId": record["memoryId"],
                "kind": record["kind"],
                "summary": summary,
                "locator": _memory_locator(record),
                "contentHash": _content_hash(record["content"]),
                "truncated": truncated,
            })

    return {
        "version": CONTEXT_ENVELOPE_VERSION,
        "requiredFacts": required,
        "memoryCatalog": catalog,
        "retrievalPolicy": {
            "taskId": _required_text(frozen_run.get("task_id")),
            "runId": _required_text(frozen_run.get("run_id")),
            "workUnitId": _required_text(work_unit.get("work_unit_id", work_unit.get("workUnitId"))),
            "agentId": _required_text(work_unit.get("agent_id", work_unit.get("agentId"))),
            "allowedMemoryIds": allowed_ids,
            "maxRecordsPerRead": MAX_RETRIEVAL_RECORDS,
            "maxCharsPerRead": MAX_RETRIEVAL_CHARS,
        },
    }


def get_memory_by_ref(
    *,
    frozen_run: dict[str, Any],
    context_envelope: dict[str, Any],
    current_work_unit_id: str,
    current_agent_id: str,
    memory_ids: list[str],
) -> list[dict[str, Any]]:
    """Read authorized snapshot memory only, with a uniform denial boundary.

    The caller must provide the server-authoritative work-unit and Agent
    identity; the envelope does not establish its own caller authority.
    Invalid, missing, and unauthorized IDs all raise the same error to avoid
    an existence oracle.
    """

    try:
        snapshot = frozen_run.get("input_snapshot")
        policy = context_envelope.get("retrievalPolicy")
        if not isinstance(snapshot, dict) or not isinstance(policy, dict):
            raise MemoryAccessDenied("memory_access_denied")
        if context_envelope.get("version") != CONTEXT_ENVELOPE_VERSION:
            raise MemoryAccessDenied("memory_access_denied")
        if policy.get("taskId") != _required_text(frozen_run.get("task_id")) or policy.get("runId") != _required_text(frozen_run.get("run_id")):
            raise MemoryAccessDenied("memory_access_denied")
        if policy.get("workUnitId") != _required_text(current_work_unit_id) or policy.get("agentId") != _required_text(current_agent_id):
            raise MemoryAccessDenied("memory_access_denied")
        requested = _memory_refs(memory_ids)
        allowed = _memory_refs(policy.get("allowedMemoryIds"))
        if not requested or len(requested) > MAX_RETRIEVAL_RECORDS or any(memory_id not in allowed for memory_id in requested):
            raise MemoryAccessDenied("memory_access_denied")
        records = _snapshot_memory_by_id(snapshot)
        if any(memory_id not in records for memory_id in requested):
            raise MemoryAccessDenied("memory_access_denied")
    except (AttributeError, TypeError, MemoryAccessDenied):
        raise MemoryAccessDenied("memory_access_denied") from None

    remaining = MAX_RETRIEVAL_CHARS
    resolved: list[dict[str, Any]] = []
    for memory_id in requested:
        record = records[memory_id]
        content = record["content"][:remaining]
        resolved.append({
            **_memory_identity(record),
            "content": content,
            "truncated": len(content) < len(record["content"]),
        })
        remaining -= len(content)
        if remaining == 0:
            break
    return resolved


def _snapshot_memory_by_id(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = snapshot.get("memoryRecords")
    if not isinstance(rows, list):
        return {}
    records: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        memory_id = _required_text(raw.get("memoryId"))
        kind = _required_text(raw.get("kind"))
        content = raw.get("content")
        if not memory_id or not kind or not isinstance(content, str) or memory_id in records:
            continue
        records[memory_id] = dict(raw)
    return records


def _memory_identity(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "memoryId": record["memoryId"],
        "kind": record["kind"],
        "provenance": {
            "createdBy": _required_text(record.get("createdBy")),
            "createdAt": _required_text(record.get("createdAt")),
            "scope": _required_text(record.get("scope")),
            "confidence": _required_text(record.get("confidence")),
        },
        "contentHash": _content_hash(record["content"]),
    }


def _memory_locator(record: dict[str, Any]) -> dict[str, list[str]]:
    return {
        "linkedMaterials": _memory_refs(record.get("linkedMaterials")),
        "linkedArtifacts": _memory_refs(record.get("linkedArtifacts")),
    }


def _content_hash(content: str) -> str:
    return sha256(content.encode("utf-8")).hexdigest()


def _bounded_text(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[:limit], True


def _memory_refs(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    refs = [_required_text(item) for item in value]
    return list(dict.fromkeys(ref for ref in refs if ref))


def _required_text(value: Any) -> str:
    return str(value or "").strip()


def compact_preview(value: str | None, limit: int = 1200) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", value).strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def infer_material_kind(*, filename: str, kind: str, content_type: str, content: str | None) -> str:
    haystack = " ".join(part for part in [filename, kind, content_type, content[:500] if content else ""] if part)
    lower = haystack.lower()
    if "materials_probe_seed_note" in lower:
        return "materials_probe_seed_note"
    if "seed note" in lower or "seed_note" in lower:
        return "seed_note"
    if "evidence" in lower and ("row" in lower or "table" in lower):
        return "evidence_row"
    if "source" in lower and ("fragment" in lower or "excerpt" in lower or "locator" in lower):
        return "source_fragment"
    return kind or "user_upload"


def is_material_upload(kind: str, filename: str, content_type: str) -> bool:
    lower = " ".join([kind, filename, content_type]).lower()
    return kind in MATERIAL_LIKE_KINDS or "material" in lower or "seed_note" in lower or "seed note" in lower


def missing_fields_for_upload(*, material_kind: str, content: str | None) -> list[str]:
    if material_kind in {"materials_probe_seed_note", "seed_note"}:
        missing: list[str] = []
        text = (content or "").lower()
        if "doi" not in text and "source" not in text and "paper" not in text:
            missing.append("source_locator")
        if "raw" not in text and "data" not in text and "spectra" not in text:
            missing.append("raw_evidence")
        return missing
    return []


def material_context_pack(task: dict[str, Any], material_ids: list[str]) -> dict[str, Any]:
    """Build a small context-pack-shaped payload from task material IDs.

    This is not the full Phase-D Context Pack Builder. It deliberately exposes
    the stable boundary that later builders need: select by materialId and carry
    previews, locators, readiness, and linked artifact IDs forward.
    """

    requested = set(material_ids)
    materials = [
        material
        for material in task.get("materials") or []
        if not requested or material.get("materialId") in requested
    ]
    return {
        "contextPackId": f"ctx_{task.get('taskId', 'unknown')}_materials",
        "taskId": task.get("taskId"),
        "materials": {
            "mustUse": [m.get("materialId") for m in materials if m.get("materialId")],
            "selectedExcerpts": [
                {
                    "materialId": m.get("materialId"),
                    "locator": (m.get("source") or {}).get("locator"),
                    "text": m.get("contentPreview") or "",
                    "linkedArtifactIds": m.get("linkedArtifactIds") or [],
                }
                for m in materials
            ],
        },
        "readiness": [
            {
                "materialId": m.get("materialId"),
                "readable": (m.get("quality") or {}).get("readable", True),
                "sourceBounded": (m.get("quality") or {}).get("sourceBounded", True),
                "missingFields": (m.get("quality") or {}).get("missingFields") or [],
            }
            for m in materials
        ],
    }
