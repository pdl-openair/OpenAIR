"""Actionable failure copy: known reason codes must map to user-fix guidance."""

from types import SimpleNamespace

from app.modules.research_blocker_semantics import blocker_user_explanation
from app.modules.workbench_user_actions import _reason_code_copy


def test_quota_copy_points_to_settings_not_vague_system_issue():
    copy = _reason_code_copy("quota_exhausted")
    assert "额度" in copy
    assert "设置" in copy


def test_provider_configuration_copy_mentions_admin_action():
    copy = _reason_code_copy("provider_configuration_unavailable")
    assert "模型" in copy
    assert "管理员" in copy


def test_blocker_explanation_covers_provider_configuration_unavailable():
    fact = SimpleNamespace(kind="runtime", reason_code="provider_configuration_unavailable")
    explanation = blocker_user_explanation(fact)
    assert explanation["reasonCode"] == "provider_configuration_unavailable"
    assert "模型网关配置不可用" in explanation["reason"]
    assert "测试连接" in explanation["reason"]


def test_blocker_explanation_covers_skill_runtime_unavailable():
    fact = SimpleNamespace(kind="runtime", reason_code="skill_runtime_unavailable")
    explanation = blocker_user_explanation(fact)
    assert explanation["reasonCode"] == "skill_runtime_unavailable"
    assert "运行时" in explanation["reason"]
    assert "原进度继续" in explanation["reason"]
