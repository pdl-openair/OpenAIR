# mocks（legacy）



历史静态样例目录：**生产路由不直接读取**此目录；保留至数据迁移完成。



## 与 `fixtures/` 的关系



- **`fixtures/`** 是后续标准的**流程样例 / 录制 / 联调**落点（见 `fixtures/README.md`）。  

- **`mocks/`** 保留至引用迁移完成；**不要**在未改路由与契约的前提下删除本目录数据。  

- 新 PR 若增加「流程级」展示样例，优先放 `fixtures/`；若仍影响现有 dev seed 路径，继续同步更新 `mocks/` 直至迁移结束。**禁止仅在 `mocks/` 新增而不在 `fixtures/` 留标准样例。**

- 这里的“标准样例”指：至少有一组可被联调/回放消费的最小样例（如 task state、artifact 或 capability call），并与 `packages/shared/` 契约字段对齐。



## dev seed 仍可能读取



本地开发（`OPENAIR_ENV=development`，默认开启 dev seed）时，`services/api/app/modules/task_dev_seed.py` 会从 `fixtures/recorded_runs` 加载任务，并**补充合并** `mocks/tasks/` 下的 JSON。这与生产运行时无关，仅用于本地联调。



子目录 `tasks/`、`artifacts/`、`capability-calls/` 内各有 README 说明用途。

