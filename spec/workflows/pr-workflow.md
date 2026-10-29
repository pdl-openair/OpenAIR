# PR 工作流（pr-workflow）——证据合同与合并门禁

> PR 证据合同字段清单是**单一权威清单**：PR 模板（B3 升级）、校验脚本（validate_pr_evidence.py，B3 交付）、CI 接线（B5）三方消费同一清单，不得各自发明。

## 职责

定义 PR 必填证据字段、描述生成规则与合并门禁。

## 适用场景

发起 PR / 写 PR 描述；reviewer 核对证据完整性；CI 校验脚本维护。

## 证据合同必填节（权威清单，逐字消费）

1. **改了什么**
2. **没改什么**
3. **怎么验证**——必含一行 `验证层级: L<1-6>`（六层级词汇：已分析 / 已自测 / 单测通过 / 集成验证 / 预发验证 / 真实 E2E 已验证，定义见 [issue-workflow.md](issue-workflow.md)）
4. **影响面勾选**（shared types / fixtures / skill registry / schemas / 均不涉及）
5. **CI run 链接**
6. **风险分级**（high / medium / low，分级定义见 [review-workflow.md](review-workflow.md)）

**[SPEC-PROC-01｜L2｜validate_pr_evidence]** PR opened/edited 触发 check，校验上述字段**存在且格式合法**；字段真实性靠 L4 独立评审与抽查（自动检查只保证"记录存在"，不保证"内容为真"）。

## PR 描述生成规则（基于真实 diff）

- 必须基于真实 git diff 生成，不得凭任务描述猜；不夸大、不写 diff 中不存在的内容。
- 禁止空泛描述（"优化项目结构""完善文档体系"）；模板小节不适用写「无 / 暂无」，不凑数。
- **禁止内部代号当主语**：标题与"改了什么"面向未读过 task spec 的 reviewer——先写能力/模块（Workbench、文献检索、证据抽取…），内部编号（P07/P08b 等）只可作括号补充或出现在文件路径中。
- 「主要修改文件」按目录归类，不粘贴全量路径；整份描述控制在一屏内。
- 关联 issue：完全解决用 `Closes #xx`，部分/相关用 `Ref #xx`，拿不准默认 `Ref`。

## 合并门禁

- CI 全绿（含 required checks）；无未解决 comment。
- fix 类触产品代码：关联 `status/verified` issue（SPEC-PROC-02）。
- 风险分级 high 必人审（CODEOWNERS），见 [review-workflow.md](review-workflow.md)。
- Commit 带 Co-authored-by（AI 参与）——提交规范见 [../collaboration/git_commit_rules.md](../collaboration/git_commit_rules.md)。
- release PR 额外要求：必含 `STATUS.md` 或 `CHANGELOG.md` 变更（SPEC-HYGIENE-01）。

## 防什么失效模式

- "测试通过"被说成"完成"（六层级失真）→ 必填层级声明 + 脚本校验字段。
- PR 描述只有代号没有能力，reviewer 无法独立判断 → 禁代号当主语规则。
- 影响面瞒报（改了 shared 类型说"均不涉及"）→ 勾选项成为评审与 CI 抽查锚点。

## 同步点

- 字段清单变更 → 本文件 + PR 模板 + validate_pr_evidence + B5 CI 同 PR 对齐（清单以本文件为唯一源）。

## 不负责什么

- 风险分级定义与评审动作 → [review-workflow.md](review-workflow.md)
- issue 状态机与六层级定义 → [issue-workflow.md](issue-workflow.md)
