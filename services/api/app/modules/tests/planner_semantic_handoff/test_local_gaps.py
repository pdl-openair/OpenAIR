"""Local invalid references preserve legal work and visible uncertainty."""

from .support import build, bundle_for, confirm, decode, formal_text, real_payload


def test_workflow_bad_number_does_not_erase_valid_combination(source):
    bundle = bundle_for(source)
    payload = real_payload()
    payload["responsibilityDeltas"][4]["targetOutputNumbers"].append(99)
    service, repository, design = build(decode(payload, bundle), bundle, source)
    assert "99" in formal_text(design)
    target = next(
        row
        for row in design.coverage_report.targets
        if row.description == payload["targetOutputs"][4]
    )
    claim = next(row for row in design.coverage_report.claims if row.target_id == target.target_id)
    assert len(claim.instance_refs) == 2
    _task, confirmed = confirm(service, repository, design)
    assert payload["goal"] == confirmed.goal


def test_local_malformed_optional_expression_is_readable_with_valid_content(source):
    bundle = bundle_for(source)
    payload = real_payload()
    payload["questions"].append({"unmapped": "尚未映射的科研问题原文。"})
    service, repository, design = build(decode(payload, bundle), bundle, source)
    _task, design = confirm(service, repository, design)
    assert "尚未映射的科研问题原文。" in formal_text(design)
    assert all(value in formal_text(design) for value in payload["questions"][:-1])
