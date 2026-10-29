# 编码规则（coding-standards）

> 强制层标注见每条；工具配置（ruff.toml、mypy.ini、eslint.config.ts 等）是本文件的**投影**——冲突=投影 bug。
> 增量原则：门禁只对**新增/修改行**报红（diff 行级过滤），存量永不因新规则爆红；存量收敛走 [governance/debt-ledger.md](../governance/debt-ledger.md)。

## 职责

规定新增代码必须满足的可机判质量底线，并声明工具投影关系。

## 适用场景

写或修改任何 Python / TypeScript / React 代码时；评审代码体量与异常处理时。

## 必须

- **[SPEC-CODE-01｜L1（L0 镜像）｜diff_gate + ruff]** 新增 Python 代码过 ruff；禁止新增静默吞异常（S110 `except: pass`、S112、BLE001 宽泛 `except Exception` 吞掉）。历史 255 处宽泛 except 是存量，不新增即可，收敛进债务账本。
- **[SPEC-CODE-02｜L1｜ruff C901/PLR0915]** 新增函数圈复杂度 ≤15、单函数语句数 ≤100。依据：本仓合格新代码最长约 117 行/函数贴近事实标准；范例仓"函数≤50 行"绝对值误报率高，不采纳。
- **[SPEC-CODE-03｜L1｜tsc + eslint（增量）]** 新增 .ts/.tsx 过 `pnpm --filter @openair/web typecheck` 与 eslint（typescript-eslint recommended + react-hooks）。tsc 此前无处运行，此条补上。
- **[SPEC-CODE-04｜L1｜格式检查（增量）]** 新增/改动文件格式合规（ruff format / prettier）。格式漂移会污染 diff、掩盖真实变更。
- **[SPEC-TYPE-01｜L1｜mypy_gate + scope 棘轮]** mypy 豁免清单（spec/enforcement/ 下 B1 交付的 mypy-scope.txt）外文件必须过 mypy；清单只许缩不许涨，清理过的文件永不再入。
- **[SPEC-PLACE-01｜L1（L2 豁免）｜placement_guard]** 巨物模块棘轮：merge-base 时 >800 行的模块，PR 净变更 ≥0 即拦（净减才放行）；豁免须登记 spec/enforcement/ 下 B1 交付的 placement-exemptions.yml（每条带理由+复审日期，逾期未复审 CI 报警）。防上帝模块持续吸债（现状：>800 行模块 30+ 且加速增长）。
- **[SPEC-COV-01｜L1｜diff_gate coverage 模式]** 产品代码新增/修改行 diff-coverage ≥50%。阈值刻意低——覆盖率是**下限保险非质量证明**（承测试三部曲立场：测试有效性靠评审与验收清单，不靠覆盖率数字）。
- 平台保持领域无关：领域逻辑（materials、CS、OpenTrajectory 等）必须经 skills/ 与 schemas/ 承载，不得硬编码为平台唯一路径。〔L4 评审〕
- 字段/schema 变更同步四处：`packages/shared/`（TS 类型）、后端 Pydantic 模型、fixtures、相关 API 路由。〔L4 评审 checklist〕

## 禁止

- **[SPEC-GEN-01｜L0｜hook_guard]** 禁手改生成物目录（`.claude/skills/`、`.cursor/`、`.trae/`、`.qoder/` 等）；改源（`skills/`、`docs/dev_skills/`）后跑同步脚本。
- 新工作禁止进 `mocks/`——用 `fixtures/`（录制的真实运行样本，不是随机数据）。〔L4 评审〕
- 禁止扩展已废弃层：`services/agent-runner/`、`packages/skill-adapters/`、`packages/mcp-adapters/`。〔L4 评审〕
- 命名品味、错误信息质量、领域语义正确性**不配机器强制**（SPEC-KNOW-01，理由见 [code-quality-gates.md](code-quality-gates.md#诚实分界)——linter 误报成本高于收益，机器判不了语义）。

## 防什么失效模式

- 宽泛 except 与新增同步增长（246→255 处/4 天的实证）→ SPEC-CODE-01 增量拦截。
- 函数体量失控、职责边界糊掉 → SPEC-CODE-02（对位 `collaboration/02_工程实现与施工守则.md` 的"函数变长先查职责边界"——原只有信号无强制）。
- 前端类型检查"明天再上" → SPEC-CODE-03 把 typecheck 拉进门禁。
- 格式漂移掩盖真实 diff → SPEC-CODE-04。

## 同步点

- 工具配置变更（ruff.toml / mypy.ini / eslint.config.ts）必须与本文件同 PR 或紧随对账；`enforcement/enforcement-matrix.md` 对应行同步。
- 阈值调整走 PR：新增自动规则必须在 enforcement-matrix 登记失效模式与预估误报成本。

## 不负责什么

- 门禁层级定义与自动/人工分界 → [code-quality-gates.md](code-quality-gates.md)
- 测试资产放置与测试门禁 → [../testing/README.md](../testing/README.md) 与 B4 的 testing-gates
- 测试资产归属位规则正文 → [../testing/OpenAIR%20测试代码架构.md](../testing/OpenAIR%20测试代码架构.md)（SPEC-TEST-02 锚点）
