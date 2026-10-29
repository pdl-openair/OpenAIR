# Research Package｜外部 Agent 分发与总控

> 2026-10-09。用户负责在其他聊天分发；本聊天负责总控、规格与验收决策。本聊天不启动子 agent 或自行向其他聊天发送消息。

## 当前调度（最新）

**2026-10-10：只分发RP-3-C1-RETRY给原writer。** C1固定b46d9ae2的writer ACCEPT已收件；总控定位保存失败重试跳过来源复查，判REJECT，仅修这一处。T1/T2不重开，C2—C4尚未授权；整体仍约35%。

可复制消息：

> 请执行 `~/Desktop/code/OpenAIR-canonical/docs/task_specs/2026-10-10-research-package-rp3-c1-retry-freshness.md`。在你固定b46d9ae24457dfbf6cbe26e65f4f703570174ef9上接续，只修未保存confirmed的同key重试前提复查；真实DB证明来源新版或接受撤回阻断新保存，同时已实际保存退出后的历史资产仍可恢复。先保留固定BASE反例，再修代码及直接回归，不重审整包、不改旧tests/DBschema/合同，不推进C2。保存新固定SHA、报告/JSON、原始失败及复跑证据，不覆盖原交付、不合入部署、不启动其他agent。

推荐原writer继续GPT-6.1 medium；这个修正无需整批high。返回后总控核查，再另派独立review，技术退出后发C2。用户现在只需分发，无新增产品决策；仍在等待能在产品上实际形成的结果，最终演示由用户核查。

设计原始封存、C1输入及writer交付保持。总控核查见 `docs/dev_plans/research-package/batch3/RP-3-C1-controller-intake.md/json`，其分支探针不是DB错误资产的实跑证明；返工spec要求真实DB补齐。high合入人审继续保留。

## 历史调度（不再分发）



**最新：只分发一次RP-1-INTEGRATED-REVIEW。**用户已批准纯格式增行，总控选择不拆分，已完成格式与两条限期例外，候选 `2b4afeb7665a3dab4bf0b0d9242c56f8ef1d3360`。正式门禁全通过，分支干净；旧规格不再机械返工。以下较早分发消息保留历史，以此为当前。

给未参与协议/质量/测试/总控格式施工的独立agent（若原协议reviewer未参与施工，可承接）：

> 请执行 `docs/task_specs/2026-10-09-research-package-rp1-integrated-review.md` 最新版，固定累计BASE `f1114ce8a19e6e12d690dbdc7458350a74db7cad`、SUBMITTED `2b4afeb7665a3dab4bf0b0d9242c56f8ef1d3360`。候选位于主仓 `tmp/rp-q5-format-ffc67116`，原目录只读，另建自己的独立工作区。用户已批准两测试仅本次formatter增行、2026-10-16复审；总控自检门禁/原节点/AST保真已通过，请独立核查累计协议、质量修复、测试责任对齐与纯格式、人审范围及最终有效门禁。根与API Suite分开，不跑DB/模型，不修源码、不覆盖writer证据。日志以无损gzip核对，交付RP-1-INTEGRATED-REVIEW.md/json三态结论与原始证据；不能宣布产品可演示或自行关闭T1。

独立验收通过后总控判基础退出、释放T2真实资产集成；正式合入的CODEOWNERS人审仍按项目规则，用户本次范围批准不冒充已合并。无新的产品决策问题。

当前阶段目标：**OpenAIR产品功能实际可用、完整首案可演示**，按TASKLIST的T0—T6与T7演示出口验收。真人效率对照、异形第二案和正式开放后置；原合同与冻结基线保留。RP-Q5-FIXTURE用户已分发，等待结果，不重复派发；返回时总控必须说明具体修法、原责任如何保留、验证结果和下一步。完成基础验收后优先释放T2真实资产集成，再推进浏览器用户流程。

A/B/C/D已接收，不要再次分发。用户确认合同补充与首案标准，T0已收口、T1为CURRENT；全量顺序见 [分批施工与验收安排](分批施工与验收安排.md)。协议开发与质量修复已交付、隔离环境已验收；现在仅补分发Q5测试合同对齐，协议独立reviewer继续原任务，重要交付由独立reviewer及用户核查。

### 最新可分发：RP-Q5-FIXTURE（给原质量writer）

该项现已交付ffc67116，不再分发。原114节点及所有回归通过；总控格式预览触发两个旧大文件纯格式增行门禁，正在呈交限路径/期限例外决定。格式补偿由总控执行，不让用户转发机械返工；最终候选SHA产生后才释放 `2026-10-09-research-package-rp1-integrated-review.md`，一次收口独立验收。以下原消息仅保留历史。

> 请执行 `docs/task_specs/2026-10-09-research-package-rp-q5-fixture.md`。固定输入 `abe8b121be1877c9f18d396be03c33f409250978`，新建独立分支/工作区。只允许三份旧测试与当前外部尝试身份、方案确认出口合同对齐，保持全部114项责任及原业务断言，不改生产构造器/worker/状态/投影。原61失败由60个缺external_attempt构造和1个旧文案断言直接触发；补齐后若揭示真业务反例，保留证据交总控，不反改期望求绿。按规格完整复跑Q5及累计质量门禁，交付RP-Q5-FIXTURE.md/json与提交；不要关闭T1、合并或运行DB。

此项完成后由不同主体复核质量代码及测试断言映射；RP-1-REVIEW固定原协议输入继续，不重复分发已在执行的任务。总控整合后才判T1退出；不将原RP-QG REJECT改写为PASS。

### 现在分发：RP-1-REVIEW（不同于协议施工者）

> 请执行 `docs/task_specs/2026-10-09-research-package-rp1-review.md` 最新版。固定 BASE `f1114ce8a19e6e12d690dbdc7458350a74db7cad`、SUBMITTED `8f9ee6dbbac9d28ad635f06d11c2c40589570715`，从协议交付的确切提交建立自己的独立工作区；原 writer 工作区只读。按 R1—R6 核查范围、正式合同、独立正负行为、v2哈希、科研结构保真及原始门禁证据。已知P5不足应明确列出，不能把协议测试通过写成整体ACCEPT。只写 reviewer 报告与证据，不修产品或运行DB，不覆盖 writer 日志。交付给用户带回总控。

### 已交付：RP-QG（以下保留原分发，不再重复）

> 请执行 `docs/task_specs/2026-10-09-research-package-rp-qg.md`。从固定 `8f9ee6dbbac9d28ad635f06d11c2c40589570715` 建新的独立分支/工作区；修复ruff失败被门禁误报通过，准备独立有效工具，同环境复测BASE/SUBMITTED六个mypy错误，按白名单最小修复并验证运行行为保持。修产品类型边界前须有已复现的verified Issue。不得改协议、合同、类型豁免、主环境或数据库，不让用户排环境。交付提交和RP-QG.md/json及原始证据；若需变业务策略，停对应修复并报告。与reviewer写入区独立。

当前RP-Q5-FIXTURE与已执行的RP-1-REVIEW可并行。收到 reviewer 反例由原协议 writer 回修；质量与测试交付由不同执行主体复核。总控再核对累计候选与有效P5，协议契约合入前按工程规则安排人审，不能用双AI替代。T1未关闭，T2暂不分发。

### 已交付：协议开发（以下留作历史，不再分发）

> 请执行 `docs/task_specs/2026-10-09-research-package-rp1-protocol.md`。一个protocol writer，按最新origin/test建独立工作区，仅带入已批准合同输入提交`fc69e2c2bb7a0b742955e65b6db145b4201b4ba3`，记录之后HEAD为代码BASE_SHA。实现v2/v3分版本校验与科研结构保真，不改旧包/hash，不修数据库、规划/UI或共享环境，不形成真实首案包。按allowlist和DoD交付代码提交、RP-1-PROTOCOL.md/json与原始验证证据。不要自行宣布完整功能通过。

### 已验收：隔离环境准备（以下留作历史，不再分发）

> 请执行 `docs/task_specs/2026-10-09-research-package-rp-env.md`。只准备本任务独立的解释器、隔离 PostgreSQL 与真实内容存储运行条件，不改主库环境/产品、不碰共享业务数据，不生成 Package 或开展科研/比较。真实留证，未证不能报 ready；交付 RP-ENV.md/json 与脱敏重跑/清理办法，由用户带回总控。

协议/质量交付已收到；[RP-1-REVIEW](../../task_specs/2026-10-09-research-package-rp1-review.md) 若已执行则继续，[RP-Q5-FIXTURE](../../task_specs/2026-10-09-research-package-rp-q5-fixture.md) 现在可补分发。用户无需重复审核已确认合同，后续新合同问题或重要结果由总控另行呈交。

## 历史批次：T0/R1，三个独立核查交付

| 外部 agent            | 可直接分发的 task spec                                                     | 产出                                      | 写入边界        |
| --------------------- | -------------------------------------------------------------------------- | ----------------------------------------- | --------------- |
| A：源码与环境         | [RP-R1-A](../../task_specs/2026-10-09-research-package-r1-a-baseline.md)   | 最新基线、确定性测试、集成环境条件        | 仅 A 报告和日志 |
| B：真实案例与价值基线 | [RP-R1-B](../../task_specs/2026-10-09-research-package-r1-b-case.md)       | 首案/第二案候选、事前公平比较提案         | 仅 B 报告       |
| C：实现适配           | [RP-R1-C](../../task_specs/2026-10-09-research-package-r1-c-adaptation.md) | 最小缺口、接口提案、后续 writer/test 映射 | 仅 C 报告       |

三份 spec 可同时分发。各 agent 使用独立 checkout/worktree，避免共同施工目录；没有独立工作区时保持仓库只读，直接在回复里交付报告内容。创建分支/工作区按项目流程执行，不搬走用户现有未提交改动。

A 独占本批测试环境；B/C 不跑数据库、Provider 或测试重流程。只允许 A fetch；不 merge/rebase/reset/stash，不改全局 SSH/Git 配置。研究包代码施工开始前必须确认执行时最新 `origin/test`；本地盘点报告可先完成，但不能把缓存远端当已刷新基线。

## 可复制的分发消息

给 A：

> 请执行 `docs/task_specs/2026-10-09-research-package-r1-a-baseline.md`。你只负责源码与环境核查，遵守写入区与禁改清单，不修代码或改共享环境。交付报告、基线提交、实际测试与阻塞；不要替总控关闭 T0。没有独立工作区时返回报告内容，不写主工作区。

给 B：

> 请执行 `docs/task_specs/2026-10-09-research-package-r1-b-case.md`。你只负责真实研究候选和事前价值基线提案，区分真实来源与 Stub/Fixture，不形成 Package、不启动付费或真实实验。交付来源证据和可执行比较口径，最终选择/冻结由总控处理。没有独立工作区时返回报告内容。

给 C：

> 请执行 `docs/task_specs/2026-10-09-research-package-r1-c-adaptation.md`。你只读核查实现，交付 v3 与完整用户链的最小差距、接口提案、不重叠写入区和验证映射。不修产品代码、不改 PVD 工作、不创建新架构。没有独立工作区时返回报告内容。

每个 agent 的最终回复至少包括：执行的 spec、基线 SHA、产出路径或完整内容、结论与依据、实际验证和未验证范围、阻塞和建议。用户将回复或报告带回本聊天；不需要 agent 直接向本聊天发消息。

## 总控收口

### RP-ENV 支持交付已验收（2026-10-09）

隔离环境通过，总控已补正测试库名及候选源码绑定，原交付逐字节保留。当前无新的环境分发任务；RP-1-PROTOCOL 已提交，正在独立验收及质量解阻，通过后安排 T2。

T2 必须使用主仓 `tools/diagnostics/research_package_env/controller-handoff.ps1`，显式传入自身候选 `services/api` 路径；不使用原归档旧 handoff。独占已保留的 `rp_env_test` 与本地存储写入窗口；不停止/重启 tmpfs 容器。详细证据及重跑范围见 `docs/dev_plans/research-package/batch1/RP-ENV-review.md/json`。本环境验收不意味着集成、v3 或产品功能已通过，不请求用户排环境。

### A/B/C 接收后的补充批次（D 已完成并验收，以下保留历史）

A/B/C 调查已接收，用户已选择 SimCSE、本人担任贡献者并声明首次类似分享。建议将 [RP-R1-D 来源与形成前条件收口](../../task_specs/2026-10-09-research-package-r1-d-freeze.md) 分发给原 B agent；不重做全仓盘点，不创建 Package。

可复制消息：

> 请承接 `docs/task_specs/2026-10-09-research-package-r1-d-freeze.md`。用户已优先选择 SimCSE 并担任贡献者，接手者可从团队安排，此前未做类似分享。请在独立工作区核查固定来源、研究切片、8项答案及可访问业务范围中的形成/比较历史，保持比较方案为 proposal，不生成 Package、不运行复现或计时实验。交付 D-preformation.md/json 和明确未证条件，由用户带回总控冻结。

本总控按以下顺序整合：

1. 对账 A/B/C 的真实基线与来源；基线不同则只复核差异影响，不机械重跑全部任务。
2. 验证首案真实性与形成前条件，选定首案及 P1/P3 比较方案并正式记录；需要仅用户掌握的信息时明确请求该项，不让用户替工程排障。
3. 确认 R1 所需条件满足后关闭 T0；否则只记录哪项未证，不提前声明产品开发已完成。
4. 写下一批代码 spec，按真实根因冻结允许路径、API/共享类型 owner、依赖、有效测试和回退方案。候选划分是协议、规划语义与前端可读视图；可并行部分由本轮证据决定。
5. 重要施工完成后安排与该施工者不同的外部验收 agent；验收只读，不替施工者修实现。验收包由本总控写，用户分发。
6. 本总控核对 diff 与证据，更新 TASKLIST。代码与测试通过、真实用户价值通过、正式环境已开放分别判断。

后续各代码任务按对应新spec的固定输入建立独立branch/worktree，并核对最新origin/test差异；不同 writer 不同时改共享文件。PR 合并与发布按照项目授权与流程处理，本批不自动合并产品 PR。

## 历史调查规格的授权边界

这批 spec 只授权各自报告和规定的只读/确定性验证，不授权 v3 代码修改、数据库写入、形成真实 Package、付费模型调用、生产执行或发布。下一批施工必须拿到对应新 spec，不能从“后续建议”自行开工。
