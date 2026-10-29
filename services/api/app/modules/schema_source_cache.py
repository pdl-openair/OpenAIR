"""Reuse Schema syntax admission only while the full document is unchanged."""

import json
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from jsonschema.validators import validator_for


@lru_cache(maxsize=128)
def _check_schema(serialized: str) -> None:
    schema = json.loads(serialized)
    validator_for(schema).check_schema(schema)


def validate_schema_document(schema: Mapping[str, Any]) -> None:
    _check_schema(json.dumps(schema, sort_keys=True, separators=(",", ":")))
