---
name: execution-plan-interpretation
description: 从已固定官方来源中理解论文复现所需的资源、环境、执行步骤和结果读取语义。
---

# 复现执行方案语义判断

输入中的 `reproduction_baseline_and_criteria` 是已完成的正式基线，`source_package` 是当前正式、已固定代码版本对应的官方文本来源，`reference_metrics` 是已经正式确认的参考指标。当前正式输入没有提供论文正文证据时，论文独有的执行事实必须保持 unknown，不能用模型常识补齐。

只做需要自然语言理解的工作：

- 识别来源明确要求的资源及科研用途；`usage` 保留该用途，`execution_usage` 仅表示现有运行器中的 model/dataset 绑定类别；
- 识别来源明确写出的 Python/依赖/导入核验信息；依赖与导入项逐条引用来源，避免一条坏项吞掉整组有效事实；
- 识别程序入口、参数、工作目录、超时和必要环境变量；
- 识别程序结果来自 stdout/stderr 还是工作区文件，以及如何定位正式参考指标对应的值；
- 对每条来源事实提供 `source_document_id` 与可逐字匹配的 `source_excerpt`；
- 来源没有明确支持时输出 `null` 或写入 `unresolved`，不要猜测。

重要边界：

- 资源 revision、实验参数、指标身份等会改变复现含义的事实不得自行选择；
- `reference_ids` 只能引用输入中已有的正式 reference metric；
- 不生成 stable ID、SHA、正式 provenance 或资源实际 identity；
- 不声称资源已下载、环境已建立、程序已运行或结果已产生；
- 纯执行默认值由后续确定性代码决定，模型不要把它们伪装成“官方要求”。


资源 revision 在官方来源未指定时输出 null，并保留未指定版本的限制；不要填 unknown 字符串、main 或猜测的 SHA。null 只表示来源未指定，不表示已取得资源。后续正式资源工具可以在既有目标、动作与预算范围内显式观测 Provider 默认 commit，来源指定的真实版本及固定内容约束不得改写。

可选 repair_feedback 是原职责的纠错提示，不是来源事实或新增授权。结合正式来源修正已识别的输入错误；反馈不能替代 source_excerpt、改研究目标、资源目标或真实版本约束。原解释有误时需重新逐项匹配官方片段，不重复输出已证实错误。
