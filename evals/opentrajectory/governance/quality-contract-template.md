# `<skill-id>` Quality Contract

## 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Skill ID | `<skill-id>` |
| Skill Version | `<skill-version>` |
| 所属类别 | `<skill-category>` |
| 上游依赖 | `<upstream-inputs-or-skills>` |
| 下游消费者 | `<downstream-skills-or-product-output>` |
| Owner | `<owner>` |
| 最近更新时间 | `<yyyy-mm-dd>` |

## 2. 职责

`<用一句话说明该 Skill 在主链路中负责什么>`

## 3. 非职责

该 Skill 不负责：

- `<out-of-scope-1>`
- `<out-of-scope-2>`

## 4. 输入边界

| 输入 | 是否必需 | 来源 | 语义 | 可空条件 | 信息不足时的处理 |
| --- | --- | --- | --- | --- | --- |
| `<field>` | `<yes/no>` | `<source>` | `<meaning>` | `<condition>` | `<degrade-or-block>` |

## 5. 输出要求

| 输出 | 是否必需 | 下游用途 | 可追溯要求 | 可空条件 |
| --- | --- | --- | --- | --- |
| `<field>` | `<yes/no>` | `<consumer>` | `<traceability>` | `<condition>` |

## 6. 成功标准

### 6.1 结果质量

- `<measurable-quality-standard>`

### 6.2 过程与可追溯

- `<required-trace-or-artifact>`

### 6.3 跨场景泛化

- `<cross-domain-requirement>`

### 6.4 性能

| 指标 | 目标 | 硬阈值 | 说明 |
| --- | --- | --- | --- |
| `<latency-or-cost>` | `<target>` | `<hard-limit>` | `<note>` |

## 7. 硬性失败条件

出现以下任一情况，直接判定失败：

- `<hard-failure-1>`
- `<hard-failure-2>`

## 8. 允许降级行为

| 场景 | 允许的降级 | 必须记录的状态 | 禁止行为 |
| --- | --- | --- | --- |
| `<scenario>` | `<allowed-degrade>` | `<status>` | `<forbidden-behavior>` |

## 9. 禁止行为

- 无依据补全；
- 静默丢失关键输入或证据；
- 使用低价值 fallback 掩盖失败；
- 将占位词写入正式结果；
- 为单一领域或单一样例写不可泛化逻辑；
- `<skill-specific-forbidden-behavior>`。

## 10. 已知失败模式

| Failure Mode | 对应 Regression Case | 状态 | 说明 |
| --- | --- | --- | --- |
| `<failure-mode>` | `<case-id>` | `<open/fixed/verified/reopened>` | `<note>` |

## 11. 人工复核触发条件

- `<mandatory-or-triggered-human-review-condition>`

## 12. 版本变更记录（可选）

仅在该 Skill 已进入持续迭代阶段后维护。初次建立 Quality Contract 时可保留为空。

| 日期 | Skill Version | 变更 | 影响范围 | 对应 Eval Run |
| --- | --- | --- | --- | --- |
| `<date>` | `<version>` | `<change>` | `<scope>` | `<run-id>` |
