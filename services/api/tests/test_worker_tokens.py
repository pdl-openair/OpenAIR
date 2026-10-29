"""Phase-2 #1: per-worker ephemeral token.

A stolen worker token must only work for one exact execution epoch. The token is
HMAC-signed over task/owner/execution/epoch and callback endpoints validate it against
server-side callback authority. Global credentials remain control-plane only.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config import settings

SECRET = "test-secret"


def _expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=10)


def _run_async(awaitable):
    """Run a coroutine without clearing the process-wide legacy event loop."""
    try:
        previous = asyncio.get_event_loop()
    except RuntimeError:
        previous = None
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(awaitable)
    finally:
        loop.close()
        if previous is not None and not previous.is_closed():
            asyncio.set_event_loop(previous)
        else:
            asyncio.set_event_loop(asyncio.new_event_loop())


async def _register(reg, eid: str, tid: str) -> None:
    reg.register(eid, tid, "owner-1", "epoch-1", _expiry())


async def _register_stream(reg, eid: str, tid: str) -> None:
    reg.register_stream(eid, tid, "owner-1", "epoch-1", _expiry())


def _secret(monkeypatch):
    monkeypatch.setattr(settings, "ingest_callback_secret", SECRET)


# ---------------------------------------------------------------------------
# mint / verify
# ---------------------------------------------------------------------------


class TestMintVerify:
    def test_round_trip(self, monkeypatch):
        _secret(monkeypatch)
        from app.execution.worker_tokens import mint_worker_token, verify_worker_token

        token = mint_worker_token("task-1", "owner-1", "exec-1", "epoch-1")
        assert token.startswith("wt.")
        assert token.count(".") == 7
        assert verify_worker_token(
            token,
            expected_task_id="task-1",
            expected_owner_id="owner-1",
            expected_execution_id="exec-1",
            expected_execution_epoch="epoch-1",
        )
        assert not verify_worker_token(token, expected_task_id="task-1")

    def test_rejects_wrong_execution_or_epoch(self, monkeypatch):
        _secret(monkeypatch)
        from app.execution.worker_tokens import mint_worker_token, verify_worker_token

        token = mint_worker_token("task-1", "owner-1", "exec-1", "epoch-1")
        base = {
            "expected_task_id": "task-1",
            "expected_owner_id": "owner-1",
            "expected_execution_id": "exec-1",
            "expected_execution_epoch": "epoch-1",
        }
        assert not verify_worker_token(token, **{**base, "expected_task_id": "task-OTHER"})
        assert not verify_worker_token(token, **{**base, "expected_owner_id": "owner-OTHER"})
        assert not verify_worker_token(token, **{**base, "expected_execution_id": "exec-2"})
        assert not verify_worker_token(token, **{**base, "expected_execution_epoch": "epoch-2"})

    def test_rejects_tampered_signature(self, monkeypatch):
        _secret(monkeypatch)
        from app.execution.worker_tokens import mint_worker_token, verify_worker_token

        token = mint_worker_token("task-1", "owner-1", "exec-1", "epoch-1")
        bad = token[:-1] + ("0" if token[-1] != "0" else "1")
        assert not verify_worker_token(
            bad,
            expected_task_id="task-1",
            expected_owner_id="owner-1",
            expected_execution_id="exec-1",
            expected_execution_epoch="epoch-1",
        )

    def test_rejects_garbage_and_legacy_shapes(self, monkeypatch):
        _secret(monkeypatch)
        from app.execution.worker_tokens import verify_worker_token

        assert not verify_worker_token("")
        assert not verify_worker_token("not-a-token")
        assert not verify_worker_token("wt.onlythree.parts")
        assert not verify_worker_token("wt.dGFzay0x.b3duZXItMQ==.deadbeef")

    def test_explicit_expiry(self, monkeypatch):
        _secret(monkeypatch)
        from app.execution.worker_tokens import mint_worker_token, verify_worker_token

        token = mint_worker_token(
            "task-1", "owner-1", "exec-1", "epoch-1", now=1000.0, ttl_seconds=60
        )
        expected = {
            "expected_task_id": "task-1",
            "expected_owner_id": "owner-1",
            "expected_execution_id": "exec-1",
            "expected_execution_epoch": "epoch-1",
        }
        assert verify_worker_token(token, now=1059.0, **expected)
        assert not verify_worker_token(token, now=1060.0, **expected)

    def test_task_only_legacy_token_never_authorizes_callback(self, monkeypatch):
        _secret(monkeypatch)
        from app.execution.worker_tokens import mint_worker_token, verify_worker_token

        token = mint_worker_token("task-1", "owner-1")
        assert not verify_worker_token(
            token,
            expected_task_id="task-1",
            expected_owner_id="owner-1",
            expected_execution_id="exec-new",
            expected_execution_epoch="epoch-new",
        )


# ---------------------------------------------------------------------------
# env injection (mint only when the flag is on)
# ---------------------------------------------------------------------------


def _req():
    from app.execution.runtime_boundary import ExecutionRequest

    return ExecutionRequest(
        task_id="task-1", message="m", mode="execution", task={}, owner_id="owner-1"
    )


class TestSerialiseEnvMint:
    def test_per_task_token_mode_disables_warm_worker_token_reuse(self):
        import app.execution.container_runtime as cr_mod

        with patch.object(
            cr_mod,
            "settings",
            MagicMock(
                runtime_type="container",
                worker_keepalive=True,
                worker_per_task_token=True,
            ),
        ):
            assert cr_mod._keepalive_active() is False

    def test_flag_on_mints_per_task_token(self, monkeypatch):
        _secret(monkeypatch)
        import app.execution.container_runtime as cr_mod

        mock = MagicMock(
            claude_permission_mode="bypassPermissions",
            worker_per_task_token=True,
            worker_auth_token="global-secret",
            worker_callback_url="http://api:8000",
            artifact_storage="local",
        )
        with patch.object(cr_mod, "settings", mock):
            from app.execution.container_runtime import _serialise_env

            env = _serialise_env(_req(), "exec-1", execution_epoch="epoch-1")
        tok = env["OPENAIR_WORKER_AUTH_TOKEN"]
        assert tok.startswith("wt.")
        assert tok != "global-secret"
        from app.execution.worker_tokens import verify_worker_token

        assert verify_worker_token(
            tok,
            expected_task_id="task-1",
            expected_owner_id="owner-1",
            expected_execution_id="exec-1",
            expected_execution_epoch="epoch-1",
        )

    def test_flag_off_keeps_global_token(self, monkeypatch):
        import app.execution.container_runtime as cr_mod

        mock = MagicMock(
            claude_permission_mode="bypassPermissions",
            worker_per_task_token=False,
            worker_auth_token="global-secret",
            worker_callback_url="http://api:8000",
            artifact_storage="local",
        )
        with patch.object(cr_mod, "settings", mock):
            from app.execution.container_runtime import _serialise_env

            env = _serialise_env(_req(), "exec-1", execution_epoch="epoch-1")
        assert env["OPENAIR_WORKER_AUTH_TOKEN"] == "global-secret"

    def test_flag_on_missing_owner_id_fails_closed(self, monkeypatch):
        """Per-task mode must NEVER fall back to the global secret when owner_id is absent —
        inject no token (worker will 403) rather than ship the shared secret."""
        import app.execution.container_runtime as cr_mod
        from app.execution.runtime_boundary import ExecutionRequest

        mock = MagicMock(
            claude_permission_mode="bypassPermissions",
            worker_per_task_token=True,
            worker_auth_token="global-secret",
            worker_callback_url="http://api:8000",
            artifact_storage="local",
        )
        req_no_owner = ExecutionRequest(
            task_id="task-1", message="m", mode="execution", task={}
            # owner_id deliberately omitted
        )
        with patch.object(cr_mod, "settings", mock):
            from app.execution.container_runtime import _serialise_env

            env = _serialise_env(req_no_owner, "exec-1", execution_epoch="epoch-1")
        assert "OPENAIR_WORKER_AUTH_TOKEN" not in env


# ---------------------------------------------------------------------------
# callback registry stores task_id (so result/stream-event can bind)
# ---------------------------------------------------------------------------


class TestCallbackRegistryTaskId:
    def test_register_stores_and_cleans_task_id(self):
        from app.execution.worker_callback import WorkerCallbackRegistry

        reg = WorkerCallbackRegistry()
        _run_async(_register(reg, "exec-1", "task-1"))  # register() needs a running loop
        authority = reg.get_authority("exec-1")
        assert authority is not None
        assert (authority.task_id, authority.owner_id, authority.execution_epoch) == (
            "task-1", "owner-1", "epoch-1"
        )
        assert reg.get_authority("exec-OTHER") is None
        reg.cleanup("exec-1")
        assert reg.get_authority("exec-1") is None

    def test_register_stream_stores_task_id(self):
        from app.execution.worker_callback import WorkerCallbackRegistry

        reg = WorkerCallbackRegistry()
        _run_async(_register_stream(reg, "exec-2", "task-2"))
        assert reg.get_authority("exec-2").task_id == "task-2"

    def test_postgres_mode_uses_durable_authority_not_replica_local_mapping(
        self, monkeypatch
    ):
        from app.execution.worker_callback import WorkerCallbackRegistry

        class AuthorityRepository:
            def __init__(self):
                self.rows = {}

            def issue(self, authority):
                self.rows[authority.execution_id] = authority

            def load_active(self, execution_id):
                return self.rows.get(execution_id)

            def revoke(self, execution_id, _execution_epoch=None):
                self.rows.pop(execution_id, None)

        repository = AuthorityRepository()
        monkeypatch.setattr(settings, "worker_per_task_token", True)
        monkeypatch.setattr(settings, "persistence_backend", "postgres")
        first_replica = WorkerCallbackRegistry(authority_repository=repository)
        second_replica = WorkerCallbackRegistry(authority_repository=repository)

        _run_async(_register(first_replica, "exec-durable", "task-1"))
        authority = second_replica.get_authority("exec-durable")

        assert authority is not None
        assert authority.task_id == "task-1"
        assert second_replica._authorities == {}


# ---------------------------------------------------------------------------
# endpoint task-binding (the actual security property)
# ---------------------------------------------------------------------------


class TestEndpointBinding:
    def _flag_on(self, monkeypatch):
        _secret(monkeypatch)
        monkeypatch.setattr(settings, "worker_per_task_token", True)
        monkeypatch.setattr(settings, "worker_auth_token", "global-secret")
        monkeypatch.setattr(settings, "persistence_backend", "json")

    @staticmethod
    def _authority(reg, execution_id: str, task_id: str, owner_id: str, epoch: str):
        reg.register_authority(
            execution_id,
            task_id=task_id,
            owner_id=owner_id,
            execution_epoch=epoch,
            expires_at=_expiry(),
        )

    def test_result_rejects_token_minted_for_other_task(self, monkeypatch):
        self._flag_on(monkeypatch)
        from app.execution.worker_callback import get_callback_registry
        from app.execution.worker_tokens import mint_worker_token
        from app.routes.internal import WorkerResultIn, worker_result

        reg = get_callback_registry()
        self._authority(reg, "exec-victim", "task-VICTIM", "owner-v", "epoch-v")
        wrong = mint_worker_token("task-ATTACKER", "owner-x", "exec-victim", "epoch-v")
        with pytest.raises(HTTPException) as error:
            _run_async(
                worker_result(
                    WorkerResultIn(
                        execution_id="exec-victim", reply="poison", status="completed"
                    ),
                    wrong,
                )
            )
        assert error.value.status_code == 403

    def test_result_rejects_global_control_token_in_per_task_mode(self, monkeypatch):
        self._flag_on(monkeypatch)
        from app.execution.worker_callback import get_callback_registry
        from app.routes.internal import WorkerResultIn, worker_result

        reg = get_callback_registry()
        self._authority(reg, "exec-global", "task-1", "owner-1", "epoch-1")
        with pytest.raises(HTTPException) as error:
            _run_async(
                worker_result(
                    WorkerResultIn(
                        execution_id="exec-global", reply="poison", status="completed"
                    ),
                    "global-secret",
                )
            )
        assert error.value.status_code == 403

    def test_result_accepts_exact_execution_epoch(self, monkeypatch):
        self._flag_on(monkeypatch)
        from app.execution.worker_callback import get_callback_registry
        from app.execution.worker_tokens import mint_worker_token
        from app.routes.internal import WorkerResultIn, worker_result

        reg = get_callback_registry()
        self._authority(reg, "exec-ok", "task-1", "owner-1", "epoch-1")
        good = mint_worker_token("task-1", "owner-1", "exec-ok", "epoch-1")
        response = _run_async(
            worker_result(
                WorkerResultIn(execution_id="exec-ok", reply="ok", status="completed"),
                good,
            )
        )
        assert response == {"status": "ok"}

    def test_result_rejects_old_same_task_execution_epoch(self, monkeypatch):
        self._flag_on(monkeypatch)
        from app.execution.worker_callback import get_callback_registry
        from app.execution.worker_tokens import mint_worker_token
        from app.routes.internal import WorkerResultIn, worker_result

        reg = get_callback_registry()
        self._authority(reg, "exec-new", "task-1", "owner-1", "epoch-new")
        stale = mint_worker_token("task-1", "owner-1", "exec-old", "epoch-old")
        with pytest.raises(HTTPException) as error:
            _run_async(
                worker_result(
                    WorkerResultIn(
                        execution_id="exec-new", reply="poison", status="completed"
                    ),
                    stale,
                )
            )
        assert error.value.status_code == 403

    def test_result_rejects_same_execution_with_stale_epoch(self, monkeypatch):
        self._flag_on(monkeypatch)
        from app.execution.worker_callback import get_callback_registry
        from app.execution.worker_tokens import mint_worker_token
        from app.routes.internal import WorkerResultIn, worker_result

        reg = get_callback_registry()
        self._authority(reg, "exec-rotated", "task-1", "owner-1", "epoch-new")
        stale = mint_worker_token("task-1", "owner-1", "exec-rotated", "epoch-old")
        with pytest.raises(HTTPException) as error:
            _run_async(
                worker_result(
                    WorkerResultIn(
                        execution_id="exec-rotated", reply="poison", status="completed"
                    ),
                    stale,
                )
            )
        assert error.value.status_code == 403

    def test_result_rejects_owner_mismatch(self, monkeypatch):
        self._flag_on(monkeypatch)
        from app.execution.worker_callback import get_callback_registry
        from app.execution.worker_tokens import mint_worker_token
        from app.routes.internal import WorkerResultIn, worker_result

        reg = get_callback_registry()
        self._authority(reg, "exec-owner", "task-1", "owner-victim", "epoch-1")
        wrong_owner = mint_worker_token("task-1", "owner-attacker", "exec-owner", "epoch-1")
        with pytest.raises(HTTPException) as error:
            _run_async(
                worker_result(
                    WorkerResultIn(
                        execution_id="exec-owner", reply="poison", status="completed"
                    ),
                    wrong_owner,
                )
            )
        assert error.value.status_code == 403

    def test_result_rejects_unknown_execution_id(self, monkeypatch):
        self._flag_on(monkeypatch)
        from app.execution.worker_tokens import mint_worker_token
        from app.routes.internal import WorkerResultIn, worker_result

        token = mint_worker_token("task-1", "owner-1", "exec-UNKNOWN", "epoch-1")
        with pytest.raises(HTTPException) as error:
            _run_async(
                worker_result(
                    WorkerResultIn(
                        execution_id="exec-UNKNOWN", reply="poison", status="completed"
                    ),
                    token,
                )
            )
        assert error.value.status_code == 403

    def test_result_rejects_expired_token(self, monkeypatch):
        self._flag_on(monkeypatch)
        from app.execution.worker_callback import get_callback_registry
        from app.execution.worker_tokens import mint_worker_token
        from app.routes.internal import WorkerResultIn, worker_result

        reg = get_callback_registry()
        self._authority(reg, "exec-old", "task-1", "owner-1", "epoch-1")
        ttl = settings.worker_token_ttl_seconds
        stale = mint_worker_token(
            "task-1",
            "owner-1",
            "exec-old",
            "epoch-1",
            now=time.time() - (ttl + 10),
        )
        with pytest.raises(HTTPException) as error:
            _run_async(
                worker_result(
                    WorkerResultIn(execution_id="exec-old", reply="ok", status="completed"),
                    stale,
                )
            )
        assert error.value.status_code == 403

    def test_task_endpoint_rejects_global_token(self, monkeypatch):
        self._flag_on(monkeypatch)
        from app.routes.internal import _verify_worker_token
        with pytest.raises(HTTPException):
            _verify_worker_token("global-secret", expected_task_id="task-1")

    def test_startup_contract_requires_global_worker_token(self, monkeypatch):
        self._flag_on(monkeypatch)
        from app.routes.internal import worker_startup_contract

        with pytest.raises(HTTPException) as error:
            worker_startup_contract(None)
        assert error.value.status_code == 403
        accepted = worker_startup_contract("global-secret")
        assert accepted["contract"] == settings.execution_startup_contract()

    def test_startup_contract_rejects_execution_token(self, monkeypatch):
        self._flag_on(monkeypatch)
        from app.execution.worker_tokens import mint_worker_token
        from app.routes.internal import worker_startup_contract

        token = mint_worker_token("task-1", "owner-1", "exec-1", "epoch-1")
        with pytest.raises(HTTPException) as error:
            worker_startup_contract(token)
        assert error.value.status_code == 403

    def test_containers_rejects_execution_token(self, monkeypatch):
        self._flag_on(monkeypatch)
        from app.execution.worker_tokens import mint_worker_token
        from app.routes.internal import list_worker_containers

        token = mint_worker_token("task-1", "owner-1", "exec-1", "epoch-1")
        with pytest.raises(HTTPException) as error:
            _run_async(list_worker_containers(token))
        assert error.value.status_code == 403


# ---------------------------------------------------------------------------
# owner_id propagation: chat → ExecutionRequest → per-task token in worker env
# ---------------------------------------------------------------------------


class _CapturingRuntime:
    """Fake ExecutionRuntime that records the request submitted/streamed to it."""

    def __init__(self):
        self.captured = None

    async def submit(self, request):
        self.captured = request
        from app.execution.runtime_boundary import ExecutionResult

        return ExecutionResult(reply="ok", session_id=None)

    async def submit_stream(self, request):
        self.captured = request
        yield {"type": "done", "sessionId": None}


class TestOwnerPropagationToRuntime:
    """``_run_claude_chat``/``_stream_claude_chat`` must set ``owner_id`` on the real turn's
    ExecutionRequest — ContainerRuntime keys per-task token minting on it, and per-task mode
    fail-closes (no global-token fallback) when it is absent.
    """

    async def _capture(self, *, stream: bool):
        from app.execution import agent_service as svc

        rt = _CapturingRuntime()
        with patch.object(svc, "ensure_task_session", return_value=None), patch.object(
            svc, "get_execution_runtime", return_value=rt
        ):
            if stream:
                async for _ in svc._stream_claude_chat(
                    {"ownerId": "owner-1", "status": "running"}, "hi", "task-1"
                ):
                    pass
            else:
                await svc._run_claude_chat(
                    {"ownerId": "owner-1", "status": "running"}, "hi", "task-1"
                )
        return rt.captured

    async def test_run_claude_chat_sets_owner_id(self):
        assert (await self._capture(stream=False)).owner_id == "owner-1"

    async def test_stream_claude_chat_sets_owner_id(self):
        assert (await self._capture(stream=True)).owner_id == "owner-1"

    async def test_chat_request_mints_per_task_token(self):
        """End-to-end: the request built by chat → _serialise_env (flag on) mints a wt token."""
        request = await self._capture(stream=False)
        import app.execution.container_runtime as cr_mod
        from app.execution.container_runtime import _serialise_env

        mock = MagicMock(
            claude_permission_mode="bypassPermissions",
            worker_per_task_token=True,
            worker_auth_token="global-secret",
            worker_callback_url="http://api:8000",
            artifact_storage="local",
        )
        with patch.object(cr_mod, "settings", mock):
            env = _serialise_env(request, "exec-1", execution_epoch="epoch-1")
        tok = env["OPENAIR_WORKER_AUTH_TOKEN"]
        assert tok.startswith("wt.")
        assert tok != "global-secret"


class TestDurableRunEndpointBinding:
    def _flag_on(self, monkeypatch):
        _secret(monkeypatch)
        monkeypatch.setattr(settings, "worker_per_task_token", True)
        monkeypatch.setattr(settings, "worker_auth_token", "global-secret")
        monkeypatch.setattr(settings, "persistence_backend", "postgres")

    @pytest.mark.parametrize("endpoint", ["callback", "request_upload", "confirm_upload"])
    def test_run_scoped_data_plane_rejects_global_control_token(self, monkeypatch, endpoint):
        self._flag_on(monkeypatch)
        from app.routes import internal

        class FakeRepository:
            pass

        monkeypatch.setattr(internal, "DurableExecutionRepository", FakeRepository)
        if endpoint == "callback":
            body = internal.DurableCallbackIn(
                workerId="worker-1", leaseEpoch=4, type="completed", payload={}
            )
            invoke = lambda: internal.durable_callback("run-1", body, "global-secret")
        elif endpoint == "request_upload":
            body = internal.DurableUploadIntentIn(
                workerId="worker-1",
                leaseEpoch=4,
                expectedSize=1,
                expectedSha256="0" * 64,
                presentation={},
            )
            invoke = lambda: internal.durable_request_upload("run-1", body, "global-secret")
        else:
            body = internal.DurableUploadConfirmIn(workerId="worker-1", leaseEpoch=4)
            invoke = lambda: internal.durable_confirm_upload(
                "run-1", "upload-1", body, "global-secret"
            )

        with pytest.raises(HTTPException) as error:
            invoke()
        assert error.value.status_code == 403

    def test_durable_callback_accepts_only_exact_run_lease_epoch(self, monkeypatch):
        self._flag_on(monkeypatch)
        from app.execution.worker_tokens import mint_worker_token
        from app.routes import internal

        class FakeRepository:
            def load_active_worker_authority(self, run_id, worker_id, lease_epoch):
                assert (run_id, worker_id, lease_epoch) == ("run-1", "worker-1", 4)
                return {
                    "run_id": "run-1",
                    "task_id": "task-1",
                    "owner_id": "owner-1",
                    "worker_id": "worker-1",
                    "lease_epoch": 4,
                }

            def callback(self, *_args, **_kwargs):
                return {"run_id": "run-1"}

        monkeypatch.setattr(internal, "DurableExecutionRepository", FakeRepository)
        monkeypatch.setattr(internal, "serialize_run", lambda run: run)
        body = internal.DurableCallbackIn(
            workerId="worker-1", leaseEpoch=4, type="completed", payload={}
        )
        exact = mint_worker_token("task-1", "owner-1", "run-1", "4")
        assert internal.durable_callback("run-1", body, exact) == {"run_id": "run-1"}

        stale = mint_worker_token("task-1", "owner-1", "run-1", "3")
        with pytest.raises(HTTPException) as error:
            internal.durable_callback("run-1", body, stale)
        assert error.value.status_code == 403

    def test_durable_claim_mints_run_scoped_token(self, monkeypatch):
        self._flag_on(monkeypatch)
        from app.execution.worker_tokens import parse_worker_token
        from app.routes import internal

        run = {
            "run_id": "run-1",
            "task_id": "task-1",
            "owner_id": "owner-1",
            "lease_owner": "worker-1",
            "lease_epoch": 9,
            "input_snapshot": {"question": "q"},
        }

        class FakeRepository:
            def claim(self, worker_id, _lease_seconds, *, not_before=None):
                assert worker_id == "worker-1"
                return run

        monkeypatch.setattr(internal, "DurableExecutionRepository", FakeRepository)
        monkeypatch.setattr(internal, "serialize_run", lambda value: {"runId": value["run_id"]})
        response = internal.durable_claim(
            internal.DurableClaimIn(workerId="worker-1"), "global-secret"
        )
        claims = parse_worker_token(response["workerToken"])
        assert claims is not None
        assert (claims.task_id, claims.owner_id, claims.execution_id, claims.execution_epoch) == (
            "task-1",
            "owner-1",
            "run-1",
            "9",
        )
