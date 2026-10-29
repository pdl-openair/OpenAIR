from fastapi.testclient import TestClient

from app.main import app
from app.modules import literature_live
from app.modules.literature_live import Attempt, LiteratureRun, SourceRow
from app.modules.task_store import store


client = TestClient(app)


def _artifact_content(artifact: dict) -> str:
    response = client.get(f"/api/artifacts/{artifact['id']}/content")
    assert response.status_code == 200, response.text
    return response.json()["content"]


def _row(
    title: str,
    abstract: str,
    *,
    source_api: str = "arXiv",
    external_id: str | None = None,
    year: int = 2024,
) -> SourceRow:
    external_id = external_id or title.lower().replace(" ", "-")[:48]
    source_mode = "arxiv_fulltext" if source_api == "arXiv" else "abstract_available"
    return SourceRow(
        source_ref="S0",
        title=title,
        authors=["A. Researcher"],
        year=year,
        venue=source_api,
        doi=None if source_api == "arXiv" else f"10.0000/{external_id}",
        arxiv_id=external_id if source_api == "arXiv" else None,
        url=f"https://example.org/{external_id}",
        abstract=abstract,
        source_api=source_api,
        external_id=external_id,
        retrieved_at=literature_live.utc_now(),
        source_mode=source_mode,
        readiness_label=source_mode,
        selection_reason="pending",
        provenance={
            "sourceApi": source_api,
            "query": "",
            "externalId": external_id,
            "url": f"https://example.org/{external_id}",
            "retrievedAt": literature_live.utc_now(),
            "sourceMode": source_mode,
        },
    )


def _sample_run(topic: str) -> LiteratureRun:
    run = LiteratureRun(
        topic=topic,
        strategy=literature_live.build_search_strategy(topic, top_k=2),
        key_status={
            "OPENALEX_MAILTO": "missing",
            "SEMANTIC_SCHOLAR_API_KEY": "present",
            "UNPAYWALL_EMAIL": "present",
            "ARXIV_API_KEY": "not_configured_optional",
        },
        attempted_apis=[Attempt("OpenAlex", topic, "ok", literature_live.utc_now(), result_count=2)],
    )
    run.sources = [
        SourceRow(
            source_ref="S1",
            title="Graph neural networks for molecular property prediction",
            authors=["A. Researcher"],
            year=2024,
            venue="OpenAlex",
            doi="10.0000/example",
            arxiv_id=None,
            url="https://example.org/paper1",
            abstract="Graph neural networks are widely used for molecular property prediction with benchmark datasets.",
            source_api="OpenAlex",
            external_id="https://openalex.org/W1",
            retrieved_at=literature_live.utc_now(),
            source_mode="abstract_available",
            readiness_label="有摘要",
            selection_reason="semantic relevance supplied by the controlled test fixture",
            provenance={
                "sourceApi": "OpenAlex",
                "query": topic,
                "externalId": "https://openalex.org/W1",
                "url": "https://example.org/paper1",
                "retrievedAt": literature_live.utc_now(),
                "sourceMode": "abstract_available",
            },
            score=80.0,
            relevance_score=80.0,
            relevance_reason="semantic relevance supplied by the controlled test fixture",
            is_relevant_enough=True,
            selection_status="selected",
        ),
        SourceRow(
            source_ref="S2",
            title="A survey of molecular representation learning",
            authors=["B. Scholar"],
            year=2023,
            venue="arXiv",
            doi=None,
            arxiv_id="2401.00001",
            url="https://arxiv.org/abs/2401.00001",
            abstract="This survey reviews molecular representation learning methods and evaluation settings.",
            source_api="arXiv",
            external_id="2401.00001",
            retrieved_at=literature_live.utc_now(),
            source_mode="arxiv_fulltext",
            readiness_label="arXiv 全文线索",
            selection_reason="semantic relevance supplied by the controlled test fixture",
            provenance={
                "sourceApi": "arXiv",
                "query": topic,
                "externalId": "2401.00001",
                "url": "https://arxiv.org/abs/2401.00001",
                "retrievedAt": literature_live.utc_now(),
                "sourceMode": "arxiv_fulltext",
            },
            score=72.0,
            relevance_score=72.0,
            relevance_reason="semantic relevance supplied by the controlled test fixture",
            is_relevant_enough=True,
            selection_status="selected",
        ),
    ]
    for row in run.sources:
        row.provenance["sourceRef"] = row.source_ref
        row.provenance["isRelevantEnough"] = True
    run.top_k = [
        {
            "rank": idx,
            "sourceRef": row.source_ref,
            "title": row.title,
            "relevanceScore": row.relevance_score,
            "matchedTerms": [],
            "matchedPhrases": [],
            "missingCoreTerms": [],
            "selectionReason": row.selection_reason,
            "readinessLabel": row.readiness_label,
            "boundary": "metadata/abstract bounded",
        }
        for idx, row in enumerate(run.sources, start=1)
    ]
    run.relevance_gate = {
        "passed": True,
        "reasons": [],
        "retrievalCount": 2,
        "dedupedCount": 2,
        "relevantCandidateCount": 2,
        "topKRelevantCount": 2,
        "maxRelevanceScore": 80.0,
        "averageTopKRelevance": 76.0,
    }
    return run


def _record_live_output(task_id: str, stage_key: str) -> None:
    task = store.get_task(task_id)
    assert task is not None
    stage = next(row for row in task["stageDetails"] if row["stageKey"] == stage_key)
    substage = next(
        (row for row in stage["subStages"] if row["subStage"] == "gap_next_steps"),
        stage["subStages"][-1],
    )
    if substage["status"] != "running":
        store.patch_substage_instance(task_id, substage["subStageInstanceId"], status="running")
    call = store.add_capability_call(
        task_id,
        skill_id=substage["subStage"],
        status="completed",
        runtime="live",
        stage_key=stage_key,
        substage_instance_id=substage["subStageInstanceId"],
    )
    assert call is not None
    artifact = store.add_artifact(
        task_id,
        kind="stage_summary",
        title=f"Live output for {substage['subStage']}",
        related_stage=stage_key,
        related_skill_id=substage["subStage"],
        substage_instance_id=substage["subStageInstanceId"],
        source_capability_call_id=call["id"],
        storage_type="inline",
        inline_content="# Reviewable live output",
    )
    assert artifact is not None


def _install_retrieval(
    monkeypatch,
    rows: list[SourceRow],
    *,
    openalex_rows: list[SourceRow] | None = None,
    semantic_rows: list[SourceRow] | None = None,
) -> None:
    def fake_openalex(query: str, *, limit: int, timeout: float):
        returned = openalex_rows or []
        for row in returned:
            row.provenance["query"] = query
        return returned, Attempt("OpenAlex", query, "ok", literature_live.utc_now(), result_count=len(returned))

    def fake_semantic(query: str, *, limit: int, timeout: float):
        returned = semantic_rows or []
        for row in returned:
            row.provenance["query"] = query
        return returned, Attempt("Semantic Scholar", query, "ok", literature_live.utc_now(), result_count=len(returned))

    def fake_arxiv(query: str, *, limit: int, timeout: float):
        for row in rows:
            row.provenance["query"] = query
        return rows, Attempt("arXiv", query, "ok", literature_live.utc_now(), result_count=len(rows))

    def fake_unpaywall(rows: list[SourceRow], query: str, *, timeout: float):
        return Attempt("Unpaywall", query, "not_configured", literature_live.utc_now(), result_count=0)

    monkeypatch.setattr(literature_live, "_openalex_sources", fake_openalex)
    monkeypatch.setattr(literature_live, "_semantic_scholar_sources", fake_semantic)
    monkeypatch.setattr(literature_live, "_arxiv_sources", fake_arxiv)
    monkeypatch.setattr(literature_live, "_unpaywall_enrich", fake_unpaywall)


def _assert_gap_response(body):
    assert body["outcome"] == "gap_state"
    failure = body["state"]["failureState"]
    for key in ("reason", "impact", "nextAction", "retryPolicy", "partialOutputStatus"):
        assert failure[key]
    assert body["state"]["attemptedSourceApis"]
    assert body["state"]["attemptedQueries"]
    assert "retrievalCount" in body["state"]
    assert "relevantCandidateCount" in body["state"]
    assert "topKRelevantCount" in body["state"]








def test_literature_live_attempts_all_search_sources_and_unpaywall(monkeypatch):
    arxiv_rows = [
        _row(
            "Retrieval augmented generation evaluation benchmark survey",
            "This survey covers retrieval augmented generation evaluation benchmark datasets and metrics.",
            external_id="arxiv-rag-1",
        ),
        _row(
            "Benchmarking retrieval augmented generation systems",
            "The paper evaluates retrieval augmented generation systems with benchmark tasks.",
            external_id="arxiv-rag-2",
        ),
    ]
    semantic_rows = [
        _row(
            "Evaluating retrieval augmented generation benchmarks",
            "A Semantic Scholar indexed paper on retrieval augmented generation evaluation benchmarks.",
            source_api="Semantic Scholar",
            external_id="s2-rag-1",
        ),
        _row(
            "Retrieval augmented generation benchmark datasets",
            "A benchmark dataset paper for retrieval augmented generation evaluation.",
            source_api="Semantic Scholar",
            external_id="s2-rag-2",
        ),
    ]
    openalex_rows = [
        _row(
            "Retrieval augmented generation evaluation methods",
            "OpenAlex metadata for retrieval augmented generation evaluation benchmark methods.",
            source_api="OpenAlex",
            external_id="openalex-rag-1",
        )
    ]
    _install_retrieval(monkeypatch, arxiv_rows, openalex_rows=openalex_rows, semantic_rows=semantic_rows)

    run = literature_live.run_literature_live("retrieval augmented generation evaluation benchmark", top_k=4)

    attempted = [attempt.source_api for attempt in run.attempted_apis]
    assert attempted == ["OpenAlex", "Semantic Scholar", "arXiv", "Unpaywall"]
    assert {row.source_api for row in run.sources} == {"OpenAlex", "Semantic Scholar", "arXiv"}
    source_table = literature_live.markdown_artifacts(run)["source_table"]
    assert "sourceApi=Semantic Scholar" in source_table
    assert "sourceApi=OpenAlex" in source_table




def test_literature_project_creates_traceable_artifacts(monkeypatch):
    monkeypatch.setattr("app.routes.starters.run_literature_live", lambda topic, top_k=8: _sample_run(topic))
    before = {t["taskId"] for t in store.list_tasks()}

    res = client.post(
        "/api/starters/project_research_direction_review_v0_3/use",
        json={"goalSummary": "molecular graph neural network benchmarks"},
    )

    after = {t["taskId"] for t in store.list_tasks()}
    assert res.status_code == 201
    body = res.json()
    assert body["outcome"] == "literature_artifacts"
    assert len(after - before) == 1
    assert body["task"]["status"] == "awaiting_confirmation"
    catalog_keys = [
        entry["key"]
        for stage in body["task"]["stageCatalog"]
        for entry in stage["allowedSubStages"]
    ]
    assert "gap_next_steps" in catalog_keys
    assert "literature_search" in catalog_keys
    literature_stage = next(stage for stage in body["task"]["stageDetails"] if stage["stageKey"] == "problem_modeling")
    assert literature_stage["label"] == "调研与证据整理"
    assert literature_stage["completionCondition"]
    assert literature_stage["primaryAction"]
    locked_stage = next(stage for stage in body["task"]["stageDetails"] if stage["stageKey"] == "solution_design")
    assert locked_stage["label"] == "补充来源"
    assert locked_stage["status"] == "not_started"
    assert locked_stage["lockedReason"]
    assert locked_stage["subStages"][0]["subStage"] == "literature_search"
    assert {sub["subStage"] for sub in literature_stage["subStages"]} >= {
        "research_plan",
        "source_table",
        "recommended_readings",
        "literature_synthesis",
        "gap_next_steps",
    }
    titles = {a["title"] for a in body["task"]["artifacts"]}
    assert {"检索策略", "来源表", "候选来源清单", "可读性说明", "候选摘要摘录", "缺口与下一步"} <= titles
    source_table = next(a for a in body["task"]["artifacts"] if a["title"] == "来源表")
    source_table_content = _artifact_content(source_table)
    assert "完整 provenance" in source_table_content
    assert "OpenAlex" in source_table_content
    assert "sourceMode=abstract_available" in source_table_content
    review = next(a for a in body["task"]["artifacts"] if a["title"] == "候选摘要摘录")
    review_content = _artifact_content(review)
    assert "不形成科研综述" in review_content
    assert "代码不形成科研综述" in review_content

    task_id = body["task"]["taskId"]
    blocked = client.post(f"/api/tasks/{task_id}/advance-substage", params={"substage_key": "gap_next_steps"})
    assert blocked.status_code == 409
    resume = client.patch(f"/api/tasks/{task_id}/status", json={"status": "running"})
    assert resume.status_code == 200
    out_of_order = client.post(f"/api/tasks/{task_id}/advance-substage", params={"substage_key": "research_plan"})
    assert out_of_order.status_code == 409
    assert "completed real capability call" in out_of_order.json()["detail"]
    _record_live_output(task_id, "problem_modeling")
    advanced = client.post(f"/api/tasks/{task_id}/advance-substage", params={"substage_key": "gap_next_steps"})
    assert advanced.status_code == 200, advanced.text
    assert advanced.json()["hasNext"] is True
    assert advanced.json()["requiresConfirmation"] is True
    assert advanced.json()["stageKey"] == "problem_modeling"
    assert advanced.json()["nextStageKey"] == "solution_design"
    resume = client.patch(f"/api/tasks/{task_id}/status", json={"status": "running"})
    assert resume.status_code == 200
    entered = client.post(f"/api/tasks/{task_id}/advance-substage", params={"substage_key": "gap_next_steps"})
    assert entered.status_code == 200, entered.text
    assert entered.json()["stageKey"] == "solution_design"
    assert entered.json()["substageKey"] == "literature_search"
    advanced_task = store.get_task(task_id)
    assert advanced_task["currentStage"] == "solution_design"
    next_stage = next(stage for stage in advanced_task["stageDetails"] if stage["stageKey"] == "solution_design")
    assert next_stage["status"] == "running"
    assert next_stage["lockedReason"] is None
    assert next_stage["risks"] is None
    assert next_stage["nextSteps"] is None
    skills = client.get(f"/api/tasks/{task_id}/substage-skills", params={"substage_key": "literature_search"})
    assert skills.status_code == 200
    assert "补充来源" in set(skills.json()["labels"].values())
    guard_update = client.post(
        f"/api/tasks/{task_id}/stages/solution_design/result",
        json={
            "label": "补充来源复核",
            "entryCondition": "来源表已人工复核。",
            "completionCondition": "补充来源已记录影响说明。",
            "lockedReason": "等待补充来源确认。",
            "primaryAction": "继续补充来源。",
            "canReview": True,
            "canLock": True,
        },
    )
    assert guard_update.status_code == 200
    updated_stage = next(stage for stage in guard_update.json()["stageDetails"] if stage["stageKey"] == "solution_design")
    assert updated_stage["label"] == "补充来源复核"
    assert updated_stage["entryCondition"] == "来源表已人工复核。"
    assert updated_stage["primaryAction"] == "继续补充来源。"
    assert updated_stage["canReview"] is True

    _record_live_output(task_id, "solution_design")
    gated = client.post(f"/api/tasks/{task_id}/advance-substage", params={"substage_key": "literature_search"})
    assert gated.status_code == 200
    assert gated.json()["requiresConfirmation"] is True
    assert gated.json()["stageKey"] == "solution_design"
    assert gated.json()["nextStageKey"] == "execution_validation"
    blocked_without_resume = client.post(
        f"/api/tasks/{task_id}/advance-substage",
        params={"substage_key": "literature_search"},
    )
    assert blocked_without_resume.status_code == 409
    resume = client.patch(f"/api/tasks/{task_id}/status", json={"status": "running"})
    assert resume.status_code == 200
    advanced = client.post(f"/api/tasks/{task_id}/advance-substage", params={"substage_key": "literature_search"})
    assert advanced.status_code == 200
    assert advanced.json()["stageKey"] == "execution_validation"
    assert advanced.json()["substageKey"] == "evidence_extraction"
    assert store.get_task(task_id)["status"] == "running"

    _record_live_output(task_id, "execution_validation")
    gated = client.post(f"/api/tasks/{task_id}/advance-substage", params={"substage_key": "evidence_extraction"})
    assert gated.status_code == 200
    assert gated.json()["requiresConfirmation"] is True
    assert gated.json()["stageKey"] == "execution_validation"
    assert gated.json()["nextStageKey"] == "insight_generation"
    resume = client.patch(f"/api/tasks/{task_id}/status", json={"status": "running"})
    assert resume.status_code == 200
    advanced = client.post(f"/api/tasks/{task_id}/advance-substage", params={"substage_key": "evidence_extraction"})
    assert advanced.status_code == 200
    assert advanced.json()["stageKey"] == "insight_generation"
    assert advanced.json()["substageKey"] == "report_generation"
    assert store.get_task(task_id)["status"] == "running"

    _record_live_output(task_id, "insight_generation")
    completed = client.post(f"/api/tasks/{task_id}/advance-substage", params={"substage_key": "report_generation"})
    assert completed.status_code == 200
    assert completed.json() == {"completed": True, "hasNext": False}
    completed_task = store.get_task(task_id)
    assert completed_task["status"] == "completed"
    final_stage = next(stage for stage in completed_task["stageDetails"] if stage["stageKey"] == "insight_generation")
    assert final_stage["status"] == "completed"
    blocked_after_complete = client.post(
        f"/api/tasks/{task_id}/advance-substage",
        params={"substage_key": "report_generation"},
    )
    assert blocked_after_complete.status_code == 409


def test_literature_project_bounded_failure_does_not_create_empty_task(monkeypatch):
    def empty_run(topic, top_k=8):
        run = LiteratureRun(topic=topic, strategy=literature_live.build_search_strategy(topic), key_status={})
        run.attempted_apis = [
            Attempt("OpenAlex", topic, "failed", literature_live.utc_now(), error="network"),
            Attempt("arXiv", topic, "timeout", literature_live.utc_now(), error="timeout"),
        ]
        run.failure_state = {
            "reason": "外部文献 API 没有返回可用来源。",
            "impact": "无法形成完整来源表和短综述。",
            "nextAction": "补充关键词后重试。",
            "retryPolicy": "稍后重试。",
            "partialOutputStatus": "保留检索策略和 attempted APIs。",
            "attemptedSourceApis": [a.__dict__ for a in run.attempted_apis],
        }
        return run

    monkeypatch.setattr("app.routes.starters.run_literature_live", empty_run)
    before = {t["taskId"] for t in store.list_tasks()}
    res = client.post("/api/starters/project_research_direction_review_v0_3/use", json={"goalSummary": "unlikely topic"})
    after = {t["taskId"] for t in store.list_tasks()}

    assert res.status_code == 201
    body = res.json()
    assert body["outcome"] == "gap_state"
    assert body["task"] is not None
    assert body["artifact"]["kind"] == "literature_readiness_gap"
    assert body["state"]["missingInputs"] == []
    assert body["state"]["failureState"]["reason"]
    assert body["state"]["attemptedSourceApis"]
    assert len(after - before) == 1


def test_evidence_project_topic_only_still_requires_inputs():
    res = client.post(
        "/api/starters/project_paper_support_analysis_v0_3/use",
        json={"goalSummary": "molecular graph neural network benchmarks"},
    )

    assert res.status_code == 201
    body = res.json()
    assert body["outcome"] == "requires_existing_inputs"
    assert body["task"] is None
    assert body["state"]["failureState"]["state"] == "requires_existing_inputs"
