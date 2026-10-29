"""Task store with optional JSON file persistence (enable_persistence)."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from app.models.schemas import TaskOut
from app.modules.task_constants import STAGE_ORDER, STAGE_SUBSTAGE_CATALOG
from app.modules.task_repository import TaskRepository, prepare_task_plan_candidate
from app.execution.durable_orchestrator_bridge import precompile_creation_plan
from app.modules import artifact_provenance
from app.modules.artifact_gate import (
    ARTIFACT_STATES,
    append_validator_result_to_call,
    apply_artifact_gate,
    normalize_artifact_state,
)
from app.modules.material_memory import material_context_pack
from app.modules.system_agents import build_review_agent_narrative
from app.modules.task_events import publish_task_event

_log = logging.getLogger(__name__)

AGENT_RUNTIME_CLAUDE_CODE = "claude_code"

TaskDict = dict[str, Any]


class TaskStorePostgresMutationForbidden(RuntimeError):
    """Raised before a JSON TaskStore mutation can diverge from PostgreSQL."""

    status_code = 503
    detail = "taskstore_mutation_forbidden_in_postgres"

DEFAULT_OWNER_ID = "user_demo_owner"
DEFAULT_WORKSPACE_ID = "ws_demo_default"
DEFAULT_CREATED_BY = "openair_system"
DEFAULT_TASK_EXECUTION_MODE = "stage_confirmation"
_TASK_EXECUTION_MODES = {"automatic_research", "stage_confirmation", "manual_step"}
_ARTIFACT_STATES = ARTIFACT_STATES


def _default_ownership(seed: dict[str, Any] | None = None) -> dict[str, str]:
    seed = seed or {}
    return {
        "ownerId": (seed.get("ownerId") or DEFAULT_OWNER_ID).strip() or DEFAULT_OWNER_ID,
        "workspaceId": (seed.get("workspaceId") or DEFAULT_WORKSPACE_ID).strip() or DEFAULT_WORKSPACE_ID,
        "createdBy": (seed.get("createdBy") or DEFAULT_CREATED_BY).strip() or DEFAULT_CREATED_BY,
    }


def _apply_task_ownership_defaults(task: TaskDict) -> None:
    task.update(_default_ownership(task))


def _normalize_task_execution_mode(task: TaskDict) -> None:
    if task.get("executionMode") not in _TASK_EXECUTION_MODES:
        task["executionMode"] = DEFAULT_TASK_EXECUTION_MODE


def _normalize_artifact_evidence_refs(artifact: dict[str, Any]) -> None:
    """Upgrade legacy provenance rows to the public evidence-reference contract."""
    raw_refs = artifact.get("evidenceRefs")
    if not isinstance(raw_refs, list):
        artifact["evidenceRefs"] = []
        return

    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_refs):
        if not isinstance(raw, dict):
            continue
        if all(isinstance(raw.get(key), str) for key in ("id", "claim", "sourceTitle", "sourceUrl", "excerpt")):
            normalized.append(raw)
            continue

        source_api = str(raw.get("sourceApi") or raw.get("source_api") or "legacy_source").strip()
        identifier = str(
            raw.get("externalId")
            or raw.get("external_id")
            or raw.get("arxivId")
            or raw.get("arxiv_id")
            or raw.get("doi")
            or f"{artifact.get('id') or 'artifact'}-{index + 1}"
        ).strip()
        source_url = str(raw.get("sourceUrl") or raw.get("url") or "").strip()
        title = str(raw.get("sourceTitle") or raw.get("title") or identifier).strip()
        normalized.append(
            {
                "id": f"{source_api}:{identifier}",
                "claim": f"该来源为历史导入的 {source_api} 题录追溯记录。",
                "sourceTitle": title,
                "sourceUrl": source_url,
                "excerpt": "历史记录未保留可供核对的原文摘录；使用前请人工核对来源。",
                "sourceIdentifier": identifier,
            }
        )
    artifact["evidenceRefs"] = normalized


def _default_artifact_state(kind: str | None) -> str:
    return "partial_success" if kind == "stage_summary" else "needs_review"


def _normalize_artifact_state(artifact: dict[str, Any]) -> None:
    validator_result = artifact.get("validatorResult") if isinstance(artifact.get("validatorResult"), dict) else {}
    artifact["artifactState"] = normalize_artifact_state(
        artifact.get("artifactState")
        or validator_result.get("artifactState")
        or _default_artifact_state(artifact.get("kind")),
        kind=artifact.get("kind"),
    )


# ---------------------------------------------------------------------------
# Artifact versioning helpers (issue #302)
# ---------------------------------------------------------------------------


def _artifact_content_snapshot(artifact: dict[str, Any]) -> dict[str, Any]:
    """Capture the content-bearing fields of an artifact as a version snapshot."""
    storage = artifact.get("storageType")
    inline = artifact.get("inlineContent")
    if not storage:
        storage = "inline" if (isinstance(inline, str) and inline) else "local"
    return {
        "storageType": storage,
        "inlineContent": inline if isinstance(inline, str) else None,
        "path": artifact.get("path"),
        "externalKey": artifact.get("externalKey"),
        "contentSha256": artifact.get("contentSha256"),
        "contentSize": artifact.get("contentSize"),
    }


def _versioned_view(artifact: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    """Return ``(versions, currentVersionId)`` without mutating the artifact.

    Artifacts without a ``versions`` list are presented as a single v1 ``initial``
    version synthesized from their current content fields. The initial version id
    is deterministic (derived from the artifact id) so repeated reads are stable
    and the lazy backfill never creates churn. This keeps legacy/seed artifacts
    compatible with versioning without a data migration.
    """
    versions = artifact.get("versions")
    if isinstance(versions, list) and versions:
        current = artifact.get("currentVersionId")
        if not current:
            content_versions = [v for v in versions if v.get("source") != "revision_requested"]
            current = (content_versions[-1] if content_versions else versions[-1]).get("versionId")
        return versions, current
    snap = _artifact_content_snapshot(artifact)
    initial = {
        "versionId": f"v_initial_{artifact.get('id')}",
        "versionNumber": 1,
        "createdAt": artifact.get("createdAt") or _utc_now(),
        "createdBy": artifact.get("createdBy") or DEFAULT_CREATED_BY,
        "source": "initial",
        "feedback": None,
        "parentVersionId": None,
        "restoredFromVersionId": None,
        **snap,
    }
    return [initial], initial["versionId"]


def _artifact_editable(artifact: dict[str, Any]) -> tuple[bool, str]:
    """Whether an artifact's text content can be inline-edited, with a reason."""
    if artifact.get("archivedAt"):
        return False, "archived artifacts cannot be edited"
    fmt = (artifact.get("format") or "").lower()
    kind = artifact.get("kind") or ""
    path = (artifact.get("path") or "").lower()
    inline = artifact.get("inlineContent")
    is_text = (
        fmt in {"markdown", "md", "text", "txt"}
        or kind == "research_start_draft"
        or path.endswith((".md", ".markdown", ".txt"))
        or (isinstance(inline, str) and inline)
    )
    if not is_text:
        return False, "only markdown/text artifacts can be edited inline"
    return True, ""


def _reset_review_state_after_new_version(artifact: dict[str, Any]) -> None:
    """A new content version (user_edit / restore / agent_regenerate) supersedes any
    pending revision: move out of ``rejected_or_needs_revision`` (and ``verified``)
    back to ``pending_review`` and clear the stale revision markers, so the UI no
    longer shows "needs revision" once a new version exists. (issue #302 review r2.)

    Only content-bearing version sources should call this — ``revision_requested``
    itself SETS the rejected state and must not clear it.
    """
    state = artifact.get("artifactState")
    if state in ("usable", "rejected_or_needs_revision"):
        artifact["artifactState"] = "needs_review"
    if state == "rejected_or_needs_revision":
        artifact["revisionRequestedAt"] = None
        artifact["revisionFeedback"] = None


def _apply_artifact_ownership_defaults(artifact: dict[str, Any], task: TaskDict | None = None) -> None:
    ownership_seed = task or artifact
    ownership = _default_ownership(ownership_seed)
    artifact.setdefault("ownerId", ownership["ownerId"])
    artifact.setdefault("workspaceId", ownership["workspaceId"])
    artifact.setdefault("createdBy", ownership["createdBy"])


def _apply_capability_call_ownership_defaults(call: dict[str, Any], task: TaskDict | None = None) -> None:
    ownership_seed = task or call
    ownership = _default_ownership(ownership_seed)
    call.setdefault("ownerId", ownership["ownerId"])
    call.setdefault("workspaceId", ownership["workspaceId"])
    call.setdefault("createdBy", ownership["createdBy"])

_REPLAY_RUNTIMES = {"replay", "replay_fallback"}
_DEMO_RUNTIMES = {"fixture", "demo"}
_NON_RESULT_RUNTIMES = {"openair_local_release_gate", "workbench_control"}
_LIVE_RUNTIMES = {
    "live",
    AGENT_RUNTIME_CLAUDE_CODE,
    "async_training",
    "openair_literature_live_adapter",
    "claude_code",
    "python_adapter",
    "system_guard",
    "specialist_agent_runtime_gateway",
    "deterministic",
    "external_adapter",
}
_REPLAY_ORIGINS = {"imported", "replay", "recorded", "recorded_run"}
_DEMO_ORIGINS = {"demo", "fixture"}
_CAPABILITY_CALL_METADATA_PROTECTED_KEYS = frozenset(
    {
        "id",
        "ownerId",
        "workspaceId",
        "createdBy",
        "kind",
        "name",
        "status",
        "requestedAt",
        "finishedAt",
        "runtime",
        "artifactRefs",
        "inputArtifactRefs",
        "outputArtifactRefs",
        "prevCallId",
        "inputCallRefs",
        "stageKey",
        "subStageInstanceId",
    }
)


def _run_mode_for_runtime(runtime: str | None) -> str:
    """Return a fail-closed execution mode for newly-created legacy calls.

    ``runtime`` names an executor/provider, not durable result provenance.  Only
    runtimes already declared by this repository are mapped to ``live``; a new
    or misspelled runtime remains ``unknown`` until the caller records explicit
    ``runMode`` / ``sourceMode`` facts.
    """
    if not runtime:
        return "unknown"
    if runtime in _REPLAY_RUNTIMES:
        return "replay"
    if runtime in _DEMO_RUNTIMES:
        return "fixture"
    if runtime in _NON_RESULT_RUNTIMES:
        return "unknown"
    return "live" if runtime in _LIVE_RUNTIMES else "unknown"


def _artifact_linked_call_ids(
    artifacts: list[dict[str, Any]],
    calls: list[dict[str, Any]],
) -> set[str]:
    """Return capability calls linked to a materialized artifact in either direction."""
    artifact_ids = {
        str(artifact_id)
        for artifact in artifacts
        if (artifact_id := artifact.get("id"))
    }
    linked = {
        str(call_id)
        for artifact in artifacts
        if (call_id := artifact.get("sourceCapabilityCallId"))
    }
    for call in calls:
        call_id = call.get("id")
        runtime = call.get("runtime")
        if call_id and runtime:
            out[str(call_id)] = str(runtime)
    return out


def _artifact_source_marker(artifact: dict[str, Any]) -> str | None:
    metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
    source_mode = artifact.get("sourceMode") or metadata.get("sourceMode")
    origin = str(artifact.get("origin") or "").strip().lower()
    if source_mode in {"replay", "recorded"} or origin in _REPLAY_ORIGINS:
        return "replay"
    if source_mode in {"demo", "fixture"} or origin in _DEMO_ORIGINS:
        return "demo"
    return None


def _call_source_mode(call: dict[str, Any]) -> str:
    explicit = call.get("sourceMode") or call.get("runMode")
    runtime_mode = _run_mode_for_runtime(call.get("runtime"))
    if explicit == "live":
        # A persisted label cannot promote an unknown/fixture runtime to live.
        return "live" if runtime_mode == "live" else runtime_mode
    if explicit in {"replay", "fixture", "demo", "unknown", "none"}:
        return str(explicit)
    return runtime_mode


def _has_positive_live_evidence(
    call: dict[str, Any],
    artifact: dict[str, Any],
    *,
    work_units_by_id: dict[str, dict[str, Any]],
) -> bool:
    """Require a completed live call and durable producer linkage.

    Legacy calls require a recognized live runtime plus a concrete run identity.
    Canonical v1.2 calls instead rely on the stricter persisted invariant: a
    succeeded CapabilityCall must link the exact Artifact and completed live
    WorkUnit that produced it. A persisted ``resultSource=live`` label is never
    accepted as evidence by itself.
    """
    call_id = str(call.get("id") or call.get("capabilityCallId") or "").strip()
    if not call_id or str(artifact.get("sourceCapabilityCallId") or "").strip() != call_id:
        return False
    if str(call.get("status") or "").lower() not in {"completed", "succeeded"}:
        return False
    runtime = call.get("runtime")
    if runtime is not None and _run_mode_for_runtime(str(runtime)) != "live":
        # Canonical linkage cannot promote a fixture or unknown runtime.
        return False

    artifact_id = str(artifact.get("id") or artifact.get("artifactId") or "").strip()
    output_refs = {str(value) for value in call.get("outputArtifactRefs") or call.get("artifactRefs") or []}
    work_unit_id = str(call.get("workUnitId") or artifact.get("workUnitId") or "").strip()
    work = work_units_by_id.get(work_unit_id)
    if call.get("capabilityCallId") is not None or work is not None:
        if not artifact_id or artifact_id not in output_refs or work is None:
            return False
        if str(artifact.get("workUnitId") or "").strip() != work_unit_id:
            return False
        return (
            str(work.get("sourceMode") or "").lower() == "live"
            and str(work.get("status") or "").lower() == "completed"
        )

    if _call_source_mode(call) != "live":
        return False
    if call.get("runtime") in _REPLAY_RUNTIMES | _DEMO_RUNTIMES | _NON_RESULT_RUNTIMES:
        return False
    execution_identity = artifact.get("sourceExecutionRunId") or call.get("runId")
    return isinstance(execution_identity, str) and bool(execution_identity.strip())


def _infer_task_result_source(task: TaskDict) -> str:
    """Derive provenance from positive durable evidence, never heuristics.

    Priority is explicit replay/demo product provenance, then artifact-linked
    completed live execution evidence. Drafts, manual artifacts, unknown
    runtimes and unlinked capability calls remain ``none``.
    """
    explicit = task.get("resultSource")
    artifacts = [row for row in (task.get("artifacts") or []) if isinstance(row, dict)]
    calls = [row for row in (task.get("capabilityCalls") or []) if isinstance(row, dict)]
    work_units = [row for row in (task.get("workUnits") or []) if isinstance(row, dict)]
    calls_by_id = {
        str(call.get("id") or call.get("capabilityCallId")): call
        for call in calls
        if str(call.get("id") or call.get("capabilityCallId") or "").strip()
    }
    work_units_by_id = {
        str(work.get("workUnitId")): work
        for work in work_units
        if str(work.get("workUnitId") or "").strip()
    }

    markers = {_artifact_source_marker(artifact) for artifact in artifacts}
    if "replay" in markers or explicit == "replay":
        return "replay"
    if "demo" in markers or explicit == "demo":
        return "demo"

    linked_calls: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for artifact in artifacts:
        call_id = str(artifact.get("sourceCapabilityCallId") or "").strip()
        call = calls_by_id.get(call_id)
        if call is not None:
            linked_calls.append((call, artifact))
    if any(_call_source_mode(call) == "replay" for call, _artifact in linked_calls):
        return "replay"
    if any(_call_source_mode(call) in {"fixture", "demo"} for call, _artifact in linked_calls):
        return "demo"
    if any(
        _has_positive_live_evidence(call, artifact, work_units_by_id=work_units_by_id)
        for call, artifact in linked_calls
    ):
        return "live"

    return "none"

def _normalize_task_result_source(task: TaskDict) -> None:
    task["resultSource"] = _infer_task_result_source(task)


def normalize_task_result_source(task: TaskDict) -> TaskDict:
    """Return a normalized copy for repository reads and public projections."""
    normalized = copy.deepcopy(task)
    _normalize_task_result_source(normalized)
    return normalized


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ordered_unique(values: list[str] | None) -> list[str]:
    return list(dict.fromkeys(values or []))


def _valid_stage_order(values: list[Any] | None) -> list[str]:
    ordered: list[str] = []
    for value in values or []:
        if isinstance(value, str) and value in STAGE_ORDER and value not in ordered:
            ordered.append(value)
    return ordered


def _workflow_stage_order(plan: dict[str, Any] | None) -> list[str]:
    if not isinstance(plan, dict):
        return []
    stages = plan.get("stages")
    if not isinstance(stages, list):
        return []
    return _valid_stage_order([
        stage.get("stageKey")
        for stage in stages
        if isinstance(stage, dict)
    ])


def _task_stage_order(task: TaskDict) -> list[str]:
    return (
        _workflow_stage_order(task.get("creationPlan"))
        or _valid_stage_order(task.get("stages"))
        or _valid_stage_order([
            detail.get("stageKey")
            for detail in task.get("stageDetails") or []
            if isinstance(detail, dict)
        ])
        or list(STAGE_ORDER)
    )


def _new_stage_detail(
    stage_key: str,
    status: str = "not_started",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    detail = {
        "stageKey": stage_key,
        "status": status,
        "summary": None,
        "risks": None,
        "nextSteps": None,
        "artifactRefs": [],
        "subStages": [],
    }
    for key in (
        "label",
        "entryCondition",
        "completionCondition",
        "lockedReason",
        "primaryAction",
        "canSkip",
        "canReview",
        "canLock",
    ):
        if metadata and metadata.get(key) is not None:
            detail[key] = metadata[key]
    return detail


def _status_rank(status: str | None) -> int:
    order = {
        "not_started": 0,
        "draft": 1,
        "awaiting_confirmation": 2,
        "running": 3,
        "needs_input": 4,
        "blocked_recoverable": 4,
        "insufficient_evidence": 4,
        "completed": 5,
        "failed": 6,
        "cancelled": 0,
    }
    return order.get(status or "not_started", 0)


def _mark_stage_completed(detail: dict[str, Any], now: str) -> None:
    detail["status"] = "completed"
    detail["finishedAt"] = detail.get("finishedAt") or now


def _reconcile_stage_status(detail: dict[str, Any], now: str) -> None:
    """根据 substage 状态回写 stage 状态（全部完成 → stage 完成）。"""
    substages = detail.get("subStages") or []
    if not substages:
        return
    statuses = [s.get("status") or "not_started" for s in substages]
    if any(s == "failed" for s in statuses):
        detail["status"] = "failed"
        return
    for blocked_status in ("needs_input", "blocked_recoverable", "insufficient_evidence"):
        if any(s == blocked_status for s in statuses):
            detail["status"] = blocked_status
            return
    if all(s == "completed" for s in statuses):
        _mark_stage_completed(detail, now)
        return
    if any(s == "running" for s in statuses):
        detail["status"] = "running"


def _complete_prior_stages(task: TaskDict, stage_key: str, now: str) -> None:
    """进入更后阶段时，将仍标记为 running 的前序 stage 置为 completed。"""
    stage_order = _task_stage_order(task)
    try:
        target_idx = stage_order.index(stage_key)
    except ValueError:
        return
    for detail in task.get("stageDetails") or []:
        sk = detail.get("stageKey")
        if sk not in stage_order:
            continue
        if stage_order.index(sk) < target_idx and detail.get("status") == "running":
            _mark_stage_completed(detail, now)


def _clear_stage_lock_guards(detail: dict[str, Any]) -> None:
    """A stage that is now running should no longer present future-stage lock copy."""
    if detail.get("lockedReason"):
        detail["lockedReason"] = None
        detail["risks"] = None
        detail["nextSteps"] = None


def _merge_stage_substages(detail: dict[str, Any]) -> None:
    merged: list[dict[str, Any]] = []
    seen: dict[tuple[str | None, str | None], dict[str, Any]] = {}

    for idx, sub in enumerate(detail.get("subStages") or []):
        sub.setdefault("artifactRefs", [])
        sub.setdefault("capabilityCallRefs", [])
        sub.setdefault("parentStageKey", detail.get("stageKey"))
        sub.setdefault("label", sub.get("subStage"))
        sub.setdefault("summary", None)
        sub.setdefault("sequence", idx)
        sub.setdefault("l3Plan", None)
        if not sub.get("subStageInstanceId"):
            sub["subStageInstanceId"] = f"sub_{uuid4().hex[:12]}"

        key = (sub.get("parentStageKey") or detail.get("stageKey"), sub.get("subStage"))
        existing = seen.get(key)
        if not existing:
            seen[key] = sub
            merged.append(sub)
            continue

        if not existing.get("subStageInstanceId") and sub.get("subStageInstanceId"):
            existing["subStageInstanceId"] = sub["subStageInstanceId"]
        existing["artifactRefs"] = _ordered_unique(
            [*existing.get("artifactRefs", []), *sub.get("artifactRefs", [])]
        )
        existing["capabilityCallRefs"] = _ordered_unique(
            [*existing.get("capabilityCallRefs", []), *sub.get("capabilityCallRefs", [])]
        )
        if not existing.get("summary") and sub.get("summary"):
            existing["summary"] = sub["summary"]
        if not existing.get("label") and sub.get("label"):
            existing["label"] = sub["label"]
        if not existing.get("startedAt") or (
            sub.get("startedAt") and sub["startedAt"] < existing["startedAt"]
        ):
            existing["startedAt"] = sub.get("startedAt")
        if sub.get("finishedAt") and (
            not existing.get("finishedAt") or sub["finishedAt"] > existing["finishedAt"]
        ):
            existing["finishedAt"] = sub["finishedAt"]
        if _status_rank(sub.get("status")) > _status_rank(existing.get("status")):
            existing["status"] = sub.get("status")

    for idx, sub in enumerate(merged):
        sub["sequence"] = idx

    detail["subStages"] = merged


def _default_stage_catalog(pack: str = "sdr") -> list[dict[str, Any]]:
    from app.modules.pack_config import load_stage_substage_map
    catalog = load_stage_substage_map(pack=pack)
    # Drop disabled substages (placeholders with no L3 skill yet). They must
    # not appear in any runnable catalog, otherwise the executor advances
    # into them. See task_store.initialize_from_workflow for the same guard.
    return [
        {
            "stageKey": stage_key,
            "allowedSubStages": [
                copy.deepcopy(entry)
                for entry in catalog.get(stage_key, [])
                if entry.get("enabled", True)
            ],
        }
        for stage_key in STAGE_ORDER
    ]


def _workflow_stage_entries(plan: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(plan, dict):
        return {}
    entries: dict[str, dict[str, Any]] = {}
    for stage in plan.get("stages") or []:
        if isinstance(stage, dict) and isinstance(stage.get("stageKey"), str):
            entries[stage["stageKey"]] = stage
    return entries


def _workflow_has_substages(plan: dict[str, Any] | None) -> bool:
    return any(
        isinstance(stage.get("substages"), list) and len(stage["substages"]) > 0
        for stage in _workflow_stage_entries(plan).values()
    )


def _workflow_stage_catalog(plan: dict[str, Any] | None, *, pack: str) -> list[dict[str, Any]]:
    """Build stageCatalog from a confirmed workflow plan.

    SDR research workflows can keep the full four-stage structure with
    workflow-defined substages. Agent-style workflows may intentionally choose
    a subset of the same stage key space; in that case, catalog rows must come
    from the workflow itself instead of the pack's full default catalog,
    otherwise advancing uses the wrong substages.
    """
    from app.modules import pack_config
    from app.modules.pack_config import load_stage_substage_map

    stage_entries = _workflow_stage_entries(plan)
    disabled_pack_keys: set[str] = set()
    for entries in load_stage_substage_map(pack=pack).values():
        for e in entries:
            if e.get("enabled") is False:
                disabled_pack_keys.add(e["key"])
    catalog: list[dict[str, Any]] = []
    for stage_key in _workflow_stage_order(plan):
        allowed: list[dict[str, Any]] = []
        for ss in stage_entries.get(stage_key, {}).get("substages") or []:
            if not isinstance(ss, dict):
                continue
            ss_key = ss.get("substage") or ss.get("key")
            if not isinstance(ss_key, str) or not ss_key.strip():
                continue
            ss_key = ss_key.strip()
            if ss_key in disabled_pack_keys:
                continue  # placeholder substage with no L3 skill
            allowed.append({
                "key": ss_key,
                "stageKey": stage_key,
                "label": ss.get("label") or pack_config.get_substage_label(ss_key, pack=pack),
                "description": ss.get("description", ""),
                "enabled": ss.get("enabled", True),
            })
        catalog.append({"stageKey": stage_key, "allowedSubStages": allowed})
    return catalog


def _workflow_substage_instances(
    stage_entry: dict[str, Any] | None,
    *,
    stage_key: str,
    pack: str,
    now: str,
    activate_first: bool = False,
) -> list[dict[str, Any]]:
    from app.modules import pack_config
    from app.modules.pack_config import load_stage_substage_map

    # Mirror the disabled-substage filter used by _workflow_stage_catalog —
    # placeholder substages (enabled:false in the pack's stage-substage-map)
    # must NOT leak into stageDetails even if a draft plan still lists them.
    # Otherwise the detail view renders substages that the catalog filters out,
    # making the task look runnable in places with zero real L3 capability.
    disabled_pack_keys: set[str] = set()
    for entries in load_stage_substage_map(pack=pack).values():
        for e in entries:
            if e.get("enabled") is False:
                disabled_pack_keys.add(e["key"])

    substages: list[dict[str, Any]] = []
    for idx, ss in enumerate((stage_entry or {}).get("substages") or []):
        if not isinstance(ss, dict):
            continue
        ss_key = ss.get("substage") or ss.get("key")
        if not isinstance(ss_key, str) or not ss_key.strip():
            continue
        ss_key = ss_key.strip()
        if ss_key in disabled_pack_keys:
            continue  # placeholder substage with no L3 skill
        is_active = activate_first and len(substages) == 0
        substages.append({
            "subStage": ss_key,
            "status": "running" if is_active else "not_started",
            "artifactRefs": [],
            "subStageInstanceId": f"sub_{uuid4().hex[:12]}",
            "parentStageKey": stage_key,
            "label": ss.get("label") or pack_config.get_substage_label(ss_key, pack=pack),
            "capabilityCallRefs": [],
            "summary": None,
            "sequence": idx,
            "startedAt": now if is_active else None,
            "finishedAt": None,
            "l3Plan": None,
        })
    return substages


def build_stage_catalog(pack: str = "sdr") -> list[dict[str, Any]]:
    """Public alias for _default_stage_catalog; used when callers need to rebuild
    the stageCatalog after a pack switch (e.g., new-task-planning picks a new pack)."""
    return _default_stage_catalog(pack=pack)


class TaskStore:
    _POSTGRES_FORBIDDEN_MUTATIONS = frozenset({
        "import_persisted_task", "seed_tasks", "rebind_legacy_fixture_ownership", "create_task",
        "add_material_record", "add_memory_record", "update_artifact_material_binding", "reassign_artifact_task",
        "set_profile_draft", "confirm_profile", "rename_task", "patch_status", "patch_task", "update_task_plan", "delete_task",
        "save_stage_result", "get_or_create_substage_instance", "initialize_from_workflow", "advance_substage",
        "patch_substage_instance", "patch_substage_l3_plan", "add_artifact", "remove_artifact",
        "update_artifact_review_state", "edit_artifact_content", "request_artifact_revision", "regenerate_artifact",
        "restore_artifact_version", "adopt_artifact_as_version", "patch_artifact_storage",
        "consume_pending_regenerate", "confirm_artifact_scope_expansion", "append_agent_message",
        "resolve_confirmation_request", "set_agent_session_id", "add_capability_call", "patch_capability_call",
        "record_artifact_relation",
    })

    def __getattribute__(self, name: str):
        value = super().__getattribute__(name)
        if name not in TaskStore._POSTGRES_FORBIDDEN_MUTATIONS or not callable(value):
            return value

        def guarded(*args: Any, **kwargs: Any):
            from app.config import settings

            # API task creation calls this only as a pure payload builder;
            # persist=False guarantees no TaskStore state is changed.
            if name == "create_task" and kwargs.get("persist") is False:
                return value(*args, **kwargs)
            if settings.persistence_backend == "postgres":
                raise TaskStorePostgresMutationForbidden(f"{name}: {TaskStorePostgresMutationForbidden.detail}")
            return value(*args, **kwargs)

        return guarded

    def __init__(self) -> None:
        self._tasks: dict[str, TaskDict] = {}
        self._lock = RLock()
        self._persist_dir: Path | None = None
        self._repository: TaskRepository | None = None
        self._artifact_repository: Any = None  # ArtifactRepository | None（catalog 读源）
        self._relation_repository: Any = None  # ArtifactRelationRepository | None
        self._fixture_seed_task_ids: set[str] = set()

    # ── persistence helpers ──────────────────────────────────────────

    def enable_persistence(self, persist_dir: Path) -> None:
        persist_dir.mkdir(parents=True, exist_ok=True)
        self._persist_dir = persist_dir

    def enable_repository(self, repository: TaskRepository) -> None:
        """Reject PostgreSQL wiring into the JSON compatibility store.

        PostgreSQL writes are API-repository/service commands.  Keeping this
        guard makes a future startup wiring mistake fail closed instead of
        restoring a full-payload mirror write.
        """
        raise RuntimeError("TaskStore cannot be a PostgreSQL writer")

    def enable_artifact_repository(self, repository: Any) -> None:
        """Wire the openair_artifacts repository; subsequent add/remove mirror to it."""
        self._artifact_repository = repository

    def _mirror_artifact(self, task_id: str, artifact: TaskDict, task_title: str | None) -> None:
        if not self._artifact_repository:
            return
        try:
            self._artifact_repository.upsert_from_dict(task_id, artifact, task_title)
        except Exception:
            _log.warning("mirror artifact %s to repository failed", artifact.get("id"), exc_info=True)

    def _mirror_artifact_delete(self, artifact_id: str) -> None:
        if not self._artifact_repository:
            return
        try:
            self._artifact_repository.remove(artifact_id)
        except Exception:
            _log.warning("delete artifact %s from repository failed", artifact_id, exc_info=True)

    def _migrate_artifacts_to_repo(self) -> int:
        """Lazy-migrate all embedded artifacts into openair_artifacts (idempotent upsert)."""
        if not self._artifact_repository:
            return 0
        n = 0
        for tid, task in self._tasks.items():
            title = task.get("title")
            for artifact in task.get("artifacts") or []:
                try:
                    self._artifact_repository.upsert_from_dict(tid, artifact, title)
                    n += 1
                except Exception:
                    _log.warning("migrate artifact %s failed", artifact.get("id"), exc_info=True)
        return n

    def artifact_repository_enabled(self) -> bool:
        return self._artifact_repository is not None

    def enable_relation_repository(self, repository: Any) -> None:
        self._relation_repository = repository

    def record_artifact_relation(
        self,
        *,
        source_artifact_id: str,
        target_artifact_id: str,
        relation_type: str,
        owner_id: str,
        workspace_id: str,
        capability_call_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> None:
        """Record a provenance relation when the configured backend supports it."""
        if not self._relation_repository:
            return
        try:
            self._relation_repository.upsert_relation(
                source_artifact_id=source_artifact_id,
                target_artifact_id=target_artifact_id,
                relation_type=relation_type,
                owner_id=owner_id,
                workspace_id=workspace_id,
                capability_call_id=capability_call_id,
                metadata=metadata,
                created_at=created_at,
            )
        except Exception:
            _log.warning("record relation %s→%s (%s) failed", source_artifact_id, target_artifact_id, relation_type, exc_info=True)

    def list_artifact_relations(
        self,
        artifact_id: str,
        *,
        owner_id: str,
        workspace_id: str,
        direction: str = "both",
        relation_type: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self._relation_repository:
            return self._derived_json_artifact_relations(
                artifact_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
                direction=direction,
                relation_type=relation_type,
            )
        try:
            return self._relation_repository.list_for(
                artifact_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
                direction=direction,
                relation_type=relation_type,
            )
        except Exception:
            _log.warning("list relations for %s failed", artifact_id, exc_info=True)
            return []

    def _derived_json_artifact_relations(
        self,
        artifact_id: str,
        *,
        owner_id: str,
        workspace_id: str,
        direction: str,
        relation_type: str | None,
    ) -> list[dict[str, Any]]:
        """Derive durable reuse edges from JSON-backed imported artifacts.

        JSON persistence stores an imported artifact's ``reusedFrom`` provenance
        with the artifact itself. There is no separate relation repository in
        that mode, so rebuilding this relation keeps the catalog traceable
        across process restarts without pretending that arbitrary call edges
        were persisted.
        """
        if relation_type and relation_type != "reuses":
            return []
        rows: list[dict[str, Any]] = []
        for task in self._tasks.values():
            if task.get("ownerId") != owner_id or task.get("workspaceId") != workspace_id:
                continue
            for artifact in task.get("artifacts") or []:
                reused_from = artifact.get("reusedFrom")
                if not isinstance(reused_from, dict):
                    metadata = artifact.get("metadata")
                    reused_from = metadata.get("reusedFrom") if isinstance(metadata, dict) else None
                source_id = reused_from.get("artifactId") if isinstance(reused_from, dict) else None
                if not isinstance(source_id, str) or not source_id:
                    continue
                imported_id = artifact.get("id")
                if not isinstance(imported_id, str) or not imported_id:
                    continue
                is_outgoing = imported_id == artifact_id
                is_incoming = source_id == artifact_id
                if not is_outgoing and not is_incoming:
                    continue
                if direction == "outgoing" and not is_outgoing:
                    continue
                if direction == "incoming" and not is_incoming:
                    continue
                rows.append(
                    {
                        "sourceArtifactId": imported_id,
                        "targetArtifactId": source_id,
                        "relationType": "reuses",
                        "capabilityCallId": None,
                        "metadata": {"reusedFrom": reused_from},
                        "createdAt": artifact.get("createdAt"),
                    }
                )
        return rows

    def register_fixture_seed_task_ids(self, task_ids: list[str] | set[str]) -> None:
        """Record fixture-origin task IDs for later ownership rebind.

        Some dev environments load persisted tasks after fixture seed. When a
        persisted row overwrites the seeded fixture task with non-legacy
        ownership, ownership-default matching alone cannot identify it as a
        fixture anymore. Keeping fixture IDs allows deterministic rebind.
        """
        self._fixture_seed_task_ids.update(str(tid) for tid in task_ids if str(tid).strip())

    def _persist_task(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if not task:
            return
        if self._repository:
            raise RuntimeError("TaskStore cannot persist PostgreSQL task projections")
        if not self._persist_dir:
            return
        try:
            dest = self._persist_dir / f"{task_id}.json"
            tmp = dest.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(dest)
        except Exception:
            _log.warning("persist task %s failed", task_id, exc_info=True)

    def _persist_task_projection(self, task_id: str, task: TaskDict) -> None:
        """Durably replace one JSON projection; errors intentionally propagate."""
        if self._repository:
            raise RuntimeError("TaskStore cannot persist PostgreSQL task projections")
        if not self._persist_dir:
            return
        dest = self._persist_dir / f"{task_id}.json"
        tmp = dest.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(dest)

    def _persist_delete(self, task_id: str) -> None:
        if self._repository:
            raise RuntimeError("TaskStore cannot delete PostgreSQL task projections")
        if not self._persist_dir:
            return
        try:
            (self._persist_dir / f"{task_id}.json").unlink(missing_ok=True)
        except Exception:
            _log.warning("delete persisted task %s failed", task_id, exc_info=True)

    def _emit_task_event(self, task_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
        task = self._tasks.get(task_id)
        publish_task_event(
            task_id,
            event_type,
            updated_at=task.get("updatedAt") if task else _utc_now(),
            payload=payload,
        )

    def _normalize_persisted_task(self, raw: TaskDict) -> TaskDict:
        """Normalize a persisted task dict without writing to memory or disk."""
        task = copy.deepcopy(raw)
        _apply_task_ownership_defaults(task)
        _normalize_task_execution_mode(task)
        task.setdefault("stageCatalog", _default_stage_catalog(pack=task.get("skillPack", "sdr")))
        for detail in task.get("stageDetails") or []:
            self._normalize_stage_detail(detail)
        for call in task.get("capabilityCalls") or []:
            self._normalize_capability_call(call, task)
        for artifact in task.get("artifacts") or []:
            self._normalize_artifact(artifact, task)
        for message in task.get("agentMessages") or []:
            self._normalize_agent_message(message)
        _normalize_task_result_source(task)
        task.pop("agentSessionId", None)
        calls = task.get("capabilityCalls") or []
        if calls and "lastCallId" not in task:
            task["lastCallId"] = calls[-1].get("id")
        return task

    def _ingest_persisted_task(self, raw: TaskDict, *, overwrite: bool = False) -> str | None:
        """Normalize and store a task dict loaded from disk."""
        tid = raw.get("taskId")
        if not isinstance(tid, str) or not tid.strip():
            return None
        tid = tid.strip()
        if tid in self._tasks and not overwrite:
            return None

        task = self._normalize_persisted_task(raw)
        task["taskId"] = tid
        self._tasks[tid] = task
        self._persist_task(tid)
        return tid

    def import_persisted_task(self, raw: TaskDict, *, overwrite: bool = False) -> TaskDict:
        tid = raw.get("taskId")
        if not isinstance(tid, str) or not tid.strip():
            raise ValueError("missing or empty taskId")
        tid = tid.strip()
        if tid in self._tasks and not overwrite:
            raise ValueError(f"task already exists: {tid}")

        prepared = self._normalize_persisted_task(raw)
        prepared["taskId"] = tid
        try:
            TaskOut.model_validate(prepared)
        except ValidationError as exc:
            raise ValueError(f"task payload failed TaskOut validation: {exc.errors()[0]['msg']}") from exc

        self._tasks[tid] = prepared
        self._persist_task(tid)
        task = self.get_task(tid)
        assert task is not None
        return task

    def load_persisted_tasks(self) -> int:
        if self._repository:
            loaded = 0
            try:
                for task in self._repository.load_all_tasks():
                    _apply_task_ownership_defaults(task)
                    _normalize_task_execution_mode(task)
                    tid = task.get("taskId")
                    if not tid:
                        continue
                    task.setdefault("stageCatalog", _default_stage_catalog(pack=task.get("skillPack", "sdr")))
                    for detail in task.get("stageDetails") or []:
                        self._normalize_stage_detail(detail)
                    for call in task.get("capabilityCalls") or []:
                        self._normalize_capability_call(call, task)
                    for artifact in task.get("artifacts") or []:
                        self._normalize_artifact(artifact, task)
                    _normalize_task_result_source(task)
                    task.pop("agentSessionId", None)
                    calls = task.get("capabilityCalls") or []
                    if calls and "lastCallId" not in task:
                        task["lastCallId"] = calls[-1].get("id")
                    self._tasks[tid] = task
                    loaded += 1
            except Exception:
                _log.warning("load persisted tasks from repository failed", exc_info=True)
                raise
            try:
                migrated = self._migrate_artifacts_to_repo()
                if migrated:
                    _log.info("artifact lazy-migration: %d artifact(s) upserted", migrated)
            except Exception:
                _log.warning("artifact lazy-migration failed", exc_info=True)
            return loaded
        if not self._persist_dir or not self._persist_dir.exists():
            return 0
        loaded = 0
        for fp in self._persist_dir.glob("*.json"):
            if fp.name.endswith(".tmp"):
                continue
            try:
                task = json.loads(fp.read_text(encoding="utf-8"))
                if task.get("taskId") is None:
                    task["taskId"] = fp.stem
                if self._ingest_persisted_task(task, overwrite=False):
                    loaded += 1
            except Exception:
                _log.warning("load persisted task %s failed", fp.name, exc_info=True)
        return loaded

    def seed_tasks(self, tasks: dict[str, TaskDict]) -> int:
        """Insert tasks if id not present (dev bootstrap only)."""
        added = 0
        for tid, task in tasks.items():
            if tid in self._tasks:
                continue
            seeded = copy.deepcopy(task)
            _apply_task_ownership_defaults(seeded)
            _normalize_task_execution_mode(seeded)
            seeded.setdefault("stageCatalog", _default_stage_catalog(pack=seeded.get("skillPack", "sdr")))
            for detail in seeded.get("stageDetails") or []:
                self._normalize_stage_detail(detail)
            for call in seeded.get("capabilityCalls") or []:
                self._normalize_capability_call(call, seeded)
            for artifact in seeded.get("artifacts") or []:
                self._normalize_artifact(artifact, seeded)
            _normalize_task_result_source(seeded)
            for message in seeded.get("agentMessages") or []:
                self._normalize_agent_message(message)
            calls = seeded.get("capabilityCalls") or []
            if calls and "lastCallId" not in seeded:
                seeded["lastCallId"] = calls[-1].get("id")
            self._tasks[tid] = seeded
            added += 1
        return added

    def rebind_legacy_fixture_ownership(
        self, owner_id: str, workspace_id: str
    ) -> int:
        """Rebind dev fixture tasks to the dev user's ownership.

        Targets either:
        1) legacy-default ownership tasks (owner/workspace are demo defaults), or
        2) task IDs explicitly marked as fixture-origin during seed.

        For matched tasks, only ownership identifiers are rewritten on the task
        itself and on its ``artifacts`` / ``capabilityCalls`` children. For
        legacy-default detection we keep the previous conservative child rewrite
        behavior; for fixture-ID detection we force-rebind child ownership so
        persisted overwrite cannot leave mixed ownership under JWT AuthGuard.
        ``createdBy`` and business content are left untouched.

        Returns the number of tasks rebinded.
        """
        if not owner_id or not workspace_id:
            return 0
        rebinded = 0
        for task_id, task in self._tasks.items():
            is_fixture_seeded = task_id in self._fixture_seed_task_ids
            is_legacy_default = (
                task.get("ownerId") == DEFAULT_OWNER_ID
                and task.get("workspaceId") == DEFAULT_WORKSPACE_ID
            )
            if not is_fixture_seeded and not is_legacy_default:
                continue
            updated = copy.deepcopy(task)
            updated["ownerId"] = owner_id
            updated["workspaceId"] = workspace_id
            for artifact in updated.get("artifacts") or []:
                if is_fixture_seeded or artifact.get("ownerId") == DEFAULT_OWNER_ID:
                    artifact["ownerId"] = owner_id
                if is_fixture_seeded or artifact.get("workspaceId") == DEFAULT_WORKSPACE_ID:
                    artifact["workspaceId"] = workspace_id
            for call in updated.get("capabilityCalls") or []:
                if is_fixture_seeded or call.get("ownerId") == DEFAULT_OWNER_ID:
                    call["ownerId"] = owner_id
                if is_fixture_seeded or call.get("workspaceId") == DEFAULT_WORKSPACE_ID:
                    call["workspaceId"] = workspace_id
            self._tasks[task_id] = updated
            self._persist_task(task_id)
            rebinded += 1
        return rebinded

    def list_tasks(self) -> list[TaskDict]:
        from app.config import settings

        if settings.persistence_backend == "postgres":
            # Reads remain API/repository-backed in PostgreSQL mode.  Normalize
            # the returned projection so persisted replay/demo facts cannot be
            # reclassified by legacy runtime strings.
            return [
                normalize_task_result_source(task)
                for task in TaskRepository().load_all_tasks()
            ]
        return [normalize_task_result_source(task) for task in self._tasks.values()]

    def get_task(self, task_id: str) -> TaskDict | None:
        from app.config import settings

        if settings.persistence_backend == "postgres":
            # This is read-through only; every mutation below is fail-closed
            # by _POSTGRES_FORBIDDEN_MUTATIONS.
            task = TaskRepository().get_task(task_id)
            return normalize_task_result_source(task) if task else None
        task = self._tasks.get(task_id)
        return normalize_task_result_source(task) if task else None

    def build_task_payload(self, body: dict[str, Any]) -> TaskDict:
        task_id = f"task_{uuid4().hex[:12]}"
        now = _utc_now()
        creation_plan = body.get("creationPlan")
        workflow_stage_order = _workflow_stage_order(creation_plan)
        stage_order = workflow_stage_order or _valid_stage_order(body.get("stages")) or list(STAGE_ORDER)
        pack = body.get("skillPack") or (
            creation_plan.get("skillPack") if isinstance(creation_plan, dict) else None
        ) or "sdr"
        workflow_stage_entries = _workflow_stage_entries(creation_plan)
        uses_workflow_substages = _workflow_has_substages(creation_plan)
        cur = body.get("currentStage") or (stage_order[0] if stage_order else "problem_modeling")
        if cur not in stage_order:
            cur = stage_order[0] if stage_order else "problem_modeling"
        task_status = body.get("status", "running")
        stage_details = [
            _new_stage_detail(
                s,
                metadata=workflow_stage_entries.get(s),
            )
            for s in stage_order
        ]
        for sd in stage_details:
            if sd["stageKey"] == cur and task_status == "running":
                sd["status"] = "running"
            if uses_workflow_substages:
                sd["subStages"] = _workflow_substage_instances(
                    workflow_stage_entries.get(sd["stageKey"]),
                    stage_key=sd["stageKey"],
                    pack=pack,
                    now=now,
                    activate_first=sd["stageKey"] == cur and task_status == "running",
                )

        ownership = _default_ownership(body)
        task: TaskDict = {
            "taskId": task_id,
            **ownership,
            "title": (body.get("title") or "待补充任务标题").strip() or "待补充任务标题",
            "goalSummary": body.get("goalSummary"),
            "domain": body.get("domain", "research_task"),
            "skillPack": pack,
            "currentStage": cur,
            "status": task_status,
            "resultSource": body.get("resultSource", "none"),
            "executionMode": body.get("executionMode")
            if body.get("executionMode") in _TASK_EXECUTION_MODES
            else DEFAULT_TASK_EXECUTION_MODE,
            "creationStatus": body.get("creationStatus"),
            "creationPlan": copy.deepcopy(creation_plan) if creation_plan else None,
            "createdAt": now,
            "updatedAt": now,
            "stages": list(stage_order),
            "stageCatalog": _workflow_stage_catalog(creation_plan, pack=pack)
            if uses_workflow_substages
            else [
                entry
                for entry in _default_stage_catalog(pack=pack)
                if entry.get("stageKey") in stage_order
            ],
            "stageDetails": stage_details,
            "artifacts": [],
            "materials": [],
            "memoryRecords": [],
            "confirmations": [],
            "capabilityCalls": [],
            "profileDraft": None,
            "profileNeedsConfirmation": False,
            "profileConfirmedAt": None,
        }
        return task

    def create_task(self, body: dict[str, Any], *, persist: bool = True) -> TaskDict:
        task = self.build_task_payload(body)
        if persist:
            self._tasks[task["taskId"]] = task
            self._persist_task(task["taskId"])
            self._emit_task_event(task["taskId"], "task_created", {"reason": "task_created"})
        return copy.deepcopy(task)

    def _normalize_stage_detail(self, detail: dict[str, Any]) -> None:
        detail.setdefault("artifactRefs", [])
        detail.setdefault("subStages", [])
        detail.setdefault("userFacingNarrative", None)
        _merge_stage_substages(detail)

    def _normalize_capability_call(self, call: dict[str, Any], task: TaskDict | None = None) -> None:
        _apply_capability_call_ownership_defaults(call, task)
        call.setdefault("artifactRefs", [])
        call.setdefault("inputArtifactRefs", [])
        call.setdefault("outputArtifactRefs", [])
        call.setdefault("inputCallRefs", [])
        call.setdefault("runId", None)
        call.setdefault("runMode", None)
        call.setdefault("sourceMode", None)
        call.setdefault("provider", None)
        call.setdefault("adapter", None)
        call.setdefault("model", None)
        call.setdefault("sourceAccess", None)
        call.setdefault("sourceMetadata", {})
        call.setdefault("partialFailureImpact", None)
        call.setdefault("artifactValidatorResults", [])
        call.setdefault("userFacingNarrative", None)

    def _normalize_artifact(self, artifact: dict[str, Any], task: TaskDict | None = None) -> None:
        _apply_artifact_ownership_defaults(artifact, task)
        metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
        reused_from = metadata.get("reusedFrom")
        if isinstance(reused_from, dict):
            artifact_id = reused_from.get("artifactId")
            task_id = reused_from.get("taskId")
            if isinstance(artifact_id, str) and artifact_id and isinstance(task_id, str) and task_id:
                artifact["reusedFrom"] = {
                    "artifactId": artifact_id,
                    "taskId": task_id,
                    **({"artifactTitle": reused_from["artifactTitle"]} if isinstance(reused_from.get("artifactTitle"), str) else {}),
                    **({"taskTitle": reused_from["taskTitle"]} if isinstance(reused_from.get("taskTitle"), str) else {}),
                }
        task_id = (task or {}).get("taskId")
        if task_id:
            artifact.setdefault("taskId", task_id)
            artifact.setdefault("sourceTaskId", task_id)
        if task and artifact.get("subStageInstanceId") and not artifact.get("substage"):
            for detail in task.get("stageDetails") or []:
                for sub in detail.get("subStages") or []:
                    if sub.get("subStageInstanceId") == artifact.get("subStageInstanceId"):
                        artifact["substage"] = sub.get("subStage")
                        artifact.setdefault("stage", detail.get("stageKey"))
                        break
                if artifact.get("substage"):
                    break
        artifact.setdefault("summary", None)
        artifact.setdefault("stage", artifact.get("relatedStage"))
        artifact.setdefault("substage", None)
        artifact.setdefault("artifactType", artifact.get("kind"))
        _normalize_artifact_state(artifact)
        artifact.setdefault("subStageInstanceId", None)
        artifact.setdefault("sourceCapabilityCallId", None)
        artifact.setdefault("inlineContent", None)
        artifact.setdefault("evidenceRefs", [])
        _normalize_artifact_evidence_refs(artifact)
        artifact.setdefault("linkedMaterialIds", [])
        artifact.setdefault("materialManifest", [])
        artifact.setdefault("memoryRefs", [])
        source_call = None
        if task and artifact.get("sourceCapabilityCallId"):
            source_call = next(
                (
                    call
                    for call in task.get("capabilityCalls") or []
                    if call.get("id") == artifact.get("sourceCapabilityCallId")
                ),
                None,
            )
        apply_artifact_gate(artifact, source_call=source_call)
        artifact.setdefault("userFacingNarrative", None)
        self._ensure_artifact_versioned(artifact)

    def _ensure_artifact_versioned(self, artifact: dict[str, Any]) -> None:
        """Materialize ``versions`` / ``currentVersionId`` on the artifact in place.

        Uses :func:`_versioned_view` so legacy artifacts are backfilled to a single
        v1 ``initial`` version. Idempotent.
        """
        versions, current = _versioned_view(artifact)
        artifact["versions"] = versions
        artifact["currentVersionId"] = current

    def _materialize_version_content(
        self,
        task_id: str,
        artifact_id: str,
        version: dict[str, Any],
    ) -> None:
        """Freeze a local-stored version's content into an immutable per-version
        file so a later overwrite of the shared workspace output (e.g. a regenerate
        re-run writing the same ``output_file``) cannot corrupt this historical
        version's bytes (issue #302 review r4).

        Inline versions already carry their content in the snapshot; external
        versions use unique per-upload object keys — neither needs materializing.
        Idempotent (skips paths already under ``_versions/``) and best-effort
        (skips silently when the source file is not on disk yet, e.g. the
        content-less shell before the storage patch).
        """
        if version.get("storageType") != "local":
            return
        rel = version.get("path")
        if not isinstance(rel, str) or not rel or "_versions/" in rel:
            return
        try:
            import shutil

            from app.execution.execution_context import get_task_workspace_dir

            ws_dir = get_task_workspace_dir(task_id)
            if rel.startswith("workspace/"):
                parts = rel.split("/", 2)
                src = ws_dir / parts[2] if len(parts) >= 3 else ws_dir / rel
            else:
                src = ws_dir / rel
            if not src.is_file():
                return
            ext = src.suffix or ".bin"
            version_id = version.get("versionId") or f"v_{uuid4().hex[:12]}"
            dest_dir = ws_dir / "_versions" / artifact_id
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{version_id}{ext}"
            if not dest.exists():
                shutil.copyfile(src, dest)
            version["path"] = f"workspace/{task_id}/_versions/{artifact_id}/{version_id}{ext}"
        except Exception:
            _log.warning(
                "materialize version content failed: task=%s artifact=%s",
                task_id, artifact_id, exc_info=True,
            )

    def _append_artifact_version(
        self,
        artifact: dict[str, Any],
        *,
        source: str,
        content: dict[str, Any] | None,
        feedback: str | None,
        parent_version_id: str | None,
        restored_from_version_id: str | None,
        created_by: str,
        created_at: str,
    ) -> dict[str, Any]:
        """Append a version record and (for content versions) advance current.

        ``content=None`` denotes a metadata-only event (``revision_requested``):
        it records feedback on the timeline but does not move ``currentVersionId``
        or mirror content. Content versions unconditionally mirror their snapshot
        onto the artifact's top-level content fields so all existing readers see
        the now-current version.
        """
        self._ensure_artifact_versioned(artifact)
        versions = artifact["versions"]
        if parent_version_id is None:
            parent_version_id = artifact.get("currentVersionId")
        next_number = max((v.get("versionNumber", 0) for v in versions), default=0) + 1
        version: dict[str, Any] = {
            "versionId": f"v_{uuid4().hex[:12]}",
            "versionNumber": next_number,
            "createdAt": created_at,
            "createdBy": created_by,
            "source": source,
            "feedback": feedback,
            "parentVersionId": parent_version_id,
            "restoredFromVersionId": restored_from_version_id,
            "storageType": None,
            "inlineContent": None,
            "path": None,
            "externalKey": None,
            "contentSha256": None,
            "contentSize": None,
        }
        if content is not None:
            version["storageType"] = content.get("storageType")
            version["inlineContent"] = content.get("inlineContent")
            version["path"] = content.get("path")
            version["externalKey"] = content.get("externalKey")
            version["contentSha256"] = content.get("contentSha256")
            version["contentSize"] = content.get("contentSize")
        versions.append(version)
        artifact["versions"] = versions
        if content is not None:
            artifact["currentVersionId"] = version["versionId"]
            artifact["storageType"] = content.get("storageType") or "inline"
            artifact["inlineContent"] = content.get("inlineContent")
            artifact["path"] = content.get("path")
            artifact["externalKey"] = content.get("externalKey")
            artifact["contentSha256"] = content.get("contentSha256")
            artifact["contentSize"] = content.get("contentSize")
            # issue #302 r4: freeze local content into an immutable per-version
            # file so a later regenerate re-run (same output_file) can't overwrite
            # this historical version's bytes. Top-level mirrors the frozen path.
            self._materialize_version_content(
                artifact.get("taskId") or "", artifact.get("id") or "", version
            )
            artifact["path"] = version.get("path")
        return version

    def _sync_current_version_content(self, artifact: dict[str, Any]) -> None:
        """Re-sync the current version's content snapshot to the artifact's top-level
        content fields.

        This restores the issue-#302 invariant (``currentVersionId``'s content ≡
        top-level content) after a low-level storage patch. The registration flow
        creates an artifact shell via :meth:`add_artifact` (which freezes a v1 from
        the then-current — often content-less — fields) and only later patches the
        real storage/inline content; without this sync the historical v1 would keep
        the stale pre-patch snapshot while the top-level shows the real content.
        """
        self._ensure_artifact_versioned(artifact)
        current_id = artifact.get("currentVersionId")
        if not current_id:
            return
        snap = _artifact_content_snapshot(artifact)
        for v in artifact.get("versions") or []:
            if v.get("versionId") == current_id:
                v["storageType"] = snap["storageType"]
                v["inlineContent"] = snap["inlineContent"]
                v["path"] = snap["path"]
                v["externalKey"] = snap["externalKey"]
                v["contentSha256"] = snap["contentSha256"]
                v["contentSize"] = snap["contentSize"]
                # issue #302 r4: the registration patch is when the real (first-
                # run) bytes land on disk — freeze them into an immutable
                # per-version file before a later regenerate can overwrite it.
                self._materialize_version_content(
                    artifact.get("taskId") or "", artifact.get("id") or "", v
                )
                artifact["path"] = v.get("path")
                break

    def _refresh_artifact_gates_for_capability_call(
        self,
        task: TaskDict,
        call: dict[str, Any],
    ) -> list[dict[str, Any]]:
        call_id = call.get("id")
        if not call_id:
            return []
        linked_artifact_ids = {
            str(artifact_id)
            for artifact_id in [
                *list(call.get("artifactRefs") or []),
                *list(call.get("outputArtifactRefs") or []),
            ]
            if artifact_id
        }
        refreshed: list[dict[str, Any]] = []
        for artifact in task.get("artifacts") or []:
            artifact_id = artifact.get("id")
            if artifact.get("sourceCapabilityCallId") != call_id and artifact_id not in linked_artifact_ids:
                continue
            apply_artifact_gate(artifact, source_call=call)
            append_validator_result_to_call(call, artifact["validatorResult"])
            refreshed.append(artifact)
        return refreshed

    def add_material_record(
        self,
        task_id: str,
        *,
        kind: str,
        source: dict[str, Any],
        content_preview: str | None = None,
        structured_content: dict[str, Any] | None = None,
        quality: dict[str, Any] | None = None,
        linked_artifact_ids: list[str] | None = None,
        material_id: str | None = None,
        created_by: str | None = None,
        ingestion_mode: str | None = None,
    ) -> dict[str, Any] | None:
        task = self._tasks.get(task_id)
        if not task:
            return None
        updated = copy.deepcopy(task)
        now = _utc_now()
        artifact_ids = _ordered_unique(linked_artifact_ids or [])
        material = {
            "materialId": material_id or f"material_{uuid4().hex[:12]}",
            "taskId": task_id,
            "kind": kind,
            "source": {
                **copy.deepcopy(source),
                "sourceTaskId": source.get("sourceTaskId") or task_id,
            },
            "contentPreview": content_preview,
            "structuredContent": copy.deepcopy(structured_content or {}),
            "quality": {
                "readable": True,
                "sourceBounded": True,
                "missingFields": [],
                "warnings": [],
                **copy.deepcopy(quality or {}),
            },
            "provenance": {
                "createdBy": created_by or updated.get("createdBy", DEFAULT_CREATED_BY),
                "createdAt": now,
                "ingestionMode": ingestion_mode,
            },
            "linkedArtifactIds": artifact_ids,
        }
        materials = [m for m in updated.get("materials") or [] if m.get("materialId") != material["materialId"]]
        materials.append(material)
        updated["materials"] = materials
        for artifact in updated.get("artifacts") or []:
            if artifact.get("id") not in artifact_ids:
                continue
            linked = _ordered_unique([*(artifact.get("linkedMaterialIds") or []), material["materialId"]])
            artifact["linkedMaterialIds"] = linked
            manifests = [
                m for m in artifact.get("materialManifest") or [] if m.get("materialId") != material["materialId"]
            ]
            manifests.append(copy.deepcopy(material))
            artifact["materialManifest"] = manifests
        updated["updatedAt"] = now
        self._tasks[task_id] = updated
        self._persist_task(task_id)
        for artifact in updated.get("artifacts") or []:
            if artifact.get("id") in artifact_ids:
                self._mirror_artifact(task_id, artifact, updated.get("title"))
        self._emit_task_event(
            task_id,
            "task_updated",
            {"reason": "material_record_created", "materialId": material["materialId"]},
        )
        return copy.deepcopy(material)

    def add_memory_record(
        self,
        task_id: str,
        *,
        kind: str,
        content: str,
        scope: str = "task",
        linked_materials: list[str] | None = None,
        linked_artifacts: list[str] | None = None,
        confidence: str = "unknown",
        created_by: str | None = None,
        valid_until: str | None = None,
        memory_id: str | None = None,
    ) -> dict[str, Any] | None:
        task = self._tasks.get(task_id)
        if not task:
            return None
        updated = copy.deepcopy(task)
        now = _utc_now()
        memory = {
            "memoryId": memory_id or f"mem_{uuid4().hex[:12]}",
            "taskId": task_id,
            "scope": scope,
            "kind": kind,
            "content": content,
            "linkedMaterials": _ordered_unique(linked_materials or []),
            "linkedArtifacts": _ordered_unique(linked_artifacts or []),
            "confidence": confidence,
            "createdBy": created_by or updated.get("createdBy", DEFAULT_CREATED_BY),
            "createdAt": now,
            "validUntil": valid_until,
        }
        records = [m for m in updated.get("memoryRecords") or [] if m.get("memoryId") != memory["memoryId"]]
        records.append(memory)
        updated["memoryRecords"] = records
        for artifact in updated.get("artifacts") or []:
            if artifact.get("id") in memory["linkedArtifacts"]:
                artifact["memoryRefs"] = _ordered_unique([*(artifact.get("memoryRefs") or []), memory["memoryId"]])
        updated["updatedAt"] = now
        self._tasks[task_id] = updated
        self._persist_task(task_id)
        for artifact in updated.get("artifacts") or []:
            if artifact.get("id") in memory["linkedArtifacts"]:
                self._mirror_artifact(task_id, artifact, updated.get("title"))
        self._emit_task_event(
            task_id,
            "task_updated",
            {"reason": "memory_record_created", "memoryId": memory["memoryId"], "kind": kind},
        )
        return copy.deepcopy(memory)

    def update_artifact_material_binding(
        self,
        task_id: str,
        artifact_id: str,
        material_ids: list[str],
    ) -> dict[str, Any] | None:
        task = self._tasks.get(task_id)
        if not task:
            return None
        updated = copy.deepcopy(task)
        materials = updated.get("materials") or []
        material_by_id = {m.get("materialId"): m for m in materials}
        cleaned = _ordered_unique([mid for mid in material_ids if mid in material_by_id])
        for artifact in updated.get("artifacts") or []:
            if artifact.get("id") != artifact_id:
                continue
            artifact["linkedMaterialIds"] = cleaned
            artifact["materialManifest"] = [copy.deepcopy(material_by_id[mid]) for mid in cleaned]
            for material in materials:
                linked = [aid for aid in material.get("linkedArtifactIds") or [] if aid != artifact_id]
                if material.get("materialId") in cleaned:
                    linked = _ordered_unique([*linked, artifact_id])
                material["linkedArtifactIds"] = linked
            updated["updatedAt"] = _utc_now()
            self._tasks[task_id] = updated
            self._persist_task(task_id)
            self._mirror_artifact(task_id, artifact, updated.get("title"))
            self._emit_task_event(
                task_id,
                "task_updated",
                {"reason": "artifact_material_binding_updated", "artifactId": artifact_id},
            )
            return copy.deepcopy(artifact)
        return None

    def reassign_artifact_task(
        self,
        artifact_id: str,
        target_task_id: str,
        *,
        note: str | None = None,
    ) -> dict[str, Any] | None:
        source_task_id: str | None = None
        source_artifact: dict[str, Any] | None = None
        for tid, task in self._tasks.items():
            for artifact in task.get("artifacts") or []:
                if artifact.get("id") == artifact_id:
                    source_task_id = tid
                    source_artifact = copy.deepcopy(artifact)
                    break
            if source_artifact:
                break
        if not source_task_id or source_artifact is None or target_task_id not in self._tasks:
            return None
        if source_task_id == target_task_id:
            return source_artifact

        source = copy.deepcopy(self._tasks[source_task_id])
        target = copy.deepcopy(self._tasks[target_task_id])
        material_ids = set(source_artifact.get("linkedMaterialIds") or [])
        moving_materials: list[dict[str, Any]] = []
        remaining_materials: list[dict[str, Any]] = []
        for material in source.get("materials") or []:
            linked_artifacts = material.get("linkedArtifactIds") or []
            if material.get("materialId") in material_ids or artifact_id in linked_artifacts:
                moved = copy.deepcopy(material)
                moved["taskId"] = target_task_id
                moved.setdefault("source", {})["sourceTaskId"] = target_task_id
                moved["linkedArtifactIds"] = _ordered_unique([artifact_id])
                moving_materials.append(moved)
                material_ids.add(moved.get("materialId"))
                remaining_links = [aid for aid in linked_artifacts if aid != artifact_id]
                if remaining_links:
                    kept = copy.deepcopy(material)
                    kept["linkedArtifactIds"] = remaining_links
                    remaining_materials.append(kept)
            else:
                remaining_materials.append(material)
        source["materials"] = remaining_materials

        moving_memory: list[dict[str, Any]] = []
        remaining_memory: list[dict[str, Any]] = []
        for memory in source.get("memoryRecords") or []:
            linked_artifacts = set(memory.get("linkedArtifacts") or [])
            linked_materials = set(memory.get("linkedMaterials") or [])
            if artifact_id in linked_artifacts or linked_materials & material_ids:
                moved = copy.deepcopy(memory)
                moved["taskId"] = target_task_id
                moved["linkedArtifacts"] = _ordered_unique([artifact_id])
                moving_memory.append(moved)
                remaining_artifacts = [aid for aid in memory.get("linkedArtifacts") or [] if aid != artifact_id]
                if remaining_artifacts:
                    kept = copy.deepcopy(memory)
                    kept["linkedArtifacts"] = remaining_artifacts
                    remaining_memory.append(kept)
            else:
                remaining_memory.append(memory)
        source["memoryRecords"] = remaining_memory

        source["artifacts"] = [a for a in source.get("artifacts") or [] if a.get("id") != artifact_id]
        for detail in source.get("stageDetails") or []:
            detail["artifactRefs"] = [aid for aid in detail.get("artifactRefs") or [] if aid != artifact_id]
            for sub in detail.get("subStages") or []:
                sub["artifactRefs"] = [aid for aid in sub.get("artifactRefs") or [] if aid != artifact_id]
        for call in source.get("capabilityCalls") or []:
            for key in ("artifactRefs", "inputArtifactRefs", "outputArtifactRefs"):
                call[key] = [aid for aid in call.get(key) or [] if aid != artifact_id]

        moved_artifact = copy.deepcopy(source_artifact)
        moved_artifact["taskId"] = target_task_id
        moved_artifact["sourceTaskId"] = target_task_id
        moved_artifact["ownerId"] = target.get("ownerId", moved_artifact.get("ownerId"))
        moved_artifact["workspaceId"] = target.get("workspaceId", moved_artifact.get("workspaceId"))
        moved_artifact["linkedMaterialIds"] = _ordered_unique([m.get("materialId") for m in moving_materials if m.get("materialId")])
        moved_artifact["materialManifest"] = copy.deepcopy(moving_materials)
        if note:
            moved_artifact["ownershipNote"] = note

        target.setdefault("artifacts", []).append(moved_artifact)
        existing_materials = {m.get("materialId"): m for m in target.get("materials") or []}
        for material in moving_materials:
            existing_materials[material.get("materialId")] = material
        target["materials"] = list(existing_materials.values())
        existing_memory = {m.get("memoryId"): m for m in target.get("memoryRecords") or []}
        for memory in moving_memory:
            existing_memory[memory.get("memoryId")] = memory
        target["memoryRecords"] = list(existing_memory.values())

        now = _utc_now()
        source["updatedAt"] = now
        target["updatedAt"] = now
        self._tasks[source_task_id] = source
        self._tasks[target_task_id] = target
        self._persist_task(source_task_id)
        self._persist_task(target_task_id)
        self._mirror_artifact(target_task_id, moved_artifact, target.get("title"))
        self._emit_task_event(source_task_id, "task_updated", {"reason": "artifact_reassigned_out", "artifactId": artifact_id})
        self._emit_task_event(target_task_id, "task_updated", {"reason": "artifact_reassigned_in", "artifactId": artifact_id})
        return copy.deepcopy(moved_artifact)

    def build_material_context_pack(self, task_id: str, material_ids: list[str]) -> dict[str, Any] | None:
        task = self._tasks.get(task_id)
        if not task:
            return None
        return material_context_pack(copy.deepcopy(task), material_ids)

    def _normalize_agent_message(self, message: dict[str, Any]) -> None:
        message.setdefault("runtime", None)
        message.setdefault("messageType", "chat")
        message.setdefault("authorLabel", None)
        message.setdefault("capabilityCallId", None)
        message.setdefault("artifactRefs", [])
        message.setdefault("userFacingNarrative", None)
        requests = []
        for req in message.get("confirmationRequests") or []:
            normalized = copy.deepcopy(req)
            normalized.setdefault("id", f"confirm_{uuid4().hex[:12]}")
            normalized.setdefault("title", None)
            normalized.setdefault("kind", "approval")
            normalized.setdefault("status", "pending")
            normalized.setdefault("blocking", True)
            normalized.setdefault("recommendedOptionId", None)
            normalized.setdefault("artifactRefs", [])
            options = []
            for option in normalized.get("options") or []:
                opt = copy.deepcopy(option)
                opt.setdefault("value", opt.get("id"))
                opt.setdefault("requiresNote", False)
                opt.setdefault("style", "default")
                options.append(opt)
            normalized["options"] = options
            normalized.setdefault("relatedSkillId", None)
            normalized.setdefault("relatedCallId", None)
            normalized.setdefault("userFacingNarrative", None)
            requests.append(normalized)
        message["confirmationRequests"] = requests

    def _find_stage_detail(self, task: TaskDict, stage_key: str) -> dict[str, Any] | None:
        details = task.get("stageDetails") or []
        return next((d for d in details if d.get("stageKey") == stage_key), None)

    def _find_substage(self, detail: dict[str, Any], substage_instance_id: str) -> dict[str, Any] | None:
        return next(
            (s for s in detail.get("subStages") or [] if s.get("subStageInstanceId") == substage_instance_id),
            None,
        )

    def get_profile_state(self, task_id: str) -> dict[str, Any] | None:
        task = self._tasks.get(task_id)
        if not task:
            return None
        draft = task.get("profileDraft")
        if not draft:
            draft = {
                "taskId": task.get("taskId"),
                "ownerId": task.get("ownerId"),
                "workspaceId": task.get("workspaceId"),
                "createdBy": task.get("createdBy"),
                "title": task.get("title"),
                "goalSummary": task.get("goalSummary"),
                "domain": task.get("domain"),
                "currentStage": task.get("currentStage"),
                "status": task.get("status"),
            }
        return {
            "needsConfirmation": bool(task.get("profileNeedsConfirmation")),
            "confirmedAt": task.get("profileConfirmedAt"),
            "draft": copy.deepcopy(draft),
        }

    def set_profile_draft(
        self,
        task_id: str,
        draft: dict[str, Any],
        *,
        needs_confirmation: bool = True,
    ) -> dict[str, Any] | None:
        task = self._tasks.get(task_id)
        if not task:
            return None
        updated = copy.deepcopy(task)
        updated["profileDraft"] = copy.deepcopy(draft)
        updated["profileNeedsConfirmation"] = needs_confirmation
        if needs_confirmation:
            updated["profileConfirmedAt"] = None
        updated["updatedAt"] = _utc_now()
        self._tasks[task_id] = updated
        self._persist_task(task_id)
        self._emit_task_event(task_id, "task_updated", {"reason": "profile_draft_set"})
        return copy.deepcopy(updated["profileDraft"])

    def confirm_profile(self, task_id: str, profile: dict[str, Any]) -> TaskDict | None:
        task = self._tasks.get(task_id)
        if not task:
            return None
        updated = copy.deepcopy(task)

        new_task_id = (profile.get("taskId") or task_id).strip()
        if not new_task_id:
            raise ValueError("taskId 不能为空")
        if new_task_id != task_id and new_task_id in self._tasks:
            raise ValueError(f"taskId 已存在: {new_task_id}")

        updated["taskId"] = new_task_id
        updated["title"] = (profile.get("title") or updated.get("title") or "").strip() or "待补充任务标题"
        updated["goalSummary"] = (profile.get("goalSummary") or "").strip() or None
        updated["domain"] = (profile.get("domain") or updated.get("domain") or "research_task").strip() or "research_task"
        updated["profileDraft"] = {
            "taskId": updated["taskId"],
            "title": updated["title"],
            "goalSummary": updated["goalSummary"],
            "domain": updated["domain"],
            "currentStage": updated.get("currentStage"),
            "status": updated.get("status"),
        }
        updated["profileNeedsConfirmation"] = False
        updated["profileConfirmedAt"] = _utc_now()
        updated["updatedAt"] = _utc_now()

        if new_task_id != task_id:
            self._tasks.pop(task_id, None)
            self._persist_delete(task_id)
        self._tasks[new_task_id] = updated
        self._persist_task(new_task_id)
        self._emit_task_event(new_task_id, "task_updated", {"reason": "profile_confirmed"})
        if new_task_id != task_id:
            publish_task_event(task_id, "task_deleted", updated_at=_utc_now(), payload={"reason": "task_id_changed"})
        return copy.deepcopy(updated)

    def rename_task(self, task_id: str, new_title: str) -> TaskDict | None:
        task = self._tasks.get(task_id)
        if not task:
            return None
        updated = copy.deepcopy(task)
        updated["title"] = new_title.strip()
        updated["updatedAt"] = _utc_now()
        self._tasks[task_id] = updated
        self._persist_task(task_id)
        self._emit_task_event(task_id, "task_updated", {"reason": "task_renamed"})
        return copy.deepcopy(updated)

    def patch_status(self, task_id: str, status: str) -> TaskDict | None:
        task = self._tasks.get(task_id)
        if not task:
            return None
        updated = copy.deepcopy(task)
        updated["status"] = status
        updated["updatedAt"] = _utc_now()
        self._tasks[task_id] = updated
        self._persist_task(task_id)
        self._emit_task_event(task_id, "task_updated", {"reason": "status_changed", "status": status})
        return copy.deepcopy(updated)

    def patch_task(self, task_id: str, updates: dict[str, Any]) -> TaskDict | None:
        task = self._tasks.get(task_id)
        if not task:
            return None
        updated = copy.deepcopy(task)
        for k, v in updates.items():
            updated[k] = v
        _normalize_task_execution_mode(updated)
        updated["updatedAt"] = _utc_now()
        self._tasks[task_id] = updated
        self._persist_task(task_id)
        if "artifacts" in updates:
            for artifact in updated.get("artifacts") or []:
                self._mirror_artifact(task_id, artifact, updated.get("title"))
        self._emit_task_event(task_id, "task_updated", {"reason": "task_patched", "fields": sorted(updates.keys())})
        return copy.deepcopy(updated)

    def update_task_plan(self, task_id: str, editable: dict[str, Any]) -> TaskDict | None:
        """Compare, project, and atomically publish a JSON-backed draft update."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            if task.get("status") != "draft" or task.get("creationStatus") not in {
                "awaiting_confirmation",
                "revision_requested",
            }:
                raise ValueError("creation plan is not editable")
            expected_revision = int(editable["expectedRevision"])
            if int(task.get("revision") or 0) != expected_revision:
                raise RuntimeError("task_revision_conflict")
            updated = prepare_task_plan_candidate(task, editable, updated_at=_utc_now())
            # The candidate was compiled by prepare_task_plan_candidate.  Keep
            # this final identity assertion before either disk or memory sees
            # it, so a failure leaves the old projection entirely intact.
            precompile_creation_plan(
                creation_plan=updated["creationPlan"],
                goal_summary=str(updated.get("goalSummary") or ""),
            )
            self._persist_task_projection(task_id, updated)
            self._tasks[task_id] = updated
        for artifact in updated.get("artifacts") or []:
            self._mirror_artifact(task_id, artifact, updated.get("title"))
        self._emit_task_event(task_id, "task_updated", {"reason": "task_plan_updated"})
        return copy.deepcopy(updated)

    def delete_task(self, task_id: str) -> bool:
        """Remove a task from the in-memory store and persistent backend.

        Returns True if the task existed and was deleted.
        """
        task = self._tasks.pop(task_id, None)
        if task is None:
            return False
        self._persist_delete(task_id)
        publish_task_event(task_id, "task_deleted", updated_at=_utc_now(), payload={})
        return True

    def save_stage_result(self, task_id: str, stage_key: str, body: dict[str, Any]) -> TaskDict | None:
        task = self._tasks.get(task_id)
        if not task:
            return None
        stage_order = _task_stage_order(task)
        if stage_key not in stage_order:
            return None

        updated = copy.deepcopy(task)
        details = list(updated.get("stageDetails") or [])
        if not details:
            details = [_new_stage_detail(s) for s in stage_order]

        hit = next((d for d in details if d.get("stageKey") == stage_key), None)
        if not hit:
            hit = _new_stage_detail(stage_key)
            details.append(hit)

        self._normalize_stage_detail(hit)

        if body.get("summary") is not None:
            hit["summary"] = body["summary"]
        if body.get("risks") is not None:
            hit["risks"] = body["risks"]
        if body.get("nextSteps") is not None:
            hit["nextSteps"] = body["nextSteps"]
        if body.get("label") is not None:
            hit["label"] = body["label"]
        for key in (
            "entryCondition",
            "completionCondition",
            "lockedReason",
            "primaryAction",
            "canSkip",
            "canReview",
            "canLock",
        ):
            if body.get(key) is not None:
                hit[key] = body[key]
        if body.get("status") is not None:
            hit["status"] = body["status"]
        if body.get("artifactRefs") is not None:
            hit["artifactRefs"] = _ordered_unique(
                [*hit.get("artifactRefs", []), *body["artifactRefs"]]
            )
        if body.get("subStages") is not None:
            merged_substages = [copy.deepcopy(s) for s in hit.get("subStages") or []]
            for incoming_raw in body["subStages"]:
                incoming = copy.deepcopy(incoming_raw)
                incoming.setdefault("parentStageKey", stage_key)
                incoming_key = (
                    incoming.get("subStageInstanceId"),
                    incoming.get("parentStageKey") or stage_key,
                    incoming.get("subStage"),
                )
                existing = next(
                    (
                        sub
                        for sub in merged_substages
                        if (
                            incoming_key[0]
                            and sub.get("subStageInstanceId") == incoming_key[0]
                        )
                        or (
                            not incoming_key[0]
                            and (sub.get("parentStageKey") or stage_key) == incoming_key[1]
                            and sub.get("subStage") == incoming_key[2]
                        )
                    ),
                    None,
                )
                if not existing:
                    merged_substages.append(incoming)
                    continue
                artifact_refs = _ordered_unique(
                    [*existing.get("artifactRefs", []), *incoming.get("artifactRefs", [])]
                )
                capability_refs = _ordered_unique(
                    [*existing.get("capabilityCallRefs", []), *incoming.get("capabilityCallRefs", [])]
                )
                existing.update(incoming)
                existing["artifactRefs"] = artifact_refs
                existing["capabilityCallRefs"] = capability_refs
            hit["subStages"] = merged_substages
            self._normalize_stage_detail(hit)

        updated["stageDetails"] = details
        if body.get("setCurrentStage", True):
            updated["currentStage"] = stage_key
        updated["updatedAt"] = _utc_now()
        self._tasks[task_id] = updated
        self._persist_task(task_id)
        self._emit_task_event(task_id, "task_updated", {"reason": "stage_result_saved", "stageKey": stage_key})
        return copy.deepcopy(updated)

    def get_or_create_substage_instance(
        self,
        task_id: str,
        *,
        stage_key: str,
        substage_key: str,
        label: str | None = None,
        status: str = "running",
        summary: str | None = None,
    ) -> dict[str, Any] | None:
        task = self._tasks.get(task_id)
        if not task or stage_key not in _task_stage_order(task):
            return None
        updated = copy.deepcopy(task)
        detail = self._find_stage_detail(updated, stage_key)
        if not detail:
            detail = _new_stage_detail(stage_key)
            updated.setdefault("stageDetails", []).append(detail)
        self._normalize_stage_detail(detail)

        existing = next((s for s in detail.get("subStages") or [] if s.get("subStage") == substage_key), None)
        if existing:
            if label is not None:
                existing["label"] = label
            if summary is not None:
                existing["summary"] = summary
            existing["status"] = status
            if not existing.get("startedAt"):
                existing["startedAt"] = _utc_now()
            if status == "completed":
                existing["finishedAt"] = existing.get("finishedAt") or _utc_now()
            elif status == "running":
                existing["finishedAt"] = None
            updated["updatedAt"] = _utc_now()
            self._tasks[task_id] = updated
            self._persist_task(task_id)
            self._emit_task_event(task_id, "task_updated", {"reason": "substage_updated", "substageKey": substage_key})
            return copy.deepcopy(existing)

        substage = {
            "subStage": substage_key,
            "status": status,
            "artifactRefs": [],
            "subStageInstanceId": f"sub_{uuid4().hex[:12]}",
            "parentStageKey": stage_key,
            "label": label or substage_key,
            "capabilityCallRefs": [],
            "summary": summary,
            "sequence": len(detail.get("subStages") or []),
            "startedAt": _utc_now(),
            "finishedAt": None,
            "l3Plan": None,
        }
        detail.setdefault("subStages", []).append(substage)
        detail["status"] = "running"
        updated["currentStage"] = stage_key
        updated["updatedAt"] = _utc_now()
        self._tasks[task_id] = updated
        self._persist_task(task_id)
        self._emit_task_event(task_id, "task_updated", {"reason": "substage_created", "substageKey": substage_key})
        return copy.deepcopy(substage)

    def initialize_from_workflow(self, task_id: str, workflow: dict[str, Any]) -> dict[str, Any] | None:
        """Initialize stageDetails substage instances from a confirmed workflow."""
        task = self._tasks.get(task_id)
        if not task:
            return None
        updated = copy.deepcopy(task)

        from app.modules import pack_config
        pack = task.get("skillPack", "sdr")

        workflow_stages = workflow.get("stages") or []
        workflow_stage_order = _workflow_stage_order(workflow)
        if not workflow_stage_order:
            return copy.deepcopy(updated)

        # Cross-pack bleed guard: if pack != "sdr", drop substages that are
        # registered in the sdr map but NOT in this pack's map. This prevents
        # new-task-planning from leaking sdr's 27-substage workflow into
        # domain packs (e.g. constraints_criteria inside a materials task).
        # Custom substages absent from every pack map (used by tests and some
        # workflow-defined starters) are still allowed.
        if pack != "sdr":
            pack_keys = set(pack_config.get_all_substage_keys(pack=pack))
            sdr_keys = set(pack_config.get_all_substage_keys(pack="sdr"))
        else:
            pack_keys = sdr_keys = None

        # Disabled-substage guard: pack stage-substage-map may carry
        # ``enabled: false`` for placeholder substages that have no L3 skill
        # yet (e.g. materials synthesis_execution / characterization). Such
        # substages must never enter the workflow — otherwise the executor
        # would treat them as runnable and advance into a step with no real
        # capability. Build a deny-set per pack once.
        disabled_pack_keys: set[str] = set()
        for entries in pack_config.load_stage_substage_map(pack=pack).values():
            for e in entries:
                if e.get("enabled") is False:
                    disabled_pack_keys.add(e["key"])

        new_catalog: list[dict[str, Any]] = []
        for stage_entry in workflow_stages:
            stage_key = stage_entry.get("stageKey", "")
            if stage_key not in workflow_stage_order:
                continue
            substages = stage_entry.get("substages") or []
            allowed: list[dict[str, Any]] = []
            for ss in substages:
                ss_key = ss.get("substage", "")
                if not ss_key:
                    continue
                if pack_keys is not None and ss_key in sdr_keys and ss_key not in pack_keys:
                    _log.warning(
                        "initialize_from_workflow: dropping cross-pack substage=%s "
                        "(belongs to sdr, not pack=%s) task_id=%s",
                        ss_key, pack, task_id,
                    )
                    continue
                if ss_key in disabled_pack_keys:
                    _log.info(
                        "initialize_from_workflow: skipping disabled substage=%s "
                        "pack=%s task_id=%s",
                        ss_key, pack, task_id,
                    )
                    continue
                label = pack_config.get_substage_label(ss_key, pack=pack)
                desc = ""
                # Pull description from stage-substage-map
                for e in pack_config.load_stage_substage_map(pack=pack).get(stage_key, []):
                    if e["key"] == ss_key:
                        desc = e.get("description", "")
                        break
                allowed.append({
                    "key": ss_key,
                    "stageKey": stage_key,
                    "label": label,
                    "description": desc,
                    "enabled": True,
                })
            new_catalog.append({"stageKey": stage_key, "allowedSubStages": allowed})

            # Create substage instances
            detail = self._find_stage_detail(updated, stage_key)
            if not detail:
                detail = _new_stage_detail(stage_key, metadata=stage_entry)
                updated.setdefault("stageDetails", []).append(detail)
            else:
                for key in (
                    "label",
                    "entryCondition",
                    "completionCondition",
                    "lockedReason",
                    "primaryAction",
                    "canSkip",
                    "canReview",
                    "canLock",
                ):
                    if stage_entry.get(key) is not None:
                        detail[key] = stage_entry[key]
            self._normalize_stage_detail(detail)

            selected_substage_keys: list[str] = []
            for idx, ss in enumerate(substages):
                ss_key = ss.get("substage", "")
                if not ss_key:
                    continue
                if pack_keys is not None and ss_key in sdr_keys and ss_key not in pack_keys:
                    continue  # dropped by cross-pack guard above
                if ss_key in disabled_pack_keys:
                    continue  # placeholder substage with no L3 skill
                selected_substage_keys.append(ss_key)
                label = pack_config.get_substage_label(ss_key, pack=pack)
                existing = next(
                    (s for s in detail.get("subStages", []) if s.get("subStage") == ss_key),
                    None,
                )
                if existing:
                    existing["status"] = "not_started"
                    existing["artifactRefs"] = []
                    existing["capabilityCallRefs"] = []
                    existing["summary"] = None
                    existing["label"] = label
                    existing["parentStageKey"] = stage_key
                    existing["sequence"] = idx
                    existing["startedAt"] = None
                    existing["finishedAt"] = None
                else:
                    detail.setdefault("subStages", []).append({
                        "subStage": ss_key,
                        "status": "not_started",
                        "artifactRefs": [],
                        "subStageInstanceId": f"sub_{uuid4().hex[:12]}",
                        "parentStageKey": stage_key,
                        "label": label,
                        "capabilityCallRefs": [],
                        "summary": None,
                        "sequence": idx,
                        "startedAt": None,
                        "finishedAt": None,
                        "l3Plan": None,
                    })
            order = {key: idx for idx, key in enumerate(selected_substage_keys)}
            detail["subStages"] = sorted(
                [
                    sub
                    for sub in detail.get("subStages", [])
                    if sub.get("subStage") in order
                ],
                key=lambda sub: order.get(sub.get("subStage"), 0),
            )
            detail.setdefault("artifactRefs", [])
            detail["status"] = "not_started"

        details_by_stage = {
            detail.get("stageKey"): detail
            for detail in updated.get("stageDetails") or []
            if detail.get("stageKey") in workflow_stage_order
        }
        updated["stages"] = list(workflow_stage_order)
        updated["stageDetails"] = [
            details_by_stage[stage]
            for stage in workflow_stage_order
            if stage in details_by_stage
        ]
        if updated.get("currentStage") not in workflow_stage_order:
            updated["currentStage"] = workflow_stage_order[0]
        updated["stageCatalog"] = new_catalog
        if workflow.get("researchType"):
            updated["researchType"] = workflow["researchType"]
        updated["updatedAt"] = _utc_now()
        self._tasks[task_id] = updated
        self._persist_task(task_id)
        self._emit_task_event(task_id, "task_updated", {"reason": "workflow_initialized"})
        return copy.deepcopy(updated)

    def _ordered_workflow_substages(self, task: TaskDict) -> list[tuple[str, str, str]]:
        ordered: list[tuple[str, str, str]] = []
        for catalog_entry in task.get("stageCatalog") or []:
            stage_key = catalog_entry.get("stageKey", "")
            for entry in catalog_entry.get("allowedSubStages") or []:
                ordered.append((stage_key, entry["key"], entry.get("label", entry["key"])))
        return ordered

    def _workflow_index(self, ordered: list[tuple[str, str, str]], stage_key: str, substage_key: str) -> int:
        for i, (sk, ssk, _) in enumerate(ordered):
            if sk == stage_key and ssk == substage_key:
                return i
        return -1

    def _resolve_strict_advance_target(
        self,
        task: TaskDict,
        substage_key: str,
        ordered: list[tuple[str, str, str]],
    ) -> tuple[str, str] | None:
        """Resolve the active workflow boundary without allowing out-of-order jumps."""
        current_stage = task.get("currentStage") or ""
        if not current_stage:
            return None

        detail = self._find_stage_detail(task, current_stage)
        if not detail:
            return None

        self._normalize_stage_detail(detail)
        running = [
            ss
            for ss in detail.get("subStages") or []
            if ss.get("status") == "running"
        ]
        for ss in running:
            if ss.get("subStage") == substage_key:
                return current_stage, substage_key
        if running:
            return None

        hit = next(
            (
                ss
                for ss in detail.get("subStages") or []
                if ss.get("subStage") == substage_key and ss.get("status") == "completed"
            ),
            None,
        )
        if not hit:
            return None

        current_idx = self._workflow_index(ordered, current_stage, substage_key)
        if current_idx < 0:
            return None

        completed_indices = [
            self._workflow_index(ordered, current_stage, ss.get("subStage", ""))
            for ss in detail.get("subStages") or []
            if ss.get("status") == "completed"
        ]
        completed_indices = [idx for idx in completed_indices if idx >= 0]
        if not completed_indices or current_idx != max(completed_indices):
            return None
        return current_stage, substage_key

    def advance_substage(
        self,
        task_id: str,
        current_substage_key: str,
        *,
        respect_stage_gate: bool = True,
    ) -> dict[str, Any] | None:
        """Complete current substage, activate next substage in workflow order.

        Returns the next substage info dict or None if no next substage (task complete).
        Response dict: {stageKey, substageKey, substageLabel, completed, hasNext}
        """
        task = self._tasks.get(task_id)
        if not task:
            return None

        ordered = self._ordered_workflow_substages(task)

        resolved = self._resolve_strict_advance_target(task, current_substage_key, ordered)
        if not resolved:
            return None
        current_stage_key, current_substage_key = resolved

        updated = copy.deepcopy(task)

        # 1. Mark current substage as completed (only within resolved stage)
        now = _utc_now()
        detail_for_current = self._find_stage_detail(updated, current_stage_key)
        if detail_for_current:
            self._normalize_stage_detail(detail_for_current)
            for ss in detail_for_current.get("subStages") or []:
                if ss.get("subStage") == current_substage_key:
                    ss["status"] = "completed"
                    ss["finishedAt"] = ss.get("finishedAt") or now
            _reconcile_stage_status(detail_for_current, now)

        # 3. Find next substage after current (stage + substage pair)
        current_idx = -1
        for i, (sk, ssk, _) in enumerate(ordered):
            if sk == current_stage_key and ssk == current_substage_key:
                current_idx = i
                break

        if current_idx == -1:
            _log.warning(
                "Cannot advance substage: task=%s substage=(%s, %s) missing from stageCatalog",
                task_id,
                current_stage_key,
                current_substage_key,
            )
            return None

        if current_idx + 1 >= len(ordered):
            # No next substage — task is complete at substage level
            if current_stage_key:
                detail = self._find_stage_detail(updated, current_stage_key)
                if detail:
                    _reconcile_stage_status(detail, now)
            updated["status"] = "completed"
            updated["updatedAt"] = now
            self._tasks[task_id] = updated
            self._persist_task(task_id)
            self._emit_task_event(
                task_id,
                "task_updated",
                {"reason": "substage_advanced", "completed": True, "status": "completed"},
            )
            return {"completed": True, "hasNext": False}

        next_stage_key, next_substage_key, next_label = ordered[current_idx + 1]

        if current_stage_key and next_stage_key != current_stage_key:
            prev_detail = self._find_stage_detail(updated, current_stage_key)
            if prev_detail:
                _mark_stage_completed(prev_detail, now)
            if respect_stage_gate and updated.get("executionMode") == "stage_confirmation":
                updated["status"] = "awaiting_confirmation"
                updated["stageGateResumePending"] = {
                    "stageKey": current_stage_key,
                    "substageKey": current_substage_key,
                    "nextStageKey": next_stage_key,
                    "nextSubstageKey": next_substage_key,
                }
                updated["updatedAt"] = now
                self._tasks[task_id] = updated
                self._persist_task(task_id)
                self._emit_task_event(
                    task_id,
                    "task_updated",
                    {
                        "reason": "stage_confirmation_required",
                        "completed": False,
                        "stageKey": current_stage_key,
                        "nextStageKey": next_stage_key,
                        "nextSubstageKey": next_substage_key,
                    },
                )
                return {
                    "completed": False,
                    "hasNext": True,
                    "requiresConfirmation": True,
                    "stageKey": current_stage_key,
                    "nextStageKey": next_stage_key,
                    "substageKey": current_substage_key,
                    "nextSubstageKey": next_substage_key,
                    "nextSubstageLabel": next_label,
                }

        _complete_prior_stages(updated, next_stage_key, now)

        # 4. Activate next substage
        detail = self._find_stage_detail(updated, next_stage_key)
        if not detail:
            detail = {
                "stageKey": next_stage_key,
                "status": "running",
                "summary": None,
                "risks": None,
                "nextSteps": None,
                "artifactRefs": [],
                "subStages": [],
            }
            updated.setdefault("stageDetails", []).append(detail)
        self._normalize_stage_detail(detail)

        existing = next(
            (s for s in detail.get("subStages") or [] if s.get("subStage") == next_substage_key),
            None,
        )
        if existing:
            existing["status"] = "running"
            existing["startedAt"] = existing.get("startedAt") or now
            existing["finishedAt"] = None
        else:
            detail.setdefault("subStages", []).append({
                "subStage": next_substage_key,
                "status": "running",
                "artifactRefs": [],
                "subStageInstanceId": f"sub_{uuid4().hex[:12]}",
                "parentStageKey": next_stage_key,
                "label": next_label,
                "capabilityCallRefs": [],
                "summary": None,
                "sequence": len(detail.get("subStages") or []),
                "startedAt": now,
                "finishedAt": None,
                "l3Plan": None,
            })
        _clear_stage_lock_guards(detail)
        detail["status"] = "running"
        updated["currentStage"] = next_stage_key
        updated["status"] = "running"
        updated["updatedAt"] = now
        self._tasks[task_id] = updated
        self._persist_task(task_id)
        self._emit_task_event(
            task_id,
            "task_updated",
            {
                "reason": "substage_advanced",
                "completed": False,
                "stageKey": next_stage_key,
                "substageKey": next_substage_key,
            },
        )

        return {
            "completed": False,
            "hasNext": True,
            "stageKey": next_stage_key,
            "substageKey": next_substage_key,
            "substageLabel": next_label,
        }

    def patch_substage_instance(
        self,
        task_id: str,
        substage_instance_id: str,
        *,
        status: str | None = None,
        summary: str | None = None,
        artifact_refs: list[str] | None = None,
        capability_call_refs: list[str] | None = None,
        finished_at: str | None = None,
    ) -> dict[str, Any] | None:
        task = self._tasks.get(task_id)
        if not task:
            return None
        updated = copy.deepcopy(task)
        hit = None
        for detail in updated.get("stageDetails") or []:
            self._normalize_stage_detail(detail)
            hit = self._find_substage(detail, substage_instance_id)
            if hit:
                if status is not None:
                    hit["status"] = status
                if summary is not None:
                    hit["summary"] = summary
                if artifact_refs is not None:
                    hit["artifactRefs"] = _ordered_unique([*hit.get("artifactRefs", []), *artifact_refs])
                if capability_call_refs is not None:
                    hit["capabilityCallRefs"] = _ordered_unique(
                        [*hit.get("capabilityCallRefs", []), *capability_call_refs]
                    )
                if finished_at is not None:
                    hit["finishedAt"] = finished_at
                _reconcile_stage_status(detail, _utc_now())
                updated["updatedAt"] = _utc_now()
                self._tasks[task_id] = updated
                self._persist_task(task_id)
                self._emit_task_event(
                    task_id,
                    "task_updated",
                    {"reason": "substage_updated", "subStageInstanceId": substage_instance_id},
                )
                return copy.deepcopy(hit)
        return None

    def patch_substage_l3_plan(
        self,
        task_id: str,
        substage_instance_id: str,
        plan: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Update the l3Plan field on a substage instance."""
        task = self._tasks.get(task_id)
        if not task:
            return None
        updated = copy.deepcopy(task)
        for detail in updated.get("stageDetails") or []:
            self._normalize_stage_detail(detail)
            hit = self._find_substage(detail, substage_instance_id)
            if hit:
                hit["l3Plan"] = plan
                updated["updatedAt"] = _utc_now()
                self._tasks[task_id] = updated
                self._persist_task(task_id)
                self._emit_task_event(
                    task_id,
                    "task_updated",
                    {"reason": "substage_l3_plan_updated", "subStageInstanceId": substage_instance_id},
                )
                return copy.deepcopy(hit)
        return None

    def add_artifact(
        self,
        task_id: str,
        *,
        kind: str,
        title: str,
        summary: str | None = None,
        path: str | None = None,
        format: str | None = None,
        related_stage: str | None = None,
        related_skill_id: str | None = None,
        substage_instance_id: str | None = None,
        source_capability_call_id: str | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
        storage_type: str | None = None,
        inline_content: str | None = None,
        artifact_id: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        task = self._tasks.get(task_id)
        if not task:
            return None
        updated = copy.deepcopy(task)
        substage_key: str | None = None
        if substage_instance_id:
            for detail in updated.get("stageDetails") or []:
                self._normalize_stage_detail(detail)
                sub = self._find_substage(detail, substage_instance_id)
                if sub:
                    substage_key = sub.get("subStage")
                    related_stage = related_stage or detail.get("stageKey")
                    break
        artifact = {
            "id": artifact_id or f"artifact_{uuid4().hex[:12]}",
            "taskId": task_id,
            "sourceTaskId": task_id,
            "ownerId": updated.get("ownerId", DEFAULT_OWNER_ID),
            "workspaceId": updated.get("workspaceId", DEFAULT_WORKSPACE_ID),
            "createdBy": updated.get("createdBy", DEFAULT_CREATED_BY),
            "kind": kind,
            "title": title,
            "summary": summary,
            "createdAt": _utc_now(),
            "path": path,
            "format": format,
            "relatedStage": related_stage,
            "stage": related_stage,
            "relatedSkillId": related_skill_id,
            "subStageInstanceId": substage_instance_id,
            "substage": substage_key,
            "sourceCapabilityCallId": source_capability_call_id,
            "evidenceRefs": copy.deepcopy(evidence_refs or []),
            "artifactState": None,
            "artifactType": kind,
        }
        if storage_type is not None:
            artifact["storageType"] = storage_type
        if inline_content is not None:
            artifact["inlineContent"] = inline_content
        if extra_fields:
            artifact.update(copy.deepcopy(extra_fields))
        self._normalize_artifact(artifact, updated)
        updated.setdefault("artifacts", []).append(artifact)
        _normalize_task_result_source(updated)

        if related_stage:
            detail = self._find_stage_detail(updated, related_stage)
            if detail:
                detail["artifactRefs"] = _ordered_unique([*detail.setdefault("artifactRefs", []), artifact["id"]])
        if substage_instance_id:
            for detail in updated.get("stageDetails") or []:
                self._normalize_stage_detail(detail)
                sub = self._find_substage(detail, substage_instance_id)
                if sub:
                    sub["artifactRefs"] = _ordered_unique([*sub.setdefault("artifactRefs", []), artifact["id"]])
                    break
        if source_capability_call_id:
            matched_call = None
            for call in updated.get("capabilityCalls") or []:
                if call.get("id") == source_capability_call_id:
                    call["artifactRefs"] = _ordered_unique([*call.setdefault("artifactRefs", []), artifact["id"]])
                    call["outputArtifactRefs"] = _ordered_unique(
                        [*call.setdefault("outputArtifactRefs", []), artifact["id"]]
                    )
                    matched_call = call
                    break
            if matched_call is not None:
                append_validator_result_to_call(matched_call, artifact["validatorResult"])
                # derived_from：把这次 call 的 input→output 显式化为来源关系
                artifact_provenance.record_relations_for_call(updated, matched_call, self)

        updated["updatedAt"] = _utc_now()
        self._tasks[task_id] = updated
        self._persist_task(task_id)
        self._mirror_artifact(task_id, artifact, updated.get("title"))
        self._emit_task_event(
            task_id,
            "artifact_created",
            {"artifactId": artifact["id"], "reason": "artifact_created"},
        )
        return copy.deepcopy(artifact)

    def remove_artifact(self, task_id: str, artifact_id: str) -> bool:
        """Remove an artifact from the task payload. Returns True if it was present.

        Note: dangling id refs in stageDetails/subStages/capabilityCalls are left as-is
        (advisory id lists; harmless). The catalog/task-detail render off the artifacts list.
        """
        task = self._tasks.get(task_id)
        if not task:
            return False
        if not any(a.get("id") == artifact_id for a in (task.get("artifacts") or [])):
            return False
        updated = copy.deepcopy(task)
        updated["artifacts"] = [a for a in (updated.get("artifacts") or []) if a.get("id") != artifact_id]
        updated["updatedAt"] = _utc_now()
        self._tasks[task_id] = updated
        self._persist_task(task_id)
        self._mirror_artifact_delete(artifact_id)
        self._emit_task_event(
            task_id,
            "task_updated",
            {"artifactId": artifact_id, "reason": "artifact_removed"},
        )
        return True

    def update_artifact_review_state(
        self,
        task_id: str,
        artifact_id: str,
        *,
        artifact_state: str,
        reviewer: str,
        note: str | None = None,
        source: str = "user",
    ) -> dict[str, Any] | None:
        artifact_state = normalize_artifact_state(artifact_state)
        if artifact_state not in {"usable", "rejected_or_needs_revision"}:
            raise ValueError(f"unsupported artifact review state: {artifact_state}")
        task = self._tasks.get(task_id)
        if not task:
            return None
        updated = copy.deepcopy(task)
        reviewed_at = _utc_now()
        for artifact in updated.get("artifacts") or []:
            if artifact.get("id") != artifact_id:
                continue
            _normalize_artifact_state(artifact)
            current_state = artifact.get("artifactState")
            apply_artifact_gate(artifact)
            current_gate = artifact.get("humanGate") or {}
            if artifact_state == "usable" and not current_gate.get("canConfirmPass"):
                raise ValueError(f"artifact state {current_state} cannot be confirmed as usable")
            if artifact.get("kind") == "research_start_draft" or artifact.get("artifactType") == "research_start_draft":
                raise ValueError("research_start_draft artifacts are task setup drafts, not reviewable research outputs")
            artifact["artifactState"] = artifact_state
            artifact["reviewedAt"] = reviewed_at
            artifact["reviewedBy"] = reviewer
            artifact["reviewSource"] = source
            if note:
                artifact["reviewNote"] = note
            artifact["userFacingNarrative"] = build_review_agent_narrative(
                artifact_title=artifact.get("title"),
                decision=artifact_state,
                note=note,
                source_id=artifact_id,
            )
            trust_records = list(artifact.get("trustRecords") or [])
            trust_records.append(
                {
                    "id": f"trust_{uuid4().hex[:12]}",
                    "name": "Artifact review",
                    "category": "human_review",
                    "executionStatus": "input_recorded",
                    "trustContribution": "evidence" if artifact_state == "usable" else "not_used",
                    "usedFor": "artifact_lifecycle_review",
                    "limitation": note or (
                        "Manual review recorded without additional notes."
                        if source == "user"
                        else "Review agent recorded without additional notes."
                    ),
                    "nextAction": (
                        "Artifact can be used downstream."
                        if artifact_state == "usable"
                        else "Revise or regenerate this artifact before using it downstream."
                    ),
                    "timestamp": reviewed_at,
                    "relatedArtifactIds": [artifact_id],
                    "sourceLabel": "User review" if source == "user" else "Review agent",
                }
            )
            artifact["trustRecords"] = trust_records
            if artifact_state == "rejected_or_needs_revision":
                # Record the return-for-modification on the unified version timeline
                # (issue #302): the review note becomes the version feedback so the
                # reason is visible in history even before regeneration.
                self._append_artifact_version(
                    artifact,
                    source="revision_requested",
                    content=None,
                    feedback=note,
                    parent_version_id=None,
                    restored_from_version_id=None,
                    created_by=reviewer,
                    created_at=reviewed_at,
                )
                artifact["revisionRequestedAt"] = reviewed_at
                artifact["revisionFeedback"] = note
            apply_artifact_gate(artifact)
            if artifact.get("sourceCapabilityCallId"):
                for call in updated.get("capabilityCalls") or []:
                    if call.get("id") == artifact.get("sourceCapabilityCallId"):
                        append_validator_result_to_call(call, artifact["validatorResult"])
                        break
            updated["updatedAt"] = reviewed_at
            self._tasks[task_id] = updated
            self._persist_task(task_id)
            self._mirror_artifact(task_id, artifact, updated.get("title"))
            self._emit_task_event(
                task_id,
                "task_updated",
                {
                    "artifactId": artifact_id,
                    "artifactState": artifact_state,
                    "reason": "artifact_review_state_updated",
                },
            )
            return copy.deepcopy(artifact)
        return None

    # ── artifact versioning (issue #302) ──────────────────────────────

    def _find_task_artifact(self, task: TaskDict, artifact_id: str) -> dict[str, Any] | None:
        for artifact in task.get("artifacts") or []:
            if artifact.get("id") == artifact_id:
                return artifact
        return None

    def list_artifact_versions(
        self, task_id: str, artifact_id: str
    ) -> dict[str, Any] | None:
        """Return ``{artifactId, currentVersionId, versions}`` (versions without inlineContent).

        Reads never mutate the live store; the lazy v1 backfill is computed on a
        copy via :func:`_versioned_view`.
        """
        task = self._tasks.get(task_id)
        if not task:
            return None
        artifact = self._find_task_artifact(task, artifact_id)
        if not artifact:
            return None
        versions, current = _versioned_view(copy.deepcopy(artifact))
        summaries = [
            {k: v.get(k) for k in v if k != "inlineContent"} for v in versions
        ]
        return {
            "artifactId": artifact_id,
            "currentVersionId": current,
            "versions": summaries,
        }

    def get_artifact_version(
        self, task_id: str, artifact_id: str, version_id: str
    ) -> dict[str, Any] | None:
        """Return a single version dict (including inlineContent), or None."""
        task = self._tasks.get(task_id)
        if not task:
            return None
        artifact = self._find_task_artifact(task, artifact_id)
        if not artifact:
            return None
        versions, _ = _versioned_view(copy.deepcopy(artifact))
        for v in versions:
            if v.get("versionId") == version_id:
                return copy.deepcopy(v)
        return None

    def edit_artifact_content(
        self,
        task_id: str,
        artifact_id: str,
        *,
        content: str,
        change_reason: str | None = None,
        editor: str | None = None,
    ) -> dict[str, Any] | None:
        """Inline-edit Markdown/text content → new ``user_edit`` version.

        Raises ``ValueError`` for empty or non-editable content. Stage/substage
        fields are never changed (issue #302 rule 6). A previously-verified
        artifact is reset to ``pending_review`` after an edit.
        """
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must not be empty")
        task = self._tasks.get(task_id)
        if not task:
            return None
        updated = copy.deepcopy(task)
        artifact = self._find_task_artifact(updated, artifact_id)
        if not artifact:
            return None
        self._normalize_artifact(artifact, updated)
        editable, reason = _artifact_editable(artifact)
        if not editable:
            raise ValueError(reason)
        now = _utc_now()
        data = content.encode("utf-8")
        snapshot = {
            "storageType": "inline",
            "inlineContent": content,
            "path": None,
            "externalKey": None,
            "contentSha256": hashlib.sha256(data).hexdigest(),
            "contentSize": len(data),
        }
        self._append_artifact_version(
            artifact,
            source="user_edit",
            content=snapshot,
            feedback=change_reason,
            parent_version_id=None,
            restored_from_version_id=None,
            created_by=editor or artifact.get("createdBy") or DEFAULT_CREATED_BY,
            created_at=now,
        )
        if artifact.get("artifactState") == "usable":
            artifact["artifactState"] = "needs_review"
            trust = list(artifact.get("trustRecords") or [])
            trust.append(
                {
                    "id": f"trust_{uuid4().hex[:12]}",
                    "name": "Artifact edited",
                    "category": "human_review",
                    "executionStatus": "input_recorded",
                    "trustContribution": "needs_confirmation",
                    "usedFor": "artifact_content_edit",
                    "limitation": "User edited the artifact content; verification reset to pending review.",
                    "nextAction": "Re-review the edited artifact before downstream use.",
                    "timestamp": now,
                    "relatedArtifactIds": [artifact_id],
                    "sourceLabel": "User edit",
                }
            )
            artifact["trustRecords"] = trust
        # issue #302 r2: a user edit also supersedes a pending revision — move out of
        # rejected_or_needs_revision and clear the stale revision markers.
        _reset_review_state_after_new_version(artifact)
        apply_artifact_gate(artifact)
        updated["updatedAt"] = now
        self._tasks[task_id] = updated
        self._persist_task(task_id)
        self._mirror_artifact(task_id, artifact, updated.get("title"))
        self._emit_task_event(
            task_id,
            "artifact_version_created",
            {"artifactId": artifact_id, "source": "user_edit"},
        )
        return copy.deepcopy(artifact)

    def request_artifact_revision(
        self,
        task_id: str,
        artifact_id: str,
        *,
        feedback: str | None = None,
        requested_by: str = "user",
    ) -> dict[str, Any] | None:
        """Record a return-for-modification: append a ``revision_requested`` event,
        mark the artifact ``rejected_or_needs_revision`` and store the feedback.

        Works for substage (``stage_summary``) artifacts too — this path bypasses
        the review-state guard so substage products can be returned (issue #302).
        """
        task = self._tasks.get(task_id)
        if not task:
            return None
        updated = copy.deepcopy(task)
        artifact = self._find_task_artifact(updated, artifact_id)
        if not artifact:
            return None
        # issue #302 scope: return/regenerate is only meaningful for substage
        # (stage_summary) outputs — those are the only artifacts with a substage
        # context the regeneration fold can target. Uploads, reused/imported, and
        # chat artifacts have no resolvable re-run path, so refuse here (the caller
        # gets a 409). Drafts are also excluded (edit-only).
        kind = artifact.get("kind") or artifact.get("artifactType") or ""
        if kind != "stage_summary":
            raise ValueError(
                "return/regenerate is only supported for substage (stage_summary) artifacts; "
                "edit this artifact directly instead"
            )
        self._normalize_artifact(artifact, updated)
        now = _utc_now()
        self._append_artifact_version(
            artifact,
            source="revision_requested",
            content=None,
            feedback=feedback,
            parent_version_id=None,
            restored_from_version_id=None,
            created_by=requested_by,
            created_at=now,
        )
        artifact["artifactState"] = "rejected_or_needs_revision"
        artifact["revisionRequestedAt"] = now
        artifact["revisionFeedback"] = feedback
        updated["updatedAt"] = now
        self._tasks[task_id] = updated
        self._persist_task(task_id)
        self._mirror_artifact(task_id, artifact, updated.get("title"))
        self._emit_task_event(
            task_id,
            "artifact_version_created",
            {"artifactId": artifact_id, "source": "revision_requested"},
        )
        return copy.deepcopy(artifact)

    def regenerate_artifact(
        self,
        task_id: str,
        artifact_id: str,
        *,
        feedback: str | None = None,
        skill_id: str | None = None,
        requested_by: str = "user",
    ) -> dict[str, Any] | None:
        """Return-for-modification + arm the task for an agent regeneration.

        Steps (a)-(c) are synchronous and self-contained: record the
        ``revision_requested`` event, mark pending, and set ``task.pendingRegenerate``
        so the next substage output registration folds into this artifact as an
        ``agent_regenerate`` version (see ``adopt_artifact_as_version``). Triggering
        the actual substage re-run is the caller's responsibility (existing retry /
        agent-chat path); this method never blocks on execution.
        """
        # Validate scope + substage context BEFORE recording anything, so a refusal
        # leaves the artifact untouched (no rejected half-state with no regen path).
        # issue #302 r2: regeneration is bound to a specific substage instance —
        # require explicit substage context so the fold has a precise target and a
        # later fallback output (which may carry no substage context) cannot be
        # loosely folded into this artifact.
        existing = self.get_artifact(task_id, artifact_id)
        if existing is None:
            return None
        kind = existing.get("kind") or existing.get("artifactType") or ""
        if kind != "stage_summary":
            raise ValueError(
                "return/regenerate is only supported for substage (stage_summary) artifacts; "
                "edit this artifact directly instead"
            )
        substage_instance_id = existing.get("subStageInstanceId")
        if not substage_instance_id:
            raise ValueError(
                "regeneration requires an explicit substage context; "
                "this artifact is not bound to a substage instance"
            )
        # now safe to record the revision event + arm the fold
        artifact = self.request_artifact_revision(
            task_id, artifact_id, feedback=feedback, requested_by=requested_by
        )
        if artifact is None:
            return None
        task = self._tasks.get(task_id)
        if task:
            updated = copy.deepcopy(task)
            updated["pendingRegenerate"] = {
                "artifactId": artifact_id,
                "subStageInstanceId": substage_instance_id,
                "stageKey": artifact.get("relatedStage") or artifact.get("stage"),
                "feedback": feedback,
                "skillId": skill_id,
                "requestedAt": _utc_now(),
            }
            self._tasks[task_id] = updated
            self._persist_task(task_id)
        return self.get_artifact(task_id, artifact_id)

    def restore_artifact_version(
        self,
        task_id: str,
        artifact_id: str,
        version_id: str,
        *,
        requested_by: str = "user",
    ) -> dict[str, Any] | None:
        """Restore a historical content version → new ``restore`` version.

        Copying reuses immutable storage references (path / externalKey) — blobs are
        never duplicated. ``revision_requested`` events cannot be restored.
        """
        task = self._tasks.get(task_id)
        if not task:
            return None
        updated = copy.deepcopy(task)
        artifact = self._find_task_artifact(updated, artifact_id)
        if not artifact:
            return None
        self._normalize_artifact(artifact, updated)
        versions, _ = _versioned_view(artifact)
        target = next((v for v in versions if v.get("versionId") == version_id), None)
        if target is None:
            raise ValueError("version not found")
        if target.get("source") == "revision_requested":
            raise ValueError("cannot restore a metadata-only revision_requested event")
        now = _utc_now()
        snapshot = {
            k: target.get(k)
            for k in ("storageType", "inlineContent", "path", "externalKey", "contentSha256", "contentSize")
        }
        feedback = f"恢复自版本 v{target.get('versionNumber')}"
        self._append_artifact_version(
            artifact,
            source="restore",
            content=snapshot,
            feedback=feedback,
            parent_version_id=None,
            restored_from_version_id=version_id,
            created_by=requested_by,
            created_at=now,
        )
        if artifact.get("artifactState") == "usable":
            artifact["artifactState"] = "needs_review"
        # issue #302 r2: a restore also supersedes a pending revision.
        _reset_review_state_after_new_version(artifact)
        apply_artifact_gate(artifact)
        updated["updatedAt"] = now
        self._tasks[task_id] = updated
        self._persist_task(task_id)
        self._mirror_artifact(task_id, artifact, updated.get("title"))
        self._emit_task_event(
            task_id,
            "artifact_version_created",
            {"artifactId": artifact_id, "source": "restore", "restoredFromVersionId": version_id},
        )
        return copy.deepcopy(artifact)

    def adopt_artifact_as_version(
        self,
        task_id: str,
        target_artifact_id: str,
        *,
        feedback: str | None = None,
        storage_type: str | None = None,
        inline_content: str | None = None,
        path: str | None = None,
        external_key: str | None = None,
        content_sha256: str | None = None,
        content_size: int | None = None,
        requested_by: str = "agent",
    ) -> dict[str, Any] | None:
        """Fold a freshly-generated output into an existing artifact as a new
        ``agent_regenerate`` version (used by the worker registration hook).

        Advances ``currentVersionId``, resets state to ``pending_review``, clears
        the revision-pending markers, and consumes ``task.pendingRegenerate`` when
        it targets this artifact.
        """
        task = self._tasks.get(task_id)
        if not task:
            return None
        updated = copy.deepcopy(task)
        artifact = self._find_task_artifact(updated, target_artifact_id)
        if not artifact:
            return None
        self._normalize_artifact(artifact, updated)
        now = _utc_now()
        snapshot = {
            "storageType": storage_type,
            "inlineContent": inline_content,
            "path": path,
            "externalKey": external_key,
            "contentSha256": content_sha256,
            "contentSize": content_size,
        }
        self._append_artifact_version(
            artifact,
            source="agent_regenerate",
            content=snapshot,
            feedback=feedback,
            parent_version_id=None,
            restored_from_version_id=None,
            created_by=requested_by,
            created_at=now,
        )
        artifact["artifactState"] = "needs_review"
        artifact["revisionRequestedAt"] = None
        artifact["revisionFeedback"] = None
        apply_artifact_gate(artifact)
        pending = updated.get("pendingRegenerate")
        if isinstance(pending, dict) and pending.get("artifactId") == target_artifact_id:
            updated["pendingRegenerate"] = None
        updated["updatedAt"] = now
        self._tasks[task_id] = updated
        self._persist_task(task_id)
        self._mirror_artifact(task_id, artifact, updated.get("title"))
        self._emit_task_event(
            task_id,
            "artifact_version_created",
            {"artifactId": target_artifact_id, "source": "agent_regenerate"},
        )
        return copy.deepcopy(artifact)

    def patch_artifact_storage(
        self,
        task_id: str,
        artifact_id: str,
        *,
        storage_type: str | None = None,
        inline_content: str | None = None,
        external_key: str | None = None,
        path: str | None = None,
    ) -> dict[str, Any] | None:
        """Patch an artifact's top-level storage fields AND sync the current
        version's content snapshot to match.

        The substage-output registration flow is a two-step: :meth:`add_artifact`
        creates the artifact (freezing a v1 from the then-current — usually
        content-less — fields), then the caller patches the real storage decision.
        This method is that patch, and it keeps the issue-#302 invariant honest:
        after it returns, ``currentVersionId``'s snapshot equals the top-level
        content, so viewing/restoring the initial version returns the real content.
        """
        task = self._tasks.get(task_id)
        if not task:
            return None
        updated = copy.deepcopy(task)
        artifact = self._find_task_artifact(updated, artifact_id)
        if not artifact:
            return None
        self._normalize_artifact(artifact, updated)
        if storage_type is not None:
            artifact["storageType"] = storage_type
        if inline_content is not None:
            artifact["inlineContent"] = inline_content
        if external_key is not None:
            artifact["externalKey"] = external_key
        if path is not None:
            artifact["path"] = path
        self._sync_current_version_content(artifact)
        updated["updatedAt"] = _utc_now()
        self._tasks[task_id] = updated
        self._persist_task(task_id)
        self._mirror_artifact(task_id, artifact, updated.get("title"))
        return copy.deepcopy(artifact)

    def consume_pending_regenerate(self, task_id: str, substage_instance_id: str | None) -> dict[str, Any] | None:
        """If the task has a pending regeneration for this substage, return it
        (without clearing) so the registration hook can fold the new output, else None.

        The hook calls :meth:`adopt_artifact_as_version` (which clears the marker)
        once it has produced the new content.
        """
        task = self._tasks.get(task_id)
        if not task:
            return None
        pending = task.get("pendingRegenerate")
        if not isinstance(pending, dict):
            return None
        # issue #302 r2: strict match. A regeneration is bound to ONE substage
        # instance, so require an explicit, equal subStageInstanceId on both sides.
        # This prevents an unrelated fallback output (or one carrying no substage
        # context) from being folded into the armed artifact.
        pending_sub = pending.get("subStageInstanceId")
        if not substage_instance_id or not pending_sub or substage_instance_id != pending_sub:
            return None
        return copy.deepcopy(pending)

    def get_artifact(self, task_id: str, artifact_id: str) -> dict[str, Any] | None:
        """Return a normalized deep copy of an artifact by id (or None)."""
        task = self._tasks.get(task_id)
        if not task:
            return None
        artifact = self._find_task_artifact(task, artifact_id)
        if not artifact:
            return None
        result = copy.deepcopy(artifact)
        self._normalize_artifact(result, task)
        return result

    def confirm_artifact_scope_expansion(
        self,
        task_id: str,
        artifact_id: str,
        *,
        reviewer: str,
        note: str | None = None,
        source: str = "user",
    ) -> dict[str, Any] | None:
        task = self._tasks.get(task_id)
        if not task:
            return None
        updated = copy.deepcopy(task)
        confirmed_at = _utc_now()
        for artifact in updated.get("artifacts") or []:
            if artifact.get("id") != artifact_id:
                continue
            apply_artifact_gate(artifact)
            gate = artifact.get("humanGate") or {}
            if not gate.get("requiresScopeExpansionConfirmation"):
                raise ValueError(f"artifact state {artifact.get('artifactState')} does not require scope expansion confirmation")
            artifact["scopeExpansionConfirmedAt"] = confirmed_at
            artifact["scopeExpansionConfirmedBy"] = reviewer
            artifact["scopeExpansionReviewSource"] = source
            if note:
                artifact["scopeExpansionNote"] = note
            artifact["artifactState"] = "needs_input"
            artifact["missingInputs"] = ["expanded_scope_materials_or_execution"]
            artifact["validatorIssues"] = [
                {
                    "code": "scope_expansion_confirmed",
                    "severity": "warning",
                    "message": "Scope expansion was confirmed; additional materials or execution are required before downstream use.",
                }
            ]
            trust_records = list(artifact.get("trustRecords") or [])
            trust_records.append(
                {
                    "id": f"trust_{uuid4().hex[:12]}",
                    "name": "Scope expansion confirmation",
                    "category": "human_review",
                    "executionStatus": "input_recorded",
                    "trustContribution": "needs_confirmation",
                    "usedFor": "scope_expansion_gate",
                    "limitation": note or "User confirmed scope expansion without additional notes.",
                    "nextAction": "Continue with expanded scope materials or execution before using this artifact downstream.",
                    "timestamp": confirmed_at,
                    "relatedArtifactIds": [artifact_id],
                    "sourceLabel": "User review" if source == "user" else "Review agent",
                }
            )
            artifact["trustRecords"] = trust_records
            artifact["userFacingNarrative"] = build_review_agent_narrative(
                artifact_title=artifact.get("title"),
                decision="needs_input",
                note=note or "Scope expansion confirmed. Continue with additional materials or execution before downstream use.",
                source_id=artifact_id,
            )
            apply_artifact_gate(artifact)
            if artifact.get("sourceCapabilityCallId"):
                for call in updated.get("capabilityCalls") or []:
                    if call.get("id") == artifact.get("sourceCapabilityCallId"):
                        append_validator_result_to_call(call, artifact["validatorResult"])
                        break
            updated["updatedAt"] = confirmed_at
            self._tasks[task_id] = updated
            self._persist_task(task_id)
            self._mirror_artifact(task_id, artifact, updated.get("title"))
            self._emit_task_event(
                task_id,
                "task_updated",
                {
                    "artifactId": artifact_id,
                    "artifactState": artifact.get("artifactState"),
                    "reason": "artifact_scope_expansion_confirmed",
                },
            )
            return copy.deepcopy(artifact)
        return None

    def get_agent_messages(self, task_id: str) -> list[dict[str, Any]]:
        from app.config import settings

        if settings.persistence_backend == "postgres":
            task = TaskRepository().get_task(task_id)
            return copy.deepcopy(task.get("agentMessages") or []) if task else []
        task = self._tasks.get(task_id)
        if not task:
            return []
        return copy.deepcopy(task.get("agentMessages") or [])

    def append_agent_message(
        self,
        task_id: str,
        *,
        role: str,
        text: str,
        runtime: str | None = None,
        message_type: str = "chat",
        author_label: str | None = None,
        capability_call_id: str | None = None,
        artifact_refs: list[str] | None = None,
        confirmation_requests: list[dict[str, Any]] | None = None,
        user_facing_narrative: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        task = self._tasks.get(task_id)
        if not task:
            return None
        updated = copy.deepcopy(task)
        msg = {
            "id": f"msg_{uuid4().hex[:12]}",
            "role": role,
            "text": text,
            "at": _utc_now(),
            "runtime": runtime,
            "messageType": message_type,
            "authorLabel": author_label,
            "capabilityCallId": capability_call_id,
            "artifactRefs": list(artifact_refs or []),
            "confirmationRequests": copy.deepcopy(confirmation_requests or []),
            "userFacingNarrative": copy.deepcopy(user_facing_narrative) if user_facing_narrative else None,
        }
        self._normalize_agent_message(msg)
        updated.setdefault("agentMessages", []).append(msg)
        updated["updatedAt"] = _utc_now()
        self._tasks[task_id] = updated
        self._persist_task(task_id)
        self._emit_task_event(
            task_id,
            "agent_message_appended",
            {"messageId": msg["id"], "role": role, "reason": "agent_message_appended"},
        )
        return copy.deepcopy(msg)

    def resolve_confirmation_request(
        self,
        task_id: str,
        confirmation_id: str,
        *,
        action: str,
        note: str | None = None,
        selected_option: str | None = None,
    ) -> dict[str, Any] | None:
        task = self._tasks.get(task_id)
        if not task:
            return None
        updated = copy.deepcopy(task)
        for message in updated.get("agentMessages") or []:
            self._normalize_agent_message(message)
            for req in message.get("confirmationRequests") or []:
                if req.get("id") != confirmation_id:
                    continue
                req["status"] = "resolved"
                req["resolvedAction"] = action
                req["resolvedAt"] = _utc_now()
                req["note"] = note
                req["selectedOption"] = selected_option
                updated["updatedAt"] = _utc_now()
                self._tasks[task_id] = updated
                self._persist_task(task_id)
                self._emit_task_event(
                    task_id,
                    "confirmation_resolved",
                    {"confirmationId": confirmation_id, "action": action, "reason": "confirmation_resolved"},
                )
                return copy.deepcopy(req)
        return None

    def get_agent_session_id(self, task_id: str) -> str | None:
        from app.config import settings

        if settings.persistence_backend == "postgres":
            task = TaskRepository().get_task(task_id)
            return task.get("agentSessionId") if task else None
        task = self._tasks.get(task_id)
        if not task:
            return None
        return task.get("agentSessionId")

    def set_agent_session_id(self, task_id: str, session_id: str | None) -> None:
        task = self._tasks.get(task_id)
        if not task:
            return
        updated = copy.deepcopy(task)
        if session_id:
            updated["agentSessionId"] = session_id
        else:
            updated.pop("agentSessionId", None)
        self._tasks[task_id] = updated
        self._persist_task(task_id)
        self._emit_task_event(task_id, "task_updated", {"reason": "agent_session_updated"})

    def add_capability_call(
        self,
        task_id: str,
        *,
        skill_id: str,
        status: str,
        runtime: str = AGENT_RUNTIME_CLAUDE_CODE,
        result_summary: str | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
        stage_key: str | None = None,
        substage_instance_id: str | None = None,
        input_artifact_refs: list[str] | None = None,
        output_artifact_refs: list[str] | None = None,
        input_call_refs: list[str] | None = None,
        skill_hash: str | None = None,
    ) -> dict[str, Any] | None:
        task = self._tasks.get(task_id)
        if not task:
            return None
        updated = copy.deepcopy(task)
        now = _utc_now()
        run_mode = _run_mode_for_runtime(runtime)
        source_mode = (
            "inline"
            if runtime in {"python_adapter", "system_guard", "specialist_agent_runtime_gateway"}
            else run_mode
        )
        call = {
            "id": f"call_{uuid4().hex[:12]}",
            "ownerId": updated.get("ownerId", DEFAULT_OWNER_ID),
            "workspaceId": updated.get("workspaceId", DEFAULT_WORKSPACE_ID),
            "createdBy": updated.get("createdBy", DEFAULT_CREATED_BY),
            "kind": "in_app_agent",
            "name": skill_id,
            "status": status,
            "requestedAt": started_at or now,
            "finishedAt": ended_at,
            "resultSummary": result_summary,
            "runtime": runtime,
            "provider": None,
            "adapter": "python_adapter" if runtime == "python_adapter" else None,
            "model": None,
            "artifactRefs": [],
            "stageKey": stage_key,
            "subStageInstanceId": substage_instance_id,
            "inputArtifactRefs": list(input_artifact_refs or []),
            "outputArtifactRefs": list(output_artifact_refs or []),
            "prevCallId": updated.get("lastCallId") or None,
            "inputCallRefs": list(input_call_refs or []),
            "runId": f"run_{uuid4().hex[:12]}",
            "runMode": run_mode,
            "sourceMode": source_mode,
            "sourceMetadata": {},
            "skillHash": skill_hash,
        }
        updated.setdefault("capabilityCalls", []).append(call)
        if substage_instance_id:
            for detail in updated.get("stageDetails") or []:
                self._normalize_stage_detail(detail)
                sub = self._find_substage(detail, substage_instance_id)
                if sub:
                    refs = list(sub.get("capabilityCallRefs") or [])
                    refs.append(call["id"])
                    sub["capabilityCallRefs"] = list(dict.fromkeys(refs))
                    break
        updated["lastCallId"] = call["id"]
        updated["updatedAt"] = now
        self._tasks[task_id] = updated
        self._persist_task(task_id)
        self._emit_task_event(
            task_id,
            "capability_call_started",
            {"callId": call["id"], "skillId": skill_id, "status": status, "reason": "capability_call_started"},
        )
        return copy.deepcopy(call)

    def patch_capability_call(
        self,
        task_id: str,
        call_id: str,
        *,
        status: str,
        result_summary: str | None = None,
        ended_at: str | None = None,
        artifact_refs: list[str] | None = None,
        input_artifact_refs: list[str] | None = None,
        output_artifact_refs: list[str] | None = None,
        substage_instance_id: str | None = None,
        stage_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        task = self._tasks.get(task_id)
        if not task:
            return None
        updated = copy.deepcopy(task)
        calls = updated.get("capabilityCalls") or []
        hit = next((c for c in calls if c.get("id") == call_id), None)
        if not hit:
            return None
        hit["status"] = status
        if result_summary is not None:
            hit["resultSummary"] = (
                result_summary
                if isinstance(result_summary, str)
                else str(result_summary)
            )
        if ended_at is not None:
            hit["finishedAt"] = ended_at
        if artifact_refs is not None:
            hit["artifactRefs"] = list(dict.fromkeys(artifact_refs))
        if input_artifact_refs is not None:
            hit["inputArtifactRefs"] = list(dict.fromkeys(input_artifact_refs))
        if output_artifact_refs is not None:
            hit["outputArtifactRefs"] = list(dict.fromkeys(output_artifact_refs))
        if substage_instance_id is not None:
            hit["subStageInstanceId"] = substage_instance_id
        if stage_key is not None:
            hit["stageKey"] = stage_key
        if metadata is not None:
            for key, value in metadata.items():
                if key not in _CAPABILITY_CALL_METADATA_PROTECTED_KEYS:
                    hit[key] = copy.deepcopy(value)
        refreshed_artifacts = self._refresh_artifact_gates_for_capability_call(updated, hit)
        _normalize_task_result_source(updated)
        updated["updatedAt"] = _utc_now()
        self._tasks[task_id] = updated
        self._persist_task(task_id)
        for artifact in refreshed_artifacts:
            self._mirror_artifact(task_id, artifact, updated.get("title"))
        self._emit_task_event(
            task_id,
            "capability_call_updated",
            {"callId": call_id, "status": status, "reason": "capability_call_updated"},
        )
        return copy.deepcopy(hit)


store = TaskStore()
