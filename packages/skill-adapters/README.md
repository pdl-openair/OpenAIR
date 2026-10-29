# packages/skill-adapters

**Skill 接入适配层**：统一调用上下文与返回形状（见 `src/adapter.types.ts`）。

- 放适配接口与类型，**不放**具体业务页面或某个固定外部能力的实现。  
- 不写死能力名称枚举；**具体 Skill 的说明与目录以仓库根目录 `skills/` 为唯一人工维护源**（本包仍保留 TS 侧调用形状适配）。  
- 与 Claude 运行时的同步副本见 `.claude/skills/`（由 `scripts/sync_claude_skills.py` 生成，勿手改副本内容）。

## 相关代码

- `src/adapter.types.ts` — `SkillAdapter`、`SkillInvocationContext`、`SkillInvocationResult`

## 相关文档

- `skills/README.md`
- `docs/03_工程/待专项更新/architecture.md`
- `docs/03_工程/待专项更新/CODE_README.md`
