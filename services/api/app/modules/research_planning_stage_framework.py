"""Scientific planning responsibilities; no execution capability inventory."""

from __future__ import annotations


def research_stage_framework() -> list[dict[str, str]]:
    return [
        {"stage": "problem_modeling", "label": "问题建模", "responsibility": "明确主研究问题、已知条件、关键未知和研究边界。"},
        {"stage": "solution_design", "label": "方案设计", "responsibility": "设计回答主问题的研究方法、比较或验证方案及判断标准。"},
        {"stage": "execution_validation", "label": "执行验证", "responsibility": "获取并核验回答问题所需的真实证据、分析或实验结果。"},
        {"stage": "insight_generation", "label": "洞察生成", "responsibility": "综合证据回答主问题，说明结论、限制、关键未知和下一步建议。"},
    ]
