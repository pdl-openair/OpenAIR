# Agent 接入指南（Codex / opencode / dsh / Claude Code / WorkBuddy）

本目录是标准的 **AgentSkills 布局**（`SKILL.md` + `scripts/` + `references/` + `examples/`），多数 agent 直接认这个结构。

```
flux-experiment/
├── SKILL.md# 技能主文件，YAML frontmatter 的 name/description 是触发关键
├── scripts/flux_api.py     # 唯一权威实现，纯标准库
├── references/api.md        # 字段契约与已知歧义（按需加载）
├── examples/recipes.json    # 可直接跑通的示例方案
├── config.local.json        # 凭据（禁止提交）
└── .gitignore
```

## 放置位置

| Agent | 位置 | 说明 |
| --- | --- | --- |
| Codex | `~/.codex/skills/flux-experiment/` 或项目内 `.codex/skills/` | 放用户级则所有项目可用|
| opencode | `~/.config/opencode/skill/flux-experiment/` 或项目内 `.opencode/skill/` | 注意 opencode 用单数 `skill/` |
| Claude Code | `~/.claude/skills/flux-experiment/` 或 `.claude/skills/` | |
| dsh | `~/.dsh/skills/flux-experiment/` 或项目内 `.dsh/skills/` | 按 harness 实际的加载路径调整 |
| WorkBuddy | `~/.workbuddy/skills/flux-experiment/` | 放用户级则跨项目可用 |

Windows 快速放置（PowerShell，注意 dsh/opencode 的目录名可能是单数）：

```powershell
$src = "C:\Users\alex\WorkBuddy\flux\skills\flux-experiment"
$targets = @(
  "$HOME\.codex\skills",
  "$HOME\.config\opencode\skill",
  "$HOME\.claude\skills",
  "$HOME\.dsh\skills",
  "$HOME\.workbuddy\skills"
)
foreach ($t in $targets) {
  New-Item -ItemType Directory -Force -Path $t | Out-Null
  Copy-Item "$src\SKILL.md" "$t\" -Force
  New-Item -ItemType Directory -Force -Path "$t\scripts","$t\references","$t\examples" | Out-Null
  Copy-Item "$src\scripts\*" "$t\scripts\" -Force
  Copy-Item "$src\references\*" "$t\references\" -Force
  Copy-Item "$src\examples\*" "$t\examples\" -Force
  Copy-Item "$src\config.local.json" "$t\" -Force
  Copy-Item "$src\.gitignore" "$t\" -Force
  Write-Host "installed -> $t"
}
```

`config.local.json` 属凭据文件。跨机部署时改为设置 `FLUX_API_KEY` 环境变量，不要把 key 烘进包里。

## 如果目标 agent 不支持 skills 目录

退化方案：把 `scripts/flux_api.py` 当普通 CLI 用，把下述内容作为系统提示 / `AGENTS.md` 片段：

```markdown
## 云实验平台（flux）
创建并启动试验任务一律使用 `scripts/flux_api.py`，不要自己拼 HTTP 请求体。
1. `flux_api.py probe` 先确认连通；不通就报告，不要重试。
2. `flux_api.py validate --recipes-file <方案.json>` 校验，有 ERROR 先修。
3. `flux_api.py create --task-name "<名称>" --recipes-file <方案.json> --dry-run` 让用户确认。
4. 确认后加 `--execute` 创建，记录 taskId/taskCode。
5. 用户同意后再 `flux_api.py start --task-id <id> --execute`。
加 `--json` 可获得机器可读输出。禁止手工转义 paramValue 里的方案 JSON。
```

## 给 agent 的三条硬约束

1. **不手工转义 `paramValue`。** 方案数组必须由脚本 `json.dumps` 成字符串；这是两层 JSON，手写必错。
2. **不擅自改 `templateId` / `paramName`。** 二者是固定值。
3. **不静默 start。** 创建会占用平台队列资源，`start` 前必须让用户确认。

## 冒烟自检

在能访问内网的环境执行，确认全链路可用：

```bash
python scripts/flux_api.py probe
python scripts/flux_api.py validate --recipes-file examples/recipes.json
python scripts/flux_api.py create --task-name "smoke-test" --recipes-file examples/recipes.json --dry-run
python scripts/flux_api.py start --task-id 0 --dry-run
```

前两条离线可跑；后两条不带`--execute` 也不会发请求。若`probe` 超时，说明当前不在内网，属预期行为。