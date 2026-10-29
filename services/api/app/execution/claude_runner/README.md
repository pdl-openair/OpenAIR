# claude_runner

Claude Agent SDK 的调用入口、会话边界与错误映射计划落在此目录（**当前无实现代码**）。

与 `skills/` 的衔接：运行时消费 `.claude/skills/`（由根目录 `scripts/sync_claude_skills.py` 从 `skills/` 同步）。
