# Intent Guide: retrieval-matrix-generation

## 7 个固定结构槽位

输出固定包含以下 7 个 intent，但**槽位存在不等于必须有内容**：

1. `core_topic`：研究对象与主问题，高召回入口。
2. `constraint_focused`：用户明确条件、边界、比较对象或硬约束。
3. `mechanism_focused`：仅当输入明确包含机制/过程/失效路径时使用。
4. `method_or_strategy`：仅当输入明确包含方法、策略、工具或验证方式时使用。
5. `metric_or_performance`：仅当输入明确包含评价指标、性能目标或可比较结果时使用。
6. `adjacent_but_relevant`：已确认概念的直接标准术语邻域；不得引入新的具体系统或假设。
7. `noise_exclusion`：仅用户明确排除项或 `research_context.out_of_scope`。

缺少某类事实时，保留该 intent 行并让 term 数组为空；不要为了填槽位而猜词。

## 术语来源

可用：

- `seed_terms`；
- 与已确认语义一致的 `interpreted_constraints[].downstream_search_terms`；
- `research_context` 中明确给出的检索事实；
- 从 `user_query` 可直接识别的研究对象/条件；
- 已确认概念的直接同义词、标准缩写、拼写变体和紧邻上/下位术语。

不可用：

- 风险标记或缺失信息推出来的词；
- 未确认 candidate 作为 strict / must-have；
- 用户未指定的具体材料、benchmark、阈值、数据库或实验条件；
- 领域专用默认词表；
- CJK 原句、完整用户问题、内部标签。

## 排除规则

`exclude_terms` 只来自：

1. `research_context.out_of_scope`；
2. 用户明确表达的排除项。

不得从 `risk_flags`、ambiguity、missing-input、低置信候选或“看起来无关”的领域自动生成排除项。拿不准时留空。

## 同义词按概念簇组织

同一概念的同义词/缩写应放进同一个 OR 组，不要拆成多条近重复 query。

```text
("canonical concept" OR "accepted synonym" OR "standard abbreviation")
AND
("target task" OR "task synonym")
```

明确比较任务保留双方：

```text
("candidate approach" OR "candidate alias")
AND "reference baseline"
AND "target outcome"
```

## Boolean 规则

Strict：
```text
"core object" AND "confirmed condition"
```

Broad：
```text
("core object" OR "direct synonym") AND ("condition" OR "condition synonym")
```

Explicit exclusion：
```text
"core object" AND "confirmed condition" AND NOT ("explicit exclusion A" OR "explicit exclusion B")
```

始终遵守：

- OR 组显式加括号；
- optional OR 组前写 `AND`；
- NOT 只用于明确排除项，并写成 `AND NOT (...)`；
- 不依赖检索后端默认优先级；
- 不使用 `"A" ("B" OR "C")` 这类隐式连接；
- 大小写/空白归一化后去重，并进一步消除仅单个近义词替换造成的语义重复。

## 变体角色

| `variant_type` | 使用条件 | 目标 |
|---|---|---|
| `strict` | 存在已确认的核心概念/硬条件 | 提高精确度 |
| `broad` | 存在同义词、软条件或可选 facet | 提高召回 |
| `exclusion` | 存在明确排除项 | 安全排噪 |
| `fallback` | 输入稀疏，无法形成正常 facet 组合 | 只用合法核心词保守补足 |

当前 Schema 要求恰好 8 个 query 变体。优先让 8 个变体承担不同检索作用；输入不足时宁可使用保守 fallback，也不要发明新的科研事实。
