"""Deterministic execution bridge for OpenAIR system agents.

These agents are local product helpers, not LLM research executors. They only
work from user-provided text or existing artifacts and persist traceable task
records for the workbench.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from app.modules.system_agents import (
    PRESENTATION_AGENT_ID,
    REVIEW_AGENT_ID,
    SUMMARY_AGENT_ID,
    build_presentation_agent_narrative,
    build_review_agent_narrative,
    build_summary_agent_narrative,
)
from app.modules.task_store import store

SYSTEM_AGENT_RUNTIME = "openair_system_agent"

SYSTEM_AGENT_LABELS = {
    "openair_summary_agent": "摘要",
    "openair_review_agent": "复核",
    "openair_presentation_guidance_agent": "表达建议",
}

SystemAgentId = Literal[
    "openair_summary_agent",
    "openair_review_agent",
    "openair_presentation_guidance_agent",
]


class SystemAgentExecutionError(ValueError):
    pass


_SOURCE_ID_RE = re.compile(
    r"(?i)\b(?:doi:\s*)?(10\.\d{4,9}/[-._;()/:A-Z0-9]+)\b"
    r"|\b(?:arxiv:\s*)?(\d{4}\.\d{4,5}(?:v\d+)?)\b"
    r"|\b(?:PMID|PMCID|OpenAlex|Semantic Scholar)\s*[:：]\s*([A-Z0-9._:/-]+)"
    r"|((?:https?://|doi\.org/)[^\s<>)\]]+)"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _artifact_by_id(task: dict[str, Any], artifact_id: str | None) -> dict[str, Any] | None:
    if not artifact_id:
        return None
    for artifact in task.get("artifacts") or []:
        if artifact.get("id") == artifact_id:
            return artifact
    return None


def _artifact_text(artifact: dict[str, Any] | None) -> str:
    if not artifact:
        return ""
    parts = [
        artifact.get("title"),
        artifact.get("summary"),
        artifact.get("inlineContent"),
    ]
    return "\n\n".join(_clean(part) for part in parts if _clean(part))


def _source_material(task: dict[str, Any], *, source_text: str | None, artifact_id: str | None) -> tuple[str, dict[str, Any] | None]:
    artifact = _artifact_by_id(task, artifact_id)
    text = _clean(source_text) or _artifact_text(artifact)
    return text, artifact


def _short_summary(text: str, *, limit: int = 240) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "..."


def _source_ids(text: str, *, limit: int = 8) -> list[str]:
    ids: list[str] = []
    for match in _SOURCE_ID_RE.finditer(text or ""):
        value = next((group for group in match.groups() if group), "")
        value = value.strip().rstrip(".,;)")
        if value and value not in ids:
            ids.append(value)
        if len(ids) >= limit:
            break
    return ids


def _summary_markdown(text: str, artifact: dict[str, Any] | None) -> str:
    source = artifact.get("title") if artifact else "provided material"
    summary = _short_summary(text)
    ids = _source_ids(text)
    id_line = (
        f"- Source IDs preserved: {', '.join(ids)}"
        if ids
        else "- Source IDs preserved: none found; verify DOI / URL / arXiv / PMID before citing."
    )
    return "\n".join(
        [
            "# Source-bound summary",
            "",
            f"- Source boundary: {source}",
            f"- Human-readable summary: {summary}",
            id_line,
            "- Boundary note: This is a compression of provided material, not a review verdict.",
            "",
            "## Expandable source detail",
            "",
            text,
        ]
    )


def _review_markdown(text: str, artifact: dict[str, Any] | None, criteria: str | None, decision: str | None) -> str:
    title = artifact.get("title") if artifact else "provided material"
    verdict = "pending_manual_decision"
    if decision == "approved":
        verdict = "approved_by_request"
    elif decision == "needs_revision":
        verdict = "needs_revision_by_request"
    return "\n".join(
        [
            "# Artifact review report",
            "",
            f"- Review target: {title}",
            f"- Criteria: {_clean(criteria) or 'not provided'}",
            f"- Verdict: {verdict}",
            "- Boundary note: Review is limited to provided material and explicit criteria.",
            "",
            "## Material checked",
            "",
            _short_summary(text, limit=800),
            "",
            "## Next action",
            "",
            "Confirm the verdict manually before using this artifact downstream.",
        ]
    )


def _presentation_markdown(text: str, artifact: dict[str, Any] | None, audience: str | None, output_format: str | None) -> str:
    source = artifact.get("title") if artifact else "provided material"
    audience_label = _clean(audience) or "research audience"
    format_label = _clean(output_format) or "presentation/report"
    return "\n".join(
        [
            "# Presentation guidance draft",
            "",
            f"- Source boundary: {source}",
            f"- Audience: {audience_label}",
            f"- Target format: {format_label}",
            "",
            "## Suggested structure",
            "",
            "1. Context and question",
            "2. Evidence or result summary",
            "3. Limitations and risks",
            "4. Next decision or discussion point",
            "",
            "## Material to cite",
            "",
            _short_summary(text, limit=700),
            "",
            "## Risk note",
            "",
            "Only cite existing material; do not add unverified experiments, papers, or results.",
        ]
    )


def _agent_message_narrative(
    agent_id: SystemAgentId,
    *,
    summary: str,
    source_type: Literal["assistant_message", "capability_call"],
    source_artifact: dict[str, Any] | None,
    artifact_id: str | None,
    call_id: str,
    artifact_ids: list[str],
    review_decision: Literal["approved", "needs_revision"] | None = None,
) -> dict[str, Any]:
    if agent_id == SUMMARY_AGENT_ID:
        return build_summary_agent_narrative(
            summary,
            source_type=source_type,
            source_id=call_id,
            severity="success",
        )
    if agent_id == REVIEW_AGENT_ID:
        return build_review_agent_narrative(
            artifact_title=source_artifact.get("title") if source_artifact else "provided material",
            decision=(
                "usable"
                if review_decision == "approved"
                else "rejected_or_needs_revision"
                if review_decision == "needs_revision"
                else "needs_review"
            ),
            note=summary,
            source_id=artifact_id,
        )
    return build_presentation_agent_narrative(
        summary,
        source_type=source_type,
        source_id=call_id,
        artifact_refs=artifact_ids,
        status="completed",
    )


def run_system_agent(
    task_id: str,
    *,
    agent_id: SystemAgentId,
    source_text: str | None = None,
    artifact_id: str | None = None,
    criteria: str | None = None,
    audience: str | None = None,
    output_format: str | None = None,
    review_decision: Literal["approved", "needs_revision"] | None = None,
) -> dict[str, Any]:
    task = store.get_task(task_id)
    if not task:
        raise KeyError("task not found")

    material, source_artifact = _source_material(task, source_text=source_text, artifact_id=artifact_id)
    if not material:
        raise SystemAgentExecutionError("system agent requires sourceText or a readable artifactId")

    stage_key = task.get("currentStage") or "problem_modeling"
    call = store.add_capability_call(
        task_id,
        skill_id=agent_id,
        status="running",
        runtime=SYSTEM_AGENT_RUNTIME,
        stage_key=stage_key,
        input_artifact_refs=[artifact_id] if artifact_id else [],
    )
    if not call:
        raise KeyError("task not found")
    call_id = call["id"]

    reviewed_artifact_id: str | None = None
    if agent_id == SUMMARY_AGENT_ID:
        content = _summary_markdown(material, source_artifact)
        kind = "agent_summary"
        title = "Source-bound summary"
        summary = "Generated a source-bound human-readable summary from provided material."
        artifact_narrative = build_summary_agent_narrative(
            content,
            source_type="artifact",
            source_id=None,
            severity="success",
        )
    elif agent_id == REVIEW_AGENT_ID:
        content = _review_markdown(material, source_artifact, criteria, review_decision)
        kind = "review_report"
        title = "Artifact review report"
        summary = "Generated a review report from provided material and criteria."
        artifact_narrative = build_review_agent_narrative(
            artifact_title=source_artifact.get("title") if source_artifact else title,
            decision="usable" if review_decision == "approved" else "rejected_or_needs_revision" if review_decision == "needs_revision" else "needs_review",
            note=summary,
            source_id=artifact_id,
        )
        if source_artifact and review_decision:
            state = "usable" if review_decision == "approved" else "rejected_or_needs_revision"
            updated = store.update_artifact_review_state(
                task_id,
                source_artifact["id"],
                artifact_state=state,
                reviewer=REVIEW_AGENT_ID,
                note=criteria or summary,
                source="review_agent",
            )
            if updated:
                reviewed_artifact_id = source_artifact["id"]
    elif agent_id == PRESENTATION_AGENT_ID:
        content = _presentation_markdown(material, source_artifact, audience, output_format)
        kind = "presentation_outline"
        title = "Presentation guidance draft"
        summary = "Generated a presentation/report structure from existing material."
        artifact_narrative = build_presentation_agent_narrative(
            content,
            source_type="artifact",
            source_id=None,
            status="completed",
        )
    else:
        raise SystemAgentExecutionError(f"unsupported system agent: {agent_id}")

    artifact = store.add_artifact(
        task_id,
        kind=kind,
        title=title,
        summary=summary,
        format="markdown",
        related_stage=stage_key,
        related_skill_id=agent_id,
        source_capability_call_id=call_id,
        storage_type="inline",
        inline_content=content,
        extra_fields={
            "artifactState": "needs_review",
            "userFacingNarrative": artifact_narrative,
        },
    )
    artifact_ids = [artifact["id"]] if artifact else []

    call_narrative = _agent_message_narrative(
        agent_id,
        summary=summary,
        source_type="capability_call",
        source_artifact=source_artifact,
        artifact_id=artifact_id,
        call_id=call_id,
        artifact_ids=artifact_ids,
        review_decision=review_decision,
    )
    store.patch_capability_call(
        task_id,
        call_id,
        status="completed",
        result_summary=summary,
        ended_at=_utc_now(),
        artifact_refs=artifact_ids,
        output_artifact_refs=artifact_ids,
        stage_key=stage_key,
        metadata={"userFacingNarrative": call_narrative},
    )

    message_narrative = _agent_message_narrative(
        agent_id,
        summary=summary,
        source_type="assistant_message",
        source_artifact=source_artifact,
        artifact_id=artifact_id,
        call_id=call_id,
        artifact_ids=artifact_ids,
        review_decision=review_decision,
    )
    store.append_agent_message(
        task_id,
        role="assistant",
        text=summary,
        runtime=SYSTEM_AGENT_RUNTIME,
        author_label=SYSTEM_AGENT_LABELS[agent_id],
        capability_call_id=call_id,
        artifact_refs=artifact_ids,
        user_facing_narrative=message_narrative,
    )

    return {
        "callId": call_id,
        "status": "completed",
        "summary": summary,
        "runtime": SYSTEM_AGENT_RUNTIME,
        "artifactIds": artifact_ids,
        "reviewedArtifactId": reviewed_artifact_id,
        "error": None,
    }
