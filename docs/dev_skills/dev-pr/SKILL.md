---
name: dev-pr
description: >-
  开发者准备创建 Pull Request 时触发。当用户说"建 PR"、"PR description"、
  "写 PR body"、"create pull request"、"PR 文案"、"生成 PR 描述"时使用本 skill。
  基于真实 git diff 填充项目 PR 模板；标题与正文须用 reviewer 可读的能力描述，
  禁止以 P07/P08/P2.6b 等内部代号作主语。不自动创建 PR，除非用户明确要求。
---

# Dev: PR Description Generation

## When to use

开发者准备创建 PR，需要基于 diff 生成 PR description。

> 本地门禁提示：pre-commit 钩子经 `python scripts/gates/run_local.py` 运行，依赖 PATH `python` 装有 ruff/mypy——缺失时钩子降级为警告放行，CI 兜底强制（见 `CONTRIBUTING.md` 第 8 节）。

## Step 1: Read diff and extract changes

1. Run `git log <base>..HEAD --oneline` to see all commits
2. Run `git diff <base>...HEAD` to see full diff
3. From the diff, extract:
   - What changed
   - What did NOT change

## Step 2: Link GitHub Issues (conditional: required when gh CLI is available)

Generate the diff summary, then match open Issues in the repo to fill the「关联任务」section.

**Gate 0 — Tool check**: run `gh --version`. If it fails, skip this entire step and append a note to the PR description:
> 💡 未检测到 `gh` CLI，无法自动关联 Issue。安装方式：https://cli.github.com ，安装后可用 `gh issue list` 查看 Issue。
If it succeeds, **must** complete all sub-steps below before proceeding.

### Steps

1. **Fetch open Issues** (tool already confirmed at Gate 0):
   ```bash
   [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
   gh issue list --repo <owner/repo> --state open --limit 50 --json number,title,labels --output /tmp/gh_issues.json
   ```
   > PowerShell 编码问题：JSON 输出中文可能乱码，需要 `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8`，或用 `--json` 写文件后读取。
2. **Semantic matching**: compare PR diff summary with each Issue's title / labels:
   - Branch name or commit message contains Issue number → direct link
   - Issue title semantically matches PR changes → link
   - Multiple matches → list all, let user confirm
3. **Determine Ref / Closes**:
   - PR **fully resolves** all requirements in the Issue → `Closes #xx`
   - PR is **partial** or **related but not closing** → `Ref #xx`
   - When unsure, default to `Ref #xx` and remind user to decide
4. **Write** into「关联任务」section of the PR description

### Example output

```markdown
## 关联任务

Closes #12
Ref #13
```

### Gate: Issue linking confirmation

- [ ] gh CLI availability has been checked (Gate 0)
- [ ] Open Issues have been fetched and matched
- [ ] Ref / Closes judgment has been made for each match

**Only after confirming all items, proceed to Step 3.**

## Step 3: Link Feishu tasks (conditional: required when lark-cli is available)

Match PR changes against Feishu Bitable tasks and prompt user about completion status and next actions.

**Gate 0 — Tool check**: run `lark-cli --version`. If it fails, skip this entire step and append a note to the PR description:
> 💡 未检测到 `lark-cli`，无法自动关联飞书任务。安装方式：https://www.feishu.cn/feishu-cli ，安装后可读取飞书多维表格任务。
If it succeeds, **must** complete all sub-steps below before proceeding.

### Steps

1. **Locate target Bitable** (tool already confirmed at Gate 0):
   - Prefer project config (e.g. Feishu links referenced in `docs/task_specs/`)
   - User can provide Feishu Bitable URL directly
   - No clear target → skip and remind user to link manually
2. **Parse Wiki link and read table**:
   ```bash
   # Wiki token → Base token
   lark-cli wiki spaces get_node --params "@./tmp_params.json" --as user
   # Read field structure
   lark-cli base +field-list --base-token "<obj_token>" --table-id "<tbl...>" --as user
   # Read records
   lark-cli base +record-list --base-token "<obj_token>" --table-id "<tbl...>" --view-id "<vew...>" --as user --limit 200
   ```
   > PowerShell 注意事项：中文需设 `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8`；`--params` 用文件传参避免转义问题；字段名含空格时用字段 ID。
3. **Semantic matching**: compare PR diff summary with Feishu subtasks (subtask number + name + description).
4. **Output match results**, including:
   - Matched Feishu task rows (main task number, subtask number, subtask name, current status, assignee, acceptance criteria)
   - **Completion check**: verify each acceptance criterion against the PR changes
   - **Next action suggestions**:
     - Status「未开始」→ suggest updating to「进行中」
     - Status「进行中」and all acceptance criteria met → suggest updating to「待核查」
     - Acceptance criteria partially met → list remaining items
   - **How to update status**: prompt user to modify in Feishu, or provide `lark-cli base +record-upsert` command for confirmation

### Example output

```markdown
## 飞书任务关联

匹配到飞书任务：

| 子任务编号 | 子任务名称 | 当前状态 | 负责人 |
|-----------|-----------|---------|-------|
| S01-02-01 | 确定首批联调样例范围 | 待核查 | xxx |
| S01-02-02 | 准备正常流程样例 | 待核查 | xxx |

验收标准检查：
- [ ] S01-02-02: 正常流程样例数据尚未提交（fixtures/recorded_runs/ 下无新增）

建议：S01-02-02 完成后，在飞书表格中将状态从「待核查」更新为「已完成」。
```

### Gate: Feishu task linking confirmation

- [ ] lark-cli availability has been checked (Gate 0)
- [ ] Target Bitable has been located and records fetched
- [ ] Matching results and completion status have been reported

**Only after confirming all items, proceed to Step 4.**

## Step 4: Generate PR description

### Rules source

Read before generating:

- `spec/workflows/pr-workflow.md` -- how to read diff, output format
- `.github/pull_request_template.md` -- the actual template to fill

### PR 证据合同要点（会咬人的硬事实，权威见 pr-workflow.md）

- 正文六节必填：改了什么 / 没改什么 / 怎么验证（必含一行 `验证层级: L<1-6>`）/ 影响面勾选 / CI run 链接 / 风险分级——缺节或缺层级行，`pr-evidence` check 直接红。
- **fix 类 PR 禁用 `N/A（本地验证）` 逃生**：必须给 CI run 链接（B3 严读，validate_pr_evidence 拦截）。
- 链接首跑前不存在？正文后补即可：`pr-evidence` 工作流对 `edited` 事件 ~1min 重验（sync 批拆分后的新行为），**不必 close/reopen**。
- fix 类无 issue 时的逃生门：PR 打 `no-issue-required` 标签；否则须关联带 `status/verified` 标签的 issue（check_issue_link 拦截）。

### Style (concise-first)

- 整份描述控制在 1 屏（约 40 行）以内；能 5 字说清的不写 50 字
- commit log 已说的不重复；模板小节不适用写「无 / 暂无」，不要展开凑数
- 「主要修改文件」按目录归类，不粘贴全量路径
- 「风险」无风险直接写「暂无」，不要为了凑节数硬编风险

### 可读性：禁止内部代号当主语（必须）

PR 标题与「改了什么」面向 **未读过 task spec 的 reviewer**，不得假设对方懂内部编号。

**禁止：**

- 标题或正文 **以** `P07` / `P08` / `P2.6b` / `p08b-1c` 等 **单独概括**本次改动
- 用 `P07a–P07f` 列表代替能力说明
- 标题只写目录 slug（如 `p08b-1c live smoke`）而不写用户可见能力

**必须：**

- 先写 **Reviewer 能直接理解的能力或模块**（Workbench、Harness、文献检索、证据抽取…）
- 内部编号若保留，仅作 **括号补充**，或出现在「主要修改文件」路径中；**不得**作为第一句主语

**OpenTrajectory 对照（可类推其他 pack）：**

| 内部代号 | PR 中应写 |
|---|---|
| P07 链路 | 执行验证阶段：检索 → 阅读 → 证据模型 → 证据抽取 → 机制链 |
| P07a | 学术文献检索 |
| P07b | DeepXiv 渐进式阅读 |
| P07c | 证据节点抽取（含逐字引用校验） |
| P07d | 机制链合成 |
| P07f | 证据模型（EvidenceSchema）设计 |
| P08b-1c | bounded live smoke 编排（默认 dry-run，不触发真实 API） |
| P2.6b | Workbench 证据产物来源恢复（复制链接/来源信息） |

**标题示例：**

- ❌ `test(opentrajectory): add P07 live chain contracts`
- ✅ `test(opentrajectory): add execution-phase contract tests for live smoke chain`
- ❌ `feat(workbench): P2.6b evidence recovery and p08b-1c live smoke slice`
- ✅ `feat(workbench): evidence recovery and OpenTrajectory live smoke orchestrator`

**Step 4 出口 Gate：**

- [ ] 标题不含未解释的内部代号
- [ ] 「改了什么」每条可独立理解，不依赖 task spec 编号

### Co-author（必须）

PR 关联的 commit 必须在 commit message 中加 `Co-authored-by` 标记协作 Agent。

格式：
```text
Co-authored-by: qoder <qoder@users.noreply.github.com>
Co-authored-by: cursor <cursor@users.noreply.github.com>
Co-authored-by: claude-code <claude-code@users.noreply.github.com>
Co-authored-by: gpt <gpt@users.noreply.github.com>
```

规则：
- 根据实际使用的 AI coding 工具选对应行，可多个并列
- 由开发者在 commit 时自行加入，或由 Agent 在生成 commit message 时自动追加
- PR description 本身不写 co-author（已在 commit 中体现）

### Output

Filled PR template with: what changed, linked tasks (GitHub + 飞书), modified files, how to verify, impact checklist.

## Step 5: Assign Reviewer & 飞书通知（conditional: gh + lark-cli available）

PR 创建后，必须 assign 一名 reviewer（不能是自己），并通过飞书通知被 assign 的人。

### 5.1 确定 Reviewer

选择规则：
- 优先选改动目录的 CODEOWNER（如有）
- 其次选 spec / Issue 中标注的协作人
- 再次选团队中熟悉该模块的成员
- **不能 assign 自己**

### 5.2 Assign Reviewer（gh CLI）

**Gate 0 — Tool check**: run `gh --version`. If it fails, skip and remind:
> 💡 未检测到 `gh` CLI，请手动在 GitHub PR 页面 assign reviewer。

```bash
gh pr edit <pr_number> --add-reviewer <reviewer_github_username>
```

### 5.3 飞书通知 Reviewer（lark-cli）

#### 查找通知对象

1. 从 PR 作者 / Issue assignee / spec 负责人提取 GitHub 用户名
2. **优先查本地映射**：读取 `docs/dev_skills/github-feishu-mapping.md`，按 GitHub 用户名匹配飞书 open_id
3. 本地映射未命中时，用 `lark-cli contact +search-user --query "姓名" --as user --format json` 查找对应飞书 open_id
4. **搜不到时**：用 AskUserQuestion 请用户提供飞书姓名，不要直接放弃
5. 找到新映射后，补充到 `github-feishu-mapping.md` 中，后续直接复用
6. 记录 `open_id`（`ou_xxx` 格式），供后续发消息使用

**Gate 1 — Tool check**: run `lark-cli --version`. If it fails, skip and remind:
> 💡 未检测到 `lark-cli`，无法自动发送飞书通知，请手动提醒 reviewer。

If available:

1. 确定 reviewer 的飞书 user_id（从团队通讯录或项目配置获取）
2. 生成通知消息：

```text
📬 你被指派为 PR #{pr_number} 的 Reviewer
标题：{pr_title}
仓库：{repo}
链接：{pr_url}
提交人：{author}
请及时审阅，谢谢！
```

3. 发送：

```bash
# lark-cli 发消息的正确用法（注意：不是 lark-cli message send）
# 发给个人（用 open_id）
lark-cli im +messages-send --user-id "<reviewer_open_id>" --text '消息内容' --as user

# 发到群聊（用 chat_id）
lark-cli im +messages-send --chat-id "<chat_id>" --text '消息内容' --as user

# Markdown 富文本
lark-cli im +messages-send --user-id "<reviewer_open_id>" --markdown '**标题**\n内容' --as user
```

> **常见错误**：不要使用 `lark-cli message send`（不存在）、`--receive-id-type`（不存在）、`--receive-id`（不存在）。正确子命令是 `lark-cli im +messages-send`，用 `--user-id` 或 `--chat-id` 指定接收者。

发送前必须确认用户同意（列出消息内容和发送对象，等确认后执行）。

### Gate: Reviewer assignment & notification

- [ ] Reviewer 已确定（非自己）
- [ ] PR 已 assign reviewer
- [ ] 飞书通知已发送（或已跳过并告知原因）
