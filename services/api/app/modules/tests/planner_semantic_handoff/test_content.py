"""Approved scientific content must reach readable formal consumers."""

import hashlib
from copy import deepcopy

import pytest

from .support import (
    CONSTRAINTS,
    HERE,
    QUERY,
    build,
    bundle_for,
    confirm,
    decode,
    formal_text,
    freeform_payload,
    real_payload,
    workflow_payload,
)


def test_real_response_provenance_and_formal_confirm(source):
    assert hashlib.sha256((HERE / "real-response.json").read_bytes()).hexdigest() == (
        "9e20ffc4966dc4c4347cf7341900547a9a05ef9aa7fc0d95a56c0e79358eade7"
    )
    payload = real_payload()
    original = deepcopy(payload)
    bundle = bundle_for(source)
    proposal = decode(payload, bundle)
    service, repository, design = build(proposal, bundle, source)
    assert design.goal == payload["goal"]
    assert design.understanding.primary_research_questions == payload["questions"]
    assert set(payload["targetOutputs"]).issubset(design.understanding.target_outputs)
    text = formal_text(design)
    for key in ("questions", "unknowns", "risks", "targetOutputs"):
        for value in payload[key]:
            assert value in text
    for row in payload["responsibilityDeltas"]:
        assert any(row["currentProjectWorkContext"] in item.objective for item in design.instances)
    target = next(
        row
        for row in design.coverage_report.targets
        if row.description == payload["targetOutputs"][4]
    )
    claim = next(row for row in design.coverage_report.claims if row.target_id == target.target_id)
    instances = {row.substage_instance_id: row for row in design.instances}
    assert {
        instances[ref.substage_instance_id].substage_definition_id for ref in claim.instance_refs
    } == {
        "opentrajectory:key_unknowns_candidate_explanations",
        "opentrajectory:next_research_recommendation",
    }
    assert set(claim.acceptance_criterion_refs).issubset(
        {
            criterion.criterion_id
            for ref in claim.instance_refs
            for criterion in instances[ref.substage_instance_id].acceptance_criteria
        }
    )
    for ref in claim.instance_refs:
        assert set(claim.acceptance_criterion_refs).intersection(
            {
                criterion.criterion_id
                for criterion in instances[ref.substage_instance_id].acceptance_criteria
            }
        )
    task, confirmed = confirm(service, repository, design)
    assert confirmed.status.value == "confirmed"
    assert task.active_research_design_ref.research_design_id == design.research_design_id
    assert payload == original


@pytest.mark.parametrize(
    "workflow_id",
    [
        "opentrajectory_research_workflow",
        "pvd_process_optimization_workflow",
        "cs_paper_reproduction_workflow",
    ],
)
def test_workflow_constraints_survive_formal_confirm(source, workflow_id):
    bundle = bundle_for(source, workflow_id)
    payload = workflow_payload(bundle)
    service, repository, design = build(decode(payload, bundle), bundle, source)
    assert design.constraints == CONSTRAINTS
    _task, design = confirm(service, repository, design)
    assert design.constraints == CONSTRAINTS


@pytest.mark.parametrize(
    "mode", ["missing", "empty", "compatible", "canonical", "same", "conflict"]
)
@pytest.mark.parametrize("freeform", [False, True])
def test_optional_and_alias_expressions_remain_readable(source, mode, freeform):
    bundle = bundle_for(source)
    payload = (
        freeform_payload(bundle, ["当前问题的证据与结论"], [[1]])
        if freeform
        else workflow_payload(bundle)
    )
    expected = []
    for canonical, alias in [
        ("researchGoal", "goal"),
        ("primaryResearchQuestions", "questions"),
        ("scientificUnknowns", "unknowns"),
        ("riskNotes" if freeform else "risksAndTradeoffs", "risks"),
    ]:
        payload.pop(canonical, None)
        if mode == "empty":
            payload[canonical] = "" if canonical == "researchGoal" else []
        elif mode in {"compatible", "canonical", "same", "conflict"}:
            value = f"{alias} 的当前科研表达。"
            expected.append(value)
            payload[alias if mode == "compatible" else canonical] = value
            if mode in {"same", "conflict"}:
                other = value if mode == "same" else f"{alias} 的另一个未裁决表达。"
                payload[alias] = other
                expected.append(other)
    service, repository, design = build(
        decode(payload, bundle, freeform=freeform), bundle, source, freeform=freeform
    )
    text = formal_text(design)
    assert all(value in text for value in expected)
    if mode in {"missing", "empty", "conflict"}:
        assert design.goal == QUERY
    _task, confirmed = confirm(service, repository, design)
    assert all(value in formal_text(confirmed) for value in expected)


def test_conflicting_work_contexts_are_both_formally_readable(source):
    payload = real_payload()
    row = payload["responsibilityDeltas"][0]
    alternate = "另一种当前工作表达，尚未裁决其科学差异。"
    row["workItemScientificContexts"] = [alternate]
    bundle = bundle_for(source)
    service, repository, design = build(decode(payload, bundle), bundle, source)
    _task, design = confirm(service, repository, design)
    assert row["currentProjectWorkContext"] in formal_text(design)
    assert alternate in formal_text(design)


@pytest.mark.parametrize(
    "outputs,refs",
    [
        (["A", "A", "B"], [[2], [3]]),
        (["A", "A", "", "B"], [[2], [4]]),
        (["A", "A", "B"], [[2, 99], [3]]),
        (["A", "A", "", "B"], [[2, 3, 99], [4]]),
    ],
)
def test_freeform_original_numbers_and_constraints_reach_formal_design(source, outputs, refs):
    bundle = bundle_for(source)
    payload = freeform_payload(bundle, outputs, refs)
    service, repository, design = build(
        decode(payload, bundle, freeform=True), bundle, source, freeform=True
    )
    assert design.understanding.target_outputs == ["A", "B"]
    assert design.constraints == CONSTRAINTS
    targets = {row.description: row.target_id for row in design.coverage_report.targets}
    claims = {row.target_id: row for row in design.coverage_report.claims}
    by_objective = {row.objective: row.substage_instance_id for row in design.instances}
    for index, output in enumerate(["A", "B"], 1):
        assert [ref.substage_instance_id for ref in claims[targets[output]].instance_refs] == [
            by_objective[f"核查当前证据的贡献{index}。"]
        ]
    if 99 in refs[0]:
        assert any("99" in note for note in design.understanding.risk_notes)
    _task, confirmed = confirm(service, repository, design)
    assert confirmed.constraints == CONSTRAINTS


def test_multiple_contexts_refine_one_subgoal_and_preserve_associations(source):
    payload = real_payload()
    row = deepcopy(payload["responsibilityDeltas"][4])
    row["currentProjectWorkContext"] = "分别比较不同 judge 的因果机制未知。"
    payload["responsibilityDeltas"].append(row)
    bundle = bundle_for(source)
    _service, _repository, design = build(decode(payload, bundle), bundle, source)
    assert row["currentProjectWorkContext"] in formal_text(design)
    target = next(
        item
        for item in design.coverage_report.targets
        if item.description == payload["targetOutputs"][4]
    )
    claim = next(
        item for item in design.coverage_report.claims if item.target_id == target.target_id
    )
    assert len(claim.instance_refs) == 2
    instances = [
        item
        for item in design.instances
        if item.substage_definition_id == "opentrajectory:key_unknowns_candidate_explanations"
    ]
    assert len(instances) == 1
    assert row["currentProjectWorkContext"] in instances[0].objective
    assert payload["responsibilityDeltas"][4]["currentProjectWorkContext"] in instances[0].objective


def test_local_bad_responsibility_keeps_other_scientific_content(source):
    payload = real_payload()
    invalid = "局部未登记职责的科研要求。"
    payload["responsibilityDeltas"].append(
        {
            "responsibilityChoice": "unregistered",
            "currentProjectWorkContext": invalid,
            "targetOutputNumbers": [99],
        }
    )
    bundle = bundle_for(source)
    service, repository, design = build(decode(payload, bundle), bundle, source)
    assert invalid in formal_text(design)
    assert not any(invalid in item.objective for item in design.instances)
    _task, confirmed = confirm(service, repository, design)
    assert payload["goal"] == confirmed.goal


@pytest.mark.parametrize("confirmed", [False, True])
def test_multi_focus_subgoals_keep_one_identity_in_public_plan(source, confirmed):
    from app.modules.task_public_projection import _active_research_design_projection

    payload = real_payload()
    contexts = ["收集内容等价的原始研究。", "保留长度控制与人类比较信息。"]
    first = payload["responsibilityDeltas"][0]
    first.pop("currentProjectWorkContext")
    first["workItemScientificContexts"] = contexts
    bundle = bundle_for(source)
    service, repository, design = build(decode(payload, bundle), bundle, source)
    task = repository.task
    if confirmed:
        task, design = confirm(service, repository, design)
    rows = [
        item
        for item in design.instances
        if item.substage_definition_id == "opentrajectory:research_record_establishment"
    ]
    assert len(rows) == 1
    assert all(context in rows[0].objective for context in contexts)
    ids = {item.substage_instance_id for item in design.instances}
    assert all(
        dependency.upstream_instance_ref.substage_instance_id in ids
        for item in design.instances
        for dependency in item.dependencies
    )
    projection = _active_research_design_projection(task, public_artifact_ids=set())
    projected = [
        item
        for stage in projection["creationPlan"]["stages"]
        for item in stage["substages"]
        if item["substage"] == "research_record_establishment"
    ]
    assert len(projected) == 1
    assert projected[0]["instanceId"] == rows[0].substage_instance_id
    assert all(context in projected[0]["action"] for context in contexts)


def test_freeform_distinct_subgoals_of_same_type_keep_separate_identities(source):
    bundle = bundle_for(source)
    index, definition = next(
        (index, row)
        for index, row in enumerate(bundle.definition_catalog, 1)
        if row.get("instancePolicy", {}).get("mode", "repeatable") == "repeatable"
    )
    objectives = ["判断中文问答中的长度偏差。", "判断代码任务中的长度偏差。"]
    payload = {
        "researchGoal": QUERY,
        "targetOutputs": objectives,
        "workItems": [
            {
                "workTypeChoice": f"work_type_{index}",
                "scientificObjective": objective,
                "targetOutputNumbers": [number],
            }
            for number, objective in enumerate(objectives, 1)
        ],
    }
    service, repository, design = build(
        decode(payload, bundle, freeform=True), bundle, source, freeform=True
    )
    _task, confirmed = confirm(service, repository, design)
    assert len(confirmed.instances) == 2
    assert {row.objective for row in confirmed.instances} == set(objectives)
    assert {row.substage_definition_id for row in confirmed.instances} == {
        definition["definitionId"]
    }
    assert len({row.substage_instance_id for row in confirmed.instances}) == 2
