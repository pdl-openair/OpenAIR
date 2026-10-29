from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.modules import historical_research_record_projection as projection
from app.routes import tasks


def case():
    draft = {"id": "draft", "artifactType": "research_records", "sourceRefs": ["packet", "schema"],
             "validatorResult": {"warnings": ["unfinalized_scientific_content"]}}
    return {"taskId": "task", "artifacts": [draft, {"artifactId": "packet"}, {"artifactId": "schema"}]}, draft


def test_display_uses_only_actual_associated_sources_and_retains_original(monkeypatch):
    task, artifact = case()
    content = '{"research_records":[{"research_object":"原值"}]}'
    captured = []
    monkeypatch.setattr(projection, "execute_registered_finalizer", lambda **kwargs:
                        captured.append(kwargs) or SimpleNamespace(output={"research_records": [{"record_id": "r1"}]}))
    sources = {"packet": '{"paper_packets":[{"paper_id":"p"}]}', "schema": '{"evidence_schema":{"schema_id":"s"}}'}
    result = projection.historical_record_display_projection(task, artifact, content, load_source=sources.__getitem__)
    assert json.loads(result["content"])["research_records"] == [{"record_id": "r1"}]
    assert result["originalStatus"] == "unfinalized"
    assert captured[0]["input_payload"] == {"paper_packets": [{"paper_id": "p"}], "evidence_schema": {"schema_id": "s"}}
    assert captured[0]["draft_payload"] == json.loads(content)
    assert content == '{"research_records":[{"research_object":"原值"}]}'


def test_unassociated_sources_do_not_produce_display_projection():
    task, artifact = case()
    artifact["sourceRefs"] = []
    assert projection.historical_record_display_projection(
        task, artifact, "{}", load_source=lambda _ref: (_ for _ in ()).throw(AssertionError("unexpected read")),
    ) is None


@pytest.mark.parametrize("error", [FileNotFoundError("source unavailable"), HTTPException(status_code=404), HTTPException(status_code=403)])
def test_source_read_failure_keeps_original_content_readable(monkeypatch, error):
    task, artifact = case()
    content = '{"research_records":[{"research_object":"原值"}]}'
    monkeypatch.setattr(tasks, "_read_task_scoped_artifact", lambda _task, ref, _identity:
                        (task, artifact if ref == "draft" else {"id": ref}))
    def read(_task, value, *_args, **_kwargs):
        if value["id"] != "draft":
            raise error
        return content, "inline"
    monkeypatch.setattr(tasks, "load_artifact_content", read)
    response = tasks.get_artifact_content("task", "draft", {})
    assert response.content == content
    assert response.displayProjection is None
