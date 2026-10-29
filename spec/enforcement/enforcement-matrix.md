# 强制索引（enforcement-matrix）

> 规则→正文锚点→工具→强制层的**索引**，不是规则副本：正文只在锚点处存在，本表不复述条文（索引与正文不构成双源）。路径引用由指针完整性检查（SPEC-PTR-01）保活。
> 登记制：决定不配强制的规则必须写明理由（SPEC-KNOW-01 为范式）；新增自动规则必须登记失效模式与预估误报成本。

## 职责

登记全部强制规则的正文锚点、强制层、工具配置与交付归属；查"某条规则由什么工具强制、谁交付、强制到哪层"以本表为准。

## 列定义

- **规则**：一句话标识，链到**唯一正文锚点**。
- **强制层**：L0 本地软镜像 / L1 CI 硬门禁（唯一可靠权威）/ L2 流程状态机 / L4 人审兜底；定义见 [../foundations/code-quality-gates.md](../foundations/code-quality-gates.md#层级定义五层强制)。
- **工具交付**：工具/配置的交付批次（B1 静态门禁 / B3 流程资产 / B5 CI 接线 / tg 接线 / 已交付）。

## 总表（初版=设计 §5.1 全部行）

| 规则 ID | 规则（正文锚点） | 强制层 | 工具与配置 | 工具交付 | 防什么失效模式 |
|---|---|---|---|---|---|
| SPEC-SEC-01 | [禁止提交密钥/凭证](../foundations/security.md#必须) | L0+L1 | gitleaks：pre-commit（workspace）+ CI secret-scan job（`.gitleaks.toml`） | B1（config）+ B5（CI job） | 三层防线防"漏装钩子/绕过钩子/网页直改"任一环节 |
| SPEC-SEC-02 | [禁提交 .env*（example 除外）](../foundations/security.md#必须) | L0+L1 | gitignore 既有 + pre-commit `openair-gitleaks`（`.pre-commit-config.yaml` 实名 id）+ gitleaks | B1 | 多人 .env.* 混放根目录的现状风险 |
| SPEC-CODE-01 | [新增 Python 代码过 ruff（禁新增静默吞异常）](../foundations/coding-standards.md#必须) | L1（L0 镜像） | `diff_gate.py`（增量）+ ruff.toml | B1 | 246 处 `except Exception` 与新增同步增长（现状 §5） |
| SPEC-CODE-02 | [新增代码圈复杂度 ≤15、单函数语句数 ≤100](../foundations/coding-standards.md#必须) | L1 | ruff C901/PLR0915（diff_gate 增量） | B1 | 函数体量失控（D 级最大违反项） |
| SPEC-CODE-03 | [新增 .ts/.tsx 过 tsc + eslint](../foundations/coding-standards.md#必须) | L1 | `pnpm --filter @openair/web typecheck` + eslint 增量 | B1 | tsc 无处运行的前端事实 |
| SPEC-CODE-04 | [新增/改动文件格式合规](../foundations/coding-standards.md#必须) | L1 | diff_gate 增量格式检查（ruff format / prettier） | B1 | 格式漂移污染 diff、掩盖真实变更 |
| SPEC-TYPE-01 | [mypy 豁免清单外文件过 mypy；清单只许缩](../foundations/coding-standards.md#必须) | L1 | `mypy_gate.py` + mypy.ini + 豁免清单 mypy-scope.txt（本目录） | B1 | 类型检查永远"明天再上" |
| SPEC-PLACE-01 | [巨物模块棘轮：>800 行模块净变更 ≥0 即拦](../foundations/coding-standards.md#必须) | L1（L2 豁免） | `placement_guard.py` + placement-exemptions.yml（本目录） | B1 | 上帝模块吸债（断层 ②核心） |
| SPEC-COV-01 | [产品代码 diff-coverage ≥50%](../foundations/coding-standards.md#必须) | L1 | `diff_gate.py --coverage` + pytest-cov | B1（coverage 模式）+ B5（CI 接线） | "新功能一行没测"裸奔；阈值刻意低（下限保险非质量证明） |
| SPEC-TEST-01 | [PR 须跑后端单测套件](../foundations/testing-gates.md)（目录选择：`services/api/tests` 单测部分 + 组件 `**/tests/` + `verification/suites/`；集成除外） | L1 | required-checks `unit-test` job（基于现版扩展；execution-plans 机制未落地、不引入） | B5 | CI 精选测试覆盖面过窄（现版仅 5 条手挑 P0） |
| SPEC-TEST-02 | [新增测试资产必须落在规定的归属位](../testing/OpenAIR%20测试代码架构.md)（单组件→`<组件>/tests/`+suite.yaml；跨组件→`verification/suites/`；开放质量→`evals/`；共享设施→`testkit/`；禁散落生产目录与 scripts/ 下新增长期测试）——**强制层=待 tg 合入接线**（tg 分支自带 guard，本战役不自建、防双实现）；接线前以 L4 评审项承载 | L1（接线后）；接线前 L4 | tg 合入 sync 时接线：guard 入 CI + canary 补用例；[debt-ledger](../governance/debt-ledger.md) D-007 带复审日期 | tg（orchestrator 汇合落盘） | 测试资产散落、无主、自证 fixture 冒充行为证明——权威已立、强制缺位 |
| SPEC-PROC-01 | [PR 证据合同字段完整（含验证层级、CI run、影响面）](../workflows/pr-workflow.md#证据合同必填节权威清单逐字消费) | L2 | `validate_pr_evidence.py`（PR opened/edited 触发的 check） | B3 | "测试通过"被说成"完成"（六层级失真） |
| SPEC-PROC-02 | [fix 类且触产品代码的 PR 须关联 `status/verified` issue](../workflows/issue-workflow.md#必须遵守的硬门禁)（feat/refactor/chore/docs 不受此门禁；`no-issue-required` 为逃生门） | L2 | CI `issue-link` step（gh api 读 PR 提交类型+body 关联 issue 与标签） | B5 | 看到 bug 直接写码、不复现不验证/重复修复；同时不阻碍 feat/refactor 快速开发 |
| SPEC-PROC-03 | [commit 信息符合 `<type>(<scope>):` 且 AI 参与带 Co-authored-by](../collaboration/git_commit_rules.md) | L0+L1 | `check_commit_msg.py`（hook 即时 + CI 兜底） | B1 | 「更新测试」式无信息提交 |
| SPEC-PROC-04 | [变更风险分级：high（auth/token/schema/删除/契约）必人审](../workflows/review-workflow.md) | L2/L4 | `classify_change_risk.py` + CODEOWNERS + 分支保护 require review | B3（脚本）+ B5（CODEOWNERS+分支保护）；CODEOWNERS 归因修正自 B3（batch/b5-ci-wiring 规格 §2.2 已含 `.github/CODEOWNERS`，B6 对账+orchestrator 裁决） | 高危路径双 AI 闭环放行 |
| SPEC-PTR-01 | [spec/、入口文档、dev skills 内仓库路径引用必须可解析](../README.md) | L1 | [check_pointer_integrity.py](../../scripts/gates/check_pointer_integrity.py)（`scripts/gates/`） | 已交付（B2，含 pytest） | 入口腐化（dev-task-spec→已删目录的实证） |
| SPEC-GATE-01 | [每个门禁必须通过 canary（带病样本红、干净样本绿）](../foundations/code-quality-gates.md) | L1 | `pytest scripts/gates/canary -q` 常驻 CI job `gate-canary`（canary 目录在 `scripts/gates/canary/`，pytest 形态） | B5 | 门禁空转（verifier 前科）与"配置存在≠有效" |
| SPEC-GEN-01 | [禁手改 `.claude/skills/`、`.cursor/` 等生成物](../foundations/coding-standards.md#禁止) | L0 | hook_guard PreToolUse(Write\|Edit) 拦截 | B3（hooks 注册） | 生成物被改后与源漂移 |
| SPEC-GIT-01 | [禁 `--no-verify`、`git add -A/.`、force push、跳过 staged 复核](../workflows/development-workflow.md#禁止) | L0 | hook_guard PreToolUse(Bash) | B3 | 原子提交纪律失守 |
| SPEC-HYGIENE-01 | [release PR 必含 STATUS.md 或 CHANGELOG.md 变更](../workflows/pr-workflow.md#合并门禁) | L2 | required-checks step（PR 带 release 标签时校验 diff） | B5 | 状态文档 3 周零更新的纪律失守 |
| SPEC-KNOW-01（不配强制，理由） | [命名品味、错误信息质量、领域语义正确性](../foundations/code-quality-gates.md#诚实分界) | L4 | 无工具——人工/独立评审 checklist（诚实分界） | —（显式不配） | linter 误报成本高于收益；此类失效模式机器当前判不了 |

## 登记规则

- 新增/修订强制 → 本表加行/改行 + 正文锚点落位 + 交付归属标注，同 PR。
- 规则正文搬家 → 锚点链接同 PR 更新（指针检查保活）。
- 工具交付后 → "工具交付"列从批次标记改为已交付，并在 canary 断言其真实生效（SPEC-GATE-01）。

## 防什么失效模式

- 矩阵变成第二正文（双源再生）→ 本表只放一句话标识+锚点，复述条文=评审打回。
- 规则写了没工具（范例"函数≤50 行"教训）→ 登记制：交付列空悬的行在战役看板可见。
- 强制层虚标（写 L1 实则无 CI job）→ SPEC-GATE-01 canary 对"声称有强制"的规则逐条断言。

## 同步点

- 本目录新增机器可读强制文件（mypy-scope.txt、placement-exemptions.yml，B1 交付）→ 对应行登记文件名。
- 设计档 §5.1 若再修订 → 本表为唯一存续版本，设计档不再回写。

## 不负责什么

- 规则正文 → 各锚点文件
- 失效模式与债务排期 → [../governance/debt-ledger.md](../governance/debt-ledger.md)
