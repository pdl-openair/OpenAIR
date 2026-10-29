"""Explicit reuse judgments at the existing checked Result persistence boundary."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.modules.research_scientific_state_materialization import (
    materialize_declared_scientific_facts,
)
from app.modules.target_output_completion import TargetOutputReuseAcceptance


class TargetOutputReuseJudgment(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    targetOutputRef: str = Field(min_length=1)
    responsibilityKey: str = Field(min_length=1)
    reusedResultRefs: list[str] = Field(min_length=1)
    verdict: str = Field(pattern="^accepted$")
    rationale: Any = None


def _one(payload: Mapping[str, Any], collection: str, key: str, value: Any) -> Any:
    matches = [row for row in payload.get(collection, []) if row.get(key) == value]
    return matches[0] if len(matches) == 1 else None


def record_result_scientific_state_versions(
    payload: dict[str, Any], result_id: str, previous: set[int]
) -> None:
    result = _one(payload, "results", "resultId", result_id)
    result["scientificStateVersions"] = list(
        dict.fromkeys(
            [
                *result.get("scientificStateVersions", []),
                *(
                    row["scientificStateVersion"]
                    for row in payload.get("scientificStates", [])
                    if row["scientificStateVersion"] not in previous
                ),
            ]
        )
    )


def materialize_checked_agent_scientific_facts(
    *,
    task_payload: Mapping[str, Any],
    result_id: str,
    validated_artifact_content: str,
    occurred_at: datetime,
) -> dict[str, Any]:
    """Keep pending judgments on Result; accepted facts require usable output."""
    from app.modules.ot_v3_scientific_state_materialization import (
        materialize_ot_v3_general_agent_result,
    )

    payload = materialize_ot_v3_general_agent_result(
        task_payload=task_payload,
        result_id=result_id,
        validated_artifact_content=validated_artifact_content,
        occurred_at=occurred_at,
    )
    result = _one(payload, "results", "resultId", result_id)
    if result is None or result["checkStatus"] not in {"passed", "pending"}:
        return payload
    work = _one(payload, "workUnits", "workUnitId", result["workUnitId"])
    if work is None or (work.get("executorRef") or {}).get("executorType") != "agent":
        return payload
    try:
        content = json.loads(validated_artifact_content)
    except (TypeError, ValueError):
        return payload
    rows = content.get("targetOutputReuseJudgments") if isinstance(content, Mapping) else None
    if rows is not None:
        accepted_rows = []
        gaps = []
        if not isinstance(rows, list):
            gaps.append("target_output_reuse_judgments:not_list")
            rows = []
        for index, row in enumerate(rows):
            try:
                accepted_rows.append(TargetOutputReuseJudgment.model_validate(row).model_dump())
            except ValidationError as exc:
                gaps.append(f"target_output_reuse_judgments:invalid_row:{index}")
                logging.getLogger(__name__).warning(
                    "reuse_judgment_projection_gap row=%s errors=%s", index, exc.errors()
                )
        check = result.setdefault("checkResult", {})
        check["targetOutputReuseJudgments"] = accepted_rows
        if gaps:
            check.setdefault("warnings", []).extend(gaps)
    return materialize_accepted_reuse_judgments(
        payload, result_id=result_id, occurred_at=occurred_at
    )


def materialize_accepted_reuse_judgments(
    payload: dict[str, Any],
    *,
    result_id: str,
    occurred_at: datetime,
) -> dict[str, Any]:
    result = _one(payload, "results", "resultId", result_id)
    if result is None or result["checkStatus"] != "passed":
        return payload
    rows = (result.get("checkResult") or {}).get("targetOutputReuseJudgments")
    if not rows:
        return payload
    work = _one(payload, "workUnits", "workUnitId", result["workUnitId"])
    active = payload.get("activeResearchDesignRef") or {}
    if (
        work is None
        or work["researchDesignVersion"] != active.get("version")
        or (work.get("executorRef") or {}).get("executorType") != "agent"
    ):
        return payload
    previous = {row["scientificStateVersion"] for row in payload.get("scientificStates", [])}
    for artifact_ref in result.get("artifactRefs", []):
        artifact = _one(payload, "artifacts", "artifactId", artifact_ref)
        if artifact is None or artifact.get("workUnitId") != work["workUnitId"]:
            continue
        validator = artifact.get("validatorResult") or {}
        if (
            artifact.get("sourceResultId") != result_id
            or artifact.get("artifactState") != "usable"
            or validator.get("status") != "passed"
            or validator.get("artifactContract") != "general_research_reasoning_artifact_v1"
        ):
            continue
        acceptances = []
        for index, raw in enumerate(rows):
            try:
                judgment = TargetOutputReuseJudgment.model_validate(raw)
            except ValidationError as exc:
                logging.getLogger(__name__).warning(
                    "reuse_judgment_projection_gap row=%s errors=%s", index, exc.errors()
                )
                result.setdefault("checkResult", {}).setdefault("warnings", []).append(
                    f"target_output_reuse_judgments:invalid_row:{index}"
                )
                continue
            if not set(judgment.reusedResultRefs).issubset(work.get("inputArtifactRefs") or []):
                raise ValueError("reuse judgment sources were not supplied to this work")
            acceptances.append(
                TargetOutputReuseAcceptance(
                    researchDesignId=active["researchDesignId"],
                    researchDesignVersion=work["researchDesignVersion"],
                    targetOutputRef=judgment.targetOutputRef,
                    responsibilityKey=judgment.responsibilityKey,
                    reusedResultRefs=judgment.reusedResultRefs,
                    acceptanceArtifactRef=artifact_ref,
                    verdict=judgment.verdict,
                ).model_dump()
            )
        if not acceptances:
            continue
        payload = materialize_declared_scientific_facts(
            task_payload=payload,
            projected_facts={"targetOutputReuseAcceptances": acceptances},
            source_artifact_id=artifact_ref,
            research_design_version=work["researchDesignVersion"],
            occurred_at=occurred_at,
        )
        _record_reuse_acceptance(payload, work, result_id, artifact_ref, occurred_at)
    record_result_scientific_state_versions(payload, result_id, previous)
    return payload


def _record_reuse_acceptance(
    payload: dict[str, Any],
    work: Mapping[str, Any],
    result_id: str,
    artifact_ref: str,
    occurred_at: datetime,
) -> None:
    records = payload.setdefault("agentInteractionRecords", [])
    if any(
        row["interactionType"] in {"acceptance_recorded", "target_output_reuse_accepted"}
        and (row.get("verdict") or {}).get("decision") == "approved"
        and artifact_ref in [*row.get("inputArtifactRefs", []), *row.get("outputArtifactRefs", [])]
        for row in records
    ):
        return
    identity = f"reuse-acceptance:{result_id}:{artifact_ref}"
    records.append(
        {
            "interactionId": identity,
            "idempotencyKey": identity,
            "taskId": payload["taskId"],
            "executionDecisionId": work["executionDecisionId"],
            "sourceAssignmentId": work["assignmentId"],
            "sourceWorkUnitId": work["workUnitId"],
            "targetAssignmentId": None,
            "targetWorkUnitId": None,
            "interactionType": "target_output_reuse_accepted",
            "inputArtifactRefs": [],
            "outputArtifactRefs": [artifact_ref],
            "verdict": {
                "verdictType": "acceptance",
                "decision": "approved",
                "rationaleCode": "explicit_current_target_reuse_judgment_accepted",
            },
            "summary": "Current result accepts supplied materials for scoped reuse targets.",
            "createdAt": occurred_at.isoformat(),
        }
    )
