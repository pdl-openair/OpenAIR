# T05-02｜407b 科研授权链迁移映射

## 正式责任图

`Definition → Registry/Capability → Binding → WorkUnit/Input → Skill/runtime → Artifact`

T05-02 只迁移 **Skill/runtime 内部的科研方法与失败边界**。T05-01 已冻结的输入 Authority 保持不变；正式 Registry / Binding / WorkUnit 激活留给 T05-04。

## 必须迁移的科研职责

### Global Planning
- 从冻结的 PVDProjectContext、T04 ResearchRecords / Evidence / provenance / coverage / gaps、T03 support target 中选择需要深入核验的记录。
- 明确需要核验的 support target 与 factor-response relation question。
- 只决定核验范围，不形成下游使用授权。

### record-level Deep Verification
- 一次只核验一个研究记录。
- 核验材料/系统、工艺条件、几何、测量定义、factor-response 关系、混杂因素、迁移边界与未知。
- 单记录失败只影响该记录，成功的兄弟记录继续保留。
- 严禁产生 `allowed_uses`。

### Final Synthesis
- 只消费成功完成的 Deep Verification、显式局部失败及其已引用原始 Evidence。
- 综合冲突、缺失条件、迁移边界与未知。
- 是唯一可以产生 `allowed_uses` 的科研阶段。
- 最终综合不可用或输出越权时，不形成任何正式授权。

## OpenAIR 的承载方式

- 一个确定性 Python 编排壳负责：身份冻结、输入投影、逐记录并行、局部失败隔离、provenance ref 校验、结构校验、确定性 join、授权 fail-closed。
- 三个 LLM 子 Skill 分别负责 Global Planning、Deep Verification、Final Synthesis 的科研语义。
- 该方式复用当前 OpenAIR T04 已存在的“Python adapter + durable LLM child”机制，不新增第二套 provider 或状态机。

## 禁止复制的 Standalone 部分

- Node/CALL 状态机与内部编号；
- Standalone provider wrapper、transport、retry、数据库、cache/checkpoint；
- CLI/API/Web 进度编排；
- Standalone Artifact/runtime 身份体系；
- 为旧实现服务的批处理预算、运行日志和外围 orchestration。

这些都不是 T05 科研职责，不进入 OpenAIR 正式产品合同。

## 本轮明确后置

### T05-03
- `allowed_uses` 的最终正式合同；
- 授权 provenance identity；
- transfer limits / conflicts / unknowns 的最终权威输出收口；
- “只有 synthesis 签署正式授权”的最终产品级 schema 收口。

### T05-04
- 主 T05 capability 切换到正式 Python adapter runtime；
- 三个子 Skill 进入 Registry；
- Python adapter allowlist / entrypoint；
- parent capability-call evidence 贯通；
- Definition / Registry / Binding / WorkUnit / runtime 的正式生产执行闭环。

T05-02 不提前修改以上生产启用面。
