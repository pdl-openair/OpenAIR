"""Durable handoff for per-attempt external execution results."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
from typing import Any, Iterator, Literal
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from app.config import settings


class ExternalExecutionCheckpointError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExternalExecutionCheckpoint:
    capability_call_id: str
    dispatch_id: str
    attempt_id: str
    execution_kind: str
    phase: Literal["prepared", "submitted", "response_received", "canonical_completed"]
    provider_id: str
    adapter: str
    request_hash: str
    response_kind: str | None
    response_payload: bytes | None
    response_hash: str | None
    response_http_status: int | None


class ExternalExecutionCheckpointRepository:
    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection[Any]]:
        conninfo = (
            f"host={settings.postgres_host} port={settings.postgres_port} "
            f"dbname={settings.postgres_database} user={settings.postgres_user}"
        )
        conn = psycopg.connect(
            conninfo, password=settings.postgres_password, row_factory=dict_row
        )  # type: ignore[arg-type]
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def prepare(
        self,
        *,
        capability_call_id: str,
        dispatch_id: str,
        execution_kind: str,
        provider_id: str,
        adapter: str,
        request_body: bytes,
    ) -> ExternalExecutionCheckpoint:
        if execution_kind not in {
            "provider_http",
            "external_adapter",
            "skill_script",
            "direct_skill_runtime",
        }:
            raise ExternalExecutionCheckpointError("external_checkpoint_execution_kind_invalid")
        values = tuple(
            str(value or "").strip()
            for value in (capability_call_id, dispatch_id, provider_id, adapter)
        )
        if not all(values) or not request_body:
            raise ExternalExecutionCheckpointError("external_checkpoint_identity_invalid")
        request_hash = hashlib.sha256(request_body).hexdigest()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO openair_external_execution_checkpoints (
                           capability_call_id, dispatch_id, attempt_id, execution_kind,
                           provider_id, adapter, request_hash
                       ) SELECT %s, %s, %s, %s, %s, %s, %s
                        WHERE EXISTS (
                            SELECT 1 FROM openair_dispatch WHERE dispatch_id = %s
                        )
                       ON CONFLICT (capability_call_id) DO NOTHING""",
                    (
                        values[0],
                        values[1],
                        f"eat_{uuid4().hex}",
                        execution_kind,
                        values[2],
                        values[3],
                        request_hash,
                        values[1],
                    ),
                )
        checkpoint = self.get(capability_call_id=values[0])
        if checkpoint is None:
            raise ExternalExecutionCheckpointError("external_checkpoint_prepare_failed")
        if (
            checkpoint.dispatch_id != values[1]
            or checkpoint.execution_kind != execution_kind
            or checkpoint.provider_id != values[2]
            or checkpoint.adapter != values[3]
            or checkpoint.request_hash != request_hash
        ):
            raise ExternalExecutionCheckpointError("external_checkpoint_prepare_conflict")
        return checkpoint

    def get(self, *, capability_call_id: str) -> ExternalExecutionCheckpoint | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT capability_call_id, dispatch_id, attempt_id, execution_kind,
                              provider_id, adapter, request_hash, phase, response_kind,
                              response_payload, response_hash, response_http_status
                         FROM openair_external_execution_checkpoints
                        WHERE capability_call_id = %s""",
                    (capability_call_id,),
                )
                row = cur.fetchone()
        if row is None:
            return None
        payload = row["response_payload"]
        if isinstance(payload, memoryview):
            payload = payload.tobytes()
        checkpoint = ExternalExecutionCheckpoint(
            capability_call_id=str(row["capability_call_id"]),
            dispatch_id=str(row["dispatch_id"]),
            attempt_id=str(row["attempt_id"]),
            execution_kind=str(row["execution_kind"]),
            provider_id=str(row["provider_id"]),
            adapter=str(row["adapter"]),
            request_hash=str(row["request_hash"]),
            phase=str(row["phase"]),  # type: ignore[arg-type]
            response_kind=str(row["response_kind"]) if row["response_kind"] else None,
            response_payload=payload,
            response_hash=str(row["response_hash"]) if row["response_hash"] else None,
            response_http_status=(
                int(row["response_http_status"])
                if row["response_http_status"] is not None
                else None
            ),
        )
        if payload is not None and hashlib.sha256(payload).hexdigest() != checkpoint.response_hash:
            raise ExternalExecutionCheckpointError("external_checkpoint_response_hash_mismatch")
        return checkpoint

    @staticmethod
    def _mark_submitted(cur: Any, *, capability_call_id: str) -> bool:
        cur.execute(
            """UPDATE openair_external_execution_checkpoints
                  SET phase = 'submitted', submitted_at = clock_timestamp(),
                      updated_at = clock_timestamp()
                WHERE capability_call_id = %s AND phase = 'prepared'
               RETURNING attempt_id""",
            (capability_call_id,),
        )
        return cur.fetchone() is not None

    def mark_submitted(self, *, capability_call_id: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                return self._mark_submitted(cur, capability_call_id=capability_call_id)

    def mark_submitted_in_transaction(self, cur: Any, *, capability_call_id: str) -> bool:
        return self._mark_submitted(cur, capability_call_id=capability_call_id)

    def mark_not_submitted(self, *, capability_call_id: str) -> bool:
        """Restore retry authority only after transport proved no request was sent."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE openair_external_execution_checkpoints
                          SET phase = 'prepared', submitted_at = NULL,
                              updated_at = clock_timestamp()
                        WHERE capability_call_id = %s
                          AND phase = 'submitted'
                          AND response_payload IS NULL
                       RETURNING attempt_id""",
                    (capability_call_id,),
                )
                return cur.fetchone() is not None

    def stage_response(
        self,
        *,
        capability_call_id: str,
        response_kind: str,
        payload: bytes,
        http_status: int | None = None,
    ) -> ExternalExecutionCheckpoint:
        if response_kind not in {
            "provider_http_response",
            "adapter_result",
            "skill_script_result",
            "dispatch_completed",
        } or not payload:
            raise ExternalExecutionCheckpointError("external_checkpoint_response_invalid")
        digest = hashlib.sha256(payload).hexdigest()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE openair_external_execution_checkpoints
                          SET phase = 'response_received', response_kind = %s,
                              response_payload = %s, response_hash = %s,
                              response_http_status = %s,
                              submitted_at = COALESCE(submitted_at, clock_timestamp()),
                              response_received_at = COALESCE(
                                  response_received_at, clock_timestamp()
                              ),
                              updated_at = clock_timestamp()
                        WHERE capability_call_id = %s
                          AND phase IN ('prepared', 'submitted', 'response_received')
                          AND (
                              phase = 'submitted'
                              OR phase = 'prepared' AND %s = 'dispatch_completed'
                              OR phase = 'response_received'
                                 AND response_kind = %s
                                 AND response_hash = %s
                                 AND response_http_status IS NOT DISTINCT FROM %s
                              OR phase = 'response_received'
                                 AND response_kind IN (
                                     'provider_http_response', 'adapter_result',
                                     'skill_script_result'
                                 )
                                 AND %s = 'dispatch_completed'
                          )
                       RETURNING attempt_id""",
                    (
                        response_kind,
                        payload,
                        digest,
                        http_status,
                        capability_call_id,
                        response_kind,
                        response_kind,
                        digest,
                        http_status,
                        response_kind,
                    ),
                )
                if cur.fetchone() is None:
                    raise ExternalExecutionCheckpointError(
                        "external_checkpoint_response_conflict"
                    )
        checkpoint = self.get(capability_call_id=capability_call_id)
        if checkpoint is None:
            raise ExternalExecutionCheckpointError("external_checkpoint_missing")
        return checkpoint

    def mark_canonical_completed(self, *, capability_call_id: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE openair_external_execution_checkpoints
                          SET phase = 'canonical_completed',
                              canonical_completed_at = COALESCE(
                                  canonical_completed_at, clock_timestamp()
                              ),
                              updated_at = clock_timestamp()
                        WHERE capability_call_id = %s
                          AND phase IN ('response_received', 'canonical_completed')
                       RETURNING attempt_id""",
                    (capability_call_id,),
                )
                return cur.fetchone() is not None
