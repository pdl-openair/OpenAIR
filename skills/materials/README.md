# materials

与材料相关**能力域**的 Skill 落点；OpenAIR 整体仍是**通用科研平台**，本目录仅表示一类可选域，不在产品或代码中写死「只做材料」。

## 设计理念

materials pack 采用 **L2 域无关 / L3 域特定** 分层：

- **L2 编排 skill** 用通用动作动词命名（`database-search` / `candidate-generation` / ...），描述跨域可复用的 substage 编排模式
- **L3 atomic skill** 保留 materials- 前缀（`materials-mp-constraint-search` / ...），绑定具体学科工具（MP API、mattergen MCP、Docker XRD 等）

这种分层让 L2 可以被其他域（如 cs_research / opentrajectory）的同类 substage 复用，L3 才绑定具体学科。

## 能力总览

按 OpenAIR 4 阶段 × 5 substage 编排，每个 substage 含 1 个 L2 编排 skill + 1 个 L3 atomic skill（部分占位）。

| Stage | Substage (key) | L2 编排（域无关） | L3 Atomic（materials 实现） | 产物文件 |
|---|---|---|---|---|
| **problem_modeling** | `database_search` | `database-search` | `materials-mp-constraint-search` | `mp_seeds.md` |
| **problem_modeling** | `candidate_generation` | `candidate-generation` | `materials-new-crystal-generation` | `novel_candidates.md` |
| **solution_design** | `parameter_recommendation` | `parameter-recommendation` | `materials-parameter-recommend-flux` | `flux_parameters.md` |
| **execution_validation** | `synthesis_execution` | `synthesis-execution` ⚠️ L3 占位 | —（待落地） | `synthesis_log.md` |
| **insight_generation** | `characterization` | `characterization` | `materials-auto-xrd` | `xrd_report.md` |

## L2 编排 Skills（5 个，域无关）

### `database-search`
- **Substage**：`database_search`
- **职责**：把用户的"多目标性能约束"翻译为对外部数据库的查询，检索满足约束的真实候选
- **消费**：用户约束集（性能上下界 + 元素/组成范围 + 禁用项）
- **产出**：`mp_seeds.md`（constraint_set + candidates[] + verified_by_cppred + coverage_summary）

### `candidate-generation`
- **Substage**：`candidate_generation`
- **职责**：把"性能目标 + 约束 + 可选种子"提交给生成式演化流水线
- **消费**：上游 `mp_seeds.md`（作为种子）+ objectives + constraints
- **产出**：`novel_candidates.md`（objectives + pareto_front[] + run_id + novelty_check）

### `parameter-recommendation`
- **Substage**：`parameter_recommendation`
- **职责**：把"目标候选 + 实验室约束"翻译为可执行的制备方案
- **消费**：上游 `novel_candidates.md` 或 `mp_seeds.md`（选定目标）
- **产出**：`flux_parameters.md`（target_material + flux_recipe + temperature_profile + crucible_atmosphere + expected_phase + risk_notes）

### `synthesis-execution` ⚠️ 占位
- **Substage**：`synthesis_execution`
- **职责**：按上游参数表方案执行制备/合成实验并记录过程
- **状态**：L2 骨架与 spec 已就绪，**L3 atomic skill 暂缺，等待落地**
- **产出**：`synthesis_log.md`（batch_id + sample_id + executed_recipe + timeline + observations[] + yield + product_formula + product_cif_path + spectra_path + deviations）

### `characterization`
- **Substage**：`characterization`
- **职责**：对 `synthesis-execution` 的产物做物相表征，给出相定量、拟合度、置信度
- **消费**：上游 `synthesis_log.md`（sample_id + product_formula + product_cif_path + spectra_path）
- **产出**：`xrd_report.md`（sample_id + model_training + phase_identification[] + residual + confidence + recommended_actions）

## L3 Atomic Skills（4 个 + 1 占位，materials 实现）

### `materials-mp-constraint-search`
- **入口脚本**：`scripts/search_mp_candidates.py`
- **外部依赖**：MP API、crystal-property-prediction MCP（mcp.example.internal:22222，可选交叉验证）
- **能力**：多目标约束（bandgap / formation_energy / ehull + 禁用元素）检索 MP，可选 crystal-property-prediction MCP 验证 9 项属性

### `materials-new-crystal-generation`
- **入口脚本**：`scripts/{submit_run, poll_run, probe_mcp}.py`
- **外部依赖**：mattergen MCP、crystal-property-prediction MCP（精算可选）
- **能力**：经 mattergen MCP 提交 DEMO（TriplePopCMOEA）演化 run、轮询、干预、拉回 Pareto 前沿

### `materials-parameter-recommend-flux`
- **能力**：相似材料检索 + 参数统计窗口 + Claude 推理的三步 Pipeline，给出 Flux 生长配方（原料/助熔剂/温度曲线/分离方式），含基线 + 多变体
- **外部依赖**：无（LLM 推理由 Claude 直接完成，计算步骤 Python 脚本）

### `materials-auto-xrd`
- **外部依赖**：Docker（XRD-1.1 容器）、MP API（CIF 下载）、实测 XRD 谱图
- **能力**：
  - 工作流 1（基础训练推理）：化学式/CIF/MP material ID → 训练 → 真实谱图预测主相+副相（支持双相/三相/指定空间群/MP+CIF 混合）
  - 工作流 2（单目标迭代负样本优化）：只给目标化学式 A，自动找最佳负样本组合多轮重训，直到真实数据达标

## 全链数据流

```
[database-search]        ──mp_seeds.md──▶
[candidate-generation]   ──novel_candidates.md──▶
[parameter-recommend]    ──flux_parameters.md──▶
[synthesis-execution]    ──synthesis_log.md──▶   ⚠ L3 占位
[characterization]       ──xrd_report.md──▶     (回流：主相不纯时 → 重选候选/改工艺/重训)
```

字段契约详见 `schemas/materials/specs/<substage>.md`。

## Fixture 资源

| 路径 | 故事线 | 用途 | 阶段覆盖 |
|---|---|---|---|
| `fixtures/dry_run/materials_chain/` | CrSiTe3 | L2 spec 字段对接的 dry-run 链路（5 个 .md） | 1–5 全覆盖 |
| `fixtures/skill_io/materials_*` | AlInSe3 / CrSiTe3 等 | L3 atomic skill 的单点 request/response 样例 | 各 L3 独立 |
| `fixtures/recorded_runs/materials_chain_ag4ge3s5/` | Ag4Ge3S5（Ag2GeS3 → Ag4Ge3S5 演化） | 前端 UI 端到端演示（含 2 个 CIF） | 1–5 全覆盖 + CIF |

## 前端集成

- **TaskBoard**：领域过滤器含 `materials` 选项，可按 domain 筛选任务
- **TaskDetail**：顶部嵌入 `ChainTimeline` 组件，横向展示 4 stage × 各 substage 的状态/产物计数
- **ArtifactRenderer**：`format=cif` 的产物用 3Dmol.js 渲染晶格（含晶胞边框 + 自转/复位）；其他格式走 markdown / code / csv / pdf / 目录树等通用渲染器

## 当前缺口

- ⚠️ **Stage 4 L3 缺失**：`synthesis-execution` 是占位，需要落地 `materials-synthesis-execution` atomic skill（自动化合成装置 API 或机器人调度协议）
- ⚠️ **真跑依赖外部资源**：MP API、mattergen MCP、crystal-property-prediction MCP、XRD Docker — 缺任一项则对应 stage 需走 fixture 回放
- ⚠️ **live mode producer 注册**：L2 skill 在 live 模式下自动注册 substage artifact 的链路尚未打通，replay/recorded_run 已可用
