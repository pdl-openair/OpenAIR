"""Minimal adapter wrapper for source access readiness resolver."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


def _load_resolver_module():
    """Load resolver.py via absolute file path to avoid sys.path dependency."""
    resolver_path = Path(__file__).resolve().parents[1] / "resolver.py"
    spec = importlib.util.spec_from_file_location("source_access_readiness_resolver", str(resolver_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load resolver module spec: {resolver_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_RESOLVER_MODULE = _load_resolver_module()


def resolve_access_readiness(input_data: dict[str, Any]) -> dict[str, Any]:
    """Run source access readiness resolution via resolver entrypoint."""
    fn = getattr(_RESOLVER_MODULE, "resolve_candidates", None)
    if fn is None:
        raise RuntimeError("resolve_candidates not found in resolver.py")
    return fn(input_data)
