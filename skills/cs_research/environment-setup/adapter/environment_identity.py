"""Read actual environment facts and create a stable identity record."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any

from .environment_common import RECORD_FILENAME, VERIFY_TIMEOUT_SECONDS, failure, run_command

_ENVIRONMENT_FACTS_SCRIPT = r'''import importlib.metadata as md
import json
import platform
import sys
import sysconfig
packages = []
for dist in md.distributions():
    name = dist.metadata.get("Name")
    version = dist.version
    if name and version:
        packages.append({"name": name, "version": version})
packages.sort(key=lambda item: (item["name"].lower(), item["name"], item["version"]))
print(json.dumps({
    "python": {
        "implementation": platform.python_implementation(),
        "version": platform.python_version(),
        "major": sys.version_info.major,
        "minor": sys.version_info.minor,
        "micro": sys.version_info.micro,
    },
    "platform": {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python_platform": sysconfig.get_platform(),
    },
    "packages": packages,
}, sort_keys=True))'''


def read_environment_facts(
    python: Path, *, workspace: Path
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        result = run_command(
            [str(python), "-c", _ENVIRONMENT_FACTS_SCRIPT],
            cwd=workspace,
            timeout_seconds=VERIFY_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, failure("environment_facts_unavailable", "environment_identity")
    if result.returncode != 0:
        return None, failure(
            "environment_facts_unavailable", "environment_identity", exit_code=result.returncode
        )
    try:
        facts = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError):
        return None, failure("environment_facts_invalid", "environment_identity")
    if not isinstance(facts, dict) or not isinstance(facts.get("python"), dict):
        return None, failure("environment_facts_invalid", "environment_identity")
    if not isinstance(facts.get("platform"), dict) or not isinstance(facts.get("packages"), list):
        return None, failure("environment_facts_invalid", "environment_identity")
    return facts, None


def identity_with_fingerprint(facts: dict[str, Any]) -> dict[str, Any]:
    canonical = json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {**facts, "fingerprint_sha256": fingerprint}


def pip_version(facts: dict[str, Any]) -> str:
    for package in facts["packages"]:
        if str(package.get("name", "")).lower() == "pip":
            return str(package.get("version", "unknown"))
    return "unknown"


def write_record_atomically(target: Path, record: dict[str, Any]) -> bool:
    record_path = target / RECORD_FILENAME
    temporary = target / f".{RECORD_FILENAME}.tmp"
    try:
        temporary.write_text(
            json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, record_path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True
