from __future__ import annotations

import importlib
import inspect
import threading
from datetime import datetime, timezone
from urllib.error import HTTPError

import pytest
from fastapi import HTTPException

import worker.entrypoint as entrypoint


def _reload(monkeypatch: pytest.MonkeyPatch, *, env: str, pause_claim: str = "0", pause_started: str = "0"):
    monkeypatch.setenv("OPENAIR_ENV", env)
    monkeypatch.delenv("OPENAIR_DURABLE_CLAIM_NOT_BEFORE", raising=False)
    monkeypatch.setenv("OPENAIR_DURABLE_WORKER_PAUSE_AFTER_CLAIM_SECONDS", pause_claim)
    monkeypatch.setenv("OPENAIR_DURABLE_WORKER_PAUSE_AFTER_EXECUTION_STARTED_SECONDS", pause_started)
    monkeypatch.setenv("OPENAIR_DURABLE_WORKER_EXECUTION_CAPACITY", "1")
    return importlib.reload(entrypoint)


def test_non_test_pause_controls_reject_before_worker_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _reload(monkeypatch, env="development", pause_claim="1")
    monkeypatch.setattr(module, "_post", lambda *_args, **_kwargs: pytest.fail("worker attempted a claim"))

    with pytest.raises(RuntimeError, match="only allowed when OPENAIR_ENV=test"):
        module.run_forever()


def test_test_environment_allows_pause_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _reload(monkeypatch, env="test", pause_claim="1", pause_started="1")

    module._validate_startup()


def test_pre_execution_renew_uses_claim_fencing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _reload(monkeypatch, env="test")
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(module, "_post", lambda path, body: calls.append((path, body)) or {})

    module._renew_before_execution({"runId": "run-1", "leaseEpoch": 7})

    assert calls == [
        ("/internal/durable-runs/run-1/renew", {"workerId": module.WORKER_ID, "leaseEpoch": 7}),
    ]


def test_pre_execution_renew_propagates_stale_fence(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    module = _reload(monkeypatch, env="test")

    def reject(*_args, **_kwargs):
        raise HTTPError("http://api", 409, "stale", {}, None)

    monkeypatch.setattr(module, "_post", reject)
    with pytest.raises(HTTPError):
        module._renew_before_execution({"runId": "run-1", "leaseEpoch": 7})

    assert '"event": "pre_execution_lease_lost"' in capsys.readouterr().out


def test_awaiting_human_never_reaches_upload_or_success_callback() -> None:
    assert entrypoint._orchestration_disposition({"knownPause": "awaiting_human"}) == "paused"


def test_worker_pause_skips_upload_and_success_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _reload(monkeypatch, env="test")
    run = {"runId": "run-paused", "leaseEpoch": 3}
    requests: list[tuple[str, dict]] = []
    claim_count = 0

    class Keeper:
        lost = False

        def __init__(self, _run: dict):
            pass

        def start(self) -> None:
            pass

        def close(self) -> None:
            pass

    def post(path: str, body: dict, **_kwargs):
        nonlocal claim_count
        requests.append((path, body))
        if path == "/internal/durable-runs/claim":
            claim_count += 1
            if claim_count == 1:
                return {"run": run}
            raise HTTPError("http://api", 503, "stop test loop", {}, None)
        return {}

    monkeypatch.setattr(module, "_post", post)
    monkeypatch.setattr(
        module,
        "process_durable_worker_run",
        lambda *_args: {"knownPause": "awaiting_human", "run": {"state": "awaiting_human"}},
    )
    monkeypatch.setattr(module, "_LeaseKeeper", Keeper)
    monkeypatch.setattr(module, "_claim_and_run_research_start", lambda: False)
    monkeypatch.setattr(module, '_claim_and_run_research_dispatch', lambda: False)
    monkeypatch.setattr(module, '_claim_and_run_research_continuation', lambda: False)
    monkeypatch.setattr(module, '_resume_pending_method_continuation', lambda: False)
    monkeypatch.setattr(module, 'process_model_job_once', lambda **_kwargs: False)
    monkeypatch.setattr(module, "EXIT_ON_HTTP_ERROR", True)
    monkeypatch.setattr(
        module,
        "_upload_runner_artifacts",
        lambda *_args, **_kwargs: pytest.fail("paused run attempted artifact upload"),
    )

    module.run_forever()

    assert not any(path.endswith("/callback") for path, _body in requests)


def test_known_terminal_result_never_reaches_success_callback() -> None:
    assert entrypoint._orchestration_disposition({"knownFailure": "external_result_unknown"}) == "terminal"


def test_successful_orchestration_continues_to_completion_protocol() -> None:
    assert entrypoint._orchestration_disposition({"multiAgent": True, "artifactIds": ["artifact-1"]}) == "complete"


def test_legacy_worker_orchestrates_in_process_without_http(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _reload(monkeypatch, env="test")
    calls: list[tuple[str, str, int]] = []

    monkeypatch.setattr(
        module,
        "process_durable_worker_run",
        lambda run_id, worker_id, lease_epoch: calls.append((run_id, worker_id, lease_epoch))
        or {"multiAgent": True, "artifacts": []},
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "_post",
        lambda path, *_args, **_kwargs: pytest.fail(f"worker posted orchestration over HTTP: {path}"),
    )

    result = module._orchestrate({"runId": "run-direct", "leaseEpoch": 9})

    assert result == {"multiAgent": True, "artifacts": []}
    assert calls == [("run-direct", module.WORKER_ID, 9)]
    assert "/orchestrate" not in inspect.getsource(module._orchestrate)


def test_direct_runtime_stale_fence_does_not_stop_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _reload(monkeypatch, env="test")
    run = {"runId": "run-stale", "leaseEpoch": 5}
    claim_count = 0
    execution_count = 0

    class Keeper:
        lost = False

        def __init__(self, _run: dict):
            pass

        def start(self) -> None:
            pass

        def close(self) -> None:
            pass

    def post(path: str, _body: dict, **_kwargs):
        nonlocal claim_count
        if path == "/internal/durable-runs/claim":
            claim_count += 1
            if claim_count == 1:
                return {"run": run}
            raise HTTPError("http://api", 503, "stop test loop", {}, None)
        return {}

    def reject_stale_fence(*_args):
        nonlocal execution_count
        execution_count += 1
        raise HTTPException(status_code=409, detail="durable_run_stale_fence")

    monkeypatch.setattr(module, "_post", post)
    monkeypatch.setattr(module, "_LeaseKeeper", Keeper)
    monkeypatch.setattr(module, "process_durable_worker_run", reject_stale_fence)
    monkeypatch.setattr(module, "_claim_and_run_research_start", lambda: False)
    monkeypatch.setattr(module, "EXIT_ON_HTTP_ERROR", True)
    monkeypatch.setattr(module, "POLL_SECONDS", 0)

    module.run_forever()

    assert execution_count == 1
    assert claim_count == 2


def test_legacy_claim_passes_the_same_operator_cutoff(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _reload(monkeypatch, env="production")
    monkeypatch.setenv("OPENAIR_DURABLE_CLAIM_NOT_BEFORE", "2026-07-26T08:55:00Z")
    observed: dict[str, object] = {}

    def post(path: str, body: dict, **_kwargs):
        observed.update({"path": path, "body": body})
        return {"run": None}

    monkeypatch.setattr(module, "_post", post)

    assert module._claim_and_run_legacy_durable() is False
    assert observed == {
        "path": "/internal/durable-runs/claim",
        "body": {
            "workerId": module.WORKER_ID,
            "notBefore": "2026-07-26T08:55:00+00:00",
        },
    }


def test_worker_research_path_uses_direct_repository_runtime_not_http() -> None:
    source = inspect.getsource(entrypoint)

    assert "ResearchStartRepository" in source
    assert "process_research_start_claim" in source
    for forbidden_path in ("/agent/chat", "/sse", "/advance", "/dispatch"):
        assert forbidden_path not in source


@pytest.mark.parametrize(
    ("reconciled", "claim", "expected_processed"),
    [
        ({"reclaimed": 1, "unknown": 0}, {"requestId": "rsr-reclaimed", "leaseEpoch": 2}, True),
        ({"reclaimed": 0, "unknown": 1}, None, False),
    ],
)
def test_research_claim_reconciles_expired_leases_before_claim(
    monkeypatch: pytest.MonkeyPatch,
    reconciled: dict,
    claim: dict | None,
    expected_processed: bool,
) -> None:
    module = _reload(monkeypatch, env="test")
    events: list[str] = []

    class Repository:
        def reconcile_expired_leases(self):
            events.append("reconcile")
            return reconciled

        def claim(self, _worker_id, *, not_before=None):
            assert not_before is None
            events.append("claim")
            return claim

    monkeypatch.setattr(module, "ResearchStartRepository", Repository)
    monkeypatch.setattr(
        module,
        "process_research_start_claim",
        lambda *_args, **_kwargs: events.append("process"),
    )

    assert module._claim_and_run_research_start() is expected_processed
    assert events == (["reconcile", "claim", "process"] if claim else ["reconcile", "claim"])


def test_research_claim_passes_operator_cutoff_and_logs_request_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _reload(monkeypatch, env="production")
    monkeypatch.setenv("OPENAIR_DURABLE_CLAIM_NOT_BEFORE", "2026-07-26T08:55:00Z")
    observed: dict[str, object] = {}

    class Repository:
        def reconcile_expired_leases(self):
            return {"reclaimed": 0, "unknown": 0}

        def claim(self, _worker_id, *, not_before=None):
            observed["not_before"] = not_before
            return {"requestId": "rsr_current", "leaseEpoch": 1}

    monkeypatch.setattr(module, "ResearchStartRepository", Repository)
    monkeypatch.setattr(module, "process_research_start_claim", lambda *_args, **_kwargs: None)

    assert module._claim_and_run_research_start() is True
    assert observed["not_before"] == datetime(2026, 7, 26, 8, 55, tzinfo=timezone.utc)
    output = capsys.readouterr().out
    assert '"event": "CLAIM_ACQUIRED"' in output
    assert '"requestId": "rsr_current"' in output


@pytest.mark.parametrize("raw", ["not-a-time", "2026-07-26T08:55:00"])
def test_research_claim_cutoff_fails_closed_when_invalid(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    module = _reload(monkeypatch, env="production")
    monkeypatch.setenv("OPENAIR_DURABLE_CLAIM_NOT_BEFORE", raw)

    with pytest.raises(RuntimeError, match="OPENAIR_DURABLE_CLAIM_NOT_BEFORE"):
        module._durable_claim_not_before()


@pytest.mark.parametrize("env", ["production", "prod"])
def test_production_worker_requires_claim_cutoff(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    module = _reload(monkeypatch, env=env)
    monkeypatch.delenv("OPENAIR_DURABLE_CLAIM_NOT_BEFORE", raising=False)

    with pytest.raises(RuntimeError, match="is required in production"):
        module._durable_claim_not_before()


def test_research_start_test_provider_requires_exact_test_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _reload(monkeypatch, env="test")
    monkeypatch.setenv("OPENAIR_RESEARCH_START_TEST_FAKE_PROVIDER", "1")
    provider = module._research_start_provider()
    assert provider.__class__.__name__ == "ResearchStartDeterministicTestProvider"

    module = _reload(monkeypatch, env="development")
    monkeypatch.setenv("OPENAIR_RESEARCH_START_TEST_FAKE_PROVIDER", "1")
    provider = module._research_start_provider()
    assert provider.__class__.__name__ == "ResearchStartOneShotProvider"


def test_poll_once_rotates_queues_with_fallback_and_handles_at_most_one(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _reload(monkeypatch, env="test")
    calls: list[str] = []
    monkeypatch.setattr(module, "_claim_and_run_legacy_durable", lambda: calls.append("legacy") or True)
    monkeypatch.setattr(module, "_claim_and_run_research_start", lambda: calls.append("research") or True)
    monkeypatch.setattr(module, "_claim_and_run_research_dispatch", lambda: calls.append("dispatch") or True)

    handled, cursor = module._poll_once(0)
    assert (handled, cursor, calls) == (True, 1, ["legacy"])
    calls.clear()
    handled, cursor = module._poll_once(cursor)
    assert (handled, cursor, calls) == (True, 2, ["research"])
    calls.clear()
    handled, cursor = module._poll_once(cursor)
    assert handled is True
    assert calls == ["dispatch"]


def test_execution_slots_start_fresh_work_while_long_work_is_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _reload(monkeypatch, env="test")
    long_started = threading.Event()
    release_long = threading.Event()
    fresh_started = threading.Event()

    def poll(cursor):
        if cursor == 0:
            long_started.set()
            assert release_long.wait(2)
        else:
            fresh_started.set()
        return True, cursor

    monkeypatch.setattr(module, "_poll_once", poll)
    slots = [module._ExecutionSlot(0), module._ExecutionSlot(1)]
    executor = module.ThreadPoolExecutor(max_workers=2)
    try:
        module._submit_ready_slots(executor, slots)
        assert long_started.wait(1)
        assert fresh_started.wait(1)
    finally:
        release_long.set()
        executor.shutdown(wait=True, cancel_futures=True)


def test_three_queue_poll_is_bounded_fair_and_handles_at_most_one(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _reload(monkeypatch, env="test")
    dispatch = getattr(module, "_claim_and_run_research_dispatch", None)
    assert callable(dispatch), "production worker must poll research dispatch"
    calls: list[str] = []
    monkeypatch.setattr(module, "_claim_and_run_legacy_durable", lambda: calls.append("legacy") or False)
    monkeypatch.setattr(module, "_claim_and_run_research_start", lambda: calls.append("research_start") or False)
    monkeypatch.setattr(module, "_claim_and_run_research_dispatch", lambda: calls.append("dispatch") or True)

    handled, next_queue = module._poll_once(0)

    assert handled is True
    assert calls == ["legacy", "research_start", "dispatch"]


def test_three_busy_queues_rotate_first_choice_across_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _reload(monkeypatch, env="test")
    calls: list[str] = []
    monkeypatch.setattr(module, "_claim_and_run_legacy_durable", lambda: calls.append("legacy") or True)
    monkeypatch.setattr(module, "_claim_and_run_research_start", lambda: calls.append("research_start") or True)
    monkeypatch.setattr(module, "_claim_and_run_research_dispatch", lambda: calls.append("dispatch") or True)

    cursor = 0
    for _ in range(3):
        handled, cursor = module._poll_once(cursor)
        assert handled is True

    assert calls == ["legacy", "research_start", "dispatch"]


def test_production_dispatch_is_unresolved_before_claim_and_explicit_binding_reaches_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _reload(monkeypatch, env="test")
    run_dispatch = getattr(module, "_claim_and_run_research_dispatch", None)
    assert callable(run_dispatch), "production dispatch call graph is required"
    binding_type = getattr(module, "DispatchProviderBinding", None)
    assert binding_type is not None
    monkeypatch.setattr(module.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(module.settings, "persistence_backend", "postgres")
    events: list[str] = []

    class Unresolved:
        def resolve(self):
            events.append("resolve")
            return None

    class NoClaimService:
        def claim(self, **_kwargs):
            raise AssertionError("unresolved binding must fail before claim")

    assert run_dispatch(binding_resolver=Unresolved(), provider_factory=object(), service=NoClaimService()) is False
    assert events == ["resolve"]

    class Resolved:
        def resolve(self):
            return binding_type(
                provider_id="safe-test-provider",
                dispatch_id="dispatch-1",
                task_id="task-1",
                execution_decision_id="decision-1",
                work_unit_id="work-unit-1",
                registry_snapshot_ref={"registryId": "providers", "version": 1},
                executor_ref={"executorType": "agent", "executorId": "executor-1"},
                responsible_actor_ref={"actorType": "agent", "actorId": "actor-1"},
                owner_id="owner-1",
                workspace_id="workspace-1",
                expected_task_revision=4,
            )

    class Factory:
        def create(self, provider_id):
            events.append(f"provider:{provider_id}")
            return object()

    class Service:
        def claim_authorized_recovery(self, **_kwargs):
            return []

        def claim(self, **kwargs):
            assert kwargs["dispatch_id"] == "dispatch-1"
            events.append("claim")
            return [type("Fence", (), {
                "dispatch_id": "dispatch-1",
                "task_id": "task-1",
                "execution_decision_id": "decision-1",
                "work_unit_id": "work-unit-1",
            })()]

    def process(*_args, **_kwargs):
        events.append("process")
        return type(
            "Result",
            (),
            {"status": "completed", "recorded": True, "error_code": None},
        )()

    monkeypatch.setattr(module, "process_dispatch_claim", process)
    assert run_dispatch(binding_resolver=Resolved(), provider_factory=Factory(), service=Service()) is True
    assert events[-3:] == ["claim", "provider:safe-test-provider", "process"]


@pytest.mark.parametrize(
    ("enabled", "backend"),
    ((False, "postgres"), (True, "json")),
)
def test_dispatch_flag_or_backend_mismatch_fails_before_provider_and_claim(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    backend: str,
) -> None:
    module = _reload(monkeypatch, env="test")
    events: list[str] = []

    class Resolver:
        def resolve(self):
            return module.DispatchProviderBinding(
                provider_id="safe-test-provider",
                dispatch_id="dispatch-1",
                task_id="task-1",
                execution_decision_id="decision-1",
                work_unit_id="work-unit-1",
                registry_snapshot_ref={"registryId": "providers", "version": 1},
                executor_ref={"executorType": "agent", "executorId": "executor-1"},
                responsible_actor_ref={"actorType": "agent", "actorId": "actor-1"},
                owner_id="owner-1",
                workspace_id="workspace-1",
                expected_task_revision=4,
            )

    class NoFactory:
        def create(self, _provider_id):
            raise AssertionError("disabled dispatch must fail before provider creation")

    class NoService:
        def claim(self, **_kwargs):
            raise AssertionError("disabled dispatch must fail before claim")

    monkeypatch.setattr(module.settings, "research_orchestration_v1", enabled)
    monkeypatch.setattr(module.settings, "persistence_backend", backend)

    assert module._claim_and_run_research_dispatch(
        binding_resolver=Resolver(), provider_factory=NoFactory(), service=NoService()
    ) is False
    assert events == []


def test_default_production_binding_is_unresolved_before_service_or_provider_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _reload(monkeypatch, env="test")
    monkeypatch.setenv("OPENAIR_RESEARCH_DISPATCH_PROVIDER_ID", "openai_primary")
    monkeypatch.setattr(
        module,
        "ResearchDispatchService",
        lambda: pytest.fail("unresolved production binding must not create or claim a service"),
    )
    monkeypatch.setattr(
        module,
        "RegistryDispatchProviderFactory",
        lambda: pytest.fail("registry default or environment must not become dispatch authority"),
    )

    assert module._claim_and_run_research_dispatch() is False


def test_explicit_authorized_recovery_binding_calls_recovery_worker_before_ordinary_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _reload(monkeypatch, env="test")
    binding = module.DispatchProviderBinding(
        provider_id="safe-test-provider",
        dispatch_id="dispatch-recovery",
        task_id="task-recovery",
        execution_decision_id="decision-recovery",
        work_unit_id="work-unit-recovery",
        registry_snapshot_ref={"registryId": "providers", "version": 1},
        executor_ref={"executorType": "agent", "executorId": "executor-1"},
        responsible_actor_ref={"actorType": "agent", "actorId": "actor-1"},
        owner_id="owner-1",
        workspace_id="workspace-1",
        expected_task_revision=7,
    )
    fence = type("AuthorizedFence", (), {
        "dispatch_id": binding.dispatch_id,
        "task_id": binding.task_id,
        "execution_decision_id": binding.execution_decision_id,
        "work_unit_id": binding.work_unit_id,
    })()
    events: list[str] = []

    class Resolver:
        def resolve(self):
            return binding

    class Factory:
        def create(self, provider_id):
            events.append(f"provider:{provider_id}")
            return object()

    class Service:
        def claim_authorized_recovery(self, **_kwargs):
            events.append("recovery_claim")
            return [fence]

        def claim(self, **_kwargs):
            raise AssertionError("ordinary claim must not run after a recovery winner")

    monkeypatch.setattr(module.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(module.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(module, "process_authorized_recovery", lambda *_args, **_kwargs: events.append("recovery_process"))

    assert module._claim_and_run_research_dispatch(
        binding_resolver=Resolver(), provider_factory=Factory(), service=Service()
    ) is True
    assert events == ["recovery_claim", "provider:safe-test-provider", "recovery_process"]


def test_started_single_attempt_recovery_never_constructs_or_replays_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _reload(monkeypatch, env="test")
    binding = module.DispatchProviderBinding(
        provider_id=None,
        dispatch_id="dispatch-recovery",
        task_id="task-recovery",
        execution_decision_id="decision-recovery",
        work_unit_id="work-unit-recovery",
        registry_snapshot_ref={"registryId": "providers", "version": 1},
        executor_ref={"executorType": "agent", "executorId": "executor-1"},
        responsible_actor_ref={"actorType": "agent", "actorId": "actor-1"},
        owner_id="owner-1",
        workspace_id="workspace-1",
        expected_task_revision=7,
        providerless_recovery=True,
    )
    fence = type("AuthorizedFence", (), {
        "dispatch_id": binding.dispatch_id,
        "task_id": binding.task_id,
        "execution_decision_id": binding.execution_decision_id,
        "work_unit_id": binding.work_unit_id,
    })()
    events: list[str] = []

    class Resolver:
        def resolve(self):
            return binding

    class NoProviderFactory:
        def create(self, _provider_id):
            raise AssertionError("started single-attempt recovery must not construct a provider")

    class Service:
        def claim_authorized_recovery(self, **_kwargs):
            events.append("recovery_claim")
            return [fence]

        def claim(self, **_kwargs):
            raise AssertionError("ordinary claim must not follow providerless recovery")

    monkeypatch.setattr(module.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(module.settings, "persistence_backend", "postgres")

    def process(_fence, **kwargs):
        assert kwargs["provider"] is None
        events.append("recovery_process")

    monkeypatch.setattr(module, "process_authorized_recovery", process)

    assert module._claim_and_run_research_dispatch(
        binding_resolver=Resolver(), provider_factory=NoProviderFactory(), service=Service()
    ) is True
    assert events == ["recovery_claim", "recovery_process"]


def _resolved_dispatch_binding(module):
    return module.DispatchProviderBinding(
        provider_id="safe-test-provider",
        dispatch_id="dispatch-1",
        task_id="task-1",
        execution_decision_id="decision-1",
        work_unit_id="work-unit-1",
        registry_snapshot_ref={"registryId": "providers", "version": 1},
        executor_ref={"executorType": "agent", "executorId": "executor-1"},
        responsible_actor_ref={"actorType": "agent", "actorId": "actor-1"},
        owner_id="owner-1",
        workspace_id="workspace-1",
        expected_task_revision=4,
    )


def _dispatch_fence():
    return type(
        "Fence",
        (),
        {
            "dispatch_id": "dispatch-1",
            "task_id": "task-1",
            "execution_decision_id": "decision-1",
            "work_unit_id": "work-unit-1",
        },
    )()


def test_provider_factory_none_is_durably_deferred_after_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _reload(monkeypatch, env="test")
    events: list[tuple[str, str | None]] = []

    class Resolver:
        def resolve(self):
            return _resolved_dispatch_binding(module)

    class Factory:
        def create(self, _provider_id):
            events.append(("factory", None))
            return None

    class Service:
        def claim_authorized_recovery(self, **_kwargs):
            return []

        def claim(self, **_kwargs):
            events.append(("claim", None))
            return [_dispatch_fence()]

        def defer_unsubmitted(self, _fence, **kwargs):
            events.append(("defer", kwargs["error_code"]))
            return "retry_scheduled"

    monkeypatch.setattr(module.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(module.settings, "persistence_backend", "postgres")

    assert module._claim_and_run_research_dispatch(
        binding_resolver=Resolver(), provider_factory=Factory(), service=Service()
    ) is True
    assert events == [
        ("claim", None),
        ("factory", None),
        ("defer", "dispatch_provider_unavailable"),
    ]


def test_started_recovery_provider_unavailable_settles_unknown_without_presubmission_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _reload(monkeypatch, env="test")
    binding = _resolved_dispatch_binding(module)
    started = type(
        "AuthorizedFence",
        (),
        {
            "dispatch_id": binding.dispatch_id,
            "task_id": binding.task_id,
            "execution_decision_id": binding.execution_decision_id,
            "work_unit_id": binding.work_unit_id,
            "external_action_started_at": datetime.now(timezone.utc),
        },
    )()
    events: list[tuple[str, str | None]] = []

    class Resolver:
        def resolve(self):
            return binding

    class Factory:
        def create(self, _provider_id):
            events.append(("factory", None))
            return None

    class Service:
        def claim_authorized_recovery(self, **_kwargs):
            events.append(("recovery_claim", None))
            return [started]

        def record_outcome(self, _fence, **kwargs):
            events.append((kwargs["outcome"], kwargs["error_code"]))
            return True

        def defer_authorized(self, *_args, **_kwargs):
            raise AssertionError("started recovery must never use pre-submission retry")

        def claim(self, **_kwargs):
            raise AssertionError("ordinary claim must not follow started recovery")

    monkeypatch.setattr(module.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(module.settings, "persistence_backend", "postgres")

    assert module._claim_and_run_research_dispatch(
        binding_resolver=Resolver(), provider_factory=Factory(), service=Service()
    ) is True
    assert events == [
        ("recovery_claim", None),
        ("factory", None),
        ("unknown", "dispatch_provider_unavailable"),
    ]


def test_provider_factory_permanent_error_fails_claim_without_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _reload(monkeypatch, env="test")
    events: list[tuple[str, str | None]] = []

    class Resolver:
        def resolve(self):
            return _resolved_dispatch_binding(module)

    class Factory:
        def create(self, _provider_id):
            raise module.DispatchProviderPermanentPreflightError("provider_binding_invalid")

    class Service:
        def claim_authorized_recovery(self, **_kwargs):
            return []

        def claim(self, **_kwargs):
            events.append(("claim", None))
            return [_dispatch_fence()]

        def fail_unsubmitted(self, _fence, **kwargs):
            events.append(("fail", kwargs["error_code"]))
            return True

    monkeypatch.setattr(module.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(module.settings, "persistence_backend", "postgres")

    assert module._claim_and_run_research_dispatch(
        binding_resolver=Resolver(), provider_factory=Factory(), service=Service()
    ) is True
    assert events == [("claim", None), ("fail", "provider_binding_invalid")]


def test_task_programming_error_is_deferred_without_killing_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _reload(monkeypatch, env="test")
    deferred: list[str] = []

    class Resolver:
        def resolve(self):
            return _resolved_dispatch_binding(module)

    class Factory:
        def create(self, _provider_id):
            return object()

    class Service:
        def claim_authorized_recovery(self, **_kwargs):
            return []

        def claim(self, **_kwargs):
            return [_dispatch_fence()]

        def defer_unsubmitted(self, _fence, **kwargs):
            deferred.append(kwargs["error_code"])
            return "retry_scheduled"

    monkeypatch.setattr(module.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(module.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(
        module,
        "process_dispatch_claim",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("poison task")),
    )

    assert module._claim_and_run_research_dispatch(
        binding_resolver=Resolver(), provider_factory=Factory(), service=Service()
    ) is True
    assert deferred == ["dispatch_task_execution_error"]


def test_global_db_failure_exits_worker_for_supervisor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _reload(monkeypatch, env="test")
    monkeypatch.setattr(
        module,
        "_poll_once",
        lambda _cursor: (_ for _ in ()).throw(module.OperationalError("db down")),
    )

    with pytest.raises(RuntimeError, match="database authority unavailable"):
        module.run_forever()


def test_production_container_startup_requires_isolation_and_task_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAIR_ENV", "production")
    monkeypatch.setenv("OPENAIR_RUNTIME_TYPE", "container")
    monkeypatch.setenv("OPENAIR_WORKER_AUTH_TOKEN", "worker-secret")
    module = importlib.reload(entrypoint)
    monkeypatch.setattr(module.settings, "auth_required", True)
    monkeypatch.setattr(module.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(module.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(module.settings, "worker_workspace_isolation", False)
    monkeypatch.setattr(module.settings, "worker_per_task_token", False)

    with pytest.raises(RuntimeError, match="task workspace isolation"):
        module._validate_startup()

    monkeypatch.setattr(module.settings, "worker_workspace_isolation", True)
    monkeypatch.setattr(module.settings, "worker_per_task_token", True)
    module._validate_startup()


def test_production_container_startup_rejects_unsafe_callback_token_lifetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAIR_ENV", "production")
    monkeypatch.setenv("OPENAIR_RUNTIME_TYPE", "container")
    monkeypatch.setenv("OPENAIR_WORKER_AUTH_TOKEN", "worker-secret")
    module = importlib.reload(entrypoint)
    monkeypatch.setattr(module.settings, "auth_required", True)
    monkeypatch.setattr(module.settings, "research_orchestration_v1", True)
    monkeypatch.setattr(module.settings, "persistence_backend", "postgres")
    monkeypatch.setattr(module.settings, "worker_workspace_isolation", True)
    monkeypatch.setattr(module.settings, "worker_per_task_token", True)
    monkeypatch.setattr(module.settings, "worker_timeout_seconds", 600)
    monkeypatch.setattr(module.settings, "worker_token_ttl_seconds", 600)
    monkeypatch.setattr(module.settings, "worker_keepalive", False)

    with pytest.raises(RuntimeError, match="token TTL"):
        module._validate_startup()

    monkeypatch.setattr(module.settings, "worker_token_ttl_seconds", 601)
    monkeypatch.setattr(module.settings, "worker_keepalive", True)
    with pytest.raises(RuntimeError, match="keepalive"):
        module._validate_startup()


def test_production_worker_accepts_matching_api_startup_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _reload(monkeypatch, env="production")
    contract = {
        "contractVersion": 1,
        "runtimeType": "inline",
        "researchOrchestrationV1": True,
    }
    monkeypatch.setattr(module.settings, "execution_startup_contract", lambda: contract)
    monkeypatch.setattr(
        module,
        "_post",
        lambda path, body, **_kwargs: (
            {"contract": contract}
            if path == "/internal/worker/startup-contract" and body == {}
            else pytest.fail(f"unexpected startup request: {path}")
        ),
    )

    module._verify_api_startup_contract()


def test_production_worker_rejects_api_startup_contract_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _reload(monkeypatch, env="production")
    local = {
        "contractVersion": 1,
        "runtimeType": "inline",
        "researchOrchestrationV1": True,
    }
    remote = {**local, "runtimeType": "container"}
    monkeypatch.setattr(module.settings, "execution_startup_contract", lambda: local)
    monkeypatch.setattr(module, "_post", lambda *_args, **_kwargs: {"contract": remote})

    with pytest.raises(RuntimeError, match="execution contracts differ"):
        module._verify_api_startup_contract()

    output = capsys.readouterr().out
    assert '"event": "worker_startup_contract_mismatch"' in output
    assert '"runtimeType"' in output


def test_run_scoped_post_uses_claim_token_in_per_task_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _reload(monkeypatch, env="test")
    monkeypatch.setattr(module.settings, "worker_per_task_token", True)
    calls = []

    def post(path, body, **kwargs):
        calls.append((path, body, kwargs))
        return {"ok": True}

    monkeypatch.setattr(module, "_post", post)
    result = module._post_for_run(
        "/internal/durable-runs/run-1/callback",
        {"workerId": "worker-1", "leaseEpoch": 4},
        {"runId": "run-1", "leaseEpoch": 4, "workerToken": "wt.scoped"},
    )
    assert result == {"ok": True}
    assert calls == [
        (
            "/internal/durable-runs/run-1/callback",
            {"workerId": "worker-1", "leaseEpoch": 4},
            {"worker_token": "wt.scoped"},
        )
    ]
