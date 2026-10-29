from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

from jsonschema import Draft7Validator

SKILL = Path(__file__).resolve().parents[1]


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def finalizer_module():
    path = SKILL / "scripts" / "finalize.py"
    spec = importlib.util.spec_from_file_location("research_record_finalize_handoff", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    request = load(SKILL / "examples" / "request.json")
    draft = load(SKILL / "examples" / "model_semantic_output.json")
    result = finalizer_module().finalize(request, draft)
    assert result.output is not None

    final_schema = load(SKILL / "output.schema.json")
    assert not list(Draft7Validator(final_schema).iter_errors(result.output))
    records = result.output["research_records"]
    assert records

    # Frozen direct-handoff minimum: object -> conditions -> result -> source.
    for record in records:
        assert record["research_object"]
        assert record["condition_status"] in {"reported", "partially_unknown", "not_applicable"}
        expected_status = (
            "not_applicable"
            if not record["conditions"]
            else "partially_unknown"
            if any(condition["status"] == "unknown" for condition in record["conditions"])
            else "reported"
        )
        assert record["condition_status"] == expected_status
        assert record["result"]["statement"]
        assert record["source"]["candidate_id"]
        assert record["source"]["anchor_id"]
        assert record["source"]["source_locator"]
        assert record["source"]["content_hash"]
        assert record["source_quote"]

    serialized = json.dumps(result.output, ensure_ascii=False)
    for forbidden in ("evidence_items", "evidence_id", "finding_id", "conclusion_id", "hypothesis_id", "recommendation_id"):
        assert forbidden not in serialized

    print("FINAL_OUTPUT_SCHEMA=PASS")
    print("FROZEN_RESEARCH_RECORD_MINIMUM=PASS")
    print("DOWNSTREAM_EVIDENCE_JUDGMENT_IDENTITY=ABSENT")
    print("SOURCE_LOCAL_HANDOFF=PASS")


if __name__ == "__main__":
    main()
