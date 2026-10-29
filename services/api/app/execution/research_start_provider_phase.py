"""Shared fenced ownership for Research Start Provider phases.

The Planner uses the same durable boundary: a Research Start attempt is
begun under the request fence, physical Provider I/O is attributed to that
request, and every exit is settled before responsibility can move on.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from app.modules.provider_transport_evidence import provider_execution_parent
from app.modules.research_start_thinking_store import (
    push_thinking_writer,
    reset_thinking_writer,
    writer_for_request,
)


class ResearchStartProviderPhase:
    """Own one logical Planner Provider attempt until it is settled."""

    def __init__(
        self,
        *,
        repository: Any,
        request_id: str,
        worker_id: str,
        lease_epoch: int,
        phase: str,
        ordinal: int,
        lease_lost: Callable[[], bool],
    ) -> None:
        self.repository = repository
        self.request_id = request_id
        self.worker_id = worker_id
        self.lease_epoch = lease_epoch
        self.phase = phase
        self.ordinal = ordinal
        self._lease_lost = lease_lost
        self.attempt_id: str | None = None
        self.settled = False

    def begin(self) -> bool:
        begun = self.repository.begin_attempt(
            self.request_id,
            self.worker_id,
            self.lease_epoch,
            self.phase,
            self.ordinal,
        )
        self.attempt_id = str(begun["attemptId"])
        return bool(begun["externalCallPermitted"])

    def execute(self, operation: Callable[[], Any]) -> Any:
        """Execute with a durable physical-I/O parent bound for the planning phase."""
        if self.attempt_id is None:
            raise RuntimeError("research_start_provider_phase_not_begun")
        connect = getattr(self.repository, "_connect", None)
        writer = None
        token = None
        if connect is not None:
            writer = writer_for_request(connect, self.request_id, self.phase, self.ordinal)
            token = push_thinking_writer(writer)
        try:
            with provider_execution_parent(
                kind="research_start",
                parent_id=self.request_id,
                originating_authority=f"research_start:{self.phase}",
            ):
                return operation()
        finally:
            if writer is not None:
                writer.seal()
            if token is not None:
                reset_thinking_writer(token)

    def succeed(self, result: Mapping[str, Any]) -> None:
        if self.attempt_id is None or self.settled or self._lease_lost():
            return
        self.repository.finish_attempt(
            self.request_id,
            self.worker_id,
            self.lease_epoch,
            self.attempt_id,
            succeeded=True,
            result=dict(result),
        )
        self.settled = True

    def fail(self, result: Mapping[str, Any]) -> str:
        """Settle against physical evidence under the repository authority.

        Production repositories perform this as one locked decision over the
        Session, Request/fence, logical Attempt, physical attempt evidence, and
        any authoritative Task receipt. Narrow unit-test repositories retain a
        compatibility fallback to the original fenced ``finish_attempt`` call.
        """
        if self.attempt_id is None or self.settled or self._lease_lost():
            return "lease_lost"
        settle = getattr(self.repository, "settle_provider_phase_failure", None)
        if callable(settle):
            disposition = str(
                settle(
                    self.request_id,
                    self.worker_id,
                    self.lease_epoch,
                    self.attempt_id,
                    failure_result=dict(result),
                )
            )
        else:
            self.repository.finish_attempt(
                self.request_id,
                self.worker_id,
                self.lease_epoch,
                self.attempt_id,
                succeeded=False,
                result=dict(result),
            )
            disposition = "known_failure"
        self.settled = True
        return disposition

    def unknown(self, result: Mapping[str, Any]) -> None:
        if self.attempt_id is None or self.settled or self._lease_lost():
            return
        payload = dict(result)
        payload["providerExecutionId"] = self.attempt_id
        self.repository.mark_external_result_unknown(
            self.request_id,
            self.worker_id,
            self.lease_epoch,
            self.attempt_id,
            payload,
        )
        self.settled = True
