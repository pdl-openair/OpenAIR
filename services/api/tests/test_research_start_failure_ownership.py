"""ResearchStart failure ownership, response preservation, and bounded recovery.

These are offline component checks. Transport and database seams are explicit
fakes; no case is live Provider, PostgreSQL transaction, or product E2E evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest
from app.execution import research_start_provider_adapter as adapter
from app.execution.research_start_processing import (
    PlannerProcessingError, PlannerSemanticInputError, local_planning_step,
    observe_planning_response,
)
from app.execution.research_start_provider_adapter import (
    PreparedPlannerAttempt, ProviderContractError, execute_prepared_attempt,
    decode_recorded_planner_response,
)
from app.execution.research_start_worker_runtime import _failure_result, _is_planner_retryable_failure
from app.modules.research_start_response_evidence import describe_response, merge_attempt_result


SCOPE = {"ownerId": "owner", "workspaceId": "workspace", "requestId": "request", "attemptId": "attempt"}
NOW = datetime(2026, 9, 20, tzinfo=timezone.utc)


class Transport:
    def __init__(self, body=None, *, raw=None):
        self.response = httpx.Response(
            200, request=httpx.Request("POST", "https://provider.test/"),
            **({"content": raw} if raw is not None else {"json": body or {"id": "provider-call", "answer": "ok"}}),
        )
        self.calls = 0

    def post(self, *_args, **_kwargs):
        self.calls += 1
        return self.response


def prepared(decode=lambda body: dict(body)):
    return PreparedPlannerAttempt(
        phase="research_planner", ordinal=1, provider="huiyan_chat_completions",
        model="test-model", endpoint="https://provider.test/",
        payload={"model": "test-model", "messages": []}, response_format=None,
        timeout_seconds=77.0, decode=decode,
    )


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "NEVER_PERSIST_THIS_SECRET")
    # Every HTTP interaction must use the explicit in-memory seam below.
    monkeypatch.setattr(httpx.Client, "post", lambda *_a, **_k: pytest.fail("Unexpected real HTTP"))


@pytest.mark.parametrize("exception", [KeyError("SECRET_VALUE"), TypeError("SECRET_VALUE"), ValueError("SECRET_VALUE"), RuntimeError("SECRET_VALUE")])
def test_code_fault_is_local_and_cannot_request_model_repair(exception):
    def decode(_body):
        with local_planning_step("research_design_compile"):
            raise exception
    transport = Transport()
    with pytest.raises(PlannerProcessingError) as captured:
        execute_prepared_attempt(prepared(decode), transport=transport)
    failure = _failure_result(captured.value)
    assert failure["failureCode"] == "planner_processing_failed"
    assert failure["processingDiagnostic"]["stage"] == "research_design_compile"
    assert failure["processingDiagnostic"]["errorType"] == type(exception).__name__
    assert captured.value.provider_call_ref == "provider-call"
    assert not _is_planner_retryable_failure(failure)
    assert "SECRET_VALUE" not in json.dumps(failure)
    assert transport.calls == 1


def test_untyped_decoder_failure_is_not_automatically_blame_assigned_to_model():
    def decode(_body):
        raise KeyError("missing_server_owned_join")
    with pytest.raises(PlannerProcessingError) as captured:
        execute_prepared_attempt(prepared(decode), transport=Transport())
    assert captured.value.safe_processing_diagnostic["stage"] == "response_decode"


def test_preparation_failure_never_submits(monkeypatch):
    item = prepared()
    monkeypatch.setattr(adapter, "apply_provider_schema_transport", lambda *_a, **_k: (_ for _ in ()).throw(ValueError("bad server schema")))
    object.__setattr__(item, "_provider_schema_bytes", b'{"type":"object"}')
    transport = Transport()
    with pytest.raises(PlannerProcessingError) as captured:
        execute_prepared_attempt(item, transport=transport)
    assert captured.value.safe_processing_diagnostic["stage"] == "request_preparation"
    assert transport.calls == 0


def test_response_capture_precedes_decoder_and_survives_failure():
    events = []
    def capture(_prepared, raw, metadata):
        events.append(("capture", raw, metadata))
    def decode(_body):
        events.append(("decode",))
        raise PlannerSemanticInputError("invalid field", error_code="planner_scientific_brief_invalid", field_paths=("/primaryResearchQuestions",))
    transport = Transport()
    with observe_planning_response(capture), pytest.raises(ProviderContractError) as captured:
        execute_prepared_attempt(prepared(decode), transport=transport)
    assert [event[0] for event in events] == ["capture", "decode"]
    assert json.loads(events[0][1])["id"] == "provider-call"
    failure = _failure_result(captured.value)
    assert failure["validatorDiagnostic"]["fieldPaths"] == ["/primaryResearchQuestions"]
    assert failure["plannerDiagnostic"]["stage"] == "semantic_validation"
    assert "routerDiagnostic" not in failure
    assert transport.calls == 1


def test_malformed_http_json_is_preserved_before_validation():
    seen = []
    with observe_planning_response(lambda _p, raw, meta: seen.append((raw, meta))):
        with pytest.raises(ProviderContractError) as captured:
            execute_prepared_attempt(prepared(), transport=Transport(raw=b'not-json'))
    assert seen[0][0] == b'not-json'
    assert captured.value.safe_error_code == "provider_envelope_invalid"


def test_observer_is_context_scoped_and_reset_after_failure():
    seen = []
    with pytest.raises(ProviderContractError), observe_planning_response(lambda *args: seen.append(args)):
        execute_prepared_attempt(prepared(), transport=Transport(raw=b'broken'))
    execute_prepared_attempt(prepared(), transport=Transport())
    assert len(seen) == 1


def test_recorded_complete_response_can_be_reprocessed_without_second_submission():
    records = []
    item = prepared(); transport = Transport()
    with observe_planning_response(
        lambda _p, raw, meta: records.append((describe_response(raw, scope=SCOPE, metadata=meta, now=NOW), raw))
    ):
        actual = execute_prepared_attempt(item, transport=transport)
    record, raw = records[0]
    assert record["schemaVersion"] == "research-start-response-evidence.v2"
    assert "NEVER_PERSIST_THIS_SECRET" not in json.dumps(record)
    replayed = decode_recorded_planner_response(item, raw, metadata=record["metadata"])
    assert replayed == actual
    assert transport.calls == 1
    changed = prepared(); object.__setattr__(changed, "_request_bytes", b'{"model":"different","messages":[]}')
    with pytest.raises(ValueError, match="frozen Planner attempt"):
        decode_recorded_planner_response(changed, raw, metadata=record["metadata"])


def test_response_description_matches_directly_stored_body():
    raw = b'{"answer":"private response"}'
    record = describe_response(raw, scope=SCOPE, metadata={}, now=NOW)
    assert record["metadata"]["responseSha256"] == hashlib.sha256(raw).hexdigest()
    assert record["metadata"]["responseBytes"] == len(raw)


def test_response_description_requires_complete_attempt_scope():
    with pytest.raises(ValueError, match="scope is incomplete"):
        describe_response(b'{}', scope={**SCOPE, "attemptId": ""}, metadata={}, now=NOW)


def test_terminal_result_cannot_erase_or_replace_captured_response():
    evidence = {"immutable": "record"}
    for outcome in [{"failureCode": "planner_processing_failed"}, {"proposal": {}}, None, {"responseEvidence": {"forged": True}}]:
        merged = merge_attempt_result({"responseEvidence": evidence}, outcome)
        assert merged["responseEvidence"] == evidence


class Cursor:
    rowcount = 1
    def __init__(self, row): self.row, self.calls = row, []
    def __enter__(self): return self
    def __exit__(self, *_args): pass
    def execute(self, sql, params=None): self.calls.append((sql, params))
    def fetchone(self): return self.row


class Connection:
    def __init__(self, cursor): self.cur = cursor; self.commits = 0
    def cursor(self): return self.cur
    def commit(self): self.commits += 1
    def rollback(self): pass
    def close(self): pass


def repository(monkeypatch, *, attempt_state="started", lease_epoch=1, evidence=None):
    from app.modules.research_start_repository import ResearchStartRepository
    attempt = {
        "attempt_id": "attempt", "request_id": "request", "state": attempt_state,
        "phase": "research_planner", "ordinal": 1, "fence_epoch": 1,
        "result_payload": {"responseEvidence": evidence} if evidence else {},
    }
    cursor = Cursor(attempt); connection = Connection(cursor)
    repo = ResearchStartRepository(connection_factory=lambda: connection)
    session = {"state": "running", "session_id": "session", "owner_id": "owner", "workspace_id": "workspace"}
    request = {"state": "leased", "lease_owner": "worker", "lease_epoch": lease_epoch,
               "lease_is_live": True, "session_id": "session", "owner_id": "owner", "workspace_id": "workspace"}
    monkeypatch.setattr(repo, "_lock_path", lambda *_args: (session, request))
    monkeypatch.setattr(repo, "_lock_attempt", lambda *_args: attempt)
    return repo, cursor, connection


def test_repository_captures_only_matching_fenced_attempt(monkeypatch):
    repo, cursor, connection = repository(monkeypatch)
    repo.record_attempt_response("request", "worker", 1, "attempt", raw=b'{}', metadata={"phase": "research_planner", "ordinal": 1})
    value = json.loads(cursor.calls[0][1][0])
    assert value["responseEvidence"]["scope"] == SCOPE
    assert value["responseEvidence"]["schemaVersion"] == "research-start-response-evidence.v2"
    assert cursor.calls[0][1][1] == b'{}'
    assert connection.commits == 1


@pytest.mark.parametrize("state,epoch", [("failed", 1), ("succeeded", 1), ("started", 2)])
def test_repository_refuses_terminal_or_stale_writer(monkeypatch, state, epoch):
    from app.modules.research_start_repository import LeaseLostError
    repo, cursor, connection = repository(monkeypatch, attempt_state=state, lease_epoch=epoch)
    with pytest.raises(LeaseLostError):
        repo.record_attempt_response("request", "worker", 1, "attempt", raw=b'{}', metadata={"phase": "research_planner", "ordinal": 1})
    assert cursor.calls == []
    assert connection.commits == 0


@pytest.mark.parametrize("succeeded", [True, False])
def test_actual_finish_attempt_retains_response_evidence(monkeypatch, succeeded):
    record = {"captured": "earlier"}
    repo, cursor, _ = repository(monkeypatch, evidence=record)
    repo.finish_attempt("request", "worker", 1, "attempt", succeeded=succeeded, result={"failureCode": "local"})
    terminal = next(params for sql, params in cursor.calls if "UPDATE research_start_attempts" in sql)
    assert json.loads(terminal[1])["responseEvidence"] == record


def test_actual_unknown_attempt_retains_response_evidence(monkeypatch):
    record = {"captured": "earlier"}
    repo, cursor, _ = repository(monkeypatch, evidence=record)
    monkeypatch.setattr(repo, "_set_unknown", lambda *_a, **_k: {"state": "unknown"})
    repo.mark_external_result_unknown("request", "worker", 1, "attempt", {"failureCode": "unknown"})
    assert json.loads(cursor.calls[0][1][0])["responseEvidence"] == record


def pvd_plan(monkeypatch, *, fault=None):
    from app.modules.production_definition_source import ProductionDefinitionSource
    from app.modules.registry_snapshot_repository import InMemoryRegistrySnapshotRepository
    from app.modules.pvd_workflow_admission import pvd_workflow_planning_admission
    from app.modules.workflow_planning import workflow_planning_bundle
    from app.modules.research_design_workflow_semantic_provider import prepare_hybrid_workflow_planner_attempt, WorkflowSemanticProviderConfig
    from app.modules import research_design_compiler, research_design_planner_assembly
    goal = "40% AlSc 靶材，Mo 衬底。优化 Power、N₂、Ar、Deposition Time；温度、压力、靶基距未知，不得猜测。"
    source = ProductionDefinitionSource(archive=InMemoryRegistrySnapshotRepository(), planning_only=True)
    bundle = workflow_planning_bundle(selection=pvd_workflow_planning_admission(), raw_goal=goal, definition_source=source)
    context = dict(bundle.planning_context)
    nodes = [(row, "core") for row in context["coreResponsibilities"]] + [(row, "conditional") for row in context["conditionalResponsibilities"]]
    nodes.sort(key=lambda pair: pair[0]["displayOrder"])
    brief = {
        "researchGoal": "基于证据给出四参数首轮方案。", "primaryResearchQuestions": ["如何选择首轮四参数组合？"],
        "auxiliaryResearchDimensions": ["证据适用范围"], "targetOutputs": ["首轮四参数方案"],
        "constraints": ["保持未知设备条件，不补造事实"], "scientificUnknowns": ["温度、压力和靶基距未知"],
        "risksAndTradeoffs": ["文献条件可能不可直接迁移"],
        "responsibilityPlans": [
            {"mode": "planned_work" if requirement == "core" else "not_selected",
             "workItemScientificContexts": [f"结合当前材料与设备边界完成职责 {index} 的判断"] if requirement == "core" else [],
             "reusedResultRefs": [], "rationale": "按当前首轮目标决定是否进入该职责"}
            for index, (_, requirement) in enumerate(nodes)
        ],
    }
    if fault == "missing_field": brief.pop("primaryResearchQuestions")
    if fault == "missing_responsibility": brief["responsibilityPlans"].pop()
    if fault == "code":
        monkeypatch.setattr(research_design_compiler, "compile_research_design_proposal", lambda *_a, **_k: (_ for _ in ()).throw(KeyError("code_owned_reference")))
    item = prepare_hybrid_workflow_planner_attempt(
        raw_input=goal, ordinal=1, definition_catalog=bundle.definition_catalog,
        workflow_planning_context=context,
        provider_config=WorkflowSemanticProviderConfig("huiyan_chat_completions", "test-model", "https://provider.test/", 77.0),
    )
    transport = Transport({"id": "pvd-call", "choices": [{"message": {"content": json.dumps(brief)}}]})
    return item, transport, {row["definitionId"] for row in context["coreResponsibilities"]}


def test_current_pvd_workflow_compiles_without_changing_its_formal_nodes(monkeypatch):
    item, transport, core_ids = pvd_plan(monkeypatch)
    result = item.execute(transport=transport)
    assert result.provider_call_ref == "pvd-call"
    assert {row["definitionId"] for row in result.proposal["instances"]} == core_ids
    assert all(row["definitionId"].startswith("pvd:") for row in result.proposal["instances"])
    assert transport.calls == 1


@pytest.mark.parametrize("fault,code,path", [
    ("missing_field", "planner_scientific_brief_invalid", "/primaryResearchQuestions"),
    ("missing_responsibility", "workflow_responsibility_count_invalid", "/responsibilityPlans"),
])
def test_current_pvd_model_input_fault_has_specific_owner(monkeypatch, fault, code, path):
    item, transport, core_ids = pvd_plan(monkeypatch, fault=fault)
    with pytest.raises(ProviderContractError) as captured:
        item.execute(transport=transport)
    assert captured.value.safe_error_code == code
    assert path in captured.value.safe_field_paths
    assert not _is_planner_retryable_failure(_failure_result(captured.value))
    assert transport.calls == 1


def test_current_pvd_compiler_fault_is_local_not_model(monkeypatch):
    item, transport, core_ids = pvd_plan(monkeypatch, fault="code")
    with pytest.raises(PlannerProcessingError) as captured:
        item.execute(transport=transport)
    assert captured.value.safe_processing_diagnostic["stage"] == "research_design_compile"
    assert transport.calls == 1


@pytest.mark.parametrize("provider,body", [
    ("chat", {"choices": [{"finish_reason": "length", "message": {"content": "", "reasoning_content": "private thinking"}}]}),
    ("chat", {"choices": []}),
    ("responses", {"output": [{"type": "refusal", "refusal": "private refusal"}]}),
])
def test_final_content_failures_are_provider_output_not_assembly(provider, body):
    from app.execution.research_start_processing import read_provider_content
    from app.execution.llm_structured_runner import extract_final_chat_content, _extract_responses_output_text
    extract = extract_final_chat_content if provider == "chat" else _extract_responses_output_text
    item = prepared(lambda received: read_provider_content(extract, received))
    transport = Transport(body)
    with pytest.raises(ProviderContractError) as caught:
        execute_prepared_attempt(item, transport=transport)
    assert caught.value.safe_error_code == "provider_final_content_unavailable"
    failure = _failure_result(caught.value)
    assert failure["plannerDiagnostic"]["stage"] == "provider_content"
    assert not _is_planner_retryable_failure(failure)
    assert "private" not in json.dumps(failure)
    assert transport.calls == 1


def test_public_session_projection_never_exposes_internal_response_evidence():
    from app.modules.research_start_repository import _session_public
    payload = _session_public({
        "state": "failed", "session_id": "session", "reserved_task_id": "task",
        "failure_code": "planner_processing_failed", "attempt_result_payload": {
            "responseEvidence": {"internal": "sensitive internal metadata"},
            "processingDiagnostic": {"stage": "research_design_compile"},
        },
    }, attempt_phase="research_planner")
    assert payload["status"] == "failed"
    assert "sensitive" not in json.dumps(payload)
    assert "responseEvidence" not in json.dumps(payload)


@pytest.mark.parametrize("failure_stage", ["prepare", "compile", "capture"])
def test_worker_local_failure_never_reissues_planner_and_records_owner(monkeypatch, failure_stage):
    from app.execution import research_start_worker_runtime as runtime
    from app.execution.research_start_processing import record_planning_response
    from app.modules import production_definition_source as source_module
    from app.modules import research_design_service as service_module
    from app.modules import research_orchestration_repository as orchestration_module
    from app.modules import research_start_task as task_module
    from app.modules import workflow_planning
    task = SimpleNamespace(task_id="task", revision=1, research_design_versions=[], planning_attempts=[])
    events, outcomes = [], []
    class Source:
        def planner_catalog_for_research_design(self, _admission):
            return []
    class Repo:
        def reconcile_committed_design_request(self, *_args, **_kwargs):
            return None
        def begin_attempt(self, *_args):
            events.append("begin")
            return {"externalCallPermitted": True, "attemptId": "attempt"}
        def record_attempt_response(self, *_args, **_kwargs):
            events.append("capture")
            if failure_stage == "capture": raise ValueError("DATABASE_SECRET")
        def finish_attempt(self, *_args, **kwargs):
            events.append("finish")
            outcomes.append(kwargs["result"])
        def fail_request(self, *_args, **kwargs):
            events.append("fail")
            assert kwargs["failure_code"] == "planner_processing_failed"
            assert "修复系统处理问题" in kwargs["user_message"]
    class Provider:
        def prepare_planner(self, *_args, **kwargs):
            events.append("prepare")
            assert kwargs["ordinal"] == 1
            if failure_stage == "prepare": raise TypeError("BAD_LOCAL_CONFIG")
            return SimpleNamespace(phase="research_planner")
        def execute_planner(self, item):
            events.append("submit")
            record_planning_response(item, b'{}', {})
            with local_planning_step("research_design_compile"):
                raise KeyError("PRIVATE_LOCAL_STATE")
    class Service:
        def plan(self, **kwargs):
            return kwargs["provider"]("same-attempt")
    monkeypatch.setattr(source_module, "ProductionDefinitionSource", lambda **_k: Source())
    monkeypatch.setattr(service_module, "ResearchDesignService", lambda **_k: Service())
    monkeypatch.setattr(orchestration_module, "ResearchOrchestrationRepository", lambda: SimpleNamespace(load=lambda **_k: task))
    monkeypatch.setattr(task_module, "ensure_research_design_task", lambda **_k: task)
    monkeypatch.setattr(workflow_planning, "workflow_planning_bundle", lambda **_k: None)
    context = {"requestId": "request", "sessionId": "session", "workerId": "worker", "leaseEpoch": 1,
               "reservedTaskId": "task", "ownerId": "owner", "workspaceId": "workspace", "createdBy": "owner",
               "attempts": [], "intent": {"input": "research", "sourcePage": "home"}}
    runtime._run_research_design_planner(Repo(), Provider(), context, SimpleNamespace(lost=False), {})
    assert events.count("prepare") == 1
    assert events.count("submit") == (0 if failure_stage == "prepare" else 1)
    assert events[-1] == "fail"
    if failure_stage != "prepare":
        assert events == ["prepare", "begin", "submit", "capture", "finish", "fail"]
        assert outcomes[0]["failureCode"] == "planner_processing_failed"
        assert not _is_planner_retryable_failure(outcomes[0])
        assert "SECRET" not in json.dumps(outcomes)



def test_response_capture_rechecks_live_lease_at_the_write(monkeypatch):
    from app.modules.research_start_repository import LeaseLostError
    repo, cursor, connection = repository(monkeypatch)
    cursor.rowcount = 0
    with pytest.raises(LeaseLostError):
        repo.record_attempt_response("request", "worker", 1, "attempt", raw=b'{}', metadata={"phase": "research_planner", "ordinal": 1})
    assert "r.lease_expires_at > clock_timestamp()" in cursor.calls[0][0]
    assert connection.commits == 0


def test_authoritative_crash_reconciliation_preserves_response_evidence():
    from app.modules.research_start_repository import ResearchStartRepository
    evidence = {"retained": "recorded"}
    attempt = {"state": "started", "attempt_id": "attempt", "request_id": "request",
               "fence_epoch": 1, "result_payload": {"responseEvidence": evidence}}
    session = {"session_id": "session", "owner_id": "owner", "workspace_id": "workspace",
               "reserved_task_id": "task"}
    cursor = Cursor({**session, "state": "completed"})
    ResearchStartRepository._complete_committed_design_lineage(
        cursor, session, {**session, "request_id": "request"}, attempt,
        {"providerCallRef": "call", "researchDesignStatus": "draft"}, worker_id="worker",
        lease_epoch=1, require_expired_lease=False, user_message="Design ready",
    )
    assert json.loads(cursor.calls[0][1][0])["responseEvidence"] == evidence
