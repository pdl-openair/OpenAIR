# Dataset Manifest: `strategy-phase1-3`

## 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Dataset ID | `strategy-phase1-3` |
| Dataset Version | `v0.2.1` |
| 适用 Skill | `structured-requirement-generation`、`constraint-interpretation-skill`、`retrieval-matrix-generation` |
| 更新时间 | `2026-06-02` |
| 数据形态 | 共享原始科研问题 + 分阶段 Eval 标注 |

## 2. 场景范围

| 文件 | 数量 | 说明 |
| --- | ---: | --- |
| `core.jsonl` | 4 | 3 个电池核心场景 + 1 个 AI Agent 核心场景 |
| `cross-domain.jsonl` | 8 | AI Agent、RAG / Evidence Graph、理论材料、低维和功能材料 |
| `boundary.jsonl` | 12 | 模糊、信息不足、多对象、术语变体、冲突、来源规则、排噪与降级 |
| `regression.jsonl` | 1 | Scenario B 不可执行占位 |
| 合计 | 25 | 24 条可执行问题 + 1 条不可执行占位 |

## 3. Phase3 handling 分布

| `handling_class` | 数量 | 说明 |
| --- | ---: | --- |
| `normal_generate` | 13 | 可在硬门禁与评分通过后进入候选通过 |
| `conservative_generate` | 5 | 必须人工复核，不得自动通过 |
| `manual_review_required` | 5 | 必须人工裁决 |
| `reject_unreliable_input` | 1 | Eval 层拒绝，不得进入 baseline 或通过率 |

## 4. 输入来源

| 来源 | 说明 |
| --- | --- |
| Phase1 replay 与 example | 复用结构解析代表样例 |
| Phase2 `p0_1_realistic_cn_eval` | 复用已有中文科研问题 |
| 治理路线图 | 登记 Scenario B 已知失败占位 |
| 本轮 curated cases | 补齐跨领域、比较、术语变体、来源规则、排噪和降级边界 |

## 5. 文件校验

| 文件 | 类型 | 数量 | SHA-256 |
| --- | --- | ---: | --- |
| `core.jsonl` | JSONL | 4 | `d6e2e78b91af4e0f558bc97bc10447106048b09928b0263fcdcca0e67797daa9` |
| `cross-domain.jsonl` | JSONL | 8 | `535e5188dc417a8a1dd8e6543c5e446c1659e4007bb523bc873887f21569500e` |
| `boundary.jsonl` | JSONL | 12 | `5c42cd5941c69becbcab8a840a215be0dda3e1804f55bb049ae1f479ae9c8683` |
| `regression.jsonl` | JSONL | 1 | `8b062c60553c6cebe572a1130b256be1d837cdb3393c1285e522b5e08afd8afe` |

## 6. Gold 边界

| 项目 | 当前状态 | 说明 |
| --- | --- | --- |
| 原始科研问题 | 已包含 | 可作为 Phase1 输入 |
| 分阶段期望行为 | 已包含 | 用于拆分 checks、Rubric 与人工复核 |
| 冻结输出 JSON | 未包含 | 首轮 baseline 前不伪造标准输出 |
| Captured run | 未包含 | 留待 harness 生成 |
| Scenario B fixture | 缺失 | 当前占位不可执行，不得生成假 fixture |

## 7. 更新规则

- `v0.2.1`：为 25 条记录显式补齐 `execution_policy` 契约，并重算 JSONL SHA-256。
新增或修改 case 必须提升 Dataset Version，并重算 hash。不得为了提高当前 Skill 分数删除困难样例。
