---
name: actual-metric-extraction
description: 在上游已经明确需要取得哪些实际指标和真实结果来源后，从成功 execution record 关联的真实输出中确定性取得 actual metrics，严格处理歧义并保留可追溯来源定位。
---

# 实际指标确定性提取

## Product Role

本 Skill 服务于科研复现产品中的“执行验证 → 证据抽取”阶段，把已经发生的真实程序执行推进为后续可以保存、比较和判断的**实际测量结果**：

`真实 execution record → 确定性 actual metrics → 后续论文结果比较 / 统计分析`

用户不应理解 parser、行号或完整表头等内部实现细节。只有真实结果本身存在歧义时，上游系统才需要补充必要的定位规则。

## Goal

只完成一件事：

`成功 execution record → 已明确结果来源/格式 → 严格定位 → 类型/单位合同校验 → structured actual metrics → source trace`

本 Skill 不决定论文应该看什么指标，也不判断实验科研正确性。

## Input Authority

上游至少明确：

- `execution_record_relative_path`：本次真实 execution record 的 workspace 相对路径；
- `result_source`：从哪个真实来源取得值，以及该来源的声明格式；
- `metrics[]`：需要取得的 metric 名、预期 `value_type`，必要时给出 `unit`。

只有来源存在歧义时才补充最小定位信息：

- 表格列名与 metric 名不同时，给 `selector.column`；否则默认列名等于 `metric_name`；
- 表格有多行候选结果时，给 `table.row_index`；只有一行时不需要；
- stdout 中多个表无法由所需列唯一定位时，给 `table.header`；能够唯一定位时不需要；
- JSON 结构没有通用的安全默认路径，必须给精确 `selector.json_pointer`。

不得让 LLM 查看日志后自由决定“哪个数字就是最终指标”，也不得为了内部 parser 要求把完整表头/行号转嫁给用户。

## Supported Deterministic Sources

当前实现只包含小而明确的格式 parser：

- `json`：按 JSON Pointer 精确定位；
- `csv` / `tsv`：读取正式表头，按明确列名定位；多数据行时必须声明 row index；
- `pipe_table`：适用于 stdout 中真实打印的 `| ... |` 表格；优先用所需列集合唯一定位表格，存在歧义时要求明确 header。

来源可以是：

- execution record 已固定的 `stdout` / `stderr`；
- 同一受控 workspace 内由上游明确指定的结果文件；执行前已有正式 SHA256 时必须匹配，本次运行新产生的文件在读取同一份字节时计算并记录实际 SHA256。

结构化结果存在时，上游应优先声明 `json` / `csv` / `tsv`，不要把结构化文件转成日志再解析。

## Deterministic Semantics

1. execution record 必须是 `openair-execution-record/v1` 且真实成功：`state=succeeded`、`execution_status=succeeded`、`exit_code=0`、`timed_out=false`。
2. execution stream 必须完整且未截断；否则 fail closed。
3. workspace file 必须位于授权 workspace 内；输入带有正式 SHA256 时必须完全匹配，运行后新产生且执行前没有身份的文件由确定性代码读取、计算并记录实际 SHA256，不允许模型补造。
4. 表格定位必须唯一；同一候选表出现多次或单指标跨多个表无法唯一定位时显式失败。
5. 未声明 `row_index` 时只接受单一数据行；多行时显式失败，不默认取第一行。
6. metric selector 必须唯一命中；缺失、歧义或类型不符都显式失败。
7. 不从其他列、其他表、stderr warning 或相近数字做 fallback。
8. 任一 metric 失败时顶层结果为 `failed`，不得把部分提取伪装成完整成功；已成功项仍保留真实值和 provenance。

## Output

每个成功 metric 至少包含：

- `metric_name`；
- `actual_value`；
- `value_type`；
- `unit`（若上游声明）；
- `extraction_status`；
- `source_trace`：execution record、来源类型、来源 digest、格式及实际解析到的表/行/列或 JSON Pointer。

顶层同时返回请求数、成功提取数、execution record SHA256 和结果来源身份。

## Failure Semantics

显式区分至少以下失败：

- execution record 不存在或非法；
- execution 未成功；
- 指定来源不存在；
- 来源不完整、被截断或 SHA256 不符；
- 来源格式无法解析；
- 表格不存在或定位歧义；
- 多行结果未提供必要选择规则；
- 指标不存在；
- 指标 selector 歧义；
- 值不符合预期类型。

## Responsibility Boundary

本 Skill 只负责已明确指标与真实结果来源后的实际值确定性提取。

不负责指标选择、论文阅读、论文参考值读取、baseline comparison、统计显著性解释、实验正确性判断、自动总结、Artifact 平台、Workflow、ResearchDesign、OT 或 PVD。

`evidence-mining` 负责论文/记录中的语义 claim-evidence 抽取；本 Skill 负责真实程序输出中的确定性 actual metric。两者同属 `evidence_extraction`，职责不同。

SimCSE 仅用于真实验收，生产实现不得包含 SimCSE、STS、SentEval、Princeton 或任何当前 execution record 特判。
