# `app/execution/`（目标边界）

第一版后端**不**单独拆复杂的 agent-runner 服务；Claude Agent SDK 的编排与执行入口计划收敛在本目录下。

## 目标结构（演进中）

```
execution/
  claude_runner/   # SDK 调用入口、会话与工具边界（待落地）
  schemas/       # 与执行相关的请求/响应形状（可与 packages 契约对齐）
```

## 与当前代码的关系

- 当前 HTTP 路由仍在 `app/routes/`；领域模型占位在 `app/models/`。
- 新增执行逻辑时优先落在本目录，避免在路由文件内堆叠编排代码。
