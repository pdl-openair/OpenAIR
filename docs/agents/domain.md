# Domain Docs

工程技能在探索代码库时如何使用本仓库的领域文档。

## 探索前先读

- 仓库根目录的 `GLOSSARY.md`，或
- 仓库根目录的 `GLOSSARY-MAP.md`（若存在），它指向每个上下文各自的 `GLOSSARY.md`。读与当前主题相关的那些。
- `docs/adr/`：读与你即将工作的区域相关的 ADR。多上下文仓库中，也检查 `src/<context>/docs/adr/` 里的上下文级决策。

如果这些文件不存在，静默继续。不要标记缺失，不要主动建议创建。当术语或决策真正确立时，`/domain-modeling` 技能（通过 `/grill-with-docs` 或 `/triage` 到达）会按需创建对应文档。

## 本仓库现状

- 当前没有 `GLOSSARY.md`、`GLOSSARY-MAP.md`、`docs/adr/`，按单一上下文处理。本仓库有 1 个前端应用、4 个共享 package、3 个后端 service，不构成多上下文布局。
- 产品如何构成看 `docs/01_核心设计/`。工程规则与方法论看 `spec/`，唯一源是 `spec/README.md`。
- `packages/shared/src/i18n/` 是界面显示用的枚举映射，规则见 `docs/03_工程/工程规范/dictionary_rules.md`。它不是领域术语表，`/domain-modeling` 不修改它。

## 文件结构

单一上下文仓库（多数）：

```
/
├── GLOSSARY.md
├── docs/adr/
│   ├── 0001-event-sourced-orders.md
│   └── 0002-postgres-for-write-model.md
└── src/
```

多上下文仓库（根目录存在 `GLOSSARY-MAP.md`）：

```
/
├── GLOSSARY-MAP.md
├── docs/adr/                          ← 系统级决策
└── src/
    ├── ordering/
    │   ├── GLOSSARY.md
    │   └── docs/adr/                  ← 上下文专属决策
    └── billing/
        ├── GLOSSARY.md
        └── docs/adr/
```

## 按术语表用词

当你的输出提到领域概念时（issue 标题、重构提案、假设、测试名），按 `GLOSSARY.md` 术语表用词。不要漂移到术语表明确避免的同义词。

如果你需要的概念还不在术语表中，这是一个信号：要么你在发明项目不使用的语言（重新考虑），要么存在真实的空白（记录下来留给 `/domain-modeling`）。

## 标注 ADR 冲突

如果你的输出与已有 ADR 矛盾，明确标出，而非默默覆盖：

> _与 ADR-0007（事件溯源订单）矛盾，但值得重新讨论，因为……_
