"""Protect the gate's acceptance of complete, source-backed CS evidence."""

import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest
from app.execution.durable_skill_runtime import resolve_registered_durable_skill
from app.execution.research_input_contract_gate import (
    InputContractGateFailure,
    SkillInputRequest,
    resolve_registered_skill_input,
    validate_registered_skill_input_snapshot,
)
from app.modules.repo_paths import repo_root
from app.modules.skill_binding_source import load_skill_binding_source_rows

ROOT = repo_root()
SOURCE = "cs_paper_source_resolution"
TARGET = "repository_revision_acquisition"


def _module(relative):
    spec = importlib.util.spec_from_file_location(Path(relative).stem, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


producer = _module(
    "skills/cs_research/cs-paper-source-resolution/adapter/paper_source_resolution_adapter.py"
)
receiver = _module(
    "skills/cs_research/repository-revision-acquisition/adapter/repository_revision_adapter.py"
)


@pytest.fixture(scope="module")
def contracts():
    definitions = [resolve_registered_durable_skill(skill) for skill in (SOURCE, TARGET)]
    assert all(definitions)
    rows = load_skill_binding_source_rows(ROOT / "skills/skill-bindings.yaml")
    bindings = [
        {**deepcopy(row), "enabled": True}
        for row in rows
        if row["definitionId"] == "cs_reproduction:reproduction_baseline_criteria"
        and row["skillId"] in (SOURCE, TARGET)
        and row["bindingVersion"]
        == max(item["bindingVersion"] for item in rows if item["bindingId"] == row["bindingId"])
    ]
    return {"definitions": {"skills": definitions}, "bindings": {"skills": bindings}}


def _handoff(snapshot, result):
    source = next(row for row in snapshot["bindings"]["skills"] if row["skillId"] == SOURCE)
    binding = next(row for row in snapshot["bindings"]["skills"] if row["skillId"] == TARGET)
    artifact = {
        "sourceCapabilityType": "skill",
        "sourceCapabilityId": SOURCE,
        "sourceCapabilityDefinitionVersion": source["skillDefinitionVersion"],
        "sourceBindingRefs": [
            {"bindingId": source["bindingId"], "bindingVersion": source["bindingVersion"]}
        ],
        "mimeType": "application/json",
        "content": json.dumps(result).encode(),
    }
    return resolve_registered_skill_input(
        snapshot=snapshot,
        request=SkillInputRequest(
            "handoff-test", "direct_skill", TARGET, binding["skillDefinitionVersion"]
        ),
        input_context={"artifacts": [artifact]},
        skill_binding=binding,
    )


def _produce(monkeypatch, form="metadata_only"):
    hit = SimpleNamespace(
        arxiv_id="2104.08821v4",
        title="SimCSE: Simple Contrastive Learning of Sentence Embeddings",
        abs_url="https://arxiv.org/abs/2104.08821v4",
        pdf_url="https://arxiv.org/pdf/2104.08821v4",
        abstract="Method description.",
        comment="Our code is available at https://github.com/princeton-nlp/simcse"
        if form == "metadata_only"
        else None,
        authors=[],
        year=2021,
        venue="arXiv",
        doi=None,
    )
    sources = producer._shared_sources()
    monkeypatch.setattr(sources, "search_arxiv", lambda *args, **kwargs: [hit])
    monkeypatch.setattr(
        producer,
        "_http_get",
        lambda url: (
            (200, "Our code is available at https://github.com/princeton-nlp/simcse")
            if form == "landing_page"
            else (404, "")
        ),
    )
    if form == "pdf":
        import pymupdf

        doc = pymupdf.open()
        doc.new_page().insert_text(
            (72, 72), "Our code is available at https://github.com/princeton-nlp/simcse"
        )
        body = doc.tobytes()
        doc.close()
        monkeypatch.setattr(producer, "_http_get_bytes", lambda url: (200, body))
    else:
        monkeypatch.setattr(producer, "_http_get_bytes", lambda url: (404, b""))
    result = producer.run_cs_paper_source_resolution(
        {"research_goal": "复现 SimCSE 论文结果", "repository": None, "revision": None}
    )
    assert result["state"] == "succeeded"
    assert result["source_evidence"]["content_form"] == form
    return result


@pytest.mark.parametrize("form", ["metadata_only", "landing_page", "pdf"])
def test_real_producer_complete_evidence_reaches_receiver(monkeypatch, contracts, form):
    result = _produce(monkeypatch, form)
    payload = _handoff(contracts, result)
    assert payload == {
        "repository": result["repository"],
        "revision": result["requested_revision"],
        "source_evidence": result["source_evidence"],
    }
    assert receiver._validated_input(payload) == (
        payload["repository"],
        payload["revision"],
        result["source_evidence"],
    )


def test_historical_frozen_contract_stays_immutable(monkeypatch, contracts):
    result = _produce(monkeypatch)
    old = deepcopy(contracts)
    entry = old["definitions"]["skills"][1]["sourceContract"]["frozenSchemas"]["input"]
    historical = json.loads(
        (Path(__file__).parent / "historical_input_contract.json").read_text(encoding="utf-8")
    )
    entry.update(historical)
    before = deepcopy(old)
    with pytest.raises(InputContractGateFailure) as error:
        _handoff(old, result)
    assert error.value.code == "input_contract_invalid"
    assert error.value.field_paths == ("/source_evidence",)
    assert old == before
    current = contracts["definitions"]["skills"][1]["sourceContract"]["frozenSchemas"]["input"]
    assert current["sha256"] != entry["sha256"]
    assert {"content_form", "paper_identity"} <= set(
        current["schema"]["properties"]["source_evidence"]["properties"]
    )
    assert _handoff(contracts, result)["source_evidence"] == result["source_evidence"]


@pytest.mark.parametrize(
    "identity",
    [
        None,
        {"arxiv_id": "2104.08821v4"},
        {"title": "SimCSE"},
        {"doi": "10.1/example"},
        {"openalex_id": "W123"},
    ],
)
@pytest.mark.parametrize(
    "form", [None, "metadata_only", "landing_page", "html", "pdf", "xml", "tei"]
)
def test_declared_optional_evidence_shapes(monkeypatch, contracts, identity, form):
    result = _produce(monkeypatch)
    evidence = result["source_evidence"]
    evidence.pop("content_form")
    evidence.pop("paper_identity")
    if form is not None:
        evidence["content_form"] = form
    if identity is not None:
        evidence["paper_identity"] = identity
    payload = _handoff(contracts, result)
    assert payload["source_evidence"] == evidence
    assert receiver._validated_input(payload) is not None


@pytest.mark.parametrize(
    "field,value",
    [
        ("content_form", "fulltext"),
        ("content_form", 5),
        ("content_form", None),
        ("paper_identity", {}),
        ("paper_identity", "SimCSE"),
        ("paper_identity", {"title": 4}),
        ("paper_identity", {"doi": ""}),
        ("paper_identity", {"arxiv_id": "a" * 129}),
        ("paper_identity", {"title": "a" * 1025}),
        ("paper_identity", {"doi": "a" * 257}),
        ("paper_identity", {"openalex_id": "a" * 257}),
        ("paper_identity", {"title": "SimCSE", "trusted": True}),
        ("trusted", True),
    ],
)
def test_invalid_evidence_rejected_by_gate_and_receiver(monkeypatch, contracts, field, value):
    result = _produce(monkeypatch)
    result["source_evidence"][field] = value
    with pytest.raises(InputContractGateFailure):
        _handoff(contracts, result)
    payload = {
        "repository": result["repository"],
        "revision": "HEAD",
        "source_evidence": result["source_evidence"],
    }
    binding = next(row for row in contracts["bindings"]["skills"] if row["skillId"] == TARGET)
    with pytest.raises(InputContractGateFailure, match="input_contract_invalid"):
        validate_registered_skill_input_snapshot(
            snapshot=contracts,
            request=SkillInputRequest("test", "direct_skill", TARGET, 1),
            resolved_input=payload,
            skill_binding=binding,
        )
    assert receiver._validated_input(payload) is None


@pytest.mark.parametrize("field", ["kind", "source_url", "repository_url", "selection_basis"])
def test_required_evidence_fields_remain_required(monkeypatch, contracts, field):
    result = _produce(monkeypatch)
    del result["source_evidence"][field]
    with pytest.raises(InputContractGateFailure):
        _handoff(contracts, result)


def test_output_preserves_complete_evidence(monkeypatch, contracts):
    result = _produce(monkeypatch)
    payload = _handoff(contracts, result)
    # Acquisition itself is outside this gate Suite; this asserts its receiver input.
    assert receiver._validated_input(payload)[2] == result["source_evidence"]
    frozen = contracts["definitions"]["skills"][0]["sourceContract"]["frozenSchemas"]["output"][
        "schema"
    ]
    jsonschema.validate(result, frozen)
    target = contracts["definitions"]["skills"][1]["sourceContract"]["frozenSchemas"]
    for name in ("input", "output"):
        schema = target[name]["schema"]
        assert schema["definitions"]["paperIdentity"] == frozen["definitions"]["paperIdentity"]
        assert (
            schema["properties"]["source_evidence"]["properties"]["content_form"]
            == (frozen["definitions"]["sourceEvidence"]["properties"]["content_form"])
        )


@pytest.mark.parametrize("branch", ["user", "declared_page", "checkpoint"])
def test_other_real_producer_branches_without_paper_identity(monkeypatch, contracts, branch):
    page = "https://aclanthology.org/2021.emnlp-main.552/"
    payload = {
        "research_goal": "复现 exact-checkpoint 的结果",
        "repository": None,
        "revision": None,
    }
    if branch == "user":
        payload.update(repository="https://github.com/princeton-nlp/simcse.git", revision="v1.0")
        monkeypatch.setattr(
            producer.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
        )
    elif branch == "declared_page":
        payload["research_goal"] = f"复现论文结果，论文页面是 {page}"
        monkeypatch.setattr(
            producer,
            "_http_get",
            lambda url: (200, "Code: https://github.com/princeton-nlp/simcse"),
        )
    else:
        sources = producer._shared_sources()
        monkeypatch.setattr(sources, "search_arxiv", lambda *args, **kwargs: [])
        monkeypatch.setattr(sources, "search_openalex", lambda *args, **kwargs: [])
        monkeypatch.setattr(
            producer,
            "_http_get",
            lambda url: (
                (200, '[{"id":"owner/exact-checkpoint"}]')
                if "api/models?" in url
                else (200, "Code: https://github.com/princeton-nlp/simcse")
            ),
        )
    result = producer.run_cs_paper_source_resolution(payload)
    assert result["state"] == "succeeded"
    assert "paper_identity" not in result["source_evidence"]
    received = _handoff(contracts, result)
    assert received["source_evidence"] == result["source_evidence"]
    assert receiver._validated_input(received) is not None
