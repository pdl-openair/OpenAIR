"""User-facing activity must not turn coarse execution facts into false advice.

Owner: task_events presentation. No Provider or database calls are needed.
"""
from __future__ import annotations

import unittest

from app.modules.task_events import (
    _interrupted_activity_copy,
    _readable_activity_copy,
    project_research_activity_events,
)


class ResearchInterruptionActivityTests(unittest.TestCase):
    def test_system_failure_is_not_a_user_input_or_dependency_request(self):
        for status in ("needs_input", "blocked", "failed", "blocked_recoverable"):
            with self.subTest(status=status):
                headline, summary = _interrupted_activity_copy({
                    "status": status, "reasonCode": "provider_configuration_unavailable",
                })
                self.assertEqual(headline, "当前研究步骤未能继续")
                self.assertIn("系统负责", summary)
                self.assertIn("无需补充", summary)
                self.assertNotIn("重试", summary)

    def test_explicit_user_and_dependency_facts_remain_distinct(self):
        user = _interrupted_activity_copy({"status": "needs_input", "reasonCode": "required_input_missing"})
        dependency = _interrupted_activity_copy({"status": "blocked", "reasonCode": "dependency_not_satisfied"})
        unknown = _interrupted_activity_copy({"status": "external_result_unknown"})
        self.assertIn("明确输入", user[0])
        self.assertIn("具体要求", user[1])
        self.assertIn("外部条件", dependency[0])
        self.assertIn("无需操作", dependency[1])
        self.assertIn("核对外部", unknown[0])
        self.assertIn("避免重复提交", unknown[1])

    def test_coarse_needs_input_does_not_assign_responsibility(self):
        headline, summary = _interrupted_activity_copy({"status": "needs_input"})
        self.assertEqual(headline, "当前步骤等待必要信息确认")
        self.assertNotIn("等待用户", headline)
        self.assertIn("系统正在确认", summary)

    def test_old_coarse_display_is_neutralized_without_inventing_new_facts(self):
        for headline in ("正在等待用户处理", "正在等待依赖"):
            output = _readable_activity_copy(headline, None)
            self.assertEqual(output["headline"], "当前研究步骤暂未继续")
            self.assertIn("已有成果保留", output["summary"])
        self.assertEqual(_readable_activity_copy("文献筛选已完成", "已保留来源"),
                         {"headline": "文献筛选已完成", "summary": "已保留来源"})
        output = _readable_activity_copy("研究步骤未完成", "系统正在检查是否可以自动重试或继续。")
        self.assertNotIn("自动重试", output["summary"])

    def test_failed_or_stopped_phase_never_falls_through_to_completed(self):
        for phase, status in (("failed", ""), ("blocked", ""), ("stopped", ""), ("failed", "cancelled")):
            with self.subTest(phase=phase, status=status):
                events = project_research_activity_events([{
                    "id": "fact-1", "type": "agent_lifecycle", "occurredAt": "2026-10-05T00:00:00Z",
                    "payload": {"phase": phase, "status": status, "workUnitId": "work-1", "displayLabel": "研究验证"},
                }])
                self.assertEqual(len(events), 1)
                self.assertNotIn("完成当前工作", events[0]["headline"])
                self.assertNotIn("步骤已完成", events[0]["headline"])
                self.assertEqual(events[0]["workUnitId"], "work-1")
                self.assertEqual(events[0]["occurredAt"], "2026-10-05T00:00:00Z")
                self.assertEqual(events[0]["sourceEventId"], "research-activity-v2:fact-1")


if __name__ == "__main__":
    unittest.main()
