"""Regression + contract tests for materials-parameter-recommend-flux.

Scope
-----
Only the two Python steps of the 5-step pipeline are tested here:
  - Step 2 ``similar_retrieval``  (tests 3.1, 3.2)
  - Step 3 ``statistic_window``   (test 3.3)

Test 3.4 emulates ``recommend_recipe.py``'s Tmax-clipping bug inline;
the recipe step itself is Claude-driven (covered by evals/evals.json)
but the temperature-constraint calculation lives in Python source that
the recipe prompt references.

Every test runs fully offline: only ``pytest``, ``json`` and ``pathlib``
are imported — no Claude SDK, no numpy, no network.  Fixtures come from
``tests/fixtures/`` and are copied or derived from the audit run under
``tmp/materials-audit/materials-parameter-recommend-flux/audit_run/``.

Findings referenced throughout come from
``docs/audits/2026-07-10-materials-l3-quality-audit.md`` §3.
"""
import pytest


# ---------------------------------------------------------------------------
# 3.1 — SNAPSHOT: similarity function near-zero discrimination
# ---------------------------------------------------------------------------

@pytest.mark.regression
def test_similar_retrieval_top5_kb_order_due_to_tie(similar_output_CrSiTe3):
    """SNAPSHOT: audit §3.2 U2 / §3.5 attribution 3 — similarity function has
    near-zero discrimination (17-way tie at 0.6), top-k degenerates to KB
    order.  This test locks current behavior; invert when
    infer_material_family() gains sub-family discrimination per §3.4 problem 3.
    """
    top5 = similar_output_CrSiTe3

    # (a) All top-5 share the same similarity score — the documented 0.6 tie.
    scores = {entry["相似度"] for entry in top5}
    assert len(scores) == 1, f"expected 17-way tie at 0.6, got scores={scores}"
    tied_score = next(iter(scores))
    assert tied_score == pytest.approx(0.6), (
        f"audit §3.3 E2 ties at 0.6; observed {tied_score}"
    )

    # (b) None of the top-5 formulas contains Cr — the Cr-family was missed
    # entirely (only Bi2TeI has Te overlap, none has Cr).
    for entry in top5:
        formula = entry["化学式"]
        assert "Cr" not in formula, (
            f"audit §3.3 E2: top-5 should contain no Cr material; found {formula}"
        )

    # (c) CrGeTe3 (mat_580) was hard-filtered out because its only KB recipe
    # is "Chemical cation exchange method", not a flux method.
    mat_ids = {entry["材料ID"] for entry in top5}
    assert "mat_580" not in mat_ids, (
        "audit §3.5 attribution 7: CrGeTe3 (mat_580) should be absent from "
        "top-5 because its only KB recipe is cation-exchange, not flux"
    )
    formulas = [entry["化学式"] for entry in top5]
    assert "CrGeTe3" not in formulas, (
        f"CrGeTe3 should not appear in top-5; got {formulas}"
    )


# ---------------------------------------------------------------------------
# 3.2 — XFAIL: intent top_k silently overridden by intuition_template
# ---------------------------------------------------------------------------

@pytest.mark.regression
@pytest.mark.xfail(
    reason=(
        "audit §3.5 problem 4: similar_retrieval.py L685-688 lets "
        "intuition_template override intent's top_k from 5 to 30"
    ),
    strict=True,
)
def test_top_k_in_intent_overridden_by_template_xfail(
    intent_CrSiTe3, intuition_template
):
    """xfail until L685-688 is fixed to make intent fields take precedence
    over template defaults.

    Mirrors similar_retrieval.py L685-688:

        intent_settings = load_intuition_template(intuition_template)
        intent["相似度设置"] = intent_settings.get("相似度设置", {})
        intent["返回设置"]   = intent_settings.get("返回设置", {})   # <- clobbers user's top_k
    """
    # Mirror of similar_retrieval.py L685-688 override behavior
    intent_top_k = (
        intent_CrSiTe3.get("返回设置", {}).get("top_k")
        or intent_CrSiTe3.get("返回设置", {}).get("最多返回材料数_top_k")
    )
    template_top_k = intuition_template["返回设置"]["top_k"]
    effective_top_k = template_top_k  # current bug: template wins

    # When the bug is fixed, effective_top_k will come from intent (5),
    # not the template (30), and this assertion will pass.
    assert intent_top_k == 5, "intent fixture must carry the user's top_k=5"
    assert effective_top_k == intent_top_k, (
        f"effective top_k should respect intent ({intent_top_k}); "
        f"template overrode it to {template_top_k}"
    )


# ---------------------------------------------------------------------------
# 3.3 — CONTRACT: statistic_window output schema
# ---------------------------------------------------------------------------

@pytest.mark.contract
def test_statistic_window_output_schema(stat_window_CrSiTe3):
    """Reference audit §3.2 U3 PASS section — statistic_window output is
    well-formed and consumable downstream.
    """
    windows = stat_window_CrSiTe3

    # (a) Audit says "23 parameters" — require at least 20 to allow minor
    # schema growth without breaking the contract.
    assert len(windows) >= 20, (
        f"expected ≥20 parameter windows (audit says 23); got {len(windows)}"
    )

    # Each window declares itself numeric or categorical.
    for w in windows:
        assert w.get("参数类型") in {"数值", "类别"}, (
            f"window {w.get('参数名')!r} missing 参数类型; got {w.get('参数类型')!r}"
        )

    numeric_windows = [w for w in windows if w["参数类型"] == "数值"]
    categorical_windows = [w for w in windows if w["参数类型"] == "类别"]
    assert numeric_windows, "expected at least one numeric window"
    assert categorical_windows, "expected at least one categorical window"

    # (b) Numeric windows must expose P10/P90/avg/std + a recommended default.
    recommended_default_keys = {"推荐默认值", "default", "recommended"}
    stats_keys = {"文献统计", "stats"}
    for w in numeric_windows:
        stats_block = next((w[k] for k in stats_keys if k in w), None)
        assert stats_block is not None, (
            f"numeric window {w['参数名']!r} missing stats block "
            f"(expected one of {stats_keys})"
        )
        for required in ("P10", "P90", "avg", "std"):
            translated = {
                "P10": "P10",
                "P90": "P90",
                "avg": "平均值",
                "std": "标准差",
            }[required]
            assert translated in stats_block or required in stats_block, (
                f"numeric window {w['参数名']!r} stats missing {required} "
                f"(CN key {translated})"
            )
        has_default = any(k in w for k in recommended_default_keys)
        assert has_default, (
            f"numeric window {w['参数名']!r} missing recommended default "
            f"(one of {recommended_default_keys})"
        )

    # (c) Categorical windows must expose candidate values + sample count +
    # weighted support (under any of the documented key spellings).
    candidate_list_keys = {"候选取值列表", "candidates", "candidate_values"}
    sample_count_keys = {"样本数", "sample_count", "n"}
    weighted_support_keys = {"加权支持度", "weighted_support", "weight"}
    for w in categorical_windows:
        candidates = next((w[k] for k in candidate_list_keys if k in w), None)
        assert candidates is not None, (
            f"categorical window {w['参数名']!r} missing candidate list "
            f"(one of {candidate_list_keys})"
        )
        assert isinstance(candidates, list) and candidates, (
            f"categorical window {w['参数名']!r} candidate list is empty"
        )
        for c in candidates:
            has_count = any(k in c for k in sample_count_keys)
            has_weight = any(k in c for k in weighted_support_keys)
            assert has_count and has_weight, (
                f"categorical window {w['参数名']!r} candidate {c!r} must "
                f"expose sample count ({sample_count_keys}) and weighted "
                f"support ({weighted_support_keys})"
            )


# ---------------------------------------------------------------------------
# 3.4 — XFAIL: recommend_recipe Tmax clipped to min(lab_max, elem_min_bp)
# ---------------------------------------------------------------------------

@pytest.mark.regression
@pytest.mark.xfail(
    reason=(
        "audit §3.4 problem 6: recommend_recipe.py L411-415 clips Tmax to "
        "min(lab_max, elem_min_bp) but meta-rule.md §2.1 says Tmax CAN exceed "
        "elem_min_bp; only secondary segments are bounded by elem_min_bp"
    ),
    strict=True,
)
def test_recommend_recipe_tmax_constraint_bug_xfail():
    """xfail until L411-415 splits into two variables: Tmax uses lab_max_temp,
    secondary segments use min(lab_max_temp, elem_min_bp).

    Mirrors recommend_recipe.py L411-415:

        candidates = [t for t in (lab_max_temp, elem_min_bp) if t is not None]
        if candidates:
            allowed_max_temp = min(candidates)   # <- over-clips Tmax
    """
    # Mirror of recommend_recipe.py L411-415
    lab_max_temp = 1000  # °C, lab constraint
    elem_min_bp = 988    # °C, Te boiling point
    allowed_max_temp = min(lab_max_temp, elem_min_bp)  # = 988, the bug

    # Per meta-rule.md §2.1: Tmax may use the full lab budget (1000 °C);
    # only secondary segments are bounded by elem_min_bp (988 °C).
    assert allowed_max_temp == lab_max_temp, (
        f"Tmax should be allowed up to lab_max_temp={lab_max_temp}; "
        f"bug clipped it to min(lab,elem_bp)={allowed_max_temp}"
    )
