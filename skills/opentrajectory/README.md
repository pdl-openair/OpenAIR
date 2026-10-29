# opentrajectory

OpenTrajectory 相关 Skill 的**人工维护根目录**：各子目录含 `SKILL.md`、schema、脚本与 replay 测试。

## 组织方式

- 每个能力一个子目录（如 `constraint-interpretation-skill/`、`retrieval-matrix-generation/`）。
- 共享测试与 fixture 在 `tests/`；离线 smoke / regression 脚本也在此目录。
- 能力清单与迁移状态见 `docs/02_产品与Workflow/OpenTrajectory/待专项更新/Skill质量/opentrajectory-skill-inventory.md`。

## 登记与同步

新增或修改 Skill 后：

```bash
python scripts/register_skills.py check
python scripts/sync_claude_skills.py
```

在 `skills/registry.yaml` 更新 `status` / `fixture_status`。

## 效果验收

OpenTrajectory Skill 的真实效果 evals 见 [evals/opentrajectory/README.md](../../evals/opentrajectory/README.md)。
