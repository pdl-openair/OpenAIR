from app.modules.research_start_recovery_policy import PLANNER_HTTP_RECOVERY_POLICY


def test_planner_http_recovery_policy_is_bounded_and_status_based() -> None:
    assert PLANNER_HTTP_RECOVERY_POLICY.max_automatic_recoveries == 1
    for status in (408, 429, 500, 503, 599):
        assert PLANNER_HTTP_RECOVERY_POLICY.is_eligible_status(status) is True
    for status in (None, 400, 401, 404, 422, 600):
        assert PLANNER_HTTP_RECOVERY_POLICY.is_eligible_status(status) is False
