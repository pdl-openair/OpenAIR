---
name: quality-audit
description: 审计结果、图表、结论、原始数据、实验记录、运行日志、分析脚本和证据链之间是否可追溯、一致、合规和可复现；缺少原始记录时只输出审计缺口，不做通过/不通过背书。
---

# 结果可靠性审计 (L3)

## Input

需要可复核材料，至少包含以下一类：

- 结果表、图表、结论草稿、报告片段或论文结果段。
- 原始数据、实验记录、运行日志、分析脚本、统计方法或环境信息。
- claim-evidence 表、来源包、引用定位、机制图或证据链。
- 用户指定的审计标准：可复现、可追溯、统计合规、对照充分、引用可信。

只有结论口述或“帮我看看靠不靠谱”时，先要求补材料，不给质量背书。

## Steps

1. 建立审计对象清单：结论、图表、指标、数据、脚本、日志、来源和证据 ID。
2. 检查追溯完整性：
   - 每个结论能否追溯到图表/结果。
   - 每个图表/结果能否追溯到数据、实验记录或脚本。
   - 每个引用或证据是否有来源定位。
3. 检查一致性：单位、样本数、统计口径、筛选条件、版本、图表数值和正文表述是否一致。
4. 检查分析合规性：预定义分析、显著性、效应量、多重比较、异常值处理、negative result 是否记录。
5. 检查可复现性：环境、随机种子、数据版本、命令、参数、硬件/设备、批次记录是否足够。
6. 输出风险等级和修复建议；只有材料充分时才给 `pass / needs_fix / fail` 判定。

## Output

输出结构必须包含：

- `audit_scope`：审计材料、审计标准和未覆盖范围。
- `traceability_matrix`：结论 -> 图表/结果 -> 数据/记录/脚本 -> 来源 ID。
- `issues`：问题 ID、严重性、证据、影响、修复建议。
- `missing_records`：缺失的原始记录、脚本、日志、统计说明或引用定位。
- `verdict`：pass、needs_fix、fail 或 insufficient_materials，并说明理由。

## Scenario Artifact Contract v2

The audit output must be usable before a report, citation, or follow-up
experiment decision. Include these sections:

1. `audit_scope`: provided records, missing records, audit standard, and what
   was not inspected.
2. `traceability_matrix`: `claim_id`, conclusion text, figure/result ID,
   data/log/script ID, source ID, claim-to-record path, trace status, and break
   reason.
3. `issues`: `issue_id`, severity, affected claim/result, issue evidence,
   severity reason, reproducibility impact, citation risk, whether recomputation
   is needed, and next repair action.
4. `missing_records`: record type, why it matters, minimum acceptable
   replacement, and whether it blocks a verdict.
5. `reproducibility_checklist`: environment, seed, data version, command,
   parameters, sample/filter rule, statistics, and negative-result record.
6. `verdict`: one of `pass`, `needs_fix`, `fail`, `insufficient_materials`.
7. `next_record_to_inspect`: the single highest-value record to request next.

Severity labels must be `blocker`, `high`, `medium`, or `low`. A `pass`
verdict requires a complete traceability matrix and no blocker/high unresolved
issues. If original data, logs, scripts, or source IDs are missing, the verdict
must be `insufficient_materials`.

Do not stop at "linked" or "broken". Explain why a link is adequate or
inadequate for the claim. Check for stale notebook outputs, missing clean-run
evidence, sample-count/filter-rule gaps, unit/statistics mismatch, and missing
environment or command records when those records are relevant.

## Fail Closed

- 缺少原始数据、记录、脚本或证据链时，verdict 必须是 `insufficient_materials`。
- 不把“看起来合理”写成可靠性通过。
- 不代替同行评审、伦理审查或正式统计审查；只输出可复核审计结果。
