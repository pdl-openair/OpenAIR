# 技术债账本（debt-ledger）

> 唯一债务账本：优先级/状态/认领/复审日期。守卫逼缩（placement 只许缩）+ 清理还债（本账本排期）形成闭环；每完成一项，mypy-scope / lint 基线对应收窄（棘轮推进）。

## 职责

登记全部已识别技术债，跟踪状态与复审，驱动常态化清理（每周固定清理槽位；首轮不自动化，避免无人在环范围失控）。

## 列定义

| 列 | 含义 |
|---|---|
| ID | 债务条目编号（D-xxx） |
| 优先级 | P0（小而独立，立即修）/ P1（本轮）/ P2 / P3（常态化） |
| 状态 | open / in-progress / done / blocked |
| 认领 | 负责人或批次/清理轮次 |
| 复审日期 | 到期必须重评（防"登记=遗忘"） |
| 条目 | 债务描述与验收口径 |

## 初版条目（2026-09 首轮，源自重构前现状分析 §5）

| ID | 优先级 | 状态 | 认领 | 复审日期 | 条目 |
|---|---|---|---|---|---|
| D-001 | P0 | open | 首轮 R1 | 2026-10 | 点名违规：`services/api/app/execution/agent_service.py` 静默吞异常+死条件；`routes/tasks.py` 私有导入与生产 assert；防幻觉正则双抄抽取为共享模块（R2） |
| D-002 | P1 | open | 首轮 R3 | 2026-10 | `routes/tasks.py` SSE 生成器 ~350 行领域逻辑抽出（净 -350 行，placement 放行且示范守卫价值） |
| D-003 | P1 | open | 首轮 R4 | 2026-10 | STATUS.md / CHANGELOG.md 内容刷新（机制已立 SPEC-HYGIENE-01，内容基于 git log 证据补齐） |
| D-004 | P2 | open | 常态化 | 每季 | `task_store.py` legacy 7 路由模块直连改走 canonical；WorkbenchPage.tsx 拆分；前端测试恢复 |
| D-005 | P3 | open | 常态化 | 每季 | 上帝模块分解（`research_execution_decision_service.py`、`research_orchestration_repository.py` 等）；255 处宽泛 except 逐步收窄；>800 行其余 30+ 文件 |
| D-006 | P3 | open | 常态化 | 每季 | 夜间无人值守流水线按思路重建（基于 verification/suites 布局重写 verifier，非照搬旧实现；裁决点 7） |
| D-007 | P1 | in-progress | orchestrator | 2026-10-24 | tg 线 SPEC-TEST-02 接线。**sync 批已接线（2026-09-26）**：guard 两步随配方③融合进 required-checks.yml test job；组件根扩展 `scripts/gates`＋配对 suite.yaml 落盘；28 个二波 authority_refs 改写＋幻锚点修正——剩余量=最终 PR CI 观察守卫绿态（见 ledger/sync.md：批次 PR 上守卫将继承 test 分支自身的组件配对债务，需 orchestrator 与 tg 线裁定） |
| D-008 | P2 | open | 常态化 | 2026-11-07 | `WorkbenchPage.tsx` 存量 `react-hooks/set-state-in-effect` 与 `exhaustive-deps`（约 20 error / 19 warning）。不是用户可见的科研事实错误；禁止为过 lint 改中断/研究设计语义。下次真实改动相关 effect 时顺手收敛，或随 D-004 页面拆分一并处理。 |
| D-009 | P1 | open | 后续研究设计战役 | 2026-11-07 | 研究设计当前版本/确认只读/结构编辑/历史恢复的 **protected_outcome 仍有效**。已删除无证据能力的研究设计生命周期 mock 套件（历史目录已删除；夹具与真实露出时机脱节，红灯不构成产品否证）。还债=用真实 Workbench 用户路径重写组件 Suite，不得把过期 mock 测回当作回归。 |
| — | — | done | pre-flight | 2026-09-24 | 根目录残骸清理（tmp*.json / tmp*.md / failed_*.md，已归档至 personal 嵌套仓） |

首轮清理任务（R1-R4）为 `fix` 类触产品代码者，由 orchestrator 先建 GitHub issue、附本账本条目与设计档为验证证据、标 `status/verified` 后关联 PR——SPEC-PROC-02 自举示范。

## 战役债务（2026-09 AI4SE Harness 重构，①-⑥，B8 落盘）

> ①-⑥ 原散见 refactor-ledger 各批次段（B4 首登 ①②③、Wave 1 汇合增补 ④、B5 增补 ⑤⑥）；按 B8 规格 §3.7 正式落盘本账本，编号与战役内引用保持一致。明细证据见 [refactor-ledger](../../docs/dev_plans/2026-09-ai4se-harness/refactor-ledger.md) 与 [ledger/b8.md](../../docs/dev_plans/2026-09-ai4se-harness/ledger/b8.md)。

| 编号 | 优先级 | 状态 | 认领 | 复审日期 | 条目 |
|---|---|---|---|---|---|
| ① | P1 | in-progress | orchestrator | 2026-10-24 | tg-sync：**sync 批主体已消化（2026-09-26）**——test 漂移 38 提交并入重构分支，guard 两步接线（配方③融合），`scripts/gates/tests/` 放置冲突以组件根扩展处置（PRODUCTION_COMPONENT_ROOTS＋scripts/gates，配对 suite.yaml 落盘，非豁免）；剩余=最终 PR 观察 test 分支真实绿态（注：test 自身推送对守卫亦红——组件配对债务在 test 侧存量，见 ledger/sync.md §0/偏离登记） |
| ② | P2 | open | 后续批次/tg sync | — | ci-parity：unit-test deselect/排除清单收回（re-admission 探针非阻塞，CI 实证 7/7 文件可收回；225 节点跟踪 issue 已开，逐文件 checkbox 收回） |
| ③ | — | done | B5 | — | required-checks 第 5 条 P0 死节点（=D-8 红归因根因）：c7b88748 换为存活节点，已闭合 |
| ④ | — | done | B8 | — | gate-hardening：placement 改名基线取旧路径行数（F3）、run_local staged 两点式补检（F4）、豁免 review-date 过期校验 fail-closed（F7）——B8 随批修复，证据 [ledger/b8.md](../../docs/dev_plans/2026-09-ai4se-harness/ledger/b8.md) |
| ⑤ | — | done | B8 | — | mypy 相对路径：mypy_gate root/config/python 全部脚本内自锚定（abspath），workflow 侧 `$PWD` 绝对化保留为双保险——B8 关闭，证据 ledger/b8.md |
| ⑥ | P0 | in-progress | 人工（orchestrator 上报用户） | 2026-10-08 | secret-rotation：gitleaks 全史扫描 21 条疑真的人工轮换（reference-api.md 两枚 JWT 已脱敏、吊销仍待人工；dev 本地 `.agents/skills/` 镜像同对 JWT，dev 推送前处置）——逐条清单 ledger/b5.md §10.5.1 |
| ⑦ | P3 | open | 后续批次 | 2026-10-24 | gate-hardening 残留（B8 评审 F4）：hook_guard 分段器对命令替换类元字符不感知——`$(...)`、反引号、圆括号子 shell 内的被禁形态放行（实测 `git add $(echo -A)` ALLOW；B8 规格处方边界内的既有逃逸类，非本批回归）；修复＝分段器增命令替换/子 shell 感知＋误拦对照，与 ⑧ unit-test 分片并行候选同批评估 |
| ⑧ | — | done | orchestrator（D-21 提前） | — | unit-test 分片并行（D-18 登记，sync 批落盘）：**2026-09-27 经用户裁决（D-21）提前至最终 PR #523 落地**——`unit-test-shard` matrix ×6（非必需名单）＋`unit-test` 聚合 job（必需 check 名不变，B7 清单零变更）；驱动/聚合器 `run_unit_suite.py run --shard i/k`＋`merge`（缺片/坏 json/编号不稠密一律 fail-closed 红）；真实 selection 实测 345 文件→51 units、负载 58×3+57×3，预期 wall ~50min→~15min；证据 [ledger/debt8-shard.md](../../docs/dev_plans/2026-09-ai4se-harness/ledger/debt8-shard.md) |

## 登记规则

- 新债入账必须给优先级与复审日期；豁免（placement-exemptions，B1 交付）每条带理由+复审日期，逾期未复审即 CI 报警。
- 完成一项 → 状态 done + 对应棘轮基线收窄，同 PR 记录证据链接。

## 防什么失效模式

- "登记=遗忘" → 复审日期列 + 指针/CI 校验日期未过期。
- 债务中心持续吸债 → placement guard 只许缩 + 本账本对账。
- 一次性大清理不可持续 → 常态化槽位（每周一次 agent 清理任务，人确认后触发）。

## 不负责什么

- 规则正文 → 各域文件；豁免登记表 → `spec/enforcement/`（placement-exemptions.yml，B1 交付）
