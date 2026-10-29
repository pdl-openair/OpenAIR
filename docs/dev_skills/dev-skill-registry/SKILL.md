---
name: dev-skill-registry
description: >-
  开发者创建或修改 skills/ 下科研 Skill 后，需要检查、登记、同步或验证时触发。当用户说「注册 Skill」、
  「更新 registry」「同步 skill」「sync skills」「验证 Skill」「skill 管理」、
  「register_skills」「skill 自动注册」「check skill registry」时使用本 skill。
  引导执行 register_skills.py 的 check → update → sync，不手改 registry 或 .claude/skills/。
  即使用户只改了 SKILL.md，也应提醒跑 check。
---

# Dev: Skill 自动注册与管理

## When to use

- 新增或修改了 `skills/<pack>/<skill-name>/` 下的科研 / 业务 Skill
- 需要校验 `SKILL.md`、`skill.yaml` 与 `skills/registry.yaml` 是否一致
- 需要把 Skill 同步到 `.claude/skills/` 供 Claude Code 使用

**不在此 skill 范围：**

| 目录 | 处理方式 |
|------|----------|
| `docs/dev_skills/` | `python docs/dev_skills/sync_dev_skills.py` |
| `.claude/skills/`、`.cursor/skills/` 等 | 生成目录，勿手改；改 `skills/` 后走 sync |

## Rules source

Read before operating（以脚本 README 为操作主文档）：

- `scripts/skill_registry/README.md` — **首选**：`check` / `update` / `sync` 命令、边界、常见问题
- `skills/registry.yaml` — 字段与状态、生命周期（`draft` → `converting` → `ready` → `deprecated`）
- `skills/README.md` — 发现、同步、校验总流程

## Workflow

固定顺序，**不要跳过 `check`**：

```bash
python scripts/register_skills.py check --verbose
python scripts/register_skills.py update    # 仅 check 通过后；会改写 skills/registry.yaml
python scripts/register_skills.py sync      # 需要 Claude Code 使用最新 Skill 时；会改写 .claude/skills/
```

- `check`：只读，不写文件；失败时返回非 0，适合 CI
- `update`：按扫描结果重写 registry，保留已有 `owner`、`notes`、`fixture_status`、`display_name` 等人工字段
- `sync`：转发到 `sync_claude_skills.py`，将 `skills/` 同步到 `.claude/skills/`

依赖：`pip install pyyaml`（未安装时脚本会提示后退出）。

## Pre-checks

1. 改动在 `skills/<pack>/<skill-name>/`，而非 `.claude/skills/`
2. 目录含 `SKILL.md` 与 `skill.yaml`（`_template/` 不会注册）
3. `SKILL.md` 的 `name`、`skill.yaml` 的 `claude_name` 均等于父文件夹名
4. `skill.yaml` 中 `runtime` 为 `claude_code`，`skill_id` 全局唯一

## On failure

1. 根据 `check --verbose` 报错修源文件（缺文件、命名不一致、重复 `skill_id`、registry 未更新等）
2. 修完后重跑 `check`，通过后再 `update`，必要时 `sync`
3. 若同时改了 `docs/dev_skills/`，另跑 `python docs/dev_skills/sync_dev_skills.py`（与上表独立）

## Output

`check` 报告；若执行 `update` 则更新后的 `skills/registry.yaml`；若执行 `sync` 则 `.claude/skills/` 同步结果。
