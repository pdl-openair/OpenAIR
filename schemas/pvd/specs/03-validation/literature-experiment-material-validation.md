# PVD Literature Evidence Acquisition & Closure

## 用户价值

把 Evidence Acquisition Design 真正落实成可追溯、可用于后续科研判断的 Evidence 与 ResearchRecords。

## Input

- 检索设计
- EvidenceSchema
- 正式参数和 pending evidence identities

## Capability

- Literature Evidence Acquisition & Closure
- 内部包含多源检索、可降级的科研相关性排序、渐进阅读、逐篇 Evidence 抽取、ResearchRecord closure 和 Evidence coverage early-stop。
- 内部 CALL / batch / cache / checkpoint 不独立成为科研 capability。

## Output

- EvidencePacket
- ResearchRecords
- source provenance
- Evidence coverage / unresolved gaps
- conditional evidence linkage

## Acceptance

- 排序能力不可用时可按真实 retrieval order 继续，不成为硬门；
- 只由正式 Evidence coverage 决定 early-stop；
- 逐篇抽取允许并行，单篇失败局部隔离；
- grouping 失败时保留全部有效 Evidence，并使用确定性 singleton closure；
- 未被真实来源支撑的 Evidence 不得进入正式记录；
- 输出可直接交给 Comparability。
