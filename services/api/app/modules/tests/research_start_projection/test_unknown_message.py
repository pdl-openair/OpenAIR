from app.modules.research_start_projection import _session_public


def test_unknown_projects_one_safe_message_even_for_a_stored_old_retry_hint():
    public = _session_public(
        {
            "session_id": "isolated",
            "state": "external_result_unknown",
            "request_state": "unknown",
            "result_payload": None,
            "user_message": "你可以重新尝试。",
            "thinking_transcript": [
                {"key": "research_planner:1", "state": "formed", "text": "已核对研究条件。"},
            ],
        },
        attempt_phase="research_planner",
    )
    message = public["userMessage"]
    assert message == public["planningProgress"]["message"]
    assert "不能安全" in message
    assert public["automaticRetryAllowed"] is False
    assert public["planningProgress"]["thoughts"][0]["text"] == "已核对研究条件。"


def test_non_unknown_message_is_not_rewritten():
    public = _session_public(
        {
            "session_id": "isolated",
            "state": "failed",
            "request_state": "failed",
            "user_message": "已经明确失败。",
            "result_payload": None,
        }
    )
    assert public["userMessage"] == "已经明确失败。"


def test_persisted_unknown_message_reuses_public_outlet_constant():
    from app.modules.research_start_projection import UNKNOWN_USER_MESSAGE
    from app.modules.research_start_repository import UNKNOWN_USER_MESSAGE as stored_message

    assert stored_message == UNKNOWN_USER_MESSAGE
