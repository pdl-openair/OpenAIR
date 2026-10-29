# Dataset Manifest: `<dataset-id>`

> 本文件只记录测试集输入快照、范围与版本，不记录运行结果。运行结果使用 `run-manifest-template.md`。

## 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Dataset ID | `<dataset-id>` |
| Dataset Version | `<dataset-version>` |
| 适用 Skill | `<skill-id-or-category>` |
| Dataset Type | `<core/cross-domain/boundary/regression>` |
| Owner | `<owner>` |
| 创建时间 | `<yyyy-mm-dd>` |
| 最近更新时间 | `<yyyy-mm-dd>` |

## 2. 目的

`<该测试集用于验证什么能力或失败模式>`

## 3. 场景范围

| 场景 | 数量 | 说明 |
| --- | --- | --- |
| 核心场景 | `<count>` | `<note>` |
| 跨领域场景 | `<count>` | `<note>` |
| 边界场景 | `<count>` | `<note>` |
| 历史回归 | `<count>` | `<note>` |

## 4. 输入来源

| 来源 | 路径或引用 | 版本 | Hash | 说明 |
| --- | --- | --- | --- | --- |
| `<source>` | `<path-or-ref>` | `<version>` | `<hash>` | `<note>` |

## 5. 文件列表

| 文件 | 类型 | Hash | 说明 |
| --- | --- | --- | --- |
| `<file>` | `<jsonl/pdf/snapshot/...>` | `<hash>` | `<note>` |

## 6. 真实材料与 Gold 标注

| 项目 | 是否包含 | 范围 | 说明 |
| --- | --- | --- | --- |
| 真实 PDF | `<yes/no>` | `<scope>` | `<note>` |
| 真实候选文献池 | `<yes/no>` | `<scope>` | `<note>` |
| DocumentChunk / SourceAnchor | `<yes/no>` | `<scope>` | `<note>` |
| Evidence | `<yes/no>` | `<scope>` | `<note>` |
| 人工 Gold | `<yes/no>` | `<scope>` | `<reviewer-or-rule>` |

## 7. 数据集更新规则

允许更新的情况：

- 新增真实失败样例；
- 新增长期需要保持的能力；
- 修正错误标注；
- 扩展跨领域或边界覆盖。

不得仅为了提高当前版本分数而删除困难样例。

## 8. 变更记录

| 日期 | Version | 变更 | 原因 | 影响范围 | Reviewer |
| --- | --- | --- | --- | --- | --- |
| `<date>` | `<version>` | `<change>` | `<reason>` | `<scope>` | `<reviewer>` |

## 9. 隐私、版权与使用边界

- `<privacy-boundary>`
- `<copyright-boundary>`
- `<redistribution-boundary>`
