from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assert safe durable T10 completion facts.")
    parser.add_argument("task_id")
    parser.add_argument("--artifact-root", required=True)
    return parser.parse_args()


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise AssertionError(code)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    args = _parse_args()
    conninfo = (
        f"host={os.environ['POSTGRES_HOST']} port={os.environ['POSTGRES_PORT']} "
        f"dbname={os.environ['POSTGRES_DATABASE']} user={os.environ['POSTGRES_USER']}"
    )
    with psycopg.connect(
        conninfo,
        password=os.environ["POSTGRES_PASSWORD"],
        row_factory=dict_row,
        connect_timeout=10,
    ) as connection:
        task_row = connection.execute(
            "SELECT payload, revision, status FROM openair_tasks WHERE task_id = %s",
            (args.task_id,),
        ).fetchone()
        _require(task_row is not None, "task_missing")
        payload = task_row["payload"]
        _require(isinstance(payload, dict), "task_payload_invalid")

        work_units = payload.get("workUnits") or []
        calls = payload.get("capabilityCalls") or []
        artifacts = payload.get("artifacts") or []
        decisions = payload.get("executionDecisions") or []
        interactions = payload.get("agentInteractionRecords") or []
        projection = payload.get("progressProjection") or {}

        _require(task_row["status"] == "completed", "task_not_completed")
        _require(payload.get("status") == "completed", "payload_not_completed")
        _require(projection.get("revision") == task_row["revision"], "projection_revision_mismatch")
        _require(
            (projection.get("taskProgress") or {}).get("status") == "completed",
            "projection_not_completed",
        )
        _require(len(work_units) >= 2, "multiagent_work_units_missing")
        _require(all(row.get("status") == "completed" for row in work_units), "work_unit_not_completed")
        _require(len(calls) == len(work_units), "capability_call_count_mismatch")
        _require(all(row.get("status") == "succeeded" for row in calls), "capability_call_not_succeeded")
        _require(len(artifacts) >= 1, "producer_artifact_missing")
        _require(
            all(row.get("sourceCapabilityCallId") for row in artifacts),
            "artifact_source_call_missing",
        )
        _require(
            all((row.get("validatorResult") or {}).get("status") == "passed" for row in artifacts),
            "artifact_validator_not_passed",
        )
        _require(
            any((row.get("verdict") or {}).get("decision") == "approved" for row in interactions),
            "mandatory_review_not_approved",
        )
        _require(
            all(row.get("status") == "closed" and row.get("outcome") == "succeeded" for row in decisions),
            "execution_decision_not_succeeded",
        )

        lineage = connection.execute(
            """SELECT a.artifact_id, a.work_unit_id, a.execution_decision_id,
                      a.substage_instance_id, a.source_capability_call_id,
                      c.work_unit_id AS call_work_unit_id,
                      c.substage_instance_id AS call_substage_instance_id
                 FROM openair_artifact_lineage AS a
                 JOIN openair_capability_call_lineage AS c
                   ON c.task_id = a.task_id
                  AND c.capability_call_id = a.source_capability_call_id
                WHERE a.task_id = %s
                ORDER BY a.artifact_id""",
            (args.task_id,),
        ).fetchall()
        _require(len(lineage) == len(artifacts), "artifact_lineage_count_mismatch")
        _require(
            all(
                row["work_unit_id"] == row["call_work_unit_id"]
                and row["substage_instance_id"] == row["call_substage_instance_id"]
                for row in lineage
            ),
            "artifact_call_lineage_mismatch",
        )

        catalog = connection.execute(
            """SELECT artifact_id, storage_type, storage_ref, content_sha256,
                      source_capability_call_id, metadata
                 FROM openair_artifacts
                WHERE source_task_id = %s
                ORDER BY artifact_id""",
            (args.task_id,),
        ).fetchall()
        _require(len(catalog) == len(artifacts), "artifact_catalog_count_mismatch")

    workspace_root = Path(args.artifact_root).resolve()
    os.environ["OPENAIR_WORKSPACE_ROOT"] = str(workspace_root)
    services_api = Path(__file__).resolve().parents[1] / "services" / "api"
    sys.path.insert(0, str(services_api))
    from app.modules.artifact_write import _durable_local_path

    artifact_root = (workspace_root / args.task_id).resolve()
    readable_artifacts = 0
    for row in catalog:
        _require(row["storage_type"] == "local", "artifact_storage_not_local")
        storage_ref = str(row["storage_ref"] or "")
        path = _durable_local_path(args.task_id, storage_ref)
        _require(path.is_relative_to(artifact_root), "artifact_storage_ref_escaped_root")
        _require(path.is_file(), "artifact_body_missing")
        body = _load_json(path)
        _require(isinstance(body, dict), "artifact_body_not_object")
        _require(
            set(body) == {
                "question_candidates",
                "scope_boundary",
                "assumptions",
                "verification_readiness",
                "next_evidence_needs",
                "handoff",
            },
            "artifact_body_contract_fields_mismatch",
        )
        _require("providerId" not in body and "adapter" not in body, "artifact_body_exposes_evidence_envelope")
        readable_artifacts += 1

    print(
        json.dumps(
            {
                "taskId": args.task_id,
                "status": "completed",
                "revision": task_row["revision"],
                "workUnitCount": len(work_units),
                "capabilityCallCount": len(calls),
                "producerArtifactCount": len(artifacts),
                "approvedReviewCount": sum(
                    (row.get("verdict") or {}).get("decision") == "approved"
                    for row in interactions
                ),
                "closedDecisionCount": len(decisions),
                "lineageCount": len(lineage),
                "readableArtifactCount": readable_artifacts,
            },
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
