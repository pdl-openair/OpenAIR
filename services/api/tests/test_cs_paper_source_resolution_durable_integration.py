from __future__ import annotations

import pytest

from app.execution import durable_skill_runtime


def _evidenced_source_provider_failure() -> dict:
    return {
        "state": "failed",
        "failure_code": "source_provider_unavailable",
        "failed_stage": "source_lookup",
        "execution_evidence": {
            "operations": [{
                "operation": "arxiv_metadata_search",
                "endpoint": "https://export.arxiv.org/api/query?search_query=all%3AExampleNet",
                "outcome": "failed",
                "http_status": 406,
            }],
            "network_action_performed": True,
        },
    }


def _schema_valid_succeeded() -> dict:
    return {
        "state": "succeeded",
        "repository": "https://github.com/princeton-nlp/SimCSE.git",
        "requested_revision": "HEAD",
        "source_evidence": {
            "kind": "primary_paper_author_declared_code_link",
            "source_url": "https://arxiv.org/abs/2104.08821v4",
            "repository_url": "https://github.com/princeton-nlp/SimCSE.git",
            "selection_basis": "unique_author_declared_code_link_in_primary_paper",
            "content_form": "metadata_only",
            "paper_identity": {
                "arxiv_id": "2104.08821v4",
                "title": "SimCSE: Simple Contrastive Learning of Sentence Embeddings",
            },
        },
        "execution_evidence": {
            "operations": [
                {
                    "operation": "literature_metadata_search",
                    "endpoint": "literature_metadata:arxiv:SimCSE",
                    "outcome": "succeeded",
                },
                {
                    "operation": "primary_paper_content_read",
                    "endpoint": "metadata:arxiv:2104.08821v4",
                    "outcome": "succeeded",
                },
            ],
            "network_action_performed": True,
        },
    }


def _schema_valid_incomplete() -> dict:
    return {
        "state": "incomplete",
        "resolution_status": "code_link_missing",
        "repository": None,
        "requested_revision": None,
        "candidate_options": [],
        "execution_evidence": {
            "operations": [{
                "operation": "literature_metadata_search",
                "endpoint": "literature_metadata:arxiv:ExampleNet",
                "outcome": "succeeded",
            }],
            "network_action_performed": True,
        },
    }


def _schema_invalid_with_execution_ops() -> dict:
    """Has real execution ops, but violates Formal Schema (helper name + extra fields)."""
    return {
        "state": "succeeded",
        "repository": "https://github.com/princeton-nlp/SimCSE.git",
        "requested_revision": "HEAD",
        "source_evidence": {
            "kind": "primary_paper_author_declared_code_link",
            "source_url": "https://arxiv.org/abs/2104.08821v4",
            "repository_url": "https://github.com/princeton-nlp/SimCSE.git",
            "selection_basis": "unique_author_declared_code_link_in_primary_paper",
            "helper_leak": "shared_arxiv_metadata_search",
        },
        "execution_evidence": {
            "operations": [{
                "operation": "shared_arxiv_metadata_search",
                "endpoint": "shared:search_arxiv:SimCSE",
                "outcome": "succeeded",
            }],
            "network_action_performed": True,
        },
    }


def _schema_valid_without_network_evidence() -> dict:
    return {
        "state": "succeeded",
        "repository": "https://github.com/princeton-nlp/SimCSE.git",
        "requested_revision": "HEAD",
        "source_evidence": {
            "kind": "primary_paper_author_declared_code_link",
            "source_url": "https://arxiv.org/abs/2104.08821v4",
            "repository_url": "https://github.com/princeton-nlp/SimCSE.git",
            "selection_basis": "unique_author_declared_code_link_in_primary_paper",
        },
        "execution_evidence": {
            "operations": [],
            "network_action_performed": False,
        },
    }


def _run(monkeypatch, output: dict):
    monkeypatch.setattr(
        durable_skill_runtime,
        "run_python_skill_adapter",
        lambda **_kwargs: output,
    )
    return durable_skill_runtime.execute_durable_skill({
        "taskId": "task-source-resolution-boundary",
        "skillId": "cs_paper_source_resolution",
        "parameters": {
            "research_goal": "复现 SimCSE 论文结果",
            "repository": None,
            "revision": None,
        },
    })


def _run_with_adapter(monkeypatch, tmp_path, adapter_id: str, output: dict):
    monkeypatch.setattr(
        durable_skill_runtime,
        "run_python_skill_adapter",
        lambda **_kwargs: output,
    )
    monkeypatch.setattr(
        durable_skill_runtime,
        "prepare_task_workspace",
        lambda _task_id: tmp_path,
    )
    skill_id = adapter_id.removeprefix("python_adapter:")
    return durable_skill_runtime.execute_durable_skill(
        {
            "taskId": "task-formal-result-boundary",
            "skillId": skill_id,
            "parameters": {},
        },
        prepared_runtime={
            "skillId": skill_id,
            "skillPath": "unused-in-mocked-runner",
            "runtimeKind": durable_skill_runtime._RUNTIME_KIND_EXTERNAL_ADAPTER,
            "adapterId": adapter_id,
        },
    )


def test_source_adapter_preserves_an_evidenced_provider_failure(monkeypatch) -> None:
    result = _run(monkeypatch, _evidenced_source_provider_failure())

    assert result["state"] == "failed"
    assert result["failureClassification"] == "cs_paper_source_resolution_failed"
    assert result["failedStage"] == "source_lookup"
    assert result["diagnostics"] == {
        "adapter_failure_code": "source_provider_unavailable",
    }


def test_schema_valid_succeeded_is_not_relabeled_evidence_missing(monkeypatch) -> None:
    result = _run(monkeypatch, _schema_valid_succeeded())

    assert result["state"] == "succeeded"
    assert result.get("failureClassification") != "external_action_evidence_missing"
    assert result.get("failureClassification") != "skill_result_contract_invalid"


def test_schema_valid_incomplete_is_preserved(monkeypatch) -> None:
    result = _run(monkeypatch, _schema_valid_incomplete())

    assert result["state"] == "succeeded"
    assert result.get("failureClassification") != "external_action_evidence_missing"
    assert durable_skill_runtime._external_adapter_evidence_outcome(
        "python_adapter:cs_paper_source_resolution",
        _schema_valid_incomplete(),
    ) == "partial"


def test_schema_invalid_is_contract_error_not_evidence_missing(monkeypatch) -> None:
    result = _run(monkeypatch, _schema_invalid_with_execution_ops())

    assert result["state"] == "failed"
    assert result["failureClassification"] == "skill_result_contract_invalid"
    assert result["failureClassification"] != "external_action_evidence_missing"


def test_schema_valid_without_required_evidence_still_fail_closes(monkeypatch) -> None:
    result = _run(monkeypatch, _schema_valid_without_network_evidence())

    assert result["state"] == "failed"
    assert result["failureClassification"] == "external_action_evidence_missing"


@pytest.mark.parametrize(
    "adapter_id",
    [
        "python_adapter:cs_paper_source_resolution",
        "python_adapter:repository_revision_acquisition",
        "python_adapter:resource_identity_acquisition",
        "python_adapter:exploratory_run",
    ],
)
def test_schema_aware_external_adapters_classify_invalid_formal_results_as_contract_errors(
    monkeypatch,
    tmp_path,
    adapter_id: str,
) -> None:
    result = _run_with_adapter(
        monkeypatch,
        tmp_path,
        adapter_id,
        {"state": "succeeded", "unexpected": True},
    )

    assert result["state"] == "failed"
    assert result["failureClassification"] == "skill_result_contract_invalid"


def test_non_schema_gated_external_adapter_keeps_existing_evidence_classification(
    monkeypatch,
    tmp_path,
) -> None:
    result = _run_with_adapter(
        monkeypatch,
        tmp_path,
        "python_adapter:academic_source_retrieval_skill",
        {},
    )

    assert result["state"] == "failed"
    assert result["failureClassification"] == "external_action_evidence_missing"
