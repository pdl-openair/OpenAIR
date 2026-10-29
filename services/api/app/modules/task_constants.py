"""Task domain constants shared by store and API models (no fixture dependency)."""

from app.modules.pack_config import load_stage_substage_map

STAGE_ORDER = [
    "problem_modeling",
    "solution_design",
    "execution_validation",
    "insight_generation",
]

STAGE_SUBSTAGE_CATALOG = load_stage_substage_map()
