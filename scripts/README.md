# scripts

项目工程脚本（同步 / 构建 / 本地联调）。

## 当前脚本

| 文件 | 用途 |
|------|------|
| [sync_claude_skills.py](sync_claude_skills.py) | 将标准结构 `skills/<domain>/<skill-name>/`（含 `SKILL.md`）同步到 `.claude/skills/`（不调用 Claude、不做校验） |
| [register_skills.py](register_skills.py) | OpenAIR Skill 自动注册入口：`check`（扫描 + 校验 + 与 registry 比对，只读）、`update`（重写 `skills/registry.yaml`）、`sync`（转发到 sync_claude_skills.py）；仅服务 `skills/`，不扫描 `teamwork_skills/` 与 `docs/dev_skills/`；详见 [skill_registry/README.md](skill_registry/README.md) |
| [validate_workflow_integration.py](validate_workflow_integration.py) | 只读检查指定 workflow pack 的配置、Skill 映射、artifact handoff 和 runtime contract；不改文件、不接入运行时 |

在**仓库根目录**执行：

```bash
# 同步业务 skills 到 .claude/
python scripts/sync_claude_skills.py

# 注册 / 校验 skills/ 下 OpenAIR Skill
python scripts/register_skills.py check

# 检查一个 workflow pack；专用组合 map 通过 --composition-map 指定
python scripts/validate_workflow_integration.py --pack <pack>
```

> 面向新人的环境体检脚本已移至 [`docs/dev_skills/doctor/`](../docs/dev_skills/doctor/)，作为 `doctor` dev skill。
> 仓库根提供双击入口 [`setup.bat`](../setup.bat)。

新增脚本时在本 README 补充一行说明与调用方式。
