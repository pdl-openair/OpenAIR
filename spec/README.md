# spec/ — 工程规则与方法论唯一源

> spec/ 是 OpenAIR **工程规则与方法论的完全单源**：一条规则只在一处有正文。
> 入口导航（AGENTS.md、CONTRIBUTING.md）不是规则源；产品设计合同在 `docs/01_核心设计/`（描述产品怎么构成，不描述怎么开发本软件）。
> 本目录所有文件的仓库内路径引用由 `scripts/gates/check_pointer_integrity.py` 强制（SPEC-PTR-01，L1）。

## 目录

| 域 | 内容 |
|---|---|
| `foundations/` | 基础规则：编码、门禁与质量分界、安全、经典软工映射 |
| `workflows/` | 操作流程：本地开发、issue、PR、评审 |
| `governance/` | 治理：真相源矩阵、AI 协作、技术债账本 |
| `enforcement/` | 机器可读强制索引：enforcement-matrix（规则→正文锚点→工具→强制层） |
| `collaboration/` | 协作方法论（收编：原 产品开发经验 12 份 + 提交规范，见 `collaboration/00_README.md`） |
| `testing/` | 测试方法论（收编：测试体系 5 份，见 `testing/README.md`） |

## 按任务→读哪篇

| 你要做什么 | 先读 |
|---|---|
| 写/改任何代码 | [foundations/coding-standards.md](foundations/coding-standards.md) |
| 判断"要不要为 X 加一条自动检查" | [foundations/code-quality-gates.md](foundations/code-quality-gates.md) |
| 处理密钥、凭证、.env | [foundations/security.md](foundations/security.md) |
| 本地起环境、跑命令、决定文件放哪 | [workflows/development-workflow.md](workflows/development-workflow.md) |
| 建/推进 Issue、判断任务能不能开工 | [workflows/issue-workflow.md](workflows/issue-workflow.md) |
| 发 PR、写 PR 描述、证据合同 | [workflows/pr-workflow.md](workflows/pr-workflow.md) |
| Review 别人的 PR / 判断风险分级 | [workflows/review-workflow.md](workflows/review-workflow.md) |
| 两个文档/配置说法打架 | [governance/source-of-truth-matrix.md](governance/source-of-truth-matrix.md) |
| 多 IDE / AI 协作、hooks 分工、权限姿态 | [governance/ai-collaboration.md](governance/ai-collaboration.md) |
| 认领/登记/清偿技术债 | [governance/debt-ledger.md](governance/debt-ledger.md) |
| 查某条规则由什么工具强制 | [enforcement/enforcement-matrix.md](enforcement/enforcement-matrix.md) |
| 施工怎么做（Apply/Verify/脚本交付） | [collaboration/09_工程施工与脚本交付规范.md](collaboration/09_工程施工与脚本交付规范.md) |
| 任务推进、交接、协作责任 | [collaboration/03_任务推进与交接.md](collaboration/03_任务推进与交接.md) |
| 验收清单（应用后/测试后/E2E 前后） | [collaboration/04_施工验收检查.md](collaboration/04_施工验收检查.md) |
| 写 commit message | [collaboration/git_commit_rules.md](collaboration/git_commit_rules.md) |
| 测试资产责任、放置、组织 | [testing/README.md](testing/README.md)（四部曲入口） |
| 想知道某词汇对应哪条经典实践 | [foundations/classic-foundations.md](foundations/classic-foundations.md) |

## 元规则

- **spec 与工具配置冲突 = 投影 bug**：spec 定义意图，工具配置（ruff.toml、mypy.ini、eslint 等）是投影，执行事实以工具运行为准。三者不一致时修 spec 或修配置，走 PR（[governance/source-of-truth-matrix.md](governance/source-of-truth-matrix.md)）。
- **enforcement-matrix 是索引不是副本**：矩阵行引用正文锚点、不复述条文；索引与正文不构成双源。
- **决定不配强制的规则必须写理由**（登记制，见 `enforcement/enforcement-matrix.md`）。
- 新增/修订 spec 文件遵循 [`_template.md`](_template.md) 骨架，走 PR 并过指针检查与评审。

## 不负责什么

- 产品设计合同（研究生命周期、能力体系、Schema 合同等）→ `docs/01_核心设计/`
- 开发 skill（触发 + 索引）→ `docs/dev_skills/`
- 任务规格与设计档 → `docs/task_specs/`、`docs/dev_plans/`
- 测试 Suite 契约与运行 → `verification/suites/`（spec/testing 引用而不复制）
