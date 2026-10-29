"""Python adapter package for the materials-magnet-design-mvp skill.

Entry point: `run_magnet_mvp(input_payload)` -> dict.

See magnet_adapter.py for the full pipeline (Phase 1 init → Phase 2 loop →
Phase 3 final report). The adapter runs as a subprocess spawned by
services/api/app/execution/python_adapter_runner.py and must NEVER raise —
it always returns a populated dict (state=succeeded|partial_failure) so the
durable worker can persist an artifact.
"""

from .magnet_adapter import run_magnet_mvp  # noqa: F401
