# fixtures（流程样例与录制）

`fixtures/` 存放**跑通过**或**约定用于联调/展示**的样例：任务状态流转、能力调用返回片段、产物样例、看板指标样例、Skill I/O 样例等。

## 不是什么

- 不是「随便造的假数据」目录；入仓样例应有明确用途（联调、回放、UI 展示、契约示例）。  
- Sprint01 **不要求**每个 Skill 都有对应 fixture；优先**流程级**样例，支撑主工作台、任务看板、任务详情。

## 子目录

| 子目录 | 用途 |
|--------|------|
| `recorded_runs/` | 录制的一次运行切片（日志/响应摘要等，脱敏） |
| `task_states/` | 任务状态机或阶段快照样例 |
| `artifacts/` | 产物 JSON 等展示样例 |
| `capability_calls/` | 能力调用记录样例（含失败样例） |
| `dashboard_samples/` | 态势看板指标样例 |
| `skill_io/` | Skill 输入输出配对样例 |

## 与 `mocks/` 的关系

**生产路由不读 `mocks/`**；本地 dev seed 会合并 `fixtures/recorded_runs` 与 legacy `mocks/tasks`（见 `mocks/README.md`）。新样例优先落在 `fixtures/`；迁移引用时保持契约一致。
