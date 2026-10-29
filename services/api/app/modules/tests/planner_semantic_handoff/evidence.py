"""Optional raw evidence collector; contains no product verdict logic."""

import gzip
import hashlib
import json
import os
from pathlib import Path


def record(stage, value):
    destination = os.environ.get("PV2_EVIDENCE_PATH")
    if not destination:
        return
    encoded = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    row = {
        "case": os.environ.get("PYTEST_CURRENT_TEST", "independent-audit"),
        "stage": stage,
        "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "value": value,
    }
    with gzip.open(Path(destination), "at", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
