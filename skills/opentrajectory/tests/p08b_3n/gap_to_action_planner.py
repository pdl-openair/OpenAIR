"""Compatibility shim for the historical P08B-3N import path."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from skills.opentrajectory.gap_to_action.planner import (  # noqa: E402,F401
    ACTION_BY_GAP_TYPE,
    BFE_FIXED_FIELDS,
    CLOSING_STATUSES,
    FORBIDDEN_OUTPUT_TERMS,
    REQUIRED_ACTION_FIELDS,
    REQUIRED_GAP_TYPES,
    TRACE_ACTION_FIELDS,
    load_suite,
    main,
    plan_gap_action,
    plan_gap_to_actions,
    run_planner,
    validate_planner_output,
)


if __name__ == "__main__":
    raise SystemExit(main())
