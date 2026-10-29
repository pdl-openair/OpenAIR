"""Deterministic API/Worker source identity for shared durable runtime fencing.

The identity deliberately covers Python production source under ``app`` only:
the API and durable worker must run the same backend source before they may
share Research Start claim authority. Frontend-only changes do not affect it.

There is intentionally no environment-variable override: the fence represents
the actual checked-out backend source, not a deployment label that two mismatched
processes could accidentally share.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from app.modules.repo_paths import backend_app_root


def _normalized_source_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    # Identical checked-out source must yield the same identity across Windows
    # API processes and Linux worker containers even if checkout line endings differ.
    return data.replace(b"\r\n", b"\n")


@lru_cache(maxsize=1)
def runtime_source_version() -> str:
    app_root = backend_app_root()
    paths = sorted(
        (
            path
            for path in app_root.rglob("*.py")
            if "__pycache__" not in path.parts
        ),
        key=lambda path: path.relative_to(app_root).as_posix(),
    )
    if not paths:
        raise RuntimeError("OpenAIR runtime source identity cannot find backend source files")

    digest = hashlib.sha256()
    digest.update(b"openair-runtime-source-tree-v1\0")
    for path in paths:
        relative = path.relative_to(app_root).as_posix().encode("utf-8")
        content = _normalized_source_bytes(path)
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


__all__ = ["runtime_source_version"]
