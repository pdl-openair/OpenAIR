from __future__ import annotations

from copy import deepcopy

import pytest

from app.modules.material_memory import (
    MAX_RETRIEVAL_CHARS,
    MemoryAccessDenied,
    build_specialist_context_envelope,
    get_memory_by_ref,
)


def _memory(memory_id: str, *, kind: str, content: str) -> dict:
    return {
        "memoryId": memory_id,
        "kind": kind,
        "content": content,
        "scope": "task",
        "createdBy": "user_1",
        "createdAt": "2026-07-23T00:00:00Z",
        "confidence": "high",
        "linkedMaterials": ["mat_1"],
        "linkedArtifacts": [],
    }


def _run() -> dict:
    return {
        "run_id": "run_1",
        "task_id": "task_1",
        "input_snapshot": {
            "memoryRecords": [
                _memory("mem_goal", kind="user_fact", content="Use the confirmed research question."),
                _memory("mem_decision", kind="decision", content="Use only bounded evidence."),
                _memory("mem_private", kind="artifact_summary", content="Other agent-only summary."),
                _memory("mem_long", kind="artifact_summary", content="x" * (MAX_RETRIEVAL_CHARS + 25)),
            ],
        },
    }


def _unit(memory_refs: list[str]) -> dict:
    return {
        "work_unit_id": "wu_1",
        "agent_id": "paper_evidence_extraction",
        "input_memory_refs": memory_refs,
    }


def test_context_envelope_contains_authorized_facts_and_bounded_catalog_only() -> None:
    envelope = build_specialist_context_envelope(
        frozen_run=_run(), work_unit=_unit(["mem_goal", "mem_decision", "mem_private"]),
    )

    assert [fact["memoryId"] for fact in envelope["requiredFacts"]] == ["mem_goal", "mem_decision"]
    assert envelope["requiredFacts"][0]["provenance"]["createdBy"] == "user_1"
    assert envelope["requiredFacts"][0]["contentHash"]
    assert envelope["memoryCatalog"] == [{
        "memoryId": "mem_private", "kind": "artifact_summary", "summary": "Other agent-only summary.",
        "locator": {"linkedMaterials": ["mat_1"], "linkedArtifacts": []},
        "contentHash": envelope["memoryCatalog"][0]["contentHash"], "truncated": False,
    }]
    assert envelope["retrievalPolicy"]["allowedMemoryIds"] == ["mem_goal", "mem_decision", "mem_private"]


def test_authorized_retrieval_is_snapshot_bound_and_denial_does_not_reveal_existence() -> None:
    run = _run()
    envelope = build_specialist_context_envelope(frozen_run=run, work_unit=_unit(["mem_goal", "mem_long"]))

    resolved = get_memory_by_ref(
        frozen_run=run, context_envelope=envelope, current_work_unit_id="wu_1",
        current_agent_id="paper_evidence_extraction", memory_ids=["mem_goal", "mem_long"],
    )
    assert [item["memoryId"] for item in resolved] == ["mem_goal", "mem_long"]
    assert resolved[1]["truncated"] is True
    assert sum(len(item["content"]) for item in resolved) == MAX_RETRIEVAL_CHARS

    for memory_id in ["mem_private", "does_not_exist"]:
        with pytest.raises(MemoryAccessDenied, match="^memory_access_denied$"):
            get_memory_by_ref(
                frozen_run=run, context_envelope=envelope, current_work_unit_id="wu_1",
                current_agent_id="paper_evidence_extraction", memory_ids=[memory_id],
            )

    for work_unit_id, agent_id in [
        ("wu_other", "paper_evidence_extraction"),
        ("wu_1", "result_reliability_audit"),
    ]:
        with pytest.raises(MemoryAccessDenied, match="^memory_access_denied$"):
            get_memory_by_ref(
                frozen_run=run, context_envelope=envelope, current_work_unit_id=work_unit_id,
                current_agent_id=agent_id, memory_ids=["mem_goal"],
            )


def test_missing_reference_fails_closed_and_mutable_payload_cannot_change_frozen_context() -> None:
    run = _run()
    envelope = build_specialist_context_envelope(frozen_run=run, work_unit=_unit(["mem_goal"]))
    mutable_task_payload = deepcopy(run["input_snapshot"])
    mutable_task_payload["memoryRecords"][0]["content"] = "changed after run start"

    assert envelope["requiredFacts"][0]["content"] == "Use the confirmed research question."
    with pytest.raises(MemoryAccessDenied, match="^memory_access_denied$"):
        build_specialist_context_envelope(frozen_run=run, work_unit=_unit(["missing"]))
