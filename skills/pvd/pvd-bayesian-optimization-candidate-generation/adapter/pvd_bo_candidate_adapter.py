from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


SKILL_ROOT = Path(__file__).resolve().parents[1]
SRC = SKILL_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pvd_bo.service import run_candidate_generation


def run_pvd_bayesian_candidate_generation(payload: Mapping[str, Any]) -> dict[str, Any]:
    """OpenAIR python_adapter entrypoint. It performs no file writes or external actions."""
    return run_candidate_generation(payload)
