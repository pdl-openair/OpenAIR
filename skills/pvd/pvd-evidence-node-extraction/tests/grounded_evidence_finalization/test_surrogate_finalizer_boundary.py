from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT.parents[2]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _payload(title: str) -> dict:
    content = "Power was set to 500 W while nitrogen flow was 20 sccm for the reported film."
    return {
        "task_id": "task-1",
        "paper_packets": [{
            "candidate_id": "paper-1",
            "title": title,
            "arxiv_id": "",
            "sections": [{
                "section_name": "Methods",
                "content": content,
                "content_hash": "a" * 64,
            }],
            "source_anchors": [{
                "anchor_id": "anchor-1",
                "candidate_id": "paper-1",
                "arxiv_id": "",
                "section_name": "Methods",
                "content_hash": "a" * 64,
            }],
        }],
        "evidence_schema": {
            "contract": "pvd-evidence-schema/v1",
            "evidence_targets": [{
                "target_id": "target_001",
                "requiredness": "required",
                "covered_parameter_keys": ["power"],
            }],
        },
    }


def _draft() -> dict:
    content = "Power was set to 500 W while nitrogen flow was 20 sccm for the reported film."
    return {"evidence_items": [{
        "source_anchor_id": "anchor-1",
        "evidence_type": "experimental_condition",
        "assertion_mode": "direct_observation",
        "claim": "The reported sputtering power was 500 W.",
        "evidence_quote": content,
        "conditions": [{"name": "power", "value_text": "500", "unit": "W"}],
        "metric": None,
        "mechanism": None,
        "comparison": None,
        "qualifiers": [],
        "confidence": 0.95,
        "matched_target_ids": ["target_001"],
        "target_match_status": "matched",
    }]}


def test_valid_unicode_fingerprint_keeps_previous_utf8_digest_contract() -> None:
    finalizer = _load(ROOT / "scripts/finalize.py", "t04_surrogate_digest_finalizer")
    source = _payload("\u4e2d\u6587 AlScN evidence")
    expected = finalizer.sha256(finalizer._canonical_json(source).encode("utf-8")).hexdigest()

    result = finalizer.finalize(source, _draft())

    assert result.status == "succeeded"
    assert result.output is not None
    assert result.output["audit"]["input_fingerprint"] == expected


def test_lone_surrogate_is_hashed_without_replacement_or_identity_collapse() -> None:
    finalizer = _load(ROOT / "scripts/finalize.py", "t04_surrogate_identity_finalizer")
    lone = _payload("source-before\udc92-source-after")
    replacement = _payload("source-before\ufffd-source-after")
    before = lone["paper_packets"][0]["title"]

    first = finalizer.finalize(lone, _draft())
    second = finalizer.finalize(replacement, _draft())

    assert first.status == "succeeded" and second.status == "succeeded"
    assert first.output is not None and second.output is not None
    assert lone["paper_packets"][0]["title"] == before
    assert any(ord(ch) == 0xDC92 for ch in lone["paper_packets"][0]["title"])
    assert first.output["audit"]["input_fingerprint"] != second.output["audit"]["input_fingerprint"]


def test_registered_finalizer_subprocess_accepts_real_pvd_payload_with_lone_surrogate() -> None:
    runtime = _load(
        REPO / "services/api/app/execution/skill_finalization_runtime.py",
        "t04_surrogate_skill_finalization_runtime",
    )
    source = _payload("source-before\udc92-source-after")

    result = runtime._execute_finalizer_subprocess(
        skill_root=ROOT,
        contract={"entrypoint": {"module": "scripts.finalize", "callable": "finalize"}},
        input_payload=source,
        draft_payload=_draft(),
    )

    assert result.status == "succeeded"
    assert result.output is not None
    assert len(result.output["audit"]["input_fingerprint"]) == 64
    assert len(result.output["evidence_items"]) == 1
