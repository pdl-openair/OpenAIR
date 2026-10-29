# 回放测试（可选）

OpenAIR 后端**不消费** `tests/` 或 `examples/` 目录。若需 SDK 回放测试，唯一被后端读取的样例路径是：

```
fixtures/skill_io/<skill_id>/response.json
```

由 `services/api/app/execution/replay_runner.py` 在 replay runtime 下加载。

## 如何做基础回放

1. 跑一次真实 skill 调用，捕获返回 JSON
2. 保存到 `fixtures/skill_io/<skill_id>/response.json`
3. 切换 task 的 runtime 为 replay，调用该 skill，验证后端能加载并返回

## 不需要回放的情况

- claude_code runtime skill 主要靠自然语言指导 LLM，无固定 JSON 输出
- 仅 llm_structured / python_adapter runtime skill 有结构化输出，值得做回放
