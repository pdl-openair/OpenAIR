# apps/web

OpenAIR 前端：**Vite + React + TypeScript**。已实现工作台、任务看板、任务详情等核心页面（态势看板尚未实现）；任务数据来自 `GET /api/tasks`（见 `src/api/client.ts`）。页面边界见 `src/pages/README.md`。

## 启动

```bash
# 在仓库根目录
pnpm install
pnpm --filter @openair/web dev
```

环境变量见根目录 `.env.example`（`VITE_OPENAIR_API_BASE_URL`，默认 `http://localhost:8000`）。

## 相关文档

- `docs/02_产品与Workflow/OpenAIR/待专项更新/workbench_human_ai_collaboration.md`
- `docs/02_产品与Workflow/OpenAIR/待专项更新/task_board_and_artifact_management.md`
- `docs/03_工程/待专项更新/CODE_README.md`
