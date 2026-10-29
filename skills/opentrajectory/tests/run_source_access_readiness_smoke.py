#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_module(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    root = repo_root()
    skill_dir = root / "skills/opentrajectory/source-access-readiness-resolver-skill"

    adapter_mod = load_module(
        "source_access_readiness_adapter",
        skill_dir / "adapter/access_readiness_adapter.py",
    )

    request = json.loads((skill_dir / "examples/request.json").read_text(encoding="utf-8"))
    response = adapter_mod.resolve_access_readiness(request)

    required = ["task_id", "skill_id", "candidates", "summary", "warnings", "errors"]
    missing = [k for k in required if k not in response]
    if missing:
        raise RuntimeError(f"missing required keys: {missing}")

    if not isinstance(response["candidates"], list):
        raise RuntimeError("candidates should be a list")

    print("[SMOKE PASS] source-access-readiness adapter wrapper")
    print(json.dumps({
        "candidate_count": len(response["candidates"]),
        "summary": response["summary"],
        "warning_count": len(response.get("warnings", [])),
        "error_count": len(response.get("errors", [])),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
