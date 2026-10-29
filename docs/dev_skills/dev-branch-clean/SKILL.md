---
name: dev-branch-clean
description: >-
  清理 git 分支时触发。当用户说"清理分支"、"删掉旧分支"、"branch clean"、
  "分支太多了"、"清理 worktree"时使用本 skill。
  基于 合并状态(含 patch 等价识别 squash-merge)+ 年龄 + 分叉关系,
  对本地和远端分支分级:先出报告,用户确认后批量删;有独有提交的分支只出报告不删。
---

# Dev: Branch Clean(分支分级清理)

## When to use

本地/远端分支堆积,需要按统一策略清理。**永远先 report 后 execute,execute 前必须拿到用户对报告的明确确认。**

## 分类标准(由脚本判定,不要人工重算)

| 判定 | 条件 | 动作 |
|---|---|---|
| PROTECTED | `dev`/`main`/`master`/`test`;`release/*`、`keep/*`、`archive/*`、`backup/*` 前缀;有 open PR;任一 worktree 正在 checkout | 永不删 |
| DELETE | 已完全合入 base:祖先合并(`--merged` 能看到),或所有独有 commit 与 base **patch 等价**(squash-merge 的分支`--merged` 看不出来,靠这条兜住) | 报告确认后删(远端 `push --delete`、本地 `branch -d`) |
| REVIEW | 有 base 不存在的真实独有提交 | **只进报告,绝不自动删** |

配套动作:陈旧 worktree(`.nightly_worktrees/`、`.claude/worktrees/` 下、分支超期无活动或已分类 DELETE)先移除,再删分支(顺序不能反,worktree 占用的分支删不掉)。

## Step 1: 生成报告

```bash
python docs/dev_skills/dev-branch-clean/scripts/branch_clean.py report
```

默认 `--base dev --stale-days 21`,先 `git fetch --prune` 校准,产出(默认在 `.git/branch-clean/`,不进版本库):

- `plan-<ts>.json` — 机器可执行清单(execute 只认这个文件)
- `report-<ts>.md` — 人读报告:P0 删除清单、worktree 清单、REVIEW 明细、保护名单

常用参数:`--extra-keep 'team-a/*'`(临时加保护)、`--no-fetch`(离线)、`--no-gh`(不用 gh 查 open PR,此时 open-PR 保护不生效,报告会标注)。

## Step 2: 展示报告并取得确认(Gate)

**STOP — 未完成本步不得 execute。**

1. 向用户完整展示:远端/本地删除数量、worktree 移除清单、REVIEW 数量及名单
2. 用户明确同意后才进入 Step 3;用户要求改动 → 调整参数重跑 report
3. REVIEW 分支逐个询问用户意愿(见 Step 4),但它们不在本次 execute 范围内

## Step 3: 执行

```bash
python docs/dev_skills/dev-branch-clean/scripts/branch_clean.py execute --plan <plan路径> --yes
```

脚本会逐条重新校验(分支仍存在、SHA 未移动;移动过则重新做合并判定,不满足即跳过),批量 `push --delete`(每批 40 个),本地只用 `branch -d`(拒绝强删未合并分支)。执行后核对输出的 FAIL/SKIP 行,异常的向用户报告。

## Step 4: REVIEW 分支处理(沿用团队惯例)

对 REVIEW 分支逐个与用户确认:

- 确认有价值 → 改名收进 keep 盘点前缀(仓库已有 `keep/inventory-20260806/*` 惯例):

  ```bash
  git push origin <branch>:refs/heads/keep/inventory-<日期>/<branch>
  git push origin --delete refs/heads/<branch>
  ```

- 确认废弃 → 单独 `git push origin --delete refs/heads/<branch>`(本地 `git branch -D`)
- 拿不准 → 保留不动,留到下次盘点

## 防再堆积(收尾建议)

- GitHub 仓库 Settings → General → Pull Requests → 勾选 **Automatically delete head branches**(改动 repo 设置需用户另行确认,不要默默改)
- 大批量清理后提醒用户:`git fetch --prune` 同步其他机器
