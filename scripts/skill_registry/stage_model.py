"""Shared research stage contract for skill registry tooling.

The internal keys stay stable. User-facing Chinese display names are fixed by
the four-stage research model and are consumed by generated pack metadata and
the migration report.
"""

from __future__ import annotations

from typing import Final

RESEARCH_STAGE_ORDER: Final[tuple[str, ...]] = (
    "problem_modeling",
    "solution_design",
    "execution_validation",
    "insight_generation",
)

RESEARCH_STAGE_DISPLAY_NAMES: Final[dict[str, str]] = {
    "problem_modeling": "问题建模",
    "solution_design": "方案设计",
    "execution_validation": "执行验证",
    "insight_generation": "结果总结",
}

RESEARCH_STAGE_DESCRIPTIONS: Final[dict[str, str]] = {
    "problem_modeling": "Define the research question, boundary, constraints, and success criteria.",
    "solution_design": "Design the research method, data strategy, analysis plan, and executable tasks.",
    "execution_validation": "Run execution or validation, extract evidence, analyze results, and check quality.",
    "insight_generation": "Summarize results, update hypotheses, and assemble traceable research outputs.",
}

RESEARCH_STAGE_META: Final[dict[str, dict[str, str]]] = {
    stage_key: {
        "label": RESEARCH_STAGE_DISPLAY_NAMES[stage_key],
        "description": RESEARCH_STAGE_DESCRIPTIONS[stage_key],
    }
    for stage_key in RESEARCH_STAGE_ORDER
}


def is_research_stage(stage_key: object) -> bool:
    return isinstance(stage_key, str) and stage_key in RESEARCH_STAGE_DISPLAY_NAMES
