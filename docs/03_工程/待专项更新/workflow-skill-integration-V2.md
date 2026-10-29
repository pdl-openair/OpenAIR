# Workflow 和 Skill 接入指南

这份指南给需要快速接入新 Skill / workflow 的开发同学。

目标很简单：

> 用户确认后，`/draft -> /confirm -> /start` 能真正执行；每一步有 artifact；下游拿到的是真实上游结果；失败能看懂；旧流程不被破坏。

---

## 先记住 5 个原则

1. **Skill `status: ready` 不等于能在生产 worker 里执行。**
2. **下游只吃上游 artifact，不从 prompt、临时变量或“猜出来的 fallback”补输入。**
3. **可以降低质量，但不能改变研究方法。**
   例如：10 个 CIF 只拿到 2 个，可以继续；0 个 CIF 就不能假装有种子继续结构演化。
4. **先找真实 resolver / runtime 路径，再改配置。** 不要看到一个 map 就默认它是 source of truth。
5. **先跑窄 gate，再跑真人 smoke。** 不要被大而杂的历史失败拖住。

---

# 1. 四个基本概念

- **workflow**：一条完整业务流程。
- **stage**：大阶段，例如问题定义、方案设计、执行验证。
- **substage**：stage 下的具体步骤。
- **Skill**：真正执行一个步骤的能力，可以调用模型、Python、MCP 或外部服务。

每一步都应产出可追踪的 **artifact**，例如 JSON、报告、CIF、CSV、文件路径。

正确的数据流是：

```text
Skill A
  ↓
artifact A
  ↓ input_handoff
Skill B
  ↓
artifact B
```

不是：

```text
Skill A
  ↓
prompt 重新描述 / 内存变量 / 猜一个默认值
  ↓
Skill B
```

---

# 2. 常见文件分别管什么

```text
skills/<domain>/<skill-name>/
  SKILL.md                  给人和模型看的说明
  skill.yaml                Skill 身份、stage/substage、runtime、合同
  input.schema.json         冻结输入 schema（部分 runtime / handoff 会要求）
  output.schema.json        冻结输出 schema
  adapter/ / scripts/       真实执行代码

skills/registry.yaml        运行时登记、handoff、review/runtime contract

schemas/<pack>/
  stage-substage-map.yaml
  substage-spec-map.yaml
  substage-skill-map.yaml
  research_type_overrides.yaml
  ...专用 workflow map
```

**不要凭文件名猜真正的组合来源。**

有的 workflow 走通用 `substage-skill-map.yaml`，有的走专用 map，有的还会在 resolver / admission / execution decision 里有限定。

先 `git grep` 找是谁在读它。

---

# 3. 接入前先判断：这是哪一种改动

## A. 配置型 workflow

通用 Planner 能识别，现有 runtime 也能执行。

通常只需要：

```text
Skill 定义
+ schema
+ workflow map
+ handoff
```

这是最便宜的路径，优先选。

## B. 固定准入 workflow

某类用户意图必须直接进入固定 workflow。

除了配置，还可能需要：

```text
admission
Router
ResearchDesign / draft materialization
execution authorization
专项测试
```

这些属于 workflow 专属逻辑，不要塞进通用 Router。

## C. 新 runtime / 脚本 / 外部服务 Skill

这是最容易“YAML 看起来都对，但 /start 不能跑”的情况。

**先确认 durable worker 真能执行，再接 workflow。**

---

# 4. 新 Skill 最少要接通哪些层

以 `python_adapter` 为例，通常要同时检查下面几层。

## 4.1 Skill 源定义

`skill.yaml` 至少要与真实执行一致：

```yaml
skill_id: my_skill
runtime: python_adapter
status: ready
```

如果是 durable external action，还要有类似：

```yaml
runtime_contract:
  kind: external_adapter
  requires_external_action: true
  adapter_id: python_adapter:my_skill
  evidence_contract_version: external-action-evidence/v1
```

如果使用 schema validated fast path / handoff，也要把对应合同写在源定义里。

---

## 4.2 Python adapter resolver

确认 `skill_id` 能解析到真实 Skill 路径。

例如项目里的：

```text
services/api/app/execution/python_adapter_skills.py
```

最小 gate：

```python
resolve_python_adapter_skill("my_skill") is not None
```

**这里没接，Skill 会表现成 runtime unavailable。**

---

## 4.3 Runner

确认 worker 知道怎么真正调用它。

例如：

```text
services/api/app/execution/python_adapter_runner.py
```

不要只登记 Skill ID；必须真的调用 entry function / subprocess / adapter。

---

## 4.4 Durable runtime authority / evidence

外部 adapter 通常还需要 durable runtime 承认它，并验证执行证据。

例如：

```text
services/api/app/execution/durable_skill_runtime.py
```

至少确认：

```text
REGISTERED = True
DURABLE_EXECUTABLE = True
LIVE_RUNTIME = True
```

**`status: ready` 不能替代这三个 gate。**

---

## 4.5 Frozen schema

如果 runtime / `input_handoff` 会冻结 schema，确保存在：

```text
input.schema.json
output.schema.json
```

有些 loader 在 schema 缺失时会直接跳过整个 Skill，最终表现为：

```text
REGISTERED = False
```

这类问题不是模型问题。

---

# 5. Handoff：最重要的是“数据语义对”

先写清楚两件事：

```text
上游 artifact 真正输出什么？
下游 adapter 真正需要什么？
```

再写 `input_handoff`。

例如：

```yaml
input_handoff:
  dependency_skill_id: upstream_skill
  field_mappings:
    task_instruction: seed_cif_files
  normalizers:
    - materials_seed_cif_projection_v1
```

normalizer 只做**表示转换**，不要发明研究数据。

正确：

```text
[{name, cif_base64}, ...]
→ 校验 / 解码
→ seed_cifs=[真实 CIF 路径]
```

错误：

```text
上游没有 CIF
→ 从 formula 猜 CIF
→ 或凭空生成 seed
→ 假装继续原 workflow
```

---

# 6. 缺输入怎么处理：允许降级，但不要跑无用流程

这是最重要的 P0 规则之一。

假设下游需要 CIF：

```text
10 个目标，拿到 6 个真实 CIF
→ 用 6 个继续

只拿到 2 个
→ 可以降低质量，用 2 个继续

部分 CIF 损坏
→ 丢掉坏的，用剩余真实 CIF

0 个真实 CIF
→ 不启动依赖 CIF 的下游科研计算
→ 返回明确 partial / recoverable 状态
```

**0 个关键输入时，不要为了“流程走完”启动无意义计算。**

质量降级可以是：

- 样本少一点；
- 非关键验证缺失；
- 次要指标缺失；
- 降级使用已有可信字段。

质量降级不能是：

- 伪造实验/数据库结果；
- 伪造文件；
- 改变研究方法后仍声称执行的是原方法。

---

# 7. Workflow 绑定不只是一张 map

接好 Skill 后，检查完整链路：

```text
用户意图
→ admission / Router
→ ResearchDesign
→ workflow / substage composition
→ Skill selection
→ execution authorization
→ dispatch
→ runtime
→ artifact
→ next handoff
```

受控 workflow 经常还有额外白名单 / fast path，例如：

```text
research_execution_decision_service.py
```

常见坑：

```text
map 已经改成新 Skill
runtime 也能跑
但 production authorization 仍只允许旧 Skill
→ /start 还是失败
```

所以替换 Skill 时，搜索旧 Skill ID：

```powershell
git grep -n "old_skill_id"
```

逐个判断哪些是：

- workflow map；
- resolver；
- runtime；
- handoff；
- authorization；
- test fixture；
- 纯文案。

不要机械全替换。

---

# 8. Frozen Task / ResearchDesign：改完代码后要用新任务验证

任务开始后，系统可能已经冻结：

```text
Skill definition
schema
runtime contract
input_handoff
workflow composition
```

所以：

> **修改 Skill / workflow 后，不要默认旧任务会自动吃到新定义。**

真人 smoke 优先新建任务。

只有系统明确支持 rebind / migration 时，才复用旧任务。

---

# 9. 看日志时先判断失败发生在哪一层

## 0 秒失败

优先看：

```text
skill_input_invalid
skill_input_handoff_contract_invalid
runtime unavailable
authorization / readiness rejected
```

这通常说明**adapter 还没真正开始跑**。

不要先怪模型。

## Worker 根本没跑

先看：

```powershell
docker compose ps
docker compose logs durable-worker
```

ImportError / crash-loop 会让前端看起来像“规划中”或“一直不动”。

## Dispatch completed 也不代表研究结果成功

有的 adapter 会正常返回：

```json
{
  "state": "partial_failure"
}
```

dispatch 层仍可能记成 `completed`。

所以同时看：

```text
dispatch status
+
artifact.state
+
run_summary / partial_failure
```

---

# 10. 注册工具怎么用，别被历史错误绑架

先做只读基线：

```powershell
python scripts/register_skills.py check --verbose
```

如果仓库本来就有无关 registration errors：

- 记录下来；
- 不顺手修；
- 不用 `--force`；
- 不因为它们阻塞当前 Skill 的窄验收。

`register_skills.py update` 只有在当前仓库状态允许、diff 可控时再用。

**注册器不会自动帮你实现 runner / durable runtime。**

---

# 11. 最小验收顺序

不要一上来跑全量测试。

## Gate 1：静态 / resolver

```text
Skill ID 正确
workflow map 正确
resolver 能找到 Skill
schema 能加载
```

## Gate 2：runtime

```text
REGISTERED = True
DURABLE_EXECUTABLE = True
LIVE_RUNTIME = True
```

## Gate 3：handoff

至少覆盖：

```text
完整输入
部分有效输入
0 个关键输入
```

要求：

```text
部分有效 → 继续
0 个关键输入 → 不启动无意义科研执行
```

## Gate 4：真实任务

跑一次：

```text
/draft
→ /confirm
→ /start
```

确认：

- 真正执行的是目标 Skill；
- artifact 可见；
- 下游拿到真实上游 artifact；
- partial failure 能正确呈现；
- worker 没 crash。

## Gate 5：最小回归

跑：

- 当前 workflow 窄测；
- 一个已有相关路径；
- 必要时一个不相关 workflow smoke。

**不要为了“全绿”去修无关历史失败。**

---

# 12. 部署验证

修改 worker/runtime 后，必须重建真实部署镜像：

```powershell
docker compose up -d --build api durable-worker
```

然后：

```powershell
docker compose ps api durable-worker
```

两者都正常后再真人 smoke。

本地 Python 能 import，不代表 Docker worker 里也能。

---

# 13. Dirty worktree 下怎么安全提交

多线并行时不要：

```powershell
git add -A
git pull
git reset --hard
```

直接作用在一个有大量未提交改动的主工作区。

推荐：

```text
当前 dirty worktree
→ 保留，作为已验证实现来源

最新 origin/dev
→ 新建 clean worktree
→ 只移植本次 Skill/workflow 的窄改动
→ 跑窄 gate
→ commit
→ push
```

如果 `origin/dev` 已经有一版旧 Bug 修复：

> 在最新 `origin/dev` 上追加 corrective commit。

不要拿旧 backup 生成的大 patch 强行套到已经变化的 `dev`；上下文不同就重新做语义级移植。

提交前只允许预期文件：

```powershell
git diff --cached --name-status
git diff --cached --stat
```

---

# 14. 高频踩坑速查

| 现象 | 先查什么 |
|---|---|
| `status: ready`，但不能执行 | resolver / runner / durable runtime |
| `REGISTERED=False` | registry + frozen schema |
| 0 秒 `skill_input_invalid` | input schema / handoff |
| `skill_input_handoff_contract_invalid` | frozen handoff contract / normalizer authority |
| map 已换新 Skill，仍被挡 | execution authorization / fast path |
| dispatch `completed`，但没结果 | artifact `state` / `partial_failure` |
| 页面一直不推进 | durable-worker 是否 crash-loop |
| 改完代码旧任务还是旧行为 | frozen Task / ResearchDesign，换新任务 |
| registry check 一堆错 | 先区分 baseline 无关错误，不要扩 scope |
| patch 套不上最新 dev | baseline 过期，别 force apply |

---

# 15. 最短接入模板

新 Skill / workflow 接入时，按这个顺序走：

```text
1. 找真实 resolver / composition source
2. 定义真实 input / output artifact
3. 写 skill.yaml + schema
4. 接 runtime：resolver → runner → durable authority
5. 写 handoff，只做真实数据转换
6. 接 workflow map
7. 检查 execution authorization
8. 跑 resolver/runtime/handoff 窄 gate
9. rebuild api + durable-worker
10. 新建任务跑 /draft → /confirm → /start
11. clean worktree 提交窄 diff
```

验收标准仍然只有一句话：

> **能确认、能启动、能真实执行；每一步有可见 artifact；下游拿到真实上游结果；缺关键输入时不瞎跑；旧流程不受影响。**
