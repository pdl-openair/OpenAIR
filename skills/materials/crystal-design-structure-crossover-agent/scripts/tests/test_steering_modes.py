# scripts/tests/test_steering_modes.py
import pytest

from steering_modes import SteeringMode, SteerAction, parse_steer_action


def test_mode_enum_has_four_members() -> None:
    assert {m.value for m in SteeringMode} == {
        "static_rules",
        "agent_no_mem",
        "agent_with_mem",
        "oracle",
    }


def test_parse_steer_action_valid() -> None:
    s = parse_steer_action('{"action": "ban", "args": {"element": "Au"}, "reason": "collapse"}')
    assert s.action == "ban"
    assert s.args == {"element": "Au"}
    assert s.reason == "collapse"


def test_parse_steer_action_missing_field() -> None:
    with pytest.raises(ValueError, match="missing"):
        parse_steer_action('{"action": "ban"}')


def test_parse_steer_action_invalid_json() -> None:
    with pytest.raises(ValueError, match="invalid json"):
        parse_steer_action("not json at all")


from steering_modes import static_rules_decide


def _snap(**kw) -> dict:
    base = {
        "generation": 2,
        "pop_size": 16,
        "pareto_size": 4,
        "stable_count": 10,
        "rmsd_median": 0.001,
        "constraint_violation_rate": 0.1,
        "single_element_individuals": [],
        "pareto_size_unchanged_gens": 0,
    }
    base.update(kw)
    return base


def test_static_rules_bans_single_element() -> None:
    snap = _snap(single_element_individuals=[{"element": "Au", "ehull": 0.001}])
    action = static_rules_decide(snap, family="x", constraints={})
    assert action.action == "ban"
    assert action.args["element"] == "Au"


def test_static_rules_lowers_noise_on_saturation() -> None:
    snap = _snap(stable_count=16, pareto_size_unchanged_gens=3)
    action = static_rules_decide(snap, family="x", constraints={})
    assert action.action == "noise"
    assert action.args["level"] == 0.2


def test_static_rules_raises_noise_on_low_rmsd() -> None:
    snap = _snap(rmsd_median=0.0001, generation=4, stable_count=8)
    action = static_rules_decide(snap, family="x", constraints={})
    assert action.action == "noise"
    assert action.args["level"] == 0.5


def test_static_rules_noop_when_healthy() -> None:
    action = static_rules_decide(_snap(), family="x", constraints={})
    assert action.action == "noop"


def test_static_rules_tightens_constraint_on_high_violation() -> None:
    snap = _snap(constraint_violation_rate=0.7, most_violated_constraint="bandgap")
    action = static_rules_decide(snap, family="x", constraints={})
    assert action.action == "constraint"
    assert action.args["key"] == "bandgap"
    assert action.args["weight_delta"] == 5


from steering_modes import oracle_decide


def test_oracle_picks_best_delta_sun() -> None:
    snap = {"generation": 2}
    a_ban = SteerAction(action="ban", args={"element": "Au"}, reason="r")
    a_noise = SteerAction(action="noise", args={"level": 0.5}, reason="r")
    a_noop = SteerAction(action="noop", args={}, reason="r")

    outcomes = {
        '{"action":"ban","args":{"element":"Au"}}': {"delta_sun": 0.20},
        '{"action":"noise","args":{"level":0.5}}': {"delta_sun": 0.05},
        '{"action":"noop","args":{}}': {"delta_sun": -0.10},
    }
    best = oracle_decide(snap, [a_ban, a_noise, a_noop], outcomes)
    assert best.action == "ban"
    assert best.args["element"] == "Au"


def test_oracle_no_actions_returns_noop() -> None:
    best = oracle_decide({}, [], {})
    assert best.action == "noop"


def test_oracle_all_negative_picks_least_bad() -> None:
    a1 = SteerAction(action="noise", args={"level": 0.5}, reason="r")
    a2 = SteerAction(action="noise", args={"level": 0.3}, reason="r")
    outcomes = {
        '{"action":"noise","args":{"level":0.5}}': {"delta_sun": -0.05},
        '{"action":"noise","args":{"level":0.3}}': {"delta_sun": -0.20},
    }
    best = oracle_decide({}, [a1, a2], outcomes)
    assert best.args["level"] == 0.5


# ================= steering_parity2 实验二:bandgap_rule_decide =================

from steering_modes import bandgap_rule_decide  # noqa: E402

_BG_CONSTRAINTS = {"bandgap": {"min": 2.7, "max": 3.3, "weight": 5.0}}


def _bg_snap(violation_rate=0.0, inband_rate=0.0, stall=0, **kw) -> dict:
    base = {
        "generation": 3,
        "pareto_size_unchanged_gens": stall,
        "constraint_key_stats": {
            "bandgap": {
                "n_known": 20, "violation_rate": violation_rate,
                "inband_rate": inband_rate, "median": 1.5,
            },
        },
    }
    base.update(kw)
    return base


def test_bandgap_rule_softens_on_high_violation() -> None:
    a = bandgap_rule_decide(_bg_snap(violation_rate=0.6), _BG_CONSTRAINTS)
    assert a.action == "constraint"
    assert a.args["weight"] == 0.0  # 5 - 5,钳到下界
    assert a.args["min"] == 2.7 and a.args["max"] == 3.3


def test_bandgap_rule_tightens_on_inband_and_stall() -> None:
    a = bandgap_rule_decide(_bg_snap(inband_rate=0.9, stall=2), _BG_CONSTRAINTS)
    assert a.action == "constraint"
    assert a.args["weight"] == 10.0  # 5 + 5,钳到上界


def test_bandgap_rule_no_tighten_when_front_moving() -> None:
    a = bandgap_rule_decide(_bg_snap(inband_rate=0.9, stall=1), _BG_CONSTRAINTS)
    assert a.action == "noop"


def test_bandgap_rule_no_tighten_below_inband_trigger() -> None:
    a = bandgap_rule_decide(_bg_snap(inband_rate=0.8, stall=5), _BG_CONSTRAINTS)
    assert a.action == "noop"  # 0.8 不严格大于 0.8


def test_bandgap_rule_soften_dominates_tighten() -> None:
    a = bandgap_rule_decide(
        _bg_snap(violation_rate=0.7, inband_rate=0.9, stall=5), _BG_CONSTRAINTS)
    assert a.action == "constraint"
    assert a.args["weight"] == 0.0


def test_bandgap_rule_noop_when_healthy() -> None:
    a = bandgap_rule_decide(_bg_snap(violation_rate=0.2, inband_rate=0.5), _BG_CONSTRAINTS)
    assert a.action == "noop"


def test_bandgap_rule_noop_when_stats_missing() -> None:
    a = bandgap_rule_decide({"generation": 1}, _BG_CONSTRAINTS)
    assert a.action == "noop"
    assert "missing" in a.reason


def test_bandgap_rule_clamps_at_bounds() -> None:
    cons = {"bandgap": {"min": 2.7, "max": 3.3, "weight": 8.0}}
    a = bandgap_rule_decide(_bg_snap(violation_rate=0.9), cons)
    assert a.args["weight"] == 3.0
    a2 = bandgap_rule_decide(_bg_snap(inband_rate=0.95, stall=3), cons)
    assert a2.args["weight"] == 10.0  # 8+5 -> 10 钳上界
