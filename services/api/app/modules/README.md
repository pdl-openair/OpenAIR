# `app/modules/`（目标边界）

按领域拆分 API 实现时的**目标落点**（与路由注册方式解耦，可渐进迁移）。

```
modules/
  tasks/
  artifacts/
  confirmations/
  capability_calls/
  dashboards/
```

当前 Sprint 骨架仍以 `app/routes/` 为主；迁移时保持对外路径与契约不变。

**任务运行时**：`task_store.py`（内存，唯一数据源）。`recorded_run_adapter.py` / `task_dev_seed.py` 仅在 `OPENAIR_DEV_SEED_FIXTURES=1` 时用于本地灌演示数据，非生产路径。
