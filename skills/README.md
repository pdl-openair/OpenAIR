# OpenAIR Skills（唯一人工维护源）

本目录是 **Skill 定义与说明的唯一人工维护根**；Claude 运行时可消费的副本在 `.claude/skills/`（由 `scripts/sync_claude_skills.py` 同步，勿手改副本）。

## 组织方式

- 按**能力域**分子目录（如 `common_research/`、`materials/`），**不按**四阶段硬切目录；四阶段出现在各 Skill 的 metadata（如 `skill.yaml` 的 `stages`）中。  
- `skill_management/`：注册、发现、合规与样例回放的**正式能力域**（与「写业务 Skill」不同，偏平台运维）。  
- `opentrajectory/`：OpenTrajectory 相关能力**逐步迁移**为 Skill 时的落点。  
- `_template/`：新建 Skill 时复制改名使用。

## 登记

`registry.yaml` 为登记权威；扩展字段必须与现有 registry 及校验逻辑保持兼容。

新增或修改 Skill 后请运行：

```bash
python scripts/register_skills.py check
```

该命令仅做扫描 / 校验 / 与 registry 比对，是只读的。详见 [scripts/skill_registry/README.md](../scripts/skill_registry/README.md)。

## 同步

```bash
python scripts/sync_claude_skills.py
```
