from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

SKILL = Path(__file__).resolve().parents[1]


def _schema():
    return json.loads((SKILL / "input.schema.json").read_text(encoding="utf-8"))


def test_task_id_trim_nonempty_is_formal_schema_constraint():
    schema = _schema()
    validator = Draft202012Validator(schema)
    base = {
        "search_matrix": [],
        "expanded_query_variants": [],
        "user_query": "research question",
        "task_id": "task-1",
    }
    assert not list(validator.iter_errors(base))
    bad = dict(base)
    bad["task_id"] = " \t "
    assert list(validator.iter_errors(bad))
