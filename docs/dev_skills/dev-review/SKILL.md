---
name: dev-review
description: >-
  开发者进行代码审查或验收检查时触发。当用户说"review PR"、"code review"、
  "验收"、"检查 PR"、"能不能合"、"review my changes"、"帮我 review"、
  "check if ready to merge"时使用本 skill。
  按 PR 验收清单逐项检查，判断是否可以合入。
  即使用户只说"看看这个 PR"，只要是代码审查场景就应该触发。
---

# Dev: PR Review, Acceptance & Post-Merge Notify

## When to use

开发者需要审查 PR、检查代码变更是否满足验收标准，或在确认 merge 后通知相关方。

## Rules source

Read before reviewing:

- `spec/collaboration/04_施工验收检查.md` -- review checklist and acceptance criteria
- `spec/collaboration/03_任务推进与交接.md` -- collaboration boundary, document sync rules
- `docs/task_specs/README.md` -- task spec 索引（验收时确认 spec 边界）

## Step 1: Review

1. Read the PR description and linked Issue / task spec
2. Check the diff:
   - 允许/禁止路径是否违反 spec 边界
   - 跨模块修改是否合理
   - 硬编码学科/场景专用字段
   - 安全性（注入、密钥泄露）
   - 类型一致性（shared 类型是否同步）
3. 逐项验收 Issue / spec 中的「验收标准」
4. 输出 review 结论：通过 / 需改 / 阻塞，附逐项说明

## Step 2: Merge 确认

Review 通过后，确认是否 merge：
- [ ] CI 通过（或无 CI 时本地验证通过）
- [ ] 无未解决 comment
- [ ] PR description 中「关联任务」正确（Closes / Ref）
- [ ] Co-author 已在 commit 中标注

确认后执行 merge（squash 或 merge commit 按团队约定）。

## Step 3: 飞书通知（conditional: lark-cli available）

适用于：review 有变更请求时通知作者尽快修复，或 merge 后通知相关人员。

**Gate 0 — Tool check**: run `lark-cli --version`. If it fails, skip and remind user:
> 💡 未检测到 `lark-cli`，无法自动发送飞书通知。

If available, proceed:

### 3.1 查找通知对象

1. 从 PR 作者 / Issue assignee / spec 负责人提取 GitHub 用户名
2. **优先查本地映射**：读取 `docs/dev_skills/github-feishu-mapping.md`，按 GitHub 用户名匹配飞书 open_id
3. 本地映射未命中时，用 `lark-cli contact +search-user --query "姓名" --as user --format json` 查找对应飞书 open_id
4. **搜不到时**：用 AskUserQuestion 请用户提供飞书姓名，不要直接放弃
5. 找到新映射后，补充到 `github-feishu-mapping.md` 中，后续直接复用
6. 记录 `open_id`（`ou_xxx` 格式），供后续发消息使用

### 3.2 读取飞书进度表差异（merge 后适用）

```bash
lark-cli base +record-list --base-token "<token>" --table-id "<tbl>" --as user --limit 200
```

比对 PR 实际完成内容与进度表中任务状态，确定状态变更。

### 3.3 差异化消息模板

**Review 有变更请求（通知作者）**：
```text
📋 PR #{pr_number} Review 有变更请求
标题：{pr_title}
主要问题：{key_issue_summary}
详情：{pr_review_url}
请修复后重新推送，谢谢！
```

**给负责人（merge 后，任务 owner）**：
```text
✅ PR #{pr_number} 已合并
关联任务：{issue_title} (#{issue_number})
完成情况：{completion_summary}
建议操作：在飞书表格中更新状态为「{suggested_status}」
```

**给协作人 / reviewer（merge 后）**：
```text
📋 PR #{pr_number} 已合并，关联你协作的任务：
- {subtask_name}: {status_change}
如有后续阻塞请及时同步
```

### 3.4 发送消息

```bash
# lark-cli 发消息的正确用法（注意：不是 lark-cli message send）
# 方式一：发给个人（用 open_id）
lark-cli im +messages-send --user-id "ou_xxx" --text '消息内容' --as user

# 方式二：发到群聊（用 chat_id）
lark-cli im +messages-send --chat-id "oc_xxx" --text '消息内容' --as user

# 方式三：发送富文本（Markdown）
lark-cli im +messages-send --user-id "ou_xxx" --markdown '**标题**\n内容' --as user
```

> **常见错误**：不要使用 `lark-cli message send`（不存在）、`--receive-id-type`（不存在）、`--receive-id`（不存在）。正确子命令是 `lark-cli im +messages-send`，用 `--user-id` 或 `--chat-id` 指定接收者。

发送前必须确认用户同意（列出消息内容和对象，等用户确认后执行）。

### Gate: Feishu notification

- [ ] lark-cli 可用
- [ ] 通知对象已通过本地映射、`+search-user` 或用户提供确定
- [ ] 消息内容已生成并经用户确认
- [ ] 消息已通过 `lark-cli im +messages-send` 发送

## Output

Review 结论 + merge 状态 + 通知发送结果。
