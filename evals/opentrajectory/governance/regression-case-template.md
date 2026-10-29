# Regression Case: `<case-id>`

> 本文件记录稳定失败模式及其验证生命周期，不承担测试集定义职责。测试数据范围与版本使用 `dataset-manifest-template.md`。

## 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Case ID | `<case-id>` |
| Skill ID | `<skill-id>` |
| First Seen At | `<iso-8601>` |
| Dataset Type | `<core/cross-domain/boundary/regression>` |
| Status | `<open/fixed/verified/reopened>` |
| Severity | `<low/medium/high/critical>` |

## 2. Failure Mode

`<一句话说明失败模式>`

## 3. 输入快照

| 项目 | 路径或引用 | Hash | 说明 |
| --- | --- | --- | --- |
| Input | `<path-or-ref>` | `<hash>` | `<note>` |

## 4. Captured Run

| 项目 | 路径或引用 | 说明 |
| --- | --- | --- |
| Trace | `<path-or-ref>` | `<note>` |
| Artifacts | `<path-or-ref>` | `<note>` |
| Output | `<path-or-ref>` | `<note>` |

## 5. 实际行为

`<actual-behavior>`

## 6. 期望行为

`<expected-behavior>`

## 7. 检查规则

### 7.1 Deterministic Checks

| Check ID | 规则 | 通过条件 |
| --- | --- | --- |
| `<check-id>` | `<rule>` | `<pass-condition>` |

### 7.2 Rubric Checks

| 维度 | 检查重点 | 通过条件 |
| --- | --- | --- |
| `<dimension>` | `<criteria>` | `<pass-condition>` |

## 8. Human Review Notes

`<human-review-notes>`

## 9. 生命周期

```text
open
→ fixed
→ verified
→ reopened
```

| 时间 | 状态 | Skill Version | Verification Run | 说明 |
| --- | --- | --- | --- | --- |
| `<timestamp>` | `<status>` | `<version>` | `<run-id>` | `<note>` |

## 10. 修复与复核

| 字段 | 内容 |
| --- | --- |
| Fixed In Version | `<version-or-na>` |
| Verification Runs | `<run-ids>` |
| Reopened At | `<timestamp-or-na>` |
| Reviewer | `<reviewer>` |
