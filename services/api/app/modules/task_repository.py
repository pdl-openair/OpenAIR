from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Mapping
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from app.config import settings
from app.execution.durable_orchestrator_bridge import FrozenPlanError, precompile_creation_plan
from app.execution.durable_skill_runtime import list_registered_durable_skills
from app.modules.agent_orchestrator import ensure_orchestrator_decision
from app.modules.agent_registry import load_agent_registry
from app.modules.capability_resolver import CapabilityResolutionError, resolve_creation_plan_capabilities
from app.modules.task_message_repository import TaskMessageRepository
from app.modules.research_progress_projection import stamp_progress_projection
from app.modules.research_task_lifecycle import (
    ResearchStartLifecycleFact,
    UserTaskLifecycleFact,
)
from app.modules.research_task_lifecycle_repository import (
    TaskLifecycleWriteConflictError,
    commit_task_lifecycle_mutation_locked,
)
from app.modules.task_title_metadata import (
    disable_automatic_title,
    sync_artifact_source_task_titles,
)
from app.modules.task_tombstone_repository import (
    TASK_TOMBSTONE_STATUS,
    commit_task_tombstone_locked,
)
from app.modules.substage_identity import canonical_substage_key
from app.modules import pack_config

TASKS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS openair_tasks (
    task_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    status TEXT NOT NULL,
    current_stage TEXT NOT NULL,
    title TEXT NOT NULL,
    domain TEXT NULL,
    created_at TIMESTAMPTZ NULL,
    updated_at TIMESTAMPTZ NULL,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_openair_tasks_status ON openair_tasks(status);
CREATE INDEX IF NOT EXISTS idx_openair_tasks_updated_at ON openair_tasks(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_openair_tasks_scope ON openair_tasks(owner_id, workspace_id);
"""

INSERT_TASK_SQL = """
INSERT INTO openair_tasks (
    task_id,
    owner_id,
    workspace_id,
    status,
    current_stage,
    title,
    domain,
    created_at,
    updated_at,
    payload
) VALUES (
    %(task_id)s,
    %(owner_id)s,
    %(workspace_id)s,
    %(status)s,
    %(current_stage)s,
    %(title)s,
    %(domain)s,
    %(created_at)s,
    %(updated_at)s,
    %(payload)s::jsonb
);
"""

DELETE_TASK_SQL = "DELETE FROM openair_tasks WHERE task_id = %s"
TASK_DELETED_STATUS = TASK_TOMBSTONE_STATUS
SELECT_ALL_TASKS_SQL = """
SELECT task_id, owner_id, workspace_id, status, current_stage, title,
       created_at, updated_at, payload, revision
FROM openair_tasks
WHERE status <> 'deleted'
ORDER BY updated_at DESC NULLS LAST, task_id ASC
"""

SELECT_TASKS_BY_SCOPE_SQL = """
SELECT task_id, owner_id, workspace_id, status, current_stage, title,
       created_at, updated_at, payload, revision
FROM openair_tasks
WHERE status <> 'deleted'
  AND owner_id = %(owner_id)s
  AND workspace_id = %(workspace_id)s
ORDER BY updated_at DESC NULLS LAST, task_id ASC
"""

# Board/list path: never transfer the full JSONB payload (designs/calls/etc.).
# Pull only scalar/list-card fields via jsonb operators + table columns.
SELECT_TASKS_BY_SCOPE_SLIM_SQL = """
SELECT task_id,
       owner_id,
       workspace_id,
       status,
       current_stage,
       title,
       created_at,
       updated_at,
       revision,
       COALESCE(domain, NULLIF(payload->>'domain', '')) AS domain,
       NULLIF(payload->>'goalSummary', '') AS goal_summary,
       NULLIF(payload->>'skillPack', '') AS skill_pack,
       NULLIF(payload->>'creationStatus', '') AS creation_status,
       NULLIF(payload->>'executionMode', '') AS execution_mode,
       NULLIF(payload->>'resultSource', '') AS result_source,
       NULLIF(payload->>'createdBy', '') AS created_by,
       CASE
         WHEN jsonb_typeof(payload->'progressProjection') = 'object'
         THEN payload->'progressProjection'
         ELSE NULL
       END AS progress_projection
FROM openair_tasks
WHERE status <> 'deleted'
  AND owner_id = %(owner_id)s
  AND workspace_id = %(workspace_id)s
ORDER BY updated_at DESC NULLS LAST, task_id ASC
"""

# Title picker (artifact library upload/filter): columns only, no payload access.
SELECT_TASK_TITLE_OPTIONS_SQL = """
SELECT task_id, title
FROM openair_tasks
WHERE status <> 'deleted'
  AND owner_id = %(owner_id)s
  AND workspace_id = %(workspace_id)s
ORDER BY updated_at DESC NULLS LAST, task_id ASC
"""

# ON DELETE RESTRICT dependents must be removed before openair_tasks.  CASCADE
# children (runs, policies, stream events, controller commands, …) follow the
# task row automatically.  Statements are skipped when the table is absent so
# older local schemas still allow draft deletion.
_TASK_DELETE_DEPENDENT_STATEMENTS: tuple[tuple[str, str], ...] = (
    ("openair_external_reconciliation_evidence", """
        DELETE FROM openair_external_reconciliation_evidence
         WHERE command_request_id IN (
               SELECT command_request_id FROM openair_external_reconciliation WHERE task_id = %s)
            OR evidence_id IN (
               SELECT evidence_id FROM openair_external_evidence WHERE task_id = %s)
        """),
    ("openair_recovery_directive", "DELETE FROM openair_recovery_directive WHERE task_id = %s"),
    ("openair_external_evidence_access_audit", """
        DELETE FROM openair_external_evidence_access_audit
         WHERE evidence_id IN (
               SELECT evidence_id FROM openair_external_evidence WHERE task_id = %s)
        """),
    ("openair_external_retry_attempt", "DELETE FROM openair_external_retry_attempt WHERE task_id = %s"),
    ("openair_external_reconciliation", "DELETE FROM openair_external_reconciliation WHERE task_id = %s"),
    ("openair_external_evidence", "DELETE FROM openair_external_evidence WHERE task_id = %s"),
    ("openair_external_execution", "DELETE FROM openair_external_execution WHERE task_id = %s"),
    ("openair_recovery_request_messages", "DELETE FROM openair_recovery_request_messages WHERE task_id = %s"),
    ("openair_recovery_execution_projections", "DELETE FROM openair_recovery_execution_projections WHERE task_id = %s"),
    ("openair_recovery_messages", "DELETE FROM openair_recovery_messages WHERE task_id = %s"),
    ("openair_task_recovery_requests", "DELETE FROM openair_task_recovery_requests WHERE task_id = %s"),
    ("openair_artifact_lineage_audit", "DELETE FROM openair_artifact_lineage_audit WHERE task_id = %s"),
    ("openair_artifact_lineage", "DELETE FROM openair_artifact_lineage WHERE task_id = %s"),
    ("openair_capability_call_lineage", "DELETE FROM openair_capability_call_lineage WHERE task_id = %s"),
    ("openair_work_unit_lineage", "DELETE FROM openair_work_unit_lineage WHERE task_id = %s"),
    ("openair_execution_selection_request", "DELETE FROM openair_execution_selection_request WHERE task_id = %s"),
    ("openair_dispatch", "DELETE FROM openair_dispatch WHERE task_id = %s"),
    ("openair_artifacts", "DELETE FROM openair_artifacts WHERE source_task_id = %s"),
)

EDITABLE_STAGE_ORDER = [
    "problem_modeling",
    "solution_design",
    "execution_validation",
    "insight_generation",
]

GENERAL_RESEARCH_AGENT_ID = "general_research_agent"


def _iso_timestamp(value: Any) -> str | None:
    """Normalize TIMESTAMPTZ / ISO strings for public TaskOut fields."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    text = str(value).strip()
    return text or None


def _with_research_design_compatibility_projection(task: dict[str, Any]) -> dict[str, Any]:
    if not task.get("researchDesignVersions"):
        return task
    from app.models.research_orchestration import ResearchTaskV12
    from app.modules.research_orchestration_repository import (
        _extract_contract_payload,
        _research_design_compatibility_projection,
    )

    canonical = ResearchTaskV12.model_validate(_extract_contract_payload(task))
    return {**task, **_research_design_compatibility_projection(canonical)}


def _with_read_only_legacy_authority_projection(task: dict[str, Any]) -> dict[str, Any]:
    """Adapt a persisted historical authority for the dedicated public GET read."""

    if not task.get("researchDesignVersions"):
        return task
    from app.models.research_orchestration import (
        READ_ONLY_LEGACY_FINAL_RESULT_AUTHORITY,
        ResearchTaskV12,
    )
    from app.modules.research_orchestration_repository import (
        _extract_contract_payload,
        _research_design_compatibility_projection,
    )

    source = deepcopy(task)
    authority = source.get("finalResultAuthority")
    legacy_read = isinstance(authority, dict) and "integrityContractVersion" not in authority
    if legacy_read:
        authority["integrityContractVersion"] = "legacy_v0"
    canonical = ResearchTaskV12.model_validate(
        _extract_contract_payload(source),
        context={READ_ONLY_LEGACY_FINAL_RESULT_AUTHORITY: legacy_read},
    )
    return {**task, **_research_design_compatibility_projection(canonical)}


class TaskPlanValidationError(ValueError):
    """A public plan edit does not select an enabled executable path."""


class TaskPlanCapabilityError(TaskPlanValidationError):
    """A valid catalog substage has no registered executable route."""


def _validated_capability_requests(plan: dict[str, Any], *, pack: str) -> list[dict[str, Any]]:
    """Resolve every selected substage from current pack and Agent authority.

    This is deliberately derived from the candidate's stages, never copied from
    an earlier plan's selectedCapabilities.  Reading registries is deterministic
    and does not create a run, invoke a model, or dispatch a Skill.
    """
    enabled_by_stage: dict[str, set[str]] = {}
    owner_by_substage: dict[str, str] = {}
    disabled: set[str] = set()
    for stage_key, entries in pack_config.load_stage_substage_map(pack=pack).items():
        enabled_by_stage[stage_key] = set()
        for entry in entries:
            key = str(entry.get("key") or "").strip()
            if not key:
                continue
            owner_by_substage[key] = stage_key
            if entry.get("enabled", True):
                enabled_by_stage[stage_key].add(key)
            else:
                disabled.add(key)

    registry = load_agent_registry()
    skills = list_registered_durable_skills()
    requests: list[dict[str, Any]] = []
    for stage in plan.get("stages") or []:
        stage_key = str(stage.get("stageKey") or "").strip()
        for substage in stage.get("substages") or []:
            substage_key = str(substage.get("substage") or "").strip()
            owner_stage = owner_by_substage.get(substage_key)
            if owner_stage is None:
                raise TaskPlanValidationError(f"substage {substage_key} is not available in pack {pack}")
            if substage_key in disabled:
                raise TaskPlanValidationError(f"substage {substage_key} is disabled in pack {pack}")
            if owner_stage != stage_key:
                raise TaskPlanValidationError(
                    f"substage {substage_key} belongs to {owner_stage}, not {stage_key}"
                )
            if substage_key not in enabled_by_stage.get(stage_key, set()):
                raise TaskPlanValidationError(f"substage {substage_key} is not enabled in pack {pack}")

            # An exact Agent mapping owns the action when one exists. Otherwise
            # resolve the action directly to durable Skills by canonical
            # Substage. Pack metadata is classification, never eligibility.
            agents = [
                agent for agent in registry.by_sdr_substage(stage_key, substage_key)
                if agent.agentId != GENERAL_RESEARCH_AGENT_ID and registry.is_callable(agent.agentId)
            ]
            if agents:
                agent = sorted(agents, key=lambda item: item.agentId)[0]
                requests.append({
                    "kind": "agent",
                    "id": agent.agentId,
                    "requestedCapability": agent.agentId,
                    "substages": [substage_key],
                    "rationale": str(substage.get("action") or substage_key),
                    "roleBrief": f"Registered {stage_key} specialist route.",
                })
                continue

            canonical_substage = canonical_substage_key(substage_key)
            matching_skills = sorted(
                {
                    str(skill.get("skillId") or "").strip()
                    for skill in skills
                    if skill.get("durableExecutable")
                    and canonical_substage
                    in {
                        canonical_substage_key(value)
                        for value in skill.get("substages") or []
                    }
                    and str(skill.get("skillId") or "").strip()
                }
            )
            if not matching_skills:
                raise TaskPlanCapabilityError(
                    f"required_capability_unavailable:{stage_key}/{substage_key}"
                )
            requests.append({
                "kind": "skill",
                "id": matching_skills[0],
                "skillIds": matching_skills,
                "requestedCapability": substage_key,
                "substages": [substage_key],
                "rationale": str(substage.get("action") or substage_key),
                "roleBrief": f"Registered canonical Substage Skill route for {stage_key}.",
            })
    if not requests:
        raise TaskPlanValidationError("creationPlan must include at least one substage")
    return requests


def prepare_task_plan_candidate(
    task: dict[str, Any],
    editable: dict[str, Any],
    *,
    updated_at: str,
) -> dict[str, Any]:
    """Build, resolve, and compile the exact draft projection to persist.

    The caller must persist this returned object unchanged.  All work here is
    in-memory registry lookup and deterministic compilation only.
    """
    candidate = build_task_plan_projection(task, editable, updated_at=updated_at)
    plan = candidate["creationPlan"]
    pack = str(task.get("skillPack") or plan.get("skillPack") or "sdr")
    plan["skillPack"] = pack
    plan["selectedCapabilities"] = _validated_capability_requests(plan, pack=pack)
    try:
        plan = resolve_creation_plan_capabilities(plan, skill_pack=pack)
        plan = ensure_orchestrator_decision(
            plan,
            user_goal=str(plan.get("goalSummary") or task.get("goalSummary") or ""),
            task_status="draft",
        )
        precompile_creation_plan(
            creation_plan=plan,
            goal_summary=str(plan.get("goalSummary") or task.get("goalSummary") or ""),
        )
    except (CapabilityResolutionError, FrozenPlanError) as exc:
        raise TaskPlanCapabilityError(str(exc)) from exc
    candidate["creationPlan"] = plan
    return candidate


def _stable_substage_instance_id(task_id: str, stage_key: str, substage_key: str) -> str:
    value = f"{task_id}:{stage_key}:{substage_key}".encode("utf-8")
    return f"sub_{hashlib.sha256(value).hexdigest()[:12]}"


def _repair_research_start_draft(task: dict[str, Any], artifact: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    goal = str(plan.get("goalSummary") or task.get("goalSummary") or artifact.get("summary") or task.get("title") or "Research start").strip()
    understanding = plan.get("understanding") if isinstance(plan.get("understanding"), dict) else {}
    plan_boundaries = [
        str(value).strip()
        for value in understanding.get("evidenceBoundaries") or []
        if str(value).strip()
    ]
    if isinstance(artifact.get("structuredResearchStart"), dict):
        repaired = deepcopy(artifact)
        structured = deepcopy(repaired["structuredResearchStart"])
        boundaries = plan_boundaries or [
            str(value).strip()
            for value in structured.get("non_citable_boundaries") or []
            if str(value).strip()
        ]
        structured["research_goal"] = goal
        structured["non_citable_boundaries"] = boundaries
        repaired["structuredResearchStart"] = structured
        repaired["nonCitableBoundaries"] = boundaries
        downstream = repaired.get("downstreamInput")
        if isinstance(downstream, dict):
            repaired["downstreamInput"] = {
                **downstream,
                "nonCitableBoundaries": boundaries,
            }
        return repaired
    missing = [str(value).strip() for value in artifact.get("missingInputs") or plan.get("clarifications") or [] if str(value).strip()]
    boundaries = plan_boundaries or [str(value).strip() for value in artifact.get("nonCitableBoundaries") or [] if str(value).strip()] or [
        "No external papers, data, or code have been reviewed; this draft is not a citable research conclusion."
    ]
    structured = {
        "research_goal": goal,
        "comparison_targets": [],
        "existing_materials": [f"User-provided research request: {goal}"],
        "missing_materials": missing,
        "non_citable_boundaries": boundaries,
        "recommended_next_actions": ["Review and confirm the research start before proceeding."],
        "risks": ["This legacy draft was migrated without verified external research materials."],
        "user_confirmation_status": {
            "status": "unconfirmed",
            "confirmed_items": [],
            "missing_materials_decision": None,
            "limitations_accepted": False,
            "confirmed_at": None,
        },
        "accepted_limitations": [],
        "source_or_run_refs": [{"artifact_id": artifact.get("id"), "source": "legacy_draft_migration"}],
    }
    repaired = deepcopy(artifact)
    repaired.update({
        "artifactState": "needs_input" if missing else "needs_review",
        "structuredResearchStart": structured,
        "researchStartConfirmation": structured["user_confirmation_status"],
        "missingInputs": missing,
        "nonCitableBoundaries": boundaries,
        "downstreamInput": {
            **(artifact.get("downstreamInput") if isinstance(artifact.get("downstreamInput"), dict) else {}),
            "status": "blocked_by_confirmation",
            "inherits": [],
            "missingMaterials": missing,
            "nonCitableBoundaries": boundaries,
        },
    })
    return repaired


def build_task_plan_projection(
    task: dict[str, Any],
    editable: dict[str, Any],
    *,
    updated_at: str,
) -> dict[str, Any]:
    """Purely build the complete task projection for one draft-plan CAS write."""
    updated = deepcopy(task)
    current_plan = deepcopy(task.get("creationPlan") or {})
    current_stages = {
        stage.get("stageKey"): stage
        for stage in current_plan.get("stages") or []
        if isinstance(stage, dict)
    }
    projected_stages: list[dict[str, Any]] = []
    for stage_edit in editable["stages"]:
        stage_key = stage_edit["stageKey"]
        stage = deepcopy(current_stages.get(stage_key) or {})
        current_substages = {
            row.get("substage"): row
            for row in stage.get("substages") or []
            if isinstance(row, dict)
        }
        substages: list[dict[str, Any]] = []
        for substage_edit in stage_edit["substages"]:
            substage_key = substage_edit["substage"]
            substage = deepcopy(current_substages.get(substage_key) or {})
            substage.update(substage_edit)
            substages.append(substage)
        stage.update({
            "stageKey": stage_key,
            "title": stage_edit["title"],
            "description": stage_edit["description"],
            "substages": substages,
            "subStages": [row["substage"] for row in substages],
        })
        projected_stages.append(stage)

    understanding = deepcopy(current_plan.get("understanding") or {})
    understanding["evidenceBoundaries"] = deepcopy(editable["understanding"]["evidenceBoundaries"])
    current_plan.update({
        "goalSummary": editable["goalSummary"],
        "understanding": understanding,
        "clarifications": deepcopy(editable["clarifications"]),
        "stages": projected_stages,
        "readyToConfirm": True,
    })

    existing_details = {
        row.get("stageKey"): row
        for row in task.get("stageDetails") or []
        if isinstance(row, dict)
    }
    existing_catalog = {
        row.get("stageKey"): row
        for row in task.get("stageCatalog") or []
        if isinstance(row, dict)
    }
    stage_details: list[dict[str, Any]] = []
    stage_catalog: list[dict[str, Any]] = []
    for stage in projected_stages:
        stage_key = stage["stageKey"]
        detail = deepcopy(existing_details.get(stage_key) or {})
        existing_instances = {
            row.get("subStage"): row
            for row in detail.get("subStages") or []
            if isinstance(row, dict)
        }
        instances: list[dict[str, Any]] = []
        for sequence, substage in enumerate(stage["substages"]):
            substage_key = substage["substage"]
            instance = deepcopy(existing_instances.get(substage_key) or {})
            instance.update({
                "subStage": substage_key,
                "status": instance.get("status") or "not_started",
                "artifactRefs": deepcopy(instance.get("artifactRefs") or []),
                "capabilityCallRefs": deepcopy(instance.get("capabilityCallRefs") or []),
                "subStageInstanceId": instance.get("subStageInstanceId") or _stable_substage_instance_id(str(task.get("taskId") or "task"), stage_key, substage_key),
                "parentStageKey": stage_key,
                "label": substage["label"],
                "action": substage["action"],
                "expectedArtifact": substage["expectedArtifact"],
                "sequence": sequence,
                "summary": instance.get("summary"),
                "l3Plan": instance.get("l3Plan"),
            })
            instances.append(instance)
        detail.update({
            "stageKey": stage_key,
            "label": stage["title"],
            "description": stage["description"],
            "status": detail.get("status") or "not_started",
            "artifactRefs": deepcopy(detail.get("artifactRefs") or []),
            "subStages": instances,
            "userFacingNarrative": detail.get("userFacingNarrative"),
        })
        stage_details.append(detail)

        catalog = deepcopy(existing_catalog.get(stage_key) or {})
        existing_entries = {
            row.get("key"): row
            for row in catalog.get("allowedSubStages") or []
            if isinstance(row, dict)
        }
        allowed: list[dict[str, Any]] = []
        for substage in stage["substages"]:
            entry = deepcopy(existing_entries.get(substage["substage"]) or {})
            entry.update({
                "key": substage["substage"],
                "stageKey": stage_key,
                "label": substage["label"],
                "description": substage["action"],
                "action": substage["action"],
                "expectedArtifact": substage["expectedArtifact"],
                "enabled": True,
            })
            allowed.append(entry)
        catalog.update({"stageKey": stage_key, "allowedSubStages": allowed})
        stage_catalog.append(catalog)

    artifacts = [
        _repair_research_start_draft(updated, artifact, current_plan)
        if artifact.get("kind") == "research_start_draft" or artifact.get("artifactType") == "research_start_draft"
        else deepcopy(artifact)
        for artifact in task.get("artifacts") or []
    ]
    expected_revision = int(editable["expectedRevision"])
    updated.update({
        "creationPlan": current_plan,
        "creationStatus": "awaiting_confirmation",
        "goalSummary": editable["goalSummary"],
        "stages": list(EDITABLE_STAGE_ORDER),
        "stageDetails": stage_details,
        "stageCatalog": stage_catalog,
        "artifacts": artifacts,
        "currentStage": updated.get("currentStage") if updated.get("currentStage") in EDITABLE_STAGE_ORDER else EDITABLE_STAGE_ORDER[0],
        "updatedAt": updated_at,
        "revision": expected_revision + 1,
    })
    return updated


class TaskRepository:
    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection[Any]]:
        # password is passed as a keyword arg, NOT stringified into the conninfo
        # string — so it never appears if the connection params are logged or repr'd.
        conninfo = (
            f"host={settings.postgres_host} port={settings.postgres_port} "
            f"dbname={settings.postgres_database} user={settings.postgres_user}"
        )
        # dict_row vs TupleRow is a known psycopg type-stub limitation; password stays a kwarg.
        conn = psycopg.connect(conninfo, password=settings.postgres_password, row_factory=dict_row)  # type: ignore[arg-type]
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def bootstrap(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(TASKS_TABLE_SQL)

    def load_all_tasks(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(SELECT_ALL_TASKS_SQL)
                rows = cur.fetchall()
        return self._project_task_rows(rows)

    def list_legacy_completed_without_final_authority(self) -> list[dict[str, Any]]:
        """Return raw pre-invariant rows for the explicit final-result backfill only."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT task_id, status, revision, payload
                         FROM openair_tasks
                        WHERE status = 'completed'
                          AND (
                              payload->'finalResultAuthority' IS NULL
                              OR payload->'finalResultAuthority' = 'null'::jsonb
                          )
                     ORDER BY task_id"""
                )
                rows = cur.fetchall()
        return [
            {
                **row,
                "payload": row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"]),
            }
            for row in rows
        ]

    def persist_legacy_final_result_backfill(
        self,
        *,
        task_id: str,
        expected_revision: int,
        payload: dict[str, Any],
    ) -> int:
        """CAS-write one explicit legacy authority backfill, never a normal task update."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE openair_tasks
                          SET updated_at = NOW(), revision = %s, payload = %s::jsonb
                        WHERE task_id = %s
                          AND status = 'completed'
                          AND revision = %s
                          AND (
                              payload->'finalResultAuthority' IS NULL
                              OR payload->'finalResultAuthority' = 'null'::jsonb
                          )
                    RETURNING revision""",
                    (
                        expected_revision + 1,
                        json.dumps(payload, ensure_ascii=False),
                        task_id,
                        expected_revision,
                    ),
                )
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("legacy_final_result_backfill_conflict")
                return int(row["revision"])

    def list_completed_with_stale_progress_projection(self) -> list[dict[str, Any]]:
        """Return only completed authority rows needing the explicit projection repair."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT task_id, status, revision, payload
                         FROM openair_tasks
                        WHERE status = 'completed'
                          AND jsonb_typeof(payload->'finalResultAuthority') = 'object'
                          AND (
                              jsonb_typeof(payload->'progressProjection') IS DISTINCT FROM 'object'
                              OR payload->'progressProjection'->>'revision' IS DISTINCT FROM revision::text
                          )
                     ORDER BY task_id"""
                )
                rows = cur.fetchall()
        return [
            {**row, "payload": row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])}
            for row in rows
        ]

    def persist_progress_projection_repair(
        self, *, task_id: str, expected_revision: int, payload: dict[str, Any]
    ) -> int:
        """CAS-write a projection-only repair at one new, matching revision."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE openair_tasks
                          SET updated_at = NOW(), revision = %s, payload = %s::jsonb
                        WHERE task_id = %s
                          AND status = 'completed'
                          AND revision = %s
                          AND jsonb_typeof(payload->'finalResultAuthority') = 'object'
                          AND (
                              jsonb_typeof(payload->'progressProjection') IS DISTINCT FROM 'object'
                              OR payload->'progressProjection'->>'revision' IS DISTINCT FROM revision::text
                          )
                    RETURNING revision""",
                    (
                        expected_revision + 1,
                        json.dumps(payload, ensure_ascii=False),
                        task_id,
                        expected_revision,
                    ),
                )
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("completed_progress_projection_repair_conflict")
                return int(row["revision"])

    def list_tasks_by_scope(
        self, owner_id: str, workspace_id: str, *,
        limit: int | None = None, offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Owner-scoped task listing.

        Pushes the multi-tenant filter into SQL (uses idx_openair_tasks_scope)
        so we decompress/project only the caller's own tasks instead of every
        task across all tenants. Response shape is identical to load_all_tasks.
        ``limit``/``offset`` paginate the result (D2); ``limit=None`` returns
        all rows (backward-compatible default).
        """
        params: dict[str, Any] = {"owner_id": owner_id, "workspace_id": workspace_id}
        sql = SELECT_TASKS_BY_SCOPE_SQL
        if limit is not None:
            sql += " LIMIT %(limit)s"
            params["limit"] = limit
        if offset:
            sql += " OFFSET %(offset)s"
            params["offset"] = offset
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        return self._project_task_rows(rows)

    def list_tasks_by_scope_slim(
        self, owner_id: str, workspace_id: str, *,
        limit: int | None = None, offset: int = 0, statuses: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Owner-scoped board/list rows without loading full task JSONB payloads.

        Extracts only list-card fields in SQL. Skips deepcopy and research-design
        compatibility projection used by the full-row path.
        """
        params: dict[str, Any] = {"owner_id": owner_id, "workspace_id": workspace_id}
        sql = SELECT_TASKS_BY_SCOPE_SLIM_SQL
        if statuses:
            sql = sql.replace(
                "ORDER BY updated_at DESC NULLS LAST, task_id ASC",
                "AND status = ANY(%(statuses)s)\nORDER BY updated_at DESC NULLS LAST, task_id ASC",
            )
            params["statuses"] = statuses
        if limit is not None:
            sql += " LIMIT %(limit)s"
            params["limit"] = limit
        if offset:
            sql += " OFFSET %(offset)s"
            params["offset"] = offset
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        return self._project_slim_list_rows(rows)

    def list_task_title_options(
        self, owner_id: str, workspace_id: str, *,
        limit: int = 50, offset: int = 0,
    ) -> list[dict[str, str]]:
        """Minimal ``{taskId, title}`` rows for pickers (no payload read)."""
        params: dict[str, Any] = {
            "owner_id": owner_id,
            "workspace_id": workspace_id,
            "limit": limit,
            "offset": offset,
        }
        sql = SELECT_TASK_TITLE_OPTIONS_SQL + " LIMIT %(limit)s"
        if offset:
            sql += " OFFSET %(offset)s"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        return [
            {
                "taskId": str(row.get("task_id") or ""),
                "title": str(row.get("title") or "").strip() or "未命名项目",
            }
            for row in rows
            if row.get("task_id")
        ]

    def count_tasks_by_scope(self, owner_id: str, workspace_id: str, *, statuses: list[str] | None = None) -> int:
        """Total in-scope task count (independent of pagination). Cheap COUNT
        over idx_openair_tasks_scope; used for list pagination totals (D2)."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                query = (
                    "SELECT COUNT(*) AS count FROM openair_tasks "
                    "WHERE status <> 'deleted' AND owner_id = %(owner_id)s AND workspace_id = %(workspace_id)s"
                )
                params: dict[str, Any] = {"owner_id": owner_id, "workspace_id": workspace_id}
                if statuses:
                    query += " AND status = ANY(%(statuses)s)"
                    params["statuses"] = statuses
                cur.execute(query, params)
                return int(cur.fetchone()["count"])

    def count_task_statuses_by_scope(self, owner_id: str, workspace_id: str) -> dict[str, int]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status, COUNT(*) AS count FROM openair_tasks "
                    "WHERE status <> 'deleted' AND owner_id = %s AND workspace_id = %s GROUP BY status",
                    (owner_id, workspace_id),
                )
                return {str(row["status"]): int(row["count"]) for row in cur.fetchall()}

    def _project_slim_list_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        for row in rows:
            owner_id = str(row.get("owner_id") or "unknown-owner")
            progress = row.get("progress_projection")
            if isinstance(progress, str):
                try:
                    progress = json.loads(progress)
                except (TypeError, json.JSONDecodeError):
                    progress = None
            if not isinstance(progress, dict):
                progress = None
            tasks.append(
                {
                    "taskId": str(row.get("task_id") or ""),
                    "ownerId": owner_id,
                    "workspaceId": str(row.get("workspace_id") or ""),
                    "createdBy": str(row.get("created_by") or owner_id),
                    "title": str(row.get("title") or ""),
                    "status": str(row.get("status") or "not_started"),
                    "currentStage": str(row.get("current_stage") or "problem_modeling"),
                    "createdAt": _iso_timestamp(row.get("created_at")),
                    "updatedAt": _iso_timestamp(row.get("updated_at")),
                    "revision": int(row.get("revision") or 0),
                    "domain": row.get("domain"),
                    "goalSummary": row.get("goal_summary"),
                    "skillPack": row.get("skill_pack"),
                    "creationStatus": row.get("creation_status"),
                    "executionMode": row.get("execution_mode") or "stage_confirmation",
                    "resultSource": row.get("result_source") or "none",
                    "progressProjection": progress,
                }
            )
        return tasks

    def _project_task_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        for row in rows:
            payload = row.get("payload")
            if isinstance(payload, dict):
                task = deepcopy(payload)
            elif isinstance(payload, str):
                try:
                    task = json.loads(payload)
                except (TypeError, json.JSONDecodeError):
                    task = {}
            else:
                task = {}
            # Database columns remain the existence/scope authority even when a
            # legacy or corrupt JSON projection cannot be adapted.
            task.setdefault("taskId", row.get("task_id"))
            task.setdefault("ownerId", row.get("owner_id"))
            task.setdefault("workspaceId", row.get("workspace_id"))
            task.setdefault("createdBy", row.get("owner_id"))
            task.setdefault("title", row.get("title"))
            task.setdefault("status", row.get("status"))
            task.setdefault("currentStage", row.get("current_stage"))
            task.setdefault("createdAt", row.get("created_at"))
            task.setdefault("updatedAt", row.get("updated_at"))
            task["revision"] = int(row.get("revision") or 0)
            try:
                task = _with_research_design_compatibility_projection(task)
            except Exception:  # malformed compatibility payload must not erase the durable row
                task["projectionError"] = "task_projection_unavailable"
            from app.modules.task_store import _infer_task_result_source
            task["resultSource"] = _infer_task_result_source(task)
            tasks.append(task)
        return tasks

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Read the authoritative task without TaskStore repair."""
        return self._read_task(task_id, public_read=False)

    def get_task_for_public_read(self, task_id: str) -> dict[str, Any] | None:
        """Read a historical snapshot without enabling commands."""
        return self._read_task(task_id, public_read=True)

    def _read_task(self, task_id: str, *, public_read: bool) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT payload, revision FROM openair_tasks WHERE task_id = %s AND status <> %s",
                    (task_id, TASK_DELETED_STATUS),
                )
                row = cur.fetchone()
                if not row:
                    return None
                from app.modules.research_planning_history import load_planning_thought_history
                thoughts = load_planning_thought_history(cur, task_id) if public_read else []
        payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
        payload = deepcopy(payload)
        payload["revision"] = int(row["revision"])
        adapt = (
            _with_read_only_legacy_authority_projection
            if public_read
            else _with_research_design_compatibility_projection
        )
        projected = adapt(payload)
        from app.modules.task_store import _infer_task_result_source
        projected["resultSource"] = _infer_task_result_source(projected)
        if public_read:
            projected["planningThoughtHistory"] = thoughts
        return projected

    @staticmethod
    def _insert_task_params(task: dict[str, Any], *, default_title: str | None = None) -> dict[str, Any]:
        title = task.get("title") or default_title
        if not isinstance(title, str) or not title.strip():
            raise ValueError("task title is required for cursor insert")
        return {
            "task_id": task.get("taskId"),
            "owner_id": task.get("ownerId"),
            "workspace_id": task.get("workspaceId"),
            "status": task.get("status") or "not_started",
            "current_stage": task.get("currentStage") or "problem_modeling",
            "title": title,
            "domain": task.get("domain"),
            "created_at": task.get("createdAt"),
            "updated_at": task.get("updatedAt"),
            "payload": json.dumps(task, ensure_ascii=False),
        }

    def insert_task_with_cursor(
        self, cur: Any, task: dict[str, Any], *, default_title: str | None = None
    ) -> None:
        """Insert using the surrounding transaction; no connection ownership."""
        cur.execute(INSERT_TASK_SQL, self._insert_task_params(task, default_title=default_title))

    def insert_task(self, task: dict[str, Any]) -> None:
        """Create a task once; never use a payload-overwriting upsert."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                self.insert_task_with_cursor(cur, task, default_title="待补充任务标题")

    def confirm_research_start_plan(
        self,
        *,
        task: dict[str, Any],
        owner_id: str,
        workspace_id: str,
    ) -> dict[str, Any]:
        """CAS-persist the user-confirmed draft without TaskStore mutation."""
        expected_revision = int(task.get("revision") or 0)
        updated = deepcopy(task)
        updated["updatedAt"] = self._chat_timestamp()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT payload, revision, status, current_stage, title, domain
                         FROM openair_tasks
                        WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                          AND status <> 'deleted'
                        FOR UPDATE""",
                    (updated.get("taskId"), owner_id, workspace_id),
                )
                row = cur.fetchone()
                if row is None or int(row.get("revision") or 0) != expected_revision:
                    raise RuntimeError("task_revision_conflict")
                fact_task = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
                try:
                    changed = commit_task_lifecycle_mutation_locked(
                        cur,
                        task_row=row,
                        payload=updated,
                        task_id=str(updated["taskId"]),
                        owner_id=owner_id,
                        workspace_id=workspace_id,
                        fact=ResearchStartLifecycleFact.PLAN_CONFIRMED,
                        fact_task=fact_task,
                        task_column_updates={
                            "current_stage": updated.get("currentStage") or "problem_modeling",
                            "title": updated.get("title") or "待补充任务标题",
                            "domain": updated.get("domain"),
                        },
                    )
                except TaskLifecycleWriteConflictError as exc:
                    raise RuntimeError("task_revision_conflict") from exc
                if not changed:
                    raise RuntimeError("task_lifecycle_transition_rejected")
        return updated

    def update_task_plan(
        self,
        *,
        task: dict[str, Any],
        editable: dict[str, Any],
        owner_id: str,
        workspace_id: str,
    ) -> dict[str, Any]:
        """CAS-persist one complete draft-plan projection with one SQL UPDATE."""
        expected_revision = int(editable["expectedRevision"])
        if int(task.get("revision") or 0) != expected_revision:
            raise RuntimeError("task_revision_conflict")
        updated = prepare_task_plan_candidate(task, editable, updated_at=self._chat_timestamp())
        payload = json.dumps(updated, ensure_ascii=False)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE openair_tasks
                    SET title = %(title)s,
                        domain = %(domain)s,
                        updated_at = NOW(),
                        revision = %(next_revision)s,
                        payload = %(payload)s::jsonb
                    WHERE task_id = %(task_id)s
                      AND owner_id = %(owner_id)s
                      AND workspace_id = %(workspace_id)s
                      AND revision = %(expected_revision)s
                      AND status = 'draft'
                      AND payload->>'creationStatus' IN ('awaiting_confirmation', 'revision_requested')
                    RETURNING payload, revision
                    """,
                    {
                        "title": updated.get("title") or "待补充任务标题",
                        "domain": updated.get("domain"),
                        "next_revision": expected_revision + 1,
                        "payload": payload,
                        "task_id": task.get("taskId"),
                        "owner_id": owner_id,
                        "workspace_id": workspace_id,
                        "expected_revision": expected_revision,
                    },
                )
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("task_revision_conflict")
                returned = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
                returned["revision"] = int(row["revision"])
                if returned != updated:
                    raise RuntimeError("task_plan_payload_mismatch")
                # Both the database-returned payload comparison and final
                # compile stay inside _connect(), so any failure rolls back.
                precompile_creation_plan(
                    creation_plan=returned["creationPlan"],
                    goal_summary=str(returned.get("goalSummary") or ""),
                )
        return returned

    def rename_task(
        self,
        *,
        task_id: str,
        new_title: str,
        owner_id: str,
        workspace_id: str,
    ) -> dict[str, Any] | None:
        """Persist one scoped rename and return the authoritative projection.

        A missing, deleted, or out-of-scope task is deliberately indistinguishable
        to preserve the route's 404 anti-enumeration contract.
        """
        title = new_title.strip()
        if not title:
            raise ValueError("task title is required")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (task_id,),
                )
                cur.execute(
                    """SELECT payload, revision
                         FROM openair_tasks
                        WHERE task_id = %s
                          AND owner_id = %s
                          AND workspace_id = %s
                          AND status <> %s
                        FOR UPDATE""",
                    (task_id, owner_id, workspace_id, TASK_DELETED_STATUS),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
                updated = deepcopy(payload)
                next_revision = int(row["revision"]) + 1
                updated["title"] = title
                updated["updatedAt"] = self._chat_timestamp()
                updated["revision"] = next_revision
                cur.execute(
                    """UPDATE openair_tasks
                          SET title = %s, updated_at = NOW(), revision = %s, payload = %s::jsonb
                        WHERE task_id = %s
                          AND owner_id = %s
                          AND workspace_id = %s
                          AND status <> %s
                          AND revision = %s
                    RETURNING payload, revision""",
                    (
                        title,
                        next_revision,
                        json.dumps(updated, ensure_ascii=False),
                        task_id,
                        owner_id,
                        workspace_id,
                        TASK_DELETED_STATUS,
                        int(row["revision"]),
                    ),
                )
                persisted = cur.fetchone()
                if persisted is None:
                    raise RuntimeError("task_revision_conflict")
                sync_artifact_source_task_titles(
                    cur,
                    task_id=task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    title=title,
                )
                result = persisted["payload"] if isinstance(persisted["payload"], dict) else json.loads(persisted["payload"])
                result["revision"] = int(persisted["revision"])
                return _with_research_design_compatibility_projection(result)

    def soft_delete_task(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        expected_revision: int | None = None,
    ) -> bool:
        """Logically delete one scoped task in a single PostgreSQL transaction.

        Immutable/restrict-linked execution and recovery facts remain as historical
        evidence. Active execution is fenced, public artifact storage references are
        cleared, and the task is hidden from authoritative reads before this method
        returns (and therefore before object-storage cleanup starts).

        ``deleted`` is an internal tombstone: it never enters the public Task API,
        every Task status writer excludes it, and the task ID cannot be reused.
        """
        deleted_at = self._chat_timestamp()
        with self._connect() as conn:
            with conn.cursor() as cur:
                # Use the same task-scoped advisory lock as durable execution so a
                # claim/callback cannot race the tombstone transaction.
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (task_id,),
                )
                cur.execute(
                    """SELECT payload, revision
                         FROM openair_tasks
                        WHERE task_id = %s
                          AND owner_id = %s
                          AND workspace_id = %s
                          AND status <> %s
                        FOR UPDATE""",
                    (task_id, owner_id, workspace_id, TASK_DELETED_STATUS),
                )
                row = cur.fetchone()
                if row is None:
                    return False
                current_revision = int(row["revision"])
                if expected_revision is not None and current_revision != expected_revision:
                    raise RuntimeError("task_revision_conflict")

                # Fence both durable-execution implementations before publishing
                # the tombstone. Terminal rows remain immutable historical facts.
                cur.execute(
                    """UPDATE openair_execution_gates
                          SET state = 'cancelled',
                              resolution_action = COALESCE(resolution_action, 'terminate_run'),
                              resolution_note = COALESCE(resolution_note, 'Task deleted by owner.'),
                              resolved_by = COALESCE(resolved_by, %s),
                              resolved_at = COALESCE(resolved_at, NOW())
                        WHERE task_id = %s AND state = 'pending'""",
                    (owner_id, task_id),
                )
                cur.execute(
                    """UPDATE openair_execution_capability_calls
                          SET state = 'cancelled',
                              ended_at = COALESCE(ended_at, NOW()),
                              failure_classification = COALESCE(failure_classification, 'task_deleted')
                        WHERE task_id = %s
                          AND state NOT IN ('succeeded', 'failed', 'blocked', 'cancelled', 'abandoned', 'unknown')""",
                    (task_id,),
                )
                cur.execute(
                    """UPDATE openair_execution_work_units
                          SET state = 'cancelled', ended_at = COALESCE(ended_at, NOW())
                        WHERE task_id = %s
                          AND state NOT IN ('succeeded', 'failed', 'cancelled', 'unknown')""",
                    (task_id,),
                )
                cur.execute(
                    """UPDATE openair_execution_runs
                          SET state = 'cancelled',
                              lease_owner = NULL,
                              lease_expires_at = NULL,
                              ended_at = COALESCE(ended_at, NOW()),
                              failure_classification = COALESCE(failure_classification, 'task_deleted')
                        WHERE task_id = %s
                          AND state NOT IN ('succeeded', 'failed', 'cancelled', 'abandoned', 'superseded')""",
                    (task_id,),
                )
                cur.execute(
                    """UPDATE openair_dispatch
                          SET status = 'suppressed',
                              lease_owner = NULL,
                              lease_expires_at = NULL,
                              last_error_code = COALESCE(last_error_code, 'task_deleted'),
                              updated_at = clock_timestamp()
                        WHERE task_id = %s
                          AND status IN ('pending', 'claimed', 'submission_authorized')""",
                    (task_id,),
                )
                cur.execute(
                    """UPDATE openair_controller_commands
                          SET state = 'rejected',
                              terminal_reason_code = COALESCE(terminal_reason_code, 'task_deleted'),
                              updated_at = NOW()
                        WHERE task_id = %s AND state = 'received'""",
                    (task_id,),
                )

                # Remove every public resolver pointer before object deletion.
                # If MinIO cleanup later fails, only unreachable orphan objects remain.
                cur.execute(
                    """UPDATE openair_artifacts
                          SET storage_ref = NULL,
                              external_uri = NULL,
                              archived_at = COALESCE(archived_at, NOW()),
                              updated_at = NOW(),
                              metadata = COALESCE(metadata, '{}'::jsonb)
                                  || jsonb_build_object('deletedWithTask', true, 'deletedAt', %s::text)
                        WHERE source_task_id = %s""",
                    (deleted_at, task_id),
                )
                cur.execute(
                    """UPDATE openair_artifact_uploads AS upload
                          SET cleanup_state = 'task_deleted'
                         FROM openair_execution_runs AS run
                        WHERE upload.run_id = run.run_id
                          AND run.task_id = %s""",
                    (task_id,),
                )

                commit_task_tombstone_locked(
                    cur,
                    task_row=row,
                    task_id=task_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    deleted_at=deleted_at,
                )
        return True

    def delete_task(self, task_id: str) -> None:
        """Hard-delete a just-created task during same-flow rollback only.

        Public Task deletion uses :meth:`soft_delete_task`; this unscoped helper
        is retained for research-start finalization rollback before child facts exist.
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(DELETE_TASK_SQL, (task_id,))

    def _commit_user_task_mutation(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        action: str,
        mutate: Any,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
        fingerprint: str | None = None,
        lifecycle_fact: UserTaskLifecycleFact | None = None,
    ) -> dict[str, Any]:
        """Commit one scoped user command with CAS and durable idempotency evidence."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT payload, revision, status, current_stage, title, domain
                         FROM openair_tasks
                        WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                        FOR UPDATE""",
                    (task_id, owner_id, workspace_id),
                )
                row = cur.fetchone()
                if row is None:
                    raise KeyError("task not found")
                raw = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
                current_revision = int(row.get("revision") or 0)
                receipts = list(raw.get("userCommandReceipts") or [])
                normalized_key = (idempotency_key or "").strip()
                if normalized_key:
                    for receipt in receipts:
                        if not isinstance(receipt, dict):
                            continue
                        if receipt.get("action") == action and receipt.get("idempotencyKey") == normalized_key:
                            if receipt.get("fingerprint") != fingerprint:
                                raise RuntimeError("idempotency_payload_conflict")
                            raw["revision"] = current_revision
                            return raw
                if expected_revision is not None and current_revision != expected_revision:
                    raise RuntimeError("task_revision_conflict")
                updated = mutate(deepcopy(raw))
                if not isinstance(updated, dict):
                    raise TypeError("task mutation must return a dict")
                updated["updatedAt"] = self._chat_timestamp()
                if normalized_key:
                    receipts.append({
                        "action": action,
                        "idempotencyKey": normalized_key,
                        "fingerprint": fingerprint,
                        "status": "committed",
                        "committedRevision": current_revision + 1,
                        "committedAt": updated["updatedAt"],
                    })
                    updated["userCommandReceipts"] = receipts[-50:]
                try:
                    changed = commit_task_lifecycle_mutation_locked(
                        cur,
                        task_row=row,
                        payload=updated,
                        task_id=task_id,
                        owner_id=owner_id,
                        workspace_id=workspace_id,
                        fact=lifecycle_fact,
                        fact_task=raw,
                        task_column_updates={
                            "current_stage": updated.get("currentStage")
                            or row.get("current_stage")
                            or "problem_modeling",
                            "title": updated.get("title") or row.get("title") or "待补充任务标题",
                            "domain": updated.get("domain")
                            if "domain" in updated
                            else row.get("domain"),
                        },
                    )
                except TaskLifecycleWriteConflictError as exc:
                    raise RuntimeError("task_revision_conflict") from exc
                if not changed:
                    raise RuntimeError("task_lifecycle_transition_rejected")
                if action == "rename":
                    disable_automatic_title(cur, task_id, owner_id, workspace_id)
                    renamed_title = str(
                        updated.get("title") or row.get("title") or ""
                    ).strip()
                    if renamed_title:
                        sync_artifact_source_task_titles(
                            cur,
                            task_id=task_id,
                            owner_id=owner_id,
                            workspace_id=workspace_id,
                            title=renamed_title,
                        )
        return updated

    def rename_task_scoped(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        title: str,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        normalized = title.strip()
        if not normalized:
            raise ValueError("task title is required")
        return self._commit_user_task_mutation(
            task_id=task_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            action="rename",
            mutate=lambda task: {**task, "title": normalized},
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            fingerprint=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        )

    def request_draft_revision(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        artifacts: list[dict[str, Any]] | None = None,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        def mutate(task: dict[str, Any]) -> dict[str, Any]:
            if task.get("status") != "draft":
                raise ValueError("task must be a draft")
            task["creationStatus"] = "revision_requested"
            if artifacts is not None:
                task["artifacts"] = artifacts
            return task
        return self._commit_user_task_mutation(
            task_id=task_id, owner_id=owner_id, workspace_id=workspace_id,
            action="revise_draft", mutate=mutate, expected_revision=expected_revision,
            idempotency_key=idempotency_key, fingerprint="revision_requested",
        )

    def cancel_draft_scoped(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        def mutate(task: dict[str, Any]) -> dict[str, Any]:
            if task.get("status") not in {"draft", "cancelled"}:
                raise ValueError("task must be a draft")
            task["status"] = "cancelled"
            task["creationStatus"] = "cancelled"
            return task
        return self._commit_user_task_mutation(
            task_id=task_id, owner_id=owner_id, workspace_id=workspace_id,
            action="cancel_draft", mutate=mutate, expected_revision=expected_revision,
            idempotency_key=idempotency_key, fingerprint="cancelled",
            lifecycle_fact=UserTaskLifecycleFact.DRAFT_CANCELLED,
        )

    def delete_task_scoped(
        self,
        *,
        task_id: str,
        owner_id: str,
        workspace_id: str,
        expected_revision: int | None = None,
    ) -> list[dict[str, str]]:
        """Atomically delete task/catalog facts and enqueue fenced cleanup intents."""
        storage_refs: list[dict[str, str]] = []
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT revision FROM openair_tasks
                        WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                        FOR UPDATE""",
                    (task_id, owner_id, workspace_id),
                )
                row = cur.fetchone()
                if row is None:
                    cur.execute(
                        """SELECT payload FROM openair_user_delete_receipts
                             WHERE target_type = 'task' AND target_id = %s
                               AND owner_id = %s AND workspace_id = %s""",
                        (task_id, owner_id, workspace_id),
                    )
                    receipt = cur.fetchone()
                    if receipt is not None:
                        payload = receipt.get("payload") or {}
                        if isinstance(payload, str):
                            payload = json.loads(payload)
                        return list(payload.get("storageCleanups") or [])
                    raise KeyError("task not found")
                current_revision = int(row.get("revision") or 0)
                if expected_revision is not None and current_revision != expected_revision:
                    raise RuntimeError("task_revision_conflict")
                cur.execute(
                    """SELECT reference.artifact_id, reference.reference_kind,
                              reference.version_id, reference.storage_type,
                              reference.storage_scope, reference.storage_ref
                         FROM openair_artifact_storage_references AS reference
                         JOIN openair_artifacts AS artifact
                           ON artifact.artifact_id = reference.artifact_id
                        WHERE artifact.source_task_id = %s
                          AND reference.storage_type IN ('external', 'local')
                        ORDER BY reference.artifact_id, reference.reference_kind, reference.version_id
                        FOR UPDATE OF reference""",
                    (task_id,),
                )
                storage_refs = [
                    {
                        "artifactId": str(r.get("artifact_id") or ""),
                        "referenceKind": str(r.get("reference_kind") or ""),
                        "versionId": str(r.get("version_id") or ""),
                        "storageType": str(r["storage_type"]),
                        "storageScope": str(r.get("storage_scope") or ""),
                        "storageRef": str(r["storage_ref"]),
                        "cleanupOutboxTracked": "true",
                    }
                    for r in cur.fetchall()
                ]
                for cleanup in storage_refs:
                    storage_scope = cleanup["storageScope"]
                    cur.execute(
                        """INSERT INTO openair_artifact_cleanup_outbox (
                               cleanup_id, task_id, artifact_id, storage_type, storage_scope,
                               storage_ref, reason_code, state, attempt_count
                           ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', 0)
                           ON CONFLICT (storage_type, storage_scope, storage_ref) DO UPDATE SET
                               task_id = EXCLUDED.task_id, artifact_id = EXCLUDED.artifact_id,
                               reason_code = EXCLUDED.reason_code, state = 'pending',
                               attempt_count = 0, last_error = NULL,
                               next_attempt_at = NOW(), lease_owner = NULL,
                               lease_expires_at = NULL, terminal_at = NULL,
                               updated_at = NOW()""",
                        (
                            f"artifact_cleanup_{uuid4().hex}", task_id,
                            cleanup["artifactId"] or None, cleanup["storageType"],
                            storage_scope, cleanup["storageRef"],
                            "task_deleted_object_cleanup_pending",
                        ),
                    )
                cur.execute(
                    """INSERT INTO openair_user_delete_receipts
                           (target_type, target_id, owner_id, workspace_id, task_id, payload)
                       VALUES ('task', %s, %s, %s, %s, %s::jsonb)
                       ON CONFLICT (target_type, target_id, owner_id, workspace_id) DO NOTHING""",
                    (
                        task_id, owner_id, workspace_id, task_id,
                        json.dumps({"storageCleanups": storage_refs}, ensure_ascii=False),
                    ),
                )
                cur.execute("DELETE FROM openair_artifacts WHERE source_task_id = %s", (task_id,))
                cur.execute(
                    """DELETE FROM openair_tasks
                        WHERE task_id = %s AND owner_id = %s AND workspace_id = %s
                          AND revision = %s""",
                    (task_id, owner_id, workspace_id, current_revision),
                )
                if cur.rowcount != 1:
                    raise RuntimeError("task_revision_conflict")
        return storage_refs

    @staticmethod
    def _chat_timestamp() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _chat_message(*, role: str, text: str, runtime: str | None = None, message_type: str = "chat", call_id: str | None = None) -> dict[str, Any]:
        return {
            "id": f"msg_{uuid4().hex[:12]}", "role": role, "text": text,
            "at": TaskRepository._chat_timestamp(), "runtime": runtime,
            "messageType": message_type, "capabilityCallId": call_id,
            "artifactRefs": [], "confirmationRequests": [], "userFacingNarrative": None,
        }

    def _append_message_row(
        self, cur: Any, *, task_id: str, owner_id: str, workspace_id: str,
        role: str, text: str, runtime: str | None = None, message_type: str = "chat", call_id: str | None = None,
    ) -> None:
        """Persist one chat message to ``openair_task_messages`` (E1).

        Replaces the legacy ``payload["agentMessages"].append(...)`` mirror.
        The caller holds the task row lock (``_lock_chat_task``) which
        serializes per-task ``seq`` allocation.
        """
        _msg = self._chat_message(role=role, text=text, runtime=runtime, message_type=message_type, call_id=call_id)
        TaskMessageRepository().append(
            cur,
            task_id=task_id, owner_id=owner_id, workspace_id=workspace_id,
            role=_msg["role"], text=_msg["text"], message_uid=_msg["id"],
            runtime=_msg["runtime"], message_type=_msg["messageType"],
            capability_call_id=_msg["capabilityCallId"],
        )

    def _lock_chat_task(self, cur: Any, *, task_id: str, owner_id: str, workspace_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        cur.execute(
            "SELECT * FROM openair_tasks WHERE task_id = %s AND status <> %s FOR UPDATE",
            (task_id, TASK_DELETED_STATUS),
        )
        row = cur.fetchone()
        if not row:
            raise KeyError("task not found")
        if row["owner_id"] != owner_id or row["workspace_id"] != workspace_id:
            raise PermissionError("task is outside request scope")
        raw = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
        payload = deepcopy(raw)
        payload["taskId"] = task_id
        payload["revision"] = int(row["revision"])
        return row, payload

    def _write_chat_payload(self, cur: Any, *, row: dict[str, Any], payload: dict[str, Any]) -> None:
        expected_revision = int(row["revision"])
        next_revision = expected_revision + 1
        payload["updatedAt"] = self._chat_timestamp()
        payload["revision"] = next_revision
        stamp_progress_projection(payload, next_revision=next_revision)
        cur.execute(
            """UPDATE openair_tasks SET updated_at = NOW(), revision = %s, payload = %s::jsonb
               WHERE task_id = %s AND revision = %s AND status <> 'deleted'""",
            (next_revision, json.dumps(payload, ensure_ascii=False), row["task_id"], expected_revision),
        )
        if cur.rowcount != 1:
            raise RuntimeError("task_revision_conflict")

    @staticmethod
    def _is_collaboration_pollution_call(call: Mapping[str, Any]) -> bool:
        """Chat turns belong in messages; only strip known chat pollution shapes."""
        if not isinstance(call, Mapping):
            return False
        call_id = str(call.get("id") or call.get("capabilityCallId") or "")
        if call_id.startswith("collab_"):
            return True
        name = str(call.get("name") or call.get("capabilityId") or "")
        return name in {"workbench_chat", "workbench-chat"}

    @classmethod
    def _strip_non_research_capability_calls(cls, payload: dict[str, Any]) -> None:
        calls = payload.get("capabilityCalls")
        if not isinstance(calls, list):
            return
        payload["capabilityCalls"] = [
            call for call in calls if not cls._is_collaboration_pollution_call(call)
        ]

    def begin_collaboration_chat(
        self, *, task_id: str, owner_id: str, workspace_id: str, message: str,
        skill_id: str, runtime: str, source_mode: str, stage_key: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Atomically append an explicit chat input. Does not touch research capabilityCalls."""
        del skill_id, runtime, source_mode, stage_key  # retained for call-site compatibility
        with self._connect() as conn:
            with conn.cursor() as cur:
                row, payload = self._lock_chat_task(cur, task_id=task_id, owner_id=owner_id, workspace_id=workspace_id)
                self._strip_non_research_capability_calls(payload)
                turn_id = f"collab_{uuid4().hex[:12]}"
                self._append_message_row(
                    cur, task_id=task_id, owner_id=owner_id, workspace_id=workspace_id,
                    role="user", text=message, call_id=turn_id,
                )
                payload["lastCollaborationTurnId"] = turn_id
                self._write_chat_payload(cur, row=row, payload=payload)
                return {"id": turn_id, "status": "running"}, payload

    def finish_collaboration_chat(
        self, *, task_id: str, owner_id: str, workspace_id: str, call_id: str,
        reply: str, runtime: str, error_code: str | None = None, session_id: str | None = None,
        source_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically persist the terminal assistant message. No capabilityCalls mutation."""
        del source_metadata  # optional diagnostics stay out of the research aggregate
        with self._connect() as conn:
            with conn.cursor() as cur:
                row, payload = self._lock_chat_task(cur, task_id=task_id, owner_id=owner_id, workspace_id=workspace_id)
                self._strip_non_research_capability_calls(payload)
                if session_id:
                    payload["agentSessionId"] = session_id
                self._append_message_row(
                    cur, task_id=task_id, owner_id=owner_id, workspace_id=workspace_id,
                    role="assistant", text=reply, runtime=runtime, call_id=call_id,
                    message_type="chat_error" if error_code else "chat",
                )
                self._write_chat_payload(cur, row=row, payload=payload)
                return payload

    def record_collaboration_gateway(
        self, *, task_id: str, owner_id: str, workspace_id: str, reply: str,
        action: str, call_name: str | None = None, call_status: str = "failed",
        user_message: str | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        """Persist a non-runtime explicit-chat reply as messages only."""
        del call_name, call_status
        with self._connect() as conn:
            with conn.cursor() as cur:
                row, payload = self._lock_chat_task(cur, task_id=task_id, owner_id=owner_id, workspace_id=workspace_id)
                self._strip_non_research_capability_calls(payload)
                turn_id = f"collab_{uuid4().hex[:12]}"
                if user_message:
                    self._append_message_row(
                        cur, task_id=task_id, owner_id=owner_id, workspace_id=workspace_id,
                        role="user", text=user_message, runtime="specialist_agent_runtime_gateway",
                        message_type=f"{action}_while_running", call_id=turn_id,
                    )
                self._append_message_row(
                    cur, task_id=task_id, owner_id=owner_id, workspace_id=workspace_id,
                    role="assistant", text=reply, runtime="specialist_agent_runtime_gateway",
                    message_type=f"runtime_gateway_{action}", call_id=turn_id,
                )
                payload["lastCollaborationTurnId"] = turn_id
                self._write_chat_payload(cur, row=row, payload=payload)
                return payload, turn_id

    def cancel_collaboration_call(
        self, *, task_id: str, owner_id: str, workspace_id: str, call_id: str,
    ) -> dict[str, Any]:
        """No-op for research aggregate: collaboration turns are not capabilityCalls."""
        del call_id
        with self._connect() as conn:
            with conn.cursor() as cur:
                row, payload = self._lock_chat_task(cur, task_id=task_id, owner_id=owner_id, workspace_id=workspace_id)
                before = len(payload.get("capabilityCalls") or [])
                self._strip_non_research_capability_calls(payload)
                after = len(payload.get("capabilityCalls") or [])
                if before != after:
                    self._write_chat_payload(cur, row=row, payload=payload)
                return payload
