# Skill 自动注册工具

这个工具用于管理仓库 `skills/` 下的科研 / 业务 Skill。

它做三件事：

1. 检查 Skill 是否按规范编写；
2. 更新 `skills/registry.yaml`；
3. 同步 Skill 到 `.claude/skills/`，供 Claude Code 使用。

当前只处理标准目录：

```text
skills/<domain>/<skill-name>/
```

不处理：

```text
teamwork_skills/
docs/teamwork_skills/
docs/dev_skills/
.claude/
.cursor/
.trae/
.qoder/
```

---

## 什么时候用

| 场景 | 执行命令 |
|---|---|
| 新增或修改 Skill 后，先检查是否合规 | `python scripts/register_skills.py check --verbose` |
| 检查通过后，更新登记表 | `python scripts/register_skills.py update` |
| 需要让 Claude Code 使用最新 Skill | `python scripts/register_skills.py sync` |
| 接入 workflow 前做只读一致性检查 | `python scripts/validate_workflow_integration.py --pack <pack>` |

---

## 新增 Skill 的标准流程

```bash
# 1. 新建 Skill 目录
skills/<domain>/<skill-name>/

# 2. 添加必要文件
SKILL.md
skill.yaml

# 3. 校验，只读，不改文件
python scripts/register_skills.py check --verbose

# 4. 更新 registry，会改 skills/registry.yaml
python scripts/register_skills.py update

# 5. 同步到 Claude Code，会改 .claude/skills/
python scripts/register_skills.py sync
```

建议顺序固定为：

```text
check → update → sync
```

不要跳过 `check`。

新增或重做 workflow 时，再运行 `validate_workflow_integration.py`。它只读检查 pack 配置、Skill 映射、spec、handoff 和 runtime contract；不会修改 registry，也不会替代真实 `/draft -> /confirm -> /start` 验证。历史 `claude_code` Skill 缺少 durable contract 时会给 warning；脚本或 adapter Skill 缺少 contract 会失败。

---

## 三个命令分别做什么

### `check`

```bash
python scripts/register_skills.py check --verbose
```

只读检查，不写文件。

检查内容包括：

- 是否存在 `SKILL.md`；
- 是否存在 `skill.yaml`；
- `SKILL.md` 里的 `name` 是否等于父文件夹名；
- `skill.yaml.claude_name` 是否等于父文件夹名；
- `skill_id` 是否唯一；
- `runtime` 是否为 `claude_code`；
- `skills/registry.yaml` 是否和当前目录扫描结果一致。

`check` 发现错误或 registry 不一致时会返回非 0，后续可接入 CI。

---

### `update`

```bash
python scripts/register_skills.py update
```

根据当前 `skills/` 目录重写：

```text
skills/registry.yaml
```

它会保留 registry 中已有的部分人工字段，例如：

- `owner`
- `notes`
- `fixture_status`
- `display_name`

其他字段以 `skill.yaml` 和扫描结果为准。

---

### `sync`

```bash
python scripts/register_skills.py sync
```

把标准 Skill 目录同步到：

```text
.claude/skills/
```

示例：

```text
skills/general/fixture-recorded-run-creation/
→ .claude/skills/fixture-recorded-run-creation/
```

注意：`sync` 会重建同名目标目录。`.claude/skills/` 是生成目录，不应提交到仓库。

---

## Skill 目录必须长什么样

标准结构：

```text
skills/<domain>/<skill-name>/
  SKILL.md
  skill.yaml
  specs/        # 可选
  examples/     # 可选
```

要求：

- `<skill-name>` 使用小写英文、数字和连字符；
- `SKILL.md` 的 `name` 必须等于 `<skill-name>`；
- `skill.yaml.claude_name` 必须等于 `<skill-name>`；
- `skill.yaml.skill_id` 使用小写英文、数字和下划线；
- `skill.yaml.runtime` 必须是 `claude_code`。

---

## 这个工具不做什么

- 不扫描 `teamwork_skills/`；
- 不扫描 `docs/teamwork_skills/`；
- 不扫描 `docs/dev_skills/`；
- 不扫描 `.claude/`、`.cursor/`、`.trae/`、`.qoder/`；
- 不自动修复 `SKILL.md` 或 `skill.yaml`；
- 不把模板目录 `skills/_template/` 注册成正式 Skill；
- 不做 Skill 市场、UI 或复杂能力治理。

---

## 常见问题

### `check` 失败怎么办？

先看错误信息。常见原因：

- 缺少 `skill.yaml`；
- `SKILL.md` 的 `name` 和文件夹名不一致；
- `skill.yaml.claude_name` 和文件夹名不一致；
- `skill_id` 重复；
- registry 还没更新。

修完后重新执行：

```bash
python scripts/register_skills.py check --verbose
```

### 什么时候执行 `update`？

当 Skill 文件已经检查通过，但 registry 和当前目录不一致时执行。

### 什么时候执行 `sync`？

当你需要让 Claude Code 使用最新 Skill 时执行。

---

## 依赖

需要安装 `pyyaml`：

```bash
pip install pyyaml
```

如果没安装，脚本会给出提示后退出。
