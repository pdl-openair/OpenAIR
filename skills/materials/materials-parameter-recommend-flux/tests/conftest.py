"""Pytest fixtures for materials-parameter-recommend-flux tests.

Offline-by-design: every fixture is loaded from tests/fixtures/.
No Claude SDK, no numpy import, no network.

The four Python-step tests here (3.1 / 3.2 / 3.3 / 3.4) cover only Step 2
(similar_retrieval) and Step 3 (statistic_window) of the 5-step pipeline —
Claude-driven steps 1/4/5 are exercised via evals/evals.json.

All fixtures are copied or derived from the audit run recorded under
tmp/materials-audit/materials-parameter-recommend-flux/audit_run/ and
pinned to the §3 audit findings in
docs/audits/2026-07-10-materials-l3-quality-audit.md.
"""
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _load_json(name: str):
    with open(FIXTURES / name, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_jsonl(name: str) -> list:
    """Load a .jsonl file into a list of parsed objects (one per line)."""
    items = []
    with open(FIXTURES / name, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            items.append(json.loads(line))
    return items


@pytest.fixture
def intent_CrSiTe3():
    """Step-1 Claude intent output for the CrSiTe3 flux task.

    Records user-requested top_k=5 under 返回设置.最多返回材料数_top_k.
    Used by audit §3.3 E1 / §3.4 problem 4 (top_k override).
    Copy of tmp/.../audit_run/intent.json.
    """
    return _load_json("intent_CrSiTe3.json")


@pytest.fixture
def stat_window_CrSiTe3():
    """Step-3 statistic_window output as a JSONL list (one window per line).

    Each line is one parameter window with 参数名 + the window definition.
    23 windows total (numeric windows carry P10/P90/avg/std/推荐默认值;
    categorical windows carry 候选取值列表 + 样本数 + 加权支持度).
    Derived from tmp/.../audit_run/stat.json.
    """
    return _load_jsonl("window_output_CrSiTe3.jsonl")


@pytest.fixture
def similar_output_CrSiTe3():
    """Step-2 similar_retrieval output as a JSONL list (one material per line).

    Contains the 5 returned top-k materials — all tied at 相似度=0.6
    (audit §3.3 E2).  None of the 5 contains element Cr; CrGeTe3 (mat_580)
    is absent because its only KB recipe is cation-exchange, not flux
    (audit §3.5 attribution 7).
    Derived from tmp/.../audit_run/similar.json.
    """
    return _load_jsonl("similar_output_CrSiTe3.jsonl")


@pytest.fixture
def intuition_template():
    """Synthetic intuition_template that overrides intent's top_k from 5 → 30.

    Mirrors audit §3.3 E4: the template's 返回设置.top_k silently wins over
    intent's 返回设置.最多返回材料数_top_k due to similar_retrieval.py L685-688.
    """
    return _load_jsonl("intuition_template.jsonl")[0]


@pytest.fixture
def mini_kb():
    """Curated 8-line KB slice covering the 7 mat_ids referenced in audit §3.3 E2:

    mat_039 KBiSiS4, mat_141 KSnPS4, mat_151 CsAgGa2Se4, mat_347 Ce(O,F)SbS2,
    mat_377 Bi2TeI, mat_580 CrGeTe3, mat_630/mat_631 CrSiTe3.
    Lets tests reason about tie-group membership and hard-filtered recipes
    without loading the full ~1.5 MB knowledge_base_processed.jsonl.
    """
    return _load_jsonl("mini_kb.jsonl")
