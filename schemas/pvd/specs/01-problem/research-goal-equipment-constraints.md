# PVD 研究请求理解

## 用户价值

在用户提交 PVD 研究请求后，一次完成当前研究任务的正式科研理解，形成后续研究可以直接消费的事实、动态参数范围和关键未知项。

## Input

- `canonical_user_query`：用户当前 PVD 研究请求原文。

模型业务输入只使用这一项；平台身份、WorkUnit、Artifact、ScientificState 等执行元数据不进入科研语义输入。

## Capability

- `pvd_research_request_understanding`
- 一个逻辑科研语义任务；正常成功只需要一次 Provider attempt。
- 结构化输出可恢复失败最多允许一次公共 recovery；它仍属于同一个 capability。

## Output

正式 Artifact 的科研内容同时包含：

- `structured_context`
- `recommendation_parameter_set`
- `run_preferences`

其中推荐参数集合由当前请求动态确定；仅待评估的条件参数保留在准入判断中，不提前进入正式推荐范围。

## ScientificState

确定性 materializer 只写入正式事实：

- 当前实验系统事实；
- 同一请求中明确陈述的已执行 / 已测量实验。

未来计划不写成真实历史。

## Acceptance

- 研究目标、指标、材料、工艺和动态参数语义可追溯到用户原文；
- 未提供的温度、压力、靶基距和设备边界保持 unknown / missing，不猜值；
- hard constraint 只来自用户明确边界；
- conditional 参数不进入正式 recommendation scope；
- integer / categorical / boolean 等非固定四参数类型可以进入正式 scope；
- 同一 query 的已有实验不再启动第二个科研 LLM。
