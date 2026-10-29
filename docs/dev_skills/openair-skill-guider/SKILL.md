---
name: openair-skill-guider
description: >-
  开发者需要创建或修改 OpenAIR Skill 时触发。当用户说"创建 OpenAIR Skill"、"新建能力"、
  "写一个 Skill"、"add a new skill"、"新建 skill"、"skill 怎么写"、"迁移 skill 到 OpenAIR"时使用本 skill。
  本 skill 是 OpenAIR skill 创建的**权威元 skill**：内置 OpenAIR 三层级机制（一级阶段-二级节点-三级原子技能）、
  skill.yaml 字段消费矩阵、runtime→schema 耦合规则、L2/L3 设计差异、命名陷阱，
  引导开发者高效、正确创建 OpenAIR skill。即使用户只说"加个能力"，只要涉及 `skills/` 目录新增就应该触发。
---

# OpenAIR Skill Guider

## When to use

开发者需要在 `skills/` 下创建新 Skill 或修改已有 Skill 的结构和元数据。

支持两类场景：

- **迁移**：已有普通 skill（如开源 skill 或自研非 OpenAIR skill），要接入 OpenAIR 机制
- **新建**：从零开始新建 OpenAIR skill

OpenAIR Skill = 通用 skill 规范 + OpenAIR 增强约束（三层级机制 + pack/spec/substage 编排 + 产物注册）。本 skill 负责把增强约束讲清楚，通用规范由 `skill-creator` 负责。

---

## OpenAIR skill 机制（必读）

### 三层级：一级阶段 - 二级节点 - 三级原子技能

OpenAIR skill 是对**三层级科研流程建模**的实现：

| 层级 | 业务含义 | 系统映射 | 字段 |
|---|---|---|---|
| 一级阶段 | 粗粒度划分科研阶段（固定四选） | `stages` 字段声明 | `problem_modeling` / `solution_design` / `execution_validation` / `insight_generation` |
| 二级节点 | 设立"做什么"目标，配备规范（specification）规范产出与验证 | `skill_type: orchestration` | + `pack` + `spec` + `output_file` |
| 三级原子技能 | 讲解"怎么做"，具体执行 | `skill_type: atomic` | 不需要 `pack`/`spec`/`output_file`（见 L2/L3 差异章节） |

二级和三级的关联**由 skill.yaml 决定**（不是目录位置）——同一目录可放多个 skill，也可分开存放。

### 三层各处理什么变化（创建决策依据）

新建 skill 前，先判断变化在哪一层——这决定该建 L2、L3 还是新 pack：

| 变化类型 | 该新建 | 例子 |
|---|---|---|
| 流程骨架不同（步骤/顺序不同） | **新 pack**（含同名 substage） | 材料实验流程 vs 计算机实验流程 |
| 产出物字段/约束不同（同骨架） | **新 pack 里的同名 substage**（含新 spec） | 同一"实验设计"在不同领域的产出标准不同 |
| 执行细节不同（同领域不同工具/方法） | **只加 L3** | 关键词检索 vs 语义检索 vs 专家推荐 |

**核心设计共识**（不要违反）：

- **substage : spec = 1 : 1**。不要在一个 substage 下堆多个 spec——产出标准不同就拆 pack（同名 substage 在不同 pack 各配自己的 spec）
- **多 L3 需求集中在"与物理世界/领域工具交互"的 substage**（实验执行、数据获取、代码实现等）；纯认知/推理类 substage（问题凝练、假说构建）通常一个通用 L3 即可
- **L2 跨 pack 复用仅限"无 spec 差异"的纯认知/推理类 substage**——执行类 substage 的 L2 通常不可跨 pack 复用（spec 是领域差异载体）

### 编排机制

- **一级 ↔ 二级编排在"新任务规划时"**：编排包含哪些一级、每个一级按什么次序包含哪些二级
- **二级 ↔ 三级编排在"二级调用时"**：编排该二级要按什么次序调用哪些三级

这套编排依赖 `schemas/<pack>/` 下的自动生成索引文件：

- `stage-substage-map.yaml` — 正向索引：stage → substage_keys
- `substage-skill-map.yaml` — 正向索引：substage_key → 可用 L3 skill 列表
- `substage-spec-map.yaml` — 正向索引：substage_key → spec
- `skill-id-to-substage.yaml` — 反向索引：L2 skill_id → substage_key
- `research_type_overrides.yaml` — 手动维护：四类研究类型（M/D/C/H）必须有哪些 substage
- `packs.yaml` — 手动维护：定义有哪些域-pack

这些索引由注册脚本根据 skill.yaml 自动生成。

### 文件职责分工（手写 vs 脚本生成）

| 文件 | 维护方式 | 备注 |
|---|---|---|
| `skills/<pack>/<name>/skill.yaml` | **手写** | skill 声明源 |
| `skills/<pack>/<name>/SKILL.md` | **手写** | Claude Code prompt |
| `skills/registry.yaml` | 脚本生成 | **不要手改**（派生产物，update 时合并保留 owner/notes/fixture_status/display_name 等人工字段） |
| `schemas/<pack>/stage-substage-map.yaml` | 脚本生成 | update 生成 |
| `schemas/<pack>/substage-skill-map.yaml` | 脚本生成 | update 生成（见"已知限制"） |
| `schemas/<pack>/substage-spec-map.yaml` | 脚本生成 | update 生成 |
| `schemas/<pack>/skill-id-to-substage.yaml` | 脚本生成 | update 生成 |
| `schemas/packs.yaml` | **手写** | pack 列表 |
| `schemas/<pack>/research_type_overrides.yaml` | **手写** | 研究类型必选/可选 substage |
| `schemas/<pack>/specs/*.md` | **手写** | substage 规范文档 |

### skill.yaml 字段消费矩阵

> 调研基准：2026-06-14，证据存 personal 嵌套仓（原 dev_plans/s01-how_to_migrate/note-e-openair-skill-authoring.md，2026-09 随历史 dev_plans 移出主仓）

| 字段 | 注册阶段（`scripts/skill_registry/`） | 运行时（`services/api/app/execution/`） | 必/可选 |
|---|---|---|---|
| `skill_id` | `validate.py:161-171`；registry 主键 | `agent_service.py:33-34` 路由 | **必填** |
| `claude_name` | `validate.py:139-158`；须=目录名+须=SKILL.md frontmatter.name | `agent_service.py:62` L2 编排查找 | **必填** |
| `skill_type` | `validate.py:227-231` 枚举 | 经 pack maps 间接 | **必填**（缺省 atomic） |
| `runtime` | `validate.py:174-182` 枚举 | 分派 runner | **必填**（缺省 claude_code） |
| `stages` | `validate.py:249-260` 非空+枚举 | 经 pack maps 间接 | **必填** |
| `substages` | 构建 substage maps 全靠它 | 经 pack maps 间接 | **必填** |
| `pack` | `validate.py:186-191`；orchestration 必填 | `agent_service.py:293` 来自 task.skillPack | **L2 必填**（单数，绑定一个 pack） |
| `packs` | `validate.py:192-220`；atomic 类型校验 list of str；未声明默认 `[sdr]` | registry 展开 → 各 pack 的 substage-skill-map | **L3 可选**（复数，跨 pack 复用；不声明=默认 `[sdr]`） |
| `spec` | `validate.py:194-199`；写 substage-spec-map | `agent_service.py:299-301` 作为 L2 编排 prompt | L2 **必填**；L3 不消费 |
| `input_skills` | `validate.py:202-206` | `agent_service.py:304-312` 拼输入文件路径 | 可选 |
| `output_file` | 不显式输出 | `agent_service.py:302,1454-1456` 等 | 见 L2/L3 差异章节 |
| `display_name` / `status` / `source` / `sequence` / `enabled` / `fixture_status` / `owner` / `notes` | registry 透传 | 不读 | 可选 |
| `llm` | 不读 | `llm_structured_runner.py:515-535` 读 model/temperature/max_completion_tokens/reasoning_effort | 可选（仅 llm_structured runtime） |
| `version` | 不输出 | 不读 | 可选（仅文档） |
| ~~`description`~~ | **不读**（注册读 frontmatter.description） | 不读 | **不要写**（见 description 单一源规则） |
| ~~`input_schema`/`output_schema`~~ | 仅透传到 registry | 不读 | **不要写**（schema 文件名硬编码） |
| ~~`example_request`/`example_response`~~ | 仅透传到 registry | 不读 | 可选（registry 元数据，0 运行时消费） |
| ~~`domain`~~ | 已被 `pack` 取代 | — | **不要写**（历史遗留） |
| ~~`claude_skill_path`~~ | 已废弃 | — | **不要写**（由 `sync_dev_skills.py` 自动管理） |

### SKILL.md frontmatter 字段（必读）

| 字段 | 必/可选 | 用途 |
|---|---|---|
| `name` | **必填** | 须=父目录名=skill.yaml.claude_name；Claude Code 用 |
| `description` | **必填** | 非空、≤1024 字符；**注册脚本读它写进 substage-skill-map.yaml 的 description**（`registry.py:302-305, 431, 476-478`） |

**`description` 单一源规则**：description 字段**只在 SKILL.md frontmatter 写**，skill.yaml 不要再写 description 字段（历史 skill 残留待清理）。

### L2 vs L3 设计差异

| 项 | L2 orchestration | L3 atomic |
|---|---|---|
| `skill_type` | `orchestration` | `atomic` |
| `pack` | **必填**（单数，绑定一个 pack） | **不要写**（L3 改用 `packs`） |
| `packs` | 不要写 | 可选（复数，跨 pack 复用；不声明默认 `[sdr]`） |
| `spec` | **必填** | 不需要 |
| `output_file` | **必填**（产物路径） | **不应有**（产出附 L2，见下） |
| `input_skills` | 可选（声明依赖的上游 L2 skill） | 不需要（编排由 L2 负责） |

### `output_file` 与 L3 的设计意图

- **设计原则**：L3 是"怎么做"，产出应附 L2，**L3 不应有 output_file**
- **当前实现**：`agent_service.py:1454-1456` 的 `_try_register_substage_artifact` 是 L2/L3 共用函数，若 L3 skill.yaml 声明了 output_file 会被消费——属**实现事故**，非设计
- **强制等级**：
  - `claude_code` runtime：L3 **不写** output_file；L2 必写
  - `llm_structured` / `python_adapter` runtime：L3 **必写** output_file（runner 强约束，过渡期），L2 必写
- **未来路线**：OpenAIR 产物注册将脱离 output_file 字段，支持对话里自由生成的产物文件批量注册（后续推进）

### runtime → schema 文件强耦合

| runtime | input.schema.json / output.schema.json | 说明 |
|---|---|---|
| `claude_code` | **不需要** | runner 不读 schema |
| `llm_structured` | **必填** | `llm_structured_runner.py:490-501` 缺失抛 RuntimeError；用 `jsonschema.validate` 校验输入输出 |
| `python_adapter` | **必填** | `run_single_structured_skill.py:132-160` 同上 |

### 命名陷阱（必须遵守）

- **schema 文件名硬编码点分隔**：`input.schema.json` / `output.schema.json`（点分隔）。下划线版本 `input_schema.json` **runner 不识别**（`skills/general/new-task-planning/` 有此历史 bug，新建 skill 必须用点分隔）
- **claude_name 用 kebab-case**：`literature-search`，不用下划线
- **skill_id 用 snake_case**：`literature_search`
- **目录名 = claude_name = SKILL.md frontmatter.name**：三者必须完全一致

### examples/ / tests/ 目录

**完全可选，后端 0 消费**。
- `examples/`、`skills/*/tests/` 后端不读
- 唯一被后端读取的样例路径是 `fixtures/skill_io/{skill_id}/response.json`（`replay_runner.py:46`，给 replay runner 用）
- 想做 SDK 回放，单独在 `fixtures/skill_io/{skill_id}/` 放 response.json

### 已知限制（创建时注意）

- **`update` 合并不覆盖人工字段**：跑 `update` 会刷新派生字段（skill_id、claude_name、skill_path 等），但**保留** registry 里的人工字段（`owner`、`notes`、`fixture_status`、`display_name`）——可直接编辑这些字段，不会被冲掉
- **四个 pack 配置文件无脚本校验**：`packs.yaml`、`stage-substage-map.yaml`、`substage-spec-map.yaml`、`research_type_overrides.yaml` 修改时需自行确认一致性
- **SDR 的 L2 skill_id 历史遗留为裸名**（如 `literature_search`、`task_breakdown`），未加 `sdr_` 前缀；新建 skill 按本 skill "命名陷阱"章节规则即可，不强求前缀

---

## 创建流程（7 步）

### Step 0 — 先理解 OpenAIR 科研流程建模（what + why）

先**把以下```markdown ```包裹的内容原样输出**给用户，让用户理解 OpenAIR 科研流程建模。

```markdown
OpenAIR 把科研流程建模成**三层级流水线**：

- **一级阶段（stage，固定四选）**：科研的大段——问题建模 → 解法设计 → 解法验证 → 报告撰写。每个任务按这四段推进，可跳过。
- **二级节点（substage）**：每个大段内部的小目标，比如"问题建模"段里有"意图理解"、"文献检索"、"空缺识别"等。**每个 substage 配一个 spec（规范文档）**，说明"这个目标要产出什么、怎么算完成"。实现为**L2 orchestration skill**，创建一个 L2 skill等于创建一个新 substage。
- **三级原子技能（L3 atomic skill）**：具体怎么做某个 substage。一个 substage 下可以挂多个 L3 skill（比如"文献检索"下挂"关键词扩展检索"、"语义检索"、"专家推荐检索"）。

此外，为了实现**多领域兼容**，设计了**pack = 研究领域包**，不同 pack 有不同 substage 集（如 `sdr` 通用科研 / `ope` 离线策略评估 / `opentrajectory` 证据追溯），互不冲突

**为什么这样设计**：
- **动态编排**：不同任务可选不同 substage 组合，不强求走完全程
- **规范驱动**：spec 让产出可验证（"是否产出 X、X 是否满足 Y"），不只靠 LLM 自评
- **挂载式扩展**：新增能力只要决定挂到哪个 substage，编排自动生效

**新建 skill 的本质是挂载**：
- 新建 **L3 atomic** → 必须挂到现有 substage；没合适的就**先新建 substage（= 新建 L2）**
- 新建 **L2 orchestration**（= 新建 substage）→ 必须挂到现有 pack；没合适的就**新建 pack**
- 新建 **pack** → 在 `schemas/packs.yaml` 注册（重决策，见 Step 3a 代价提示）

**术语速查**：

| 术语 | 含义 |
|---|---|
| `pack` | 研究领域包，`schemas/packs.yaml` 列出 |
| `stage` | 一级阶段，四选：`problem_modeling` / `solution_design` / `execution_validation` / `insight_generation` |
| `substage` | 二级节点 key，**从 L2 skill.yaml 的 `substages` 字段派生**（不在独立文件维护） |
| `spec` | substage 的规范文档，位于 `schemas/<pack>/specs/` |
| `L2 orchestration skill` | 实现某个 substage 编排（"做什么 + 怎么验证"） |
| `L3 atomic skill` | 具体执行某个原子能力（"怎么做"） |
```

---

### Step 1 — 决定 skill 类型

- **L2 orchestration**：你要定义"做什么"+ 规范化产出与验证 → 用 L2（**等于新建一个 substage**）
- **L3 atomic**：你要讲解"怎么做"，具体执行某原子能力 → 用 L3
- 一级、二级在新任务规划时由前端/编排决定，**新 skill 不需要新建一级**

### Step 2 — 决定 runtime

- **claude_code**（默认）：skill 主要靠自然语言指导 Claude Code 执行，无固定 JSON 输入输出 → 大多数情况用这个
- **llm_structured**：skill 需要严格 JSON 输入输出 + JSON Schema 校验 + 可配置 model/temperature → 用这个，但必须配 schema 文件
- **python_adapter**：skill 是 Python 脚本执行 → 用这个

### Step 3 — 决定挂载点（决策树）

按 Step 1 选的类型走分支：

#### 路径 A — 新建 L3 atomic skill

1. 浏览 `schemas/<pack>/research_type_overrides.yaml` 和 `stage-substage-map.yaml`，找合适的现有 substage
2. **找到** → 在 skill.yaml 写 `substages: [<该 key>]`，进入 Step 4
3. **没找到** → 告诉用户："当前 pack 没有 X 这样的 substage，需要新建。新建 substage = 新建一个 L2 orchestration skill，要先走路径 B 完成 L2 创建，再回来挂这个 L3"

#### 路径 B — 新建 L2 orchestration skill（= 新建 substage）

1. 浏览 `schemas/packs.yaml`，找合适的现有 pack
2. **找到** → 在 skill.yaml 写 `pack: <该 key>`、`substages: [<新 substage_key>]`（key 用 snake_case，描述性），进入 Step 4
3. **没找到** → 询问用户是否新建 pack
   - **是** → 走 Step 3a 完成新 pack 创建，回到此处继续
   - **否** → 重新审视 skill 定位：是否真的需要新 pack？能否归到现有 pack 下作为新 substage？

**挂载原则**（不要违反）：
- L3 必须挂到至少一个 substage；没合适的就新建 L2
- L2（substage）必须挂到一个 pack；没合适的就新建 pack
- **新建 pack 是重决策**——见 Step 3a

### Step 3a（条件）— 新建 pack

**何时新建 pack**：现有 pack 都覆盖不了你的研究领域（例如要做独立的"材料计算"研究流程，与 sdr/ope/opentrajectory 都不重合）。

**新建步骤**：

1. 在 `schemas/packs.yaml` 末尾加一个条目（参考现有 pack 写法）：
   ```yaml
   - key: <pack_key>           # snake_case，如 materials_calc
     label: <中文标签>
     description: <这个 pack 是做什么的，一句话>
     when_to_use: |
       - <什么场景下用这个 pack>
       - <什么场景下用这个 pack>
   ```

2. 创建目录骨架：
   ```bash
   mkdir -p schemas/<pack_key>/specs
   ```

3. 创建 `schemas/<pack_key>/research_type_overrides.yaml`，声明该 pack 支持的研究类型与各类型 required/optional substage。可参考 `schemas/sdr/research_type_overrides.yaml` 结构。**注：第一次创建时此文件可为空骨架**，等 L2 skill 落地后再补 required/optional。

4. 后续 L2 skill 注册时，`scripts/register_skills.py update` 会自动在 `schemas/<pack_key>/` 生成 `stage-substage-map.yaml`、`substage-skill-map.yaml`、`substage-spec-map.yaml`、`skill-id-to-substage.yaml`。

**代价提示**：
- 新 pack 的所有索引文件初始为空，需要持续填充 L2 skill 才能让 pack "活起来"
- 前端是否默认展示该 pack 取决于前端实现，可能需要额外配置
- 长期需要持续维护该 pack 的 spec 集
- **建议**：先认真评估能否归到现有 pack 作为新 substage；只有研究领域范式确有本质差异时才新建 pack

### Step 4 — 复制模板

```bash
cp -r skills/_template/ skills/<pack>/<claude_name>/
```

模板位置：`skills/_template/`（含 SKILL.md / skill.yaml / input.schema.json / output.schema.json 雏形）。

### Step 5 — 按字段矩阵填字段

按本 skill 的"字段消费矩阵"章节填 skill.yaml，按 SKILL.md frontmatter 必填 `name` 和 `description`。

#### L2 orchestration 必填清单

```yaml
# skill.yaml
skill_id: <snake_case>
claude_name: <kebab-case>
skill_type: orchestration
runtime: claude_code  # 或 llm_structured / python_adapter
stages:
  - <stage_key>       # 四选：problem_modeling / solution_design / execution_validation / insight_generation
substages:
  - <substage_key>    # 见 schemas/<pack>/ 下定义
pack: <pack_name>
spec: <spec_file>     # 如 implementation.md，相对 schemas/<pack>/ 目录
output_file: <relative/path/to/output.md>  # 相对任务工作区
# 若 runtime=llm_structured/python_adapter，还需：
# llm:
#   model: gpt-5-mini
#   temperature: 0.3
#   max_completion_tokens: 8000
```

#### L3 atomic 必填清单（claude_code runtime）

```yaml
# skill.yaml
skill_id: <snake_case>
claude_name: <kebab-case>
skill_type: atomic
runtime: claude_code
stages:
  - <stage_key>
substages:
  - <substage_key>
packs:              # 可选，复数；不声明默认 [sdr]
  - sdr
  # - opentrajectory  # 想跨 pack 复用就列出多个 pack
# 不要写 output_file（设计上 L3 不应有）
# 不要写 pack（L3 改用 packs；validator 会报 legacy warning）
```

**L3 跨 pack 复用**：一个 L3 可以同时挂到多个 pack 的对应 substage 下。例如想做一个通用 `evidence-mining` 同时服务 sdr 和 opentrajectory：

```yaml
substages:
  - evidence_extraction
packs:
  - sdr
  - opentrajectory
```

注册脚本会把该 L3 同时写进 `schemas/sdr/substage-skill-map.yaml` 和 `schemas/opentrajectory/substage-skill-map.yaml` 的 `evidence_extraction` 节点下。注意：substage key 在不同 pack 间允许重名（如 sdr 和 opentrajectory 都有 `mechanism_explanation`），由 `packs` 字段显式消歧，不会"挂混"。

#### L3 atomic 必填清单（llm_structured/python_adapter runtime）

```yaml
# skill.yaml
skill_id: <snake_case>
claude_name: <kebab-case>
skill_type: atomic
runtime: llm_structured
stages:
  - <stage_key>
substages:
  - <substage_key>
packs:              # 可选，复数；不声明默认 [sdr]
  - sdr
output_file: <relative/path/to/output.json>  # runner 强约束
llm:
  model: gpt-5-mini
  temperature: 0.3
  max_completion_tokens: 8000
```

**配套文件**：
- `SKILL.md`：frontmatter `name` + `description`（≤1024 字符），正文写"怎么做"
- `input.schema.json` / `output.schema.json`（点分隔，仅 llm_structured/python_adapter runtime 必填）

### Step 5.5 — 按 skill-creator 写 SKILL.md 正文

skill.yaml 字段填完后，写 SKILL.md 正文（frontmatter + 步骤说明）。**SKILL.md 主体内容按 `skill-creator` 指南写**——这是 SKILL.md 写作的权威指南，覆盖：意图捕获 → 用户访谈 → 写 frontmatter + 正文 → 测试用例 → eval/iterate → description 触发优化。

**调用方式**：直接 invoke `skill-creator` skill（即 `reference/skill-creator/SKILL.md`）按它的流程走。**L2 和 L3 主体都用 skill-creator 写**，OpenAIR 增强约束叠加在它的产出之上。

**OpenAIR 增强约束**（叠加在 skill-creator 产出之上）：

- frontmatter.description 非空、≤1024 字符（**注册脚本读它写进 substage-skill-map.yaml**，是 description 的单一源）
- frontmatter.name = 父目录名 = skill.yaml.claude_name（三者必须一致）
- **不要在正文写 `## When to use` 章节**——触发信息（做什么、何时用、何时不用）全部放 frontmatter.description；正文从 `## Input` 或具体步骤开始
- L2 orchestration 正文要讲清"做什么"+"怎么验证产出"，与 spec 文档呼应；编排逻辑（如何选 L3、传什么上下文）也要写清
- L3 atomic 正文要讲清"怎么做"的具体步骤、输入输出、失败策略
- Output 章节的产物路径必须与 `skill.yaml.output_file` 一致（L2 必填，L3 claude_code runtime 不应有）
- **不要把治理字段**（`skill_id` / `pack` / `substages` / `spec` 等）**写进 SKILL.md 正文**——那是 skill.yaml 的事；SKILL.md 只讲"何时用、怎么做、产出什么"

写完 SKILL.md 正文后，回到本 skill 继续 Step 6。

**人机交互门控**：进入 Step 6 注册前，把 `SKILL.md` 正文与 `skill.yaml` 给用户过一遍，重点确认：

- frontmatter `description` 触发清晰、≤1024 字符
- 字段矩阵齐全（L2 必填 `pack` / `spec` / `output_file`；L3 用 `packs` 复数）
- 命名一致（`claude_name` = 目录名 = SKILL.md frontmatter.name）
- L3 claude_code runtime 不写 `output_file`
- 正文不出现 `## When to use` 章节、不堆治理字段

得到用户确认后再跑 Step 6。

### Step 6 — 跑注册

统一入口脚本是 `scripts/register_skills.py`（内部已处理 `sys.path`，无需 `-m`）：

```bash
# 写入 registry.yaml + 生成 schemas/<pack>/*-map.yaml（核心注册步骤）
python scripts/register_skills.py update

# 详细校验（只读，与 registry 比对，发现差异即非 0 退出）
python scripts/register_skills.py check --verbose

# 同步源到 .claude/skills/、.cursor/skills/ 等副本目录（仅复制文件，不写 registry/map）
python scripts/register_skills.py sync
# 等价于：python scripts/sync_claude_skills.py
```

> 不要直接调用 `scripts/skill_registry/cli.py` —— 该模块使用相对导入，作为脚本直接跑会报 ImportError。

**子命令职责**（不要混淆）：

| 子命令 | 写 `registry.yaml` | 写 `schemas/*-map.yaml` | 写各 IDE 副本目录 |
|---|---|---|---|
| `update` | ✅ | ✅ | ❌ |
| `check`  | ❌（只读） | ❌（只读） | ❌ |
| `sync`   | ❌ | ❌ | ✅ |

新建/修改 skill 后的标准流程：先 `update`（生成 registry 与索引），再 `sync`（同步到 IDE 副本）。

`update` 会：
1. 扫描所有 `skills/**/skill.yaml`
2. 校验必填字段、命名一致性、frontmatter 合规性
3. 生成 `skills/registry.yaml`
4. 更新 `schemas/<pack>/stage-substage-map.yaml`、`substage-skill-map.yaml`、`substage-spec-map.yaml`、`skill-id-to-substage.yaml`

如果校验失败，按报错修字段后重跑 `update`。

---

## 详细规则参考（深度场景查阅）

本 skill 内联了**核心规则**，下列文档提供**深度细节**，按需查阅：

- **`skills/README.md`** — Skill 唯一人工维护源、组织与登记权威（原 docs/skills/skill_authoring_rules.md、skill_design.md、skill_management_rules.md 已并入）
- **`skills/_template/`** — 新建模板（SKILL.md / skill.yaml / schema 雏形，原 skill_spec_template.md）
- **`fixtures/fixture_rules.md`** — replay/回放样例组织要求（原 skill_replay_test_rules.md）
- **`skills/README.md`** — 代码型 skill 额外约束（原 skill_with_code_rules.md）
- **`scripts/skill_registry/README.md`** — 注册/同步/验证流程（原 skill_registry_rules.md）
- **`skill-creator`（`reference/skill-creator/`）** — 通用 SKILL.md 起草、测试与迭代工作流；创建普通 skill 时**必须先调用**，再叠加 OpenAIR 增强约束

## Pre-checks（创建前）

skill 触发后，先做以下快速核查，再进入 Step 0：

1. **意图确认**：用户是要"新建/迁移 OpenAIR skill"还是"修改已有 skill"？后者直接读现有 `skill.yaml`，不进入创建流程。
2. **能力排重**：扫 `skills/registry.yaml`，看用户描述的能力是否已有现成 skill；扫 `schemas/<pack>/substage-skill-map.yaml`，看目标 substage 下是否已有同类 L3。若有，引导用户复用而非新建。
3. **盘点上下文（为 Step 3 决策做准备）**：读 `schemas/packs.yaml` 列出当前所有 pack；按用户可能归属的 pack 读 `stage-substage-map.yaml` 列出现有 substage。
4. **目录命名预检**：若用户已提议 skill 名，确认是 kebab-case、不与现有目录冲突；提议 `claude_name` 与 `skill_id`（snake_case）候选。

## Output

新 skill 目录 `skills/<pack>/<claude_name>/`，包含：
- `SKILL.md`（frontmatter name+description + 正文）
- `skill.yaml`（按字段矩阵填）
- `input.schema.json` / `output.schema.json`（仅 llm_structured/python_adapter runtime）
- 可选：`examples/`、`tests/`、`references/`、`assets/`、`scripts/`

注册流程：`python scripts/register_skills.py update`（生成 `skills/registry.yaml` + `schemas/<pack>/*-map.yaml`）→ `python scripts/register_skills.py sync`（同步源到 `.claude/skills/`、`.cursor/skills/` 等 IDE 副本目录）。
