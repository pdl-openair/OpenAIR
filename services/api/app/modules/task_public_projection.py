"""Explicit public Task projection for canonical research orchestration facts."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Collection, Mapping

from app.models.research_orchestration import (
    READ_ONLY_LEGACY_FINAL_RESULT_AUTHORITY,
    ResearchTaskV12,
)
from app.models.result_protocol import normalize_result_protocol
from app.models.schemas import TaskOut
from app.modules import pack_config
from app.modules.research_public_delivery import public_final_delivery
from app.modules.artifact_gate import apply_artifact_gate
from app.modules.research_blocker_semantics import (
    active_user_stop_receipt,
    blocker_user_explanation,
    current_blocker_facts,
)
from app.modules.research_orchestration_repository import (
    _extract_contract_payload,
    _research_design_skill_pack,
)
from app.modules.research_progress_projection import project_research_process_history

# Stable display overrides for canonical Definition keys (user-facing Workbench).
_SUBSTAGE_DISPLAY_LABEL_ZH: dict[str, str] = {
    "reproducibility": "可复现性设计",
    "report_generation": "报告生成",
}
_STAGE_DISPLAY_LABEL_ZH: dict[str, str] = {
    "problem_modeling": "问题建模",
    "solution_design": "方案设计",
    "execution_validation": "执行验证",
    "insight_generation": "洞察生成",
}
_PROJECT_FOCUS_MARKER = "当前项目科研重点："
_CANONICAL_FINAL_OBJECTIVE_EN = "Assemble the completed research into the canonical final result."
_CANONICAL_FINAL_OBJECTIVE_ZH = "汇总已完成的研究结果，形成最终研究报告"


def _substage_key_from_definition(definition_id: str) -> str:
    return str(definition_id or "").split(":", 1)[-1].strip()


def _user_facing_substage_label(substage_key: str, *, pack: str) -> str:
    override = _SUBSTAGE_DISPLAY_LABEL_ZH.get(substage_key)
    if override:
        return override
    label = pack_config.get_substage_label(substage_key, pack=pack)
    return label if label else substage_key


def _user_facing_objective(substage_key: str, objective: str | None) -> str:
    text = str(objective or "").strip()
    if not text:
        return ""
    if text == _CANONICAL_FINAL_OBJECTIVE_EN:
        return _CANONICAL_FINAL_OBJECTIVE_ZH
    if substage_key == "report_generation" and "canonical final result" in text.lower():
        return _CANONICAL_FINAL_OBJECTIVE_ZH
    return text


def _confirmation_plan_action(objective: str | None) -> str:
    """Keep project-specific research work and hide assembly boilerplate."""

    text = " ".join(str(objective or "").strip().split())
    if _PROJECT_FOCUS_MARKER in text:
        _prefix, project_focus = text.split(_PROJECT_FOCUS_MARKER, 1)
        project_focus = project_focus.strip()
        if project_focus:
            text = project_focus
    while text.endswith("。。"):
        text = text[:-1]
    return text


def _confirmation_stage_title(stage_key: str) -> str:
    """Stable Chinese research-stage title for the confirmation plan."""

    return _STAGE_DISPLAY_LABEL_ZH.get(stage_key, stage_key)


def _public_timestamp(value: Any) -> str | None:
    """TaskOut timestamps are ISO strings; PG TIMESTAMPTZ arrives as datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    text = str(value).strip()
    return text or None


_CANONICAL_REQUIRED_TASK_KEYS = {
    field.alias or name
    for name, field in ResearchTaskV12.model_fields.items()
    if field.is_required()
}
_CALL_STATUS = {
    "queued": "not_started",
    "running": "running",
    "succeeded": "completed",
    "failed": "failed",
    "external_result_unknown": "blocked_recoverable",
}


def _normalize_legacy_artifacts(source: dict[str, Any]) -> None:
    """Coerce persisted legacy artifactState values before TaskOut validation.

    Older fixtures/rows sometimes stored artifact *kinds* (e.g. ``raw_output``,
    ``stage_summary``) in ``artifactState``. Apply the shared gate so public
    projection never 500s the task list on those rows.
    """
    artifacts = source.get("artifacts")
    if not isinstance(artifacts, list):
        return
    calls = source.get("capabilityCalls")
    call_by_id = {
        call.get("id"): call
        for call in calls
        if isinstance(call, dict) and isinstance(call.get("id"), str)
    } if isinstance(calls, list) else {}
    owner_id = source.get("ownerId")
    workspace_id = source.get("workspaceId")
    created_by = source.get("createdBy") or owner_id
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        if owner_id is not None:
            artifact.setdefault("ownerId", owner_id)
        if workspace_id is not None:
            artifact.setdefault("workspaceId", workspace_id)
        if created_by is not None:
            artifact.setdefault("createdBy", created_by)
        source_call = None
        call_id = artifact.get("sourceCapabilityCallId")
        if isinstance(call_id, str):
            source_call = call_by_id.get(call_id)
        apply_artifact_gate(artifact, source_call=source_call)


def _filter_public_artifact_surface(
    public: dict[str, Any], *, visible_artifact_ids: Collection[str],
    source_artifact_ids: Collection[str] = (),
) -> None:
    """Close public links over the supplied authority, without changing domain facts.

    Call only on the public copy, after compatibility normalization. An omitted
    authority and an explicitly empty authority both expose no Artifacts. The
    scientific design/final-result objects are not traversed or reinterpreted.
    """
    visible_ids = frozenset(visible_artifact_ids)
    artifacts = public.get("artifacts")
    if isinstance(artifacts, list):
        public["artifacts"] = [
            row for row in artifacts
            if isinstance(row, dict) and row.get("id") in visible_ids
        ]
    public_ids = {
        row["id"] for row in public.get("artifacts", [])
        if isinstance(row, dict) and row.get("id") in visible_ids
    }

    # sourceRefs is mixed lineage. Only known Artifact identities are subject
    # to this membership filter; external sources and Memory provenance survive.
    known_artifact_ids = set(source_artifact_ids) | visible_ids | {
        row.get("id") for row in artifacts or [] if isinstance(row, dict)
    }

    # These are reference fields in TaskOut's bounded public child models,
    # not visibility classes. Membership is the only visibility decision here.
    reference_lists = {
        "artifactRefs", "inputArtifactRefs", "outputArtifactRefs",
        "linkedArtifactIds", "linkedArtifacts", "relatedArtifactIds",
        "relatedArtifactRefs",
    }

    child_fields = {
        "subStages", "source", "materialManifest", "trustRecords",
        "validatorResult", "artifactValidatorResults", "reusedFrom",
        "userFacingNarrative", "narrative",
    }

    def link_rows(value: Any):
        if isinstance(value, list):
            for item in value:
                yield from link_rows(item)
        elif isinstance(value, dict):
            yield value
            for key in child_fields:
                yield from link_rows(value.get(key))

    rows = [
        row
        for collection in (
            "artifacts", "stageDetails", "workUnits", "capabilityCalls", "materials",
            "memoryRecords", "trustRecords", "userVisibleEvents",
        )
        for row in link_rows(public.get(collection))
    ]
    progress = public.get("progressProjection")
    interruption = progress.get("interruption") if isinstance(progress, dict) else None
    retained = interruption.get("retainedResults") if isinstance(interruption, dict) else None
    summary = public.get("catalogArtifacts")
    identity_rows = [*rows, *link_rows(artifacts)]
    if isinstance(retained, dict):
        identity_rows.extend(link_rows(retained.get("artifacts")))
    if isinstance(summary, dict):
        identity_rows.extend(link_rows(summary.get("items")))
    # A reference in an Artifact-typed field establishes identity even when its
    # object is absent. Include the pre-filter source rows so an omitted object
    # cannot turn its Artifact lineage into an unclassified external source.
    for row in identity_rows:
        for field in reference_lists:
            known_artifact_ids.update(ref for ref in row.get(field) or [] if isinstance(ref, str))
        if isinstance(row.get("artifactId"), str):
            known_artifact_ids.add(row["artifactId"])
        if row.get("sourceType") == "artifact" and isinstance(row.get("sourceId"), str):
            known_artifact_ids.add(row["sourceId"])

    for value in rows:
        if "artifactId" in value and value["artifactId"] not in public_ids:
            value.pop("artifactId")
        if value.get("sourceType") == "artifact" and value.get("sourceId") not in public_ids:
            value.pop("sourceId", None)
        reused_from = value.get("reusedFrom")
        if isinstance(reused_from, dict) and reused_from.get("artifactId") not in public_ids:
            value["reusedFrom"] = None
        validators = value.get("artifactValidatorResults")
        if isinstance(validators, list):
            value["artifactValidatorResults"] = [
                row for row in validators
                if isinstance(row, dict)
                and (row.get("artifactId") is None or row.get("artifactId") in public_ids)
            ]
        for key, child in value.items():
            if key in reference_lists and isinstance(child, list):
                value[key] = [ref for ref in child if ref in public_ids]
            elif key == "sourceRefs" and isinstance(child, list):
                value[key] = [ref for ref in child if ref in public_ids or ref not in known_artifact_ids]

    if isinstance(retained, dict) and isinstance(retained.get("artifacts"), list):
        retained["artifacts"] = [
            row for row in retained["artifacts"]
            if isinstance(row, dict) and row.get("artifactId") in public_ids
        ]
    if isinstance(summary, dict) and isinstance(summary.get("items"), list):
        summary["items"] = [
            row for row in summary["items"]
            if isinstance(row, dict) and row.get("artifactId") in public_ids
        ]
        summary["count"] = len(summary["items"])


def _final_result_authority_projection(canonical: ResearchTaskV12) -> dict[str, Any] | None:
    authority = canonical.final_result_authority
    if authority is None:
        return None
    artifact_by_id = {row.artifact_id: row for row in canonical.artifacts}
    result_rows = [artifact_by_id.get(ref) for ref in authority.result_artifact_refs]
    status = "current"
    if any(row is None for row in result_rows):
        status = "stale"
    elif any(row.artifact_state.value == "needs_review" for row in result_rows if row is not None):
        status = "needs_review"
    elif any(row.artifact_state.value != "usable" for row in result_rows if row is not None):
        status = "stale"
    if status == "current":
        for snapshot in authority.source_artifacts:
            current = artifact_by_id.get(snapshot.artifact_id)
            digest = (
                str((current.validator_result or {}).get("contentSha256") or "").strip().lower()
                if current is not None
                else ""
            )
            if (
                current is None
                or current.artifact_state.value != "usable"
                or current.version != snapshot.version
                or (snapshot.content_sha256 is not None and digest != snapshot.content_sha256)
            ):
                status = "stale"
                break
    projection = authority.model_dump(mode="json", by_alias=True)
    projection["status"] = status
    projection["integrityStatus"] = (
        "validated"
        if authority.integrity_contract_version in {
            "scientific_completion_integrity_v1",
            "scientific_completion_integrity_v2",
            "scientific_completion_integrity_v3",
        }
        else "legacy_unvalidated"
    )
    projection["currentResultArtifacts"] = [
        {
            "artifactId": row.artifact_id,
            "artifactType": row.artifact_type,
            "version": row.version,
            "contentSha256": str((row.validator_result or {}).get("contentSha256") or ""),
            "artifactState": row.artifact_state.value,
        }
        for row in result_rows
        if row is not None
    ]
    return projection


def _adapt_legacy_authority_for_public_read(source: dict[str, Any]) -> bool:
    """Mark a historical missing version only inside the GET projection copy."""

    authority = source.get("finalResultAuthority")
    if not isinstance(authority, dict):
        return False
    missing_version = "integrityContractVersion" not in authority
    if missing_version:
        authority["integrityContractVersion"] = "legacy_v0"
    return missing_version


def project_public_task(
    task: Mapping[str, Any],
    *,
    visible_artifact_ids: Collection[str] = (),
    catalog_artifacts: Mapping[str, Mapping[str, Any]] | None = None,
) -> TaskOut:
    """Project a current canonical aggregate for command and lifecycle responses."""

    return _project_public_task(
        task,
        allow_legacy_authority_read=False,
        visible_artifact_ids=visible_artifact_ids,
        catalog_artifacts=catalog_artifacts,
    )


def project_public_task_read_only(
    task: Mapping[str, Any],
    *,
    visible_artifact_ids: Collection[str] = (),
    catalog_artifacts: Mapping[str, Mapping[str, Any]] | None = None,
) -> TaskOut:
    """Project one historical aggregate for the dedicated GET snapshot path."""

    return _project_public_task(
        task,
        allow_legacy_authority_read=True,
        visible_artifact_ids=visible_artifact_ids,
        catalog_artifacts=catalog_artifacts,
    )


def _project_public_task(
    task: Mapping[str, Any],
    *,
    allow_legacy_authority_read: bool,
    visible_artifact_ids: Collection[str],
    catalog_artifacts: Mapping[str, Mapping[str, Any]] | None = None,
) -> TaskOut:
    """Validate and adapt one canonical aggregate without mutating persistence.

    Legacy Task payloads retain their existing strict TaskOut path. A payload
    containing the complete v1.2 aggregate is first validated as
    ResearchTaskV12, then its incompatible child shapes are explicitly mapped
    to the bounded public models.
    """
    source = deepcopy(dict(task))
    visible_ids = frozenset(
        artifact_id
        for value in visible_artifact_ids
        if (artifact_id := str(value or "").strip())
    )
    if not _CANONICAL_REQUIRED_TASK_KEYS.issubset(source):
        _normalize_legacy_artifacts(source)
        authority = source.get("finalResultAuthority")
        result = source.get("finalResult")
        if not (
            isinstance(authority, dict)
            and authority.get("status") == "current"
            and isinstance(result, dict)
            and isinstance(result.get("finalResultId"), str)
            and result.get("finalResultId") == authority.get("finalResultId")
        ):
            source["finalResult"] = None
        _filter_public_artifact_surface(source, visible_artifact_ids=visible_ids)
        return TaskOut.model_validate(source)

    contract_source = deepcopy(source)
    for name, field in ResearchTaskV12.model_fields.items():
        alias = field.alias or name
        if alias not in contract_source and not field.is_required():
            contract_source[alias] = field.get_default(call_default_factory=True)
    legacy_read = (
        _adapt_legacy_authority_for_public_read(contract_source)
        if allow_legacy_authority_read
        else False
    )
    canonical = ResearchTaskV12.model_validate(
        _extract_contract_payload(contract_source),
        context={READ_ONLY_LEGACY_FINAL_RESULT_AUTHORITY: legacy_read},
    )
    visible_artifacts = [
        artifact
        for artifact in canonical.artifacts
        if artifact.artifact_id in visible_ids
    ]
    public_artifact_ids = {artifact.artifact_id for artifact in visible_artifacts}
    final_result_authority = _final_result_authority_projection(canonical)
    # Interruption is a GET projection of durable facts, not persisted state.
    progress = deepcopy(canonical.progress_projection or {})
    progress["interruption"] = _research_interruption_projection(
        canonical,
        source=source,
        final_result_authority=final_result_authority,
        visible_artifact_ids=visible_ids,
    )
    # Task lifecycle and scoped session thoughts share one history outlet.
    # Neither can override the current canonical Task state.
    progress["researchProcessHistory"] = project_research_process_history(
        canonical,
        visible_artifact_ids=visible_ids,
    )
    progress["researchProcessHistory"].extend(source.pop("planningThoughtHistory", []))
    public = {
        **source,
        **_active_research_design_projection(
            canonical,
            public_artifact_ids=public_artifact_ids,
        ),
        "revision": canonical.revision,
        "status": canonical.status.value,
        "executionMode": canonical.execution_mode.value,
        "activeResearchDesignRef": (
            None
            if canonical.active_research_design_ref is None
            else canonical.active_research_design_ref.model_dump(mode="json", by_alias=True)
        ),
        "researchDesignVersions": [
            row.model_dump(mode="json", by_alias=True)
            for row in canonical.research_design_versions
        ],
        "progressProjection": progress,
        "finalResultAuthority": final_result_authority,
        **public_final_delivery(canonical, final_result_authority),
    }

    stage_by_instance = {
        instance.substage_instance_id: instance.stage_key.value
        for design in canonical.research_design_versions
        for instance in design.instances
    }
    work_by_id = {row.work_unit_id: row for row in canonical.work_units}
    call_by_id = {row.capability_call_id: row for row in canonical.capability_calls}
    output_refs_by_work: dict[str, list[str]] = {}
    for artifact in visible_artifacts:
        output_refs_by_work.setdefault(artifact.work_unit_id, []).append(artifact.artifact_id)

    public["workUnits"] = [
        {
            "workUnitId": work.work_unit_id,
            "executionDecisionId": work.execution_decision_id,
            "assignmentId": work.assignment_id,
            "subStageInstanceId": work.substage_instance_id,
            "responsibleActorId": work.responsible_actor_ref.actor_id,
            "executorType": work.executor_ref.executor_type,
            "executorId": work.executor_ref.executor_id,
            "status": work.status.value,
            "terminalReasonCode": work.terminal_reason_code,
            "dependencyWorkUnitIds": list(work.dependency_work_unit_ids),
            "inputArtifactRefs": [
                artifact_id
                for artifact_id in work.input_artifact_refs
                if artifact_id in public_artifact_ids
            ],
            "inputMemoryRefs": list(work.input_memory_refs),
            "outputArtifactRefs": output_refs_by_work.get(work.work_unit_id, []),
            "dispatchedAt": _timestamp(work.dispatched_at),
            "finishedAt": _timestamp(work.finished_at),
        }
        for work in canonical.work_units
    ]
    public["capabilityCalls"] = [
        _public_capability_call(
            call,
            task=source,
            work=work_by_id[call.work_unit_id],
            stage_key=stage_by_instance.get(call.substage_instance_id),
            artifact_ids=public_artifact_ids,
            execution_mode=canonical.execution_mode.value,
        )
        for call in canonical.capability_calls
    ]
    public["artifacts"] = [
        _public_artifact(
            artifact,
            task=source,
            work=work_by_id[artifact.work_unit_id],
            source_call=(
                None
                if artifact.source_capability_call_id is None
                else call_by_id[artifact.source_capability_call_id]
            ),
            stage_key=stage_by_instance.get(artifact.substage_instance_id),
            catalog_row=(
                None
                if catalog_artifacts is None
                else catalog_artifacts.get(artifact.artifact_id)
            ),
        )
        for artifact in visible_artifacts
    ]
    _filter_public_artifact_surface(
        public, visible_artifact_ids=visible_ids,
        source_artifact_ids={
            *(row.artifact_id for row in canonical.artifacts),
            *(ref for work in canonical.work_units for ref in work.input_artifact_refs),
        },
    )
    return TaskOut.model_validate(public)


def _research_interruption_projection(
    canonical: ResearchTaskV12,
    *,
    source: Mapping[str, Any],
    final_result_authority: Mapping[str, Any] | None,
    visible_artifact_ids: Collection[str] = (),
) -> dict[str, Any] | None:
    """Explain canonical facts without creating a lifecycle or result authority."""
    blockers = current_blocker_facts(canonical)
    stop = active_user_stop_receipt(canonical.task_id, source.get("stopReceipt"))
    status = canonical.status.value
    # A failed start that never formed a research design is not an execution interruption.
    if status == "failed" and not canonical.research_design_versions:
        return None
    if status == "completed" or (not blockers and stop is None and status not in {"failed", "cancelled"}):
        return None
    priorities = {"external_result_unknown": 0, "system_failure": 1, "user_input": 2, "external_dependency": 3}
    primary = min(blockers, key=lambda row: priorities[row.kind], default=None)
    kind = primary.kind if primary is not None else (
        "system_failure" if status == "failed" else "user_stop" if stop is not None else "execution_cancelled"
    )
    active_ref = canonical.active_research_design_ref
    design = next((row for row in canonical.research_design_versions if active_ref is not None
                   and row.research_design_id == active_ref.research_design_id and row.version == active_ref.version), None)
    instances = list(design.instances) if design is not None else []
    runtime_by_ref = {(row.substage_instance_id, row.instance_revision): row for row in canonical.substage_runtime_states}
    step_rows = []
    for instance in instances:
        runtime = runtime_by_ref.get((instance.substage_instance_id, instance.instance_revision))
        key = _substage_key_from_definition(instance.substage_definition_id)
        label = _user_facing_substage_label(key, pack=str(source.get("skillPack") or ""))
        if not label or label == key:
            label = _STAGE_DISPLAY_LABEL_ZH.get(instance.stage_key.value, "研究步骤")
        step_rows.append({"substageInstanceId": instance.substage_instance_id, "label": label,
                          "status": runtime.status.value if runtime is not None else "planned"})
    affected_ids = {row.substage_instance_id for row in blockers}
    affected = [row for row in step_rows if row["substageInstanceId"] in affected_ids]
    if not affected and kind in {"user_stop", "execution_cancelled"}:
        affected = [row for row in step_rows if row["status"] not in {"completed", "skipped"}]
    completed = [row for row in step_rows if row["status"] == "completed"]
    labels = list(dict.fromkeys(row["label"] for row in affected))
    affected_name = "、".join(labels[:3]) or "当前研究步骤"
    if kind == "external_result_unknown":
        impact = f"{affected_name}的执行结果尚未核实，依赖该结果的后续判断暂不能确认。"
    elif kind in {"user_stop", "execution_cancelled"}:
        impact = f"{affected_name}的未完成部分已停止推进；已有成果保留，不代表研究已经完成。"
    else:
        impact = f"{affected_name}暂未继续，依赖这部分工作的后续结论尚不能确认。"
    summaries = {
        "system_failure": "当前步骤因系统执行条件未满足而中断。",
        "user_input": "当前步骤缺少你掌握的必要信息。",
        "external_dependency": "当前步骤正在等待前置结果或外部条件。",
        "external_result_unknown": "外部执行可能已经开始，系统尚未确认执行结果。",
        "user_stop": "你已停止尚未完成的研究执行。",
        "execution_cancelled": "当前研究执行已取消，停止原因仍需系统确认。",
    }
    if primary is not None and kind == "system_failure":
        summaries[kind] = blocker_user_explanation(primary)["reason"]
    responsibility = "user" if kind in {"user_input", "user_stop"} else "external" if kind == "external_dependency" else "system"
    owner_labels = {
        "system_failure": "系统负责处理当前执行条件问题",
        "external_result_unknown": "系统负责核对原执行结果，避免重复执行",
        "external_dependency": "等待前置结果或外部条件，由系统协调后续推进",
        "user_input": "需要你提供明确的输入或确认",
        "user_stop": "已按你的停止操作控制执行",
        "execution_cancelled": "系统负责核对停止原因",
    }
    # Single public outlet for "what should the researcher do now":
    # system-owned blockers may still expose Workbench retry (same eligibility
    # Authority as workbench_user_actions). Never tell the user to do nothing
    # while that control remains a live Workbench action.
    user_action = "当前步骤执行受阻。可重试这一步，也可在下方告诉研究助手你的需求。"
    if kind == "user_input":
        from app.modules.workbench_user_actions import render_user_input_request

        concrete_request = render_user_input_request(canonical, primary.substage_instance_id) if primary is not None else None
        user_action = concrete_request or blocker_user_explanation(primary)["reason"]
        if stop is not None or status == "failed":
            user_action = "必要输入仍未补齐；当前执行已停止。你仍可在下方说明情况。"
    elif kind == "user_stop":
        user_action = "研究保持停止状态。若要继续，可在下方说明，或使用可用操作。"
    elif kind == "external_dependency":
        user_action = "正在等待前置结果或外部条件。你仍可在下方提问或说明需求。"
    elif kind == "external_result_unknown":
        user_action = "系统正在核对原执行结果。若仍未恢复，可重试这一步，或在下方说明需求。"
    elif kind == "execution_cancelled":
        user_action = "执行已取消，系统正在核对停止原因。你仍可在下方说明需求。"
    elif kind == "system_failure":
        user_action = "当前步骤执行受阻。可重试这一步，也可在下方告诉研究助手你的需求。"
    visible_ids = frozenset(visible_artifact_ids)
    artifacts = [
        {
            "artifactId": row.artifact_id,
            "artifactState": row.artifact_state.value,
            "version": row.version,
        }
        for row in canonical.artifacts
        if row.artifact_id in visible_ids
    ]
    usable = sum(row["artifactState"] == "usable" for row in artifacts)
    review = sum(row["artifactState"] == "needs_review" for row in artifacts)
    result_count = len(canonical.results)
    if artifacts:
        result_summary = f"本次执行已保留 {len(artifacts)} 项成果，其中 {usable} 项可用、{review} 项待审核。其他成果以各自审核状态为准。"
    else:
        result_summary = "本次执行尚无新增成果；已保存或复用的内容仍可在成果区查看。"
    if result_count:
        result_summary += f"另有 {result_count} 份执行记录保留；执行记录本身不等于已通过审核的科研结论。"
    control = None if stop is None else {
        "kind": "user_stop", "summary": "你已停止未完成的执行；执行中断原因仍然保留。" if primary is not None else summaries["user_stop"],
        "occurredAt": _public_timestamp(stop.get("committedAt")),
    }
    return {
        "revision": canonical.revision, "kind": kind, "summary": summaries[kind], "impact": impact,
        "responsibility": responsibility, "responsibilityLabel": owner_labels[kind], "userAction": user_action,
        "affectedSteps": affected, "completedSteps": completed,
        "retainedResults": {"artifacts": artifacts, "resultCount": result_count, "summary": result_summary,
                            "finalResultStatus": None if final_result_authority is None else final_result_authority.get("status")},
        "controlFact": control,
        "diagnostics": [{"reasonCode": row.reason_code, "workUnitId": row.work_unit_id,
                         "substageInstanceId": row.substage_instance_id} for row in blockers],
    }


def _active_research_design_projection(
    canonical: ResearchTaskV12,
    *,
    public_artifact_ids: Collection[str],
) -> dict[str, Any]:
    """Project confirmed execution authority and pending draft as distinct refs."""
    active_ref = canonical.active_research_design_ref
    active_design = None
    if active_ref is not None:
        active_design = next(
            (
                row
                for row in canonical.research_design_versions
                if row.research_design_id == active_ref.research_design_id
                and row.version == active_ref.version
            ),
            None,
        )
    pending_design = max(
        (
            row
            for row in canonical.research_design_versions
            if row.status.value == "draft"
        ),
        key=lambda row: row.version,
        default=None,
    )
    design = pending_design or active_design
    if design is None:
        return {
            "creationStatus": "planning",
            "creationPlan": None,
            "stageDetails": [],
            "confirmations": [],
            "pendingResearchDesignRef": None,
        }

    stage_order = (
        "problem_modeling",
        "solution_design",
        "execution_validation",
        "insight_generation",
    )
    skill_pack = _research_design_skill_pack(design)
    stages: list[dict[str, Any]] = []
    for stage_key in stage_order:
        instances = sorted(
            (row for row in design.instances if row.stage_key.value == stage_key),
            key=lambda row: (
                row.sequence_hint if row.sequence_hint is not None else 1_000_000,
                row.substage_instance_id,
            ),
        )
        substages = []
        for row in instances:
            substage_key = _substage_key_from_definition(row.substage_definition_id)
            objective = _user_facing_objective(substage_key, row.objective)
            substages.append(
                {
                    "instanceId": row.substage_instance_id,
                    "substage": substage_key,
                    "label": _user_facing_substage_label(substage_key, pack=skill_pack),
                    "action": _confirmation_plan_action(objective),
                    "expectedArtifactTypes": list(row.expected_artifact_types),
                    "expectedArtifact": row.expected_artifact_types[0],
                }
            )
        stages.append(
            {
                "stageKey": stage_key,
                "title": _confirmation_stage_title(stage_key),
                # Concise overview only — do not concatenate every work objective
                # (that duplicates the research-work list in the Workbench).
                "description": None,
                "subStages": [row["substage"] for row in substages],
                "substages": substages,
            }
        )

    coverage = design.coverage_report
    from app.modules.research_design_coverage_confirmation import (
        coverage_allows_confirmation,
    )

    ready = coverage_allows_confirmation(coverage)
    creation_status = {
        "draft": "awaiting_confirmation" if ready else "revision_requested",
        "confirmed": "confirmed",
        "cancelled": "cancelled",
        "superseded": "planning",
    }[design.status.value]
    stage_index = {stage: index for index, stage in enumerate(stage_order)}
    confirmations = []
    for row in canonical.confirmations:
        if (
            row.target_type != "research_design"
            or row.target_id != design.research_design_id
            or row.target_version != design.version
        ):
            continue
        stage = min(
            (instance.stage_key.value for instance in design.instances),
            key=stage_index.__getitem__,
            default="problem_modeling",
        )
        confirmations.append(
            {
                "id": row.confirmation_id,
                "stage": stage,
                "decision": "needs_revision" if row.decision == "request_revision" else row.decision,
                "createdAt": row.created_at.isoformat(),
            }
        )
    return {
        "creationStatus": creation_status,
        "confirmations": confirmations,
        "stageDetails": _active_stage_details(
            canonical,
            design,
            stage_order,
            public_artifact_ids=public_artifact_ids,
        ),
        "pendingResearchDesignRef": (
            None
            if pending_design is None
            else {
                "researchDesignId": pending_design.research_design_id,
                "version": pending_design.version,
            }
        ),
        "creationPlan": {
            "goalSummary": design.goal,
            "domain": "research_task",
            "skillPack": skill_pack,
            "researchType": design.understanding.research_type,
            "planningSource": "research_design_v1_2",
            "understanding": design.understanding.model_dump(mode="json", by_alias=True),
            "clarifications": list(design.understanding.missing_inputs),
            "stages": stages,
            "readyToConfirm": ready,
        },
    }


def _active_stage_details(
    canonical: ResearchTaskV12,
    design: Any,
    stage_order: tuple[str, ...],
    *,
    public_artifact_ids: Collection[str],
) -> list[dict[str, Any]]:
    """Adapt active instances to the established StageDetailOut shape."""
    runtime_by_instance = {
        (row.substage_instance_id, row.instance_revision): row
        for row in canonical.substage_runtime_states
    }
    artifacts_by_instance: dict[str, list[str]] = {}
    for artifact in canonical.artifacts:
        if artifact.artifact_id not in public_artifact_ids:
            continue
        artifacts_by_instance.setdefault(artifact.substage_instance_id, []).append(artifact.artifact_id)
    calls_by_instance: dict[str, list[str]] = {}
    for call in canonical.capability_calls:
        calls_by_instance.setdefault(call.substage_instance_id, []).append(call.capability_call_id)
    artifact_by_id = {artifact.artifact_id: artifact for artifact in canonical.artifacts}
    affected_instances = {
        work.substage_instance_id
        for work in canonical.work_units
        if any(
            (current := artifact_by_id.get(snapshot.artifact_id)) is None
            or current.version != snapshot.version
            or (
                snapshot.content_sha256 is not None
                and str((current.validator_result or {}).get("contentSha256") or "").strip().lower()
                != snapshot.content_sha256
            )
            for snapshot in work.input_artifact_snapshots
        )
    }
    status_map = {
        "planned": "not_started",
        "ready": "not_started",
        "queued": "not_started",
        "running": "running",
        "reviewing": "running",
        "needs_input": "needs_input",
        "blocked_recoverable": "blocked_recoverable",
        "insufficient_evidence": "insufficient_evidence",
        "completed": "completed",
        "failed": "failed",
        "cancelled": "not_started",
        "skipped": "not_started",
    }
    details: list[dict[str, Any]] = []
    skill_pack = _research_design_skill_pack(design)
    for stage_key in stage_order:
        instances = sorted(
            (row for row in design.instances if row.stage_key.value == stage_key),
            key=lambda row: (
                row.sequence_hint if row.sequence_hint is not None else 1_000_000,
                row.substage_instance_id,
            ),
        )
        substages = []
        for index, instance in enumerate(instances):
            runtime = runtime_by_instance.get((instance.substage_instance_id, instance.instance_revision))
            runtime_status = runtime.status.value if runtime is not None else "planned"
            substage_key = _substage_key_from_definition(instance.substage_definition_id)
            substages.append({
                "subStage": substage_key,
                "subStageInstanceId": instance.substage_instance_id,
                "parentStageKey": stage_key,
                "status": status_map[runtime_status],
                "label": _user_facing_substage_label(substage_key, pack=skill_pack),
                "action": _user_facing_objective(substage_key, instance.objective),
                "expectedArtifact": instance.expected_artifact_types[0],
                "artifactRefs": artifacts_by_instance.get(instance.substage_instance_id, []),
                "capabilityCallRefs": calls_by_instance.get(instance.substage_instance_id, []),
                "affected": instance.substage_instance_id in affected_instances,
                "sequence": instance.sequence_hint if instance.sequence_hint is not None else index,
            })
        statuses = [row["status"] for row in substages]
        stage_status = next(
            (status for status in ("failed", "needs_input", "blocked_recoverable", "running") if status in statuses),
            "completed" if substages and all(status == "completed" for status in statuses) else "not_started",
        )
        details.append({
            "stageKey": stage_key,
            "status": stage_status,
            "subStages": substages,
            "artifactRefs": [artifact_id for row in substages for artifact_id in row["artifactRefs"]],
        })
    return details


def _public_capability_call(
    call: Any,
    *,
    task: Mapping[str, Any],
    work: Any,
    stage_key: str | None,
    artifact_ids: set[str],
    execution_mode: str,
) -> dict[str, Any]:
    status = call.status.value
    return {
        "id": call.capability_call_id,
        "ownerId": task.get("ownerId"),
        "workspaceId": task.get("workspaceId"),
        "createdBy": task.get("createdBy"),
        "kind": "in_app_agent",
        "name": call.capability_id,
        "status": _CALL_STATUS[status],
        "canonicalStatus": status,
        "requestedAt": _timestamp(call.started_at),
        "finishedAt": _timestamp(call.finished_at),
        "runtime": call.runtime_binding_id,
        "artifactRefs": [ref for ref in call.output_artifact_refs if ref in artifact_ids],
        "stageKey": stage_key,
        "subStageInstanceId": call.substage_instance_id,
        "inputArtifactRefs": [ref for ref in call.input_refs if ref in artifact_ids],
        "outputArtifactRefs": [ref for ref in call.output_artifact_refs if ref in artifact_ids],
        "sourceMode": work.source_mode.value,
        "targetAgent": work.responsible_actor_ref.actor_id,
        "executionMode": execution_mode,
        "workUnitId": call.work_unit_id,
        "capabilityType": call.capability_type.value,
        "capabilityId": call.capability_id,
        "attemptNumber": call.attempt_number,
    }


def _public_artifact(
    artifact: Any,
    *,
    task: Mapping[str, Any],
    work: Any,
    source_call: Any | None,
    stage_key: str | None,
    catalog_row: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    diagnostic = _safe_validation_diagnostic(artifact.validator_result)
    result_protocol = _safe_result_protocol(
        artifact.validator_result.get("resultProtocol")
        if isinstance(artifact.validator_result, Mapping)
        else None
    )
    artifact_quality = (
        "degraded"
        if result_protocol is not None and result_protocol.get("status") == "degraded"
        else "standard"
    )
    protocol_warnings = (
        list(result_protocol.get("warnings") or []) if result_protocol is not None else []
    )
    usable = artifact.artifact_state.value == "usable"
    error_code = diagnostic.get("errorCode")
    issues = []
    if error_code:
        issues.append(
            {
                "code": error_code,
                "severity": "error",
                "message": "Artifact validation failed at the recorded contract field paths.",
            }
        )
    created_at = (
        source_call.finished_at
        if source_call is not None and source_call.finished_at is not None
        else work.finished_at or work.dispatched_at
    )
    # Render-facing fields (authored title, file format, producer skill) live
    # only in the openair_artifacts catalog; merge them so the workbench can
    # route formats (markdown/cif/png/svg) and show authored titles instead of
    # collapsing every artifact to its artifactType.
    catalog = catalog_row if isinstance(catalog_row, Mapping) else {}
    catalog_title = str(catalog.get("title") or "").strip()
    catalog_format = str(catalog.get("format") or "").strip()
    catalog_skill = str(
        catalog.get("relatedSkillId")
        or (catalog.get("metadata") or {}).get("relatedSkillId")
        or ""
    ).strip()
    return {
        "id": artifact.artifact_id,
        "taskId": task.get("taskId"),
        "sourceTaskId": task.get("taskId"),
        "ownerId": task.get("ownerId"),
        "workspaceId": task.get("workspaceId"),
        "createdBy": task.get("createdBy"),
        "kind": "execution_output",
        "title": catalog_title or artifact.artifact_type,
        "format": catalog_format or None,
        "relatedSkillId": catalog_skill or None,
        "createdAt": _timestamp(created_at),
        "relatedStage": stage_key,
        "stage": stage_key,
        "subStageInstanceId": artifact.substage_instance_id,
        "sourceCapabilityCallId": artifact.source_capability_call_id,
        "workUnitId": artifact.work_unit_id,
        "sourceRefs": list(artifact.source_refs),
        "lineageKind": artifact.lineage_kind.value,
        "canonicalVersion": artifact.version,
        "artifactState": artifact.artifact_state.value,
        "artifactType": artifact.artifact_type,
        "storageType": None,
        "contentSha256": diagnostic.get("contentSha256"),
        "resultProtocol": result_protocol,
        "artifactQuality": artifact_quality,
        "warnings": protocol_warnings,
        "validatorResult": {
            "contractVersion": diagnostic.get("artifactContract") or "canonical-artifact.v1",
            "evaluator": "research_orchestration_v1_2",
            "artifactId": artifact.artifact_id,
            "sourceCapabilityCallId": artifact.source_capability_call_id,
            "artifactState": artifact.artifact_state.value,
            "usable": usable,
            "missingInputs": [],
            "issues": issues,
            "nextAction": "none" if usable else "review_artifact_validation",
        },
        "canonicalValidation": diagnostic,
}


def _safe_result_protocol(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        return normalize_result_protocol(value)
    except ValueError:
        return None


def _safe_validation_diagnostic(value: Mapping[str, Any]) -> dict[str, Any]:
    def safe_text(*keys: str, limit: int = 256) -> str | None:
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()[:limit]
        return None

    raw_paths = value.get("safeFieldPaths", value.get("fieldPaths", []))
    field_paths = [
        item.strip()[:512]
        for item in raw_paths
        if isinstance(item, str) and item.strip().startswith("/")
    ][:16] if isinstance(raw_paths, list) else []
    return {
        "status": safe_text("status"),
        "artifactContract": safe_text("artifactContract"),
        "artifactSchemaDigest": safe_text("artifactSchemaDigest", limit=128),
        "contentSha256": safe_text("contentSha256", limit=128),
        "errorCode": safe_text("safeErrorCode", "errorCode"),
        "fieldPaths": field_paths,
    }


def _timestamp(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


_DEFAULT_STAGES = [
    "problem_modeling",
    "solution_design",
    "execution_validation",
    "insight_generation",
]


def project_public_task_list_item(task: Mapping[str, Any]) -> TaskOut:
    """Board/list projection: identity + status fields only, no heavy collections.

    Omits researchDesignVersions / stageCatalog / creationPlan / stageDetails /
    capabilityCalls / workUnits / embedded artifacts so list responses stay small.
    Catalog artifact summary is attached by the list route separately.
    """
    for field in (
        "stages",
        "researchDesignVersions",
        "stageCatalog",
        "stageDetails",
        "capabilityCalls",
        "workUnits",
        "artifacts",
    ):
        if field in task and task[field] is not None and not isinstance(task[field], list):
            raise ValueError(f"{field} must be a list when present")
    if (
        "progressProjection" in task
        and task["progressProjection"] is not None
        and not isinstance(task["progressProjection"], dict)
    ):
        raise ValueError("progressProjection must be an object when present")

    task_id = str(task.get("taskId") or "").strip() or "unknown-task"
    stage = str(task.get("currentStage") or "problem_modeling")
    if stage not in _DEFAULT_STAGES:
        stage = "problem_modeling"
    status = str(task.get("status") or "not_started")
    if status not in {
        "draft",
        "not_started",
        "running",
        "awaiting_confirmation",
        "completed",
        "failed",
        "cancelled",
    }:
        status = "not_started"
    owner_id = str(task.get("ownerId") or "unknown-owner")
    workspace_id = str(task.get("workspaceId") or "unknown-workspace")
    title = str(task.get("title") or "").strip() or f"任务 {task_id[:8]}"
    creation_status = task.get("creationStatus")
    if creation_status is not None:
        creation_status = str(creation_status)
        if creation_status not in {
            "clarifying",
            "planning",
            "awaiting_confirmation",
            "revision_requested",
            "confirmed",
            "cancelled",
        }:
            creation_status = None
    execution_mode = str(task.get("executionMode") or "stage_confirmation")
    if execution_mode not in {"automatic_research", "stage_confirmation", "manual_step"}:
        execution_mode = "stage_confirmation"
    result_source = str(task.get("resultSource") or "none")
    if result_source not in {"none", "live", "replay", "demo"}:
        result_source = "none"
    goal = task.get("goalSummary")
    goal_summary = str(goal).strip() if isinstance(goal, str) and goal.strip() else None
    domain = task.get("domain")
    domain_out = str(domain).strip() if isinstance(domain, str) and domain.strip() else None
    skill_pack = task.get("skillPack")
    skill_pack_out = str(skill_pack).strip() if isinstance(skill_pack, str) and skill_pack.strip() else None
    progress = task.get("progressProjection")
    progress_out = deepcopy(progress) if isinstance(progress, dict) else None
    _filter_public_artifact_surface(
        {"artifacts": [], "progressProjection": progress_out}, visible_artifact_ids=(),
    )
    return TaskOut.model_validate(
        {
            "taskId": task_id,
            "ownerId": owner_id,
            "workspaceId": workspace_id,
            "createdBy": str(task.get("createdBy") or owner_id),
            "title": title,
            "revision": max(int(task.get("revision") or 0), 0),
            "goalSummary": goal_summary,
            "domain": domain_out,
            "skillPack": skill_pack_out,
            "currentStage": stage,
            "status": status,
            "resultSource": result_source,
            "executionMode": execution_mode,
            "creationStatus": creation_status,
            "stages": list(_DEFAULT_STAGES),
            "createdAt": _public_timestamp(task.get("createdAt")),
            "updatedAt": _public_timestamp(task.get("updatedAt")),
            "progressProjection": progress_out,
            "researchDesignVersions": [],
            "stageCatalog": [],
            "stageDetails": [],
            "artifacts": [],
            "capabilityCalls": [],
            "workUnits": [],
            "materials": [],
            "memoryRecords": [],
            "confirmations": [],
            "trustRecords": [],
            "userVisibleEvents": [],
            "creationPlan": None,
            "activeResearchDesignRef": None,
            "pendingResearchDesignRef": None,
            "finalResultAuthority": None,
        }
    )
