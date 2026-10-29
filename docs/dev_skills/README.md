# dev_skills — 开发者技能

## 是什么

一组轻量 skill，在开发者执行日常操作（commit、建 PR、加 fixture、写 Skill、改前端等）时自动激活，指向项目中对应的规范文档。

**关键原则：skill 里不做规范要求，只做 description 触发 + 规范索引。**

## 为什么不直接把规则写进 skill

- 规范文档已有权威版本（`spec/collaboration/`、`docs/02_产品与Workflow/`、`skills/` 等），写在 skill 里就是重复
- 规范会迭代，skill 里内嵌的副本会过时
- AI 工具触发 skill 后读取源文档即可获得最新规则，不需要 skill 转述

## Skill 列表

| Skill | 触发场景 | 索引文档 |
|-------|---------|---------|
| **dev-commit** | 提交代码 | `spec/collaboration/git_commit_rules.md` |
| **dev-pr** | 建 PR | `spec/workflows/pr-workflow.md` |
| **dev-review** | Code review / 验收 | `spec/collaboration/04_施工验收检查.md` |
| **dev-issue** | 建 Issue | `.github/ISSUE_TEMPLATE/` |
| **dev-release** | 阶段发布、release notes、CHANGELOG/STATUS、milestone | `docs/releases/release_process.md`、`CHANGELOG.md`、`STATUS.md` |
| **dev-fixture** | 加 fixture 样例 | `fixtures/fixture_rules.md` |
| **dev-task-spec** | 写 task spec | `docs/task_specs/TEMPLATE.md` |
| **dev-ai-coding** | AI 编码守则 | `spec/collaboration/09_工程施工与脚本交付规范.md` |
| **dev-onboarding** | 新人入职 | `docs/GETTING_STARTED.md` |
| **dev-doctor** | 环境检测与一键修复（Windows） | `docs/dev_skills/dev-doctor/doctor.ps1` |
| **openair-skill-guider** | 创建/迁移 OpenAIR Skill | 内联三层级机制 + 字段矩阵 → `docs/skills/skill_authoring_rules.md` 等规范 |
| **dev-skill-with-code** | 含脚本的 Skill | `docs/skills/skill_with_code_rules.md` |
| **dev-skill-registry** | 注册/同步/验证 `skills/` 下 Skill | `scripts/skill_registry/README.md`、`docs/skills/skill_registry_rules.md` |
| **dev-frontend** | 前端页面开发 | `docs/product/` |
| **dev-dictionary** | 新增/修改枚举中文映射 | `docs/engineering/dictionary_rules.md` |
| **dev-feishu-collect** | 把 Bug/需求写入飞书底表 | 「需求及 Bug 管理 底表」字段映射 + lark-cli 调用模板（内联） |

## 面向非开发者

[`GUIDE_FOR_EVERYONE.md`](GUIDE_FOR_EVERYONE.md) — 面向产品 / 设计 / 运营等同事的使用速查（“对 AI 说什么”）。首次使用双击仓库根的 [`setup.bat`](../../setup.bat) 即可。

## 安装方式

```bash
# 同步到全部三个工具（.claude/ .cursor/ .trae/）
python docs/dev_skills/sync_dev_skills.py

# 预览
python docs/dev_skills/sync_dev_skills.py --dry-run

# 只装某一个工具
python docs/dev_skills/sync_dev_skills.py --target claude
python docs/dev_skills/sync_dev_skills.py --target cursor
python docs/dev_skills/sync_dev_skills.py --target trae
```

安装后 skill 出现在：
- `.claude/skills/<name>/SKILL.md`
- `.cursor/skills/<name>/SKILL.md`
- `.trae/skills/<name>/SKILL.md`

这三个目录已在 `.gitignore` 中，不会提交到仓库。

## 新增 dev skill

1. 在 `docs/dev_skills/` 下创建子目录，放入 `SKILL.md`
2. SKILL.md 只需包含 YAML frontmatter（name + description）和文档索引指针
3. 运行 `python docs/dev_skills/sync_dev_skills.py` 安装

## 与 skills/ 的关系

- `skills/` — 科研能力 Skill（人工维护源，通过 `scripts/sync_claude_skills.py` 同步）
- `docs/dev_skills/` — 开发者工具 Skill（通过 `docs/dev_skills/sync_dev_skills.py` 同步）
- 两者独立运行，互不冲突
