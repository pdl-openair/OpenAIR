# 材料科学 MCP Server 总览

本文档汇总 OpenAIR 材料科学场景下可用的三个 MCP server,涵盖数据制备、训练、生成、评估、部署的端到端流程。

---

## 1. material-platform(协同开发平台)

**定位**:材料科学研究全流程平台,负责代码/数据/模型资产管理、训练任务调度、应用部署。

### 核心能力

| 能力域 | 关键工具 | 说明 |
|---|---|---|
| **认证** | `login` | 读取 `mcp/.env` 的 `MMP_USERNAME/MMP_PASSWORD`,返回 token |
| **代码资产** | `code_query` / `code_create` / `code_update` / `code_delete` | 管理 Git 仓库配置 |
| **数据集** | `dataset_create` / `dataset_create_with_version` / `dataset_query` / `dataset_get_version_list` / `dataset_download_*` | 版本化数据集管理 |
| **模型** | `model_create` / `model_create_version` / `model_query` / `model_get_detail` | 预训练/导出模型管理 |
| **元数据推断** | `infer_dataset_metadata` / `infer_model_metadata` | 自动推断 data_type / model_type |
| **资源查询** | `list_resource_types` → `list_resource_specs` → `list_training_images` | JSM 资源/镜像选择 |
| **训练(模式 A)** | `validate_training_params` → `run_training_pipeline` | 从零搭建,默认 `wait=false` |
| **训练(模式 B)** | `list_workflows` → `get_workflow_detail` → `create_and_run_experiment` | 复用已保存流水线 |
| **训练(模式 C)** | `prepare_quick_training` / `provision_training_assets` | 自动建数据集/模型 + 选最大资源规格 |
| **训练进度** | `poll_training_progress` / `watch_experiment` / `wait_experiment` | 轮询至终态,含增量日志 |
| **导出模型** | `ensure_export_model` | 训练完成后自动创建导出目标,默认无需询问 |
| **应用部署** | `service_create` → `service_prepare_version` → `service_create_version` → `poll_service_deploy_progress` | 智算应用部署全流程 |

### 典型工作流

```
login → 准备资产(代码/数据/模型) → 选资源/镜像
  → 训练(模式 A/B/C) → poll_training_progress
  → 导出模型 → 创建应用版本 → 部署上线
```

---

## 2. mattergen(晶体生成与进化优化)

**定位**:基于 MatterGen 的晶体材料生成 + 多目标进化优化,产出 Pareto 前沿候选结构。

### 核心能力

| 能力域 | 关键工具 | 说明 |
|---|---|---|
| **会话管理** | `create_evolution_session` / `list_evolution_sessions` / `delete_evolution_session` | 创建/查询/删除进化会话 |
| **一键自动进化** | `run_autonomous_evolution` → `get_autonomous_evolution_result` | 异步提交 + 轮询,推荐用法 |
| **交互式进化** | `start_evolution` / `step_evolution` / `continue_evolution` | 逐步推进,支持干预点暂停 |
| **进度查询** | `get_evolution_status` / `get_evolution_population` / `get_evolution_pareto_front` | 当前代数/种群/Pareto 前沿 |
| **约束管理** | `add_evolution_constraint` | 动态添加性质约束(如 bandgap 0.2~3.5 eV) |
| **适应度干预** | `modify_crystal_fitness` | 人工调整个体适应度(乘/加系数) |
| **领域知识注入** | `inject_evolution_knowledge` | 将专家知识写入会话历史 |

### 关键参数

- **objectives_json**:优化目标,如 `{"ehull_eV":"minimize","dielectric_epsx":"maximize"}`
- **constraints_json**:性质约束,如 `{"bandgap_eV":{"min":0.2,"max":3.5,"weight":5.0}}`
- **initial_pool_mode**:`random` / `cif_dir` / `hybrid`
- **seed_cif_files**:支持 base64 单个或 JSON 数组批量

### 典型工作流

```
run_autonomous_evolution(seed_cif_files=<base64>, objectives, constraints)
  → get_autonomous_evolution_result(task_id) 轮询
  → get_evolution_pareto_front(session_id) 取最优候选
```

---

## 3. crystal-property-prediction(FLUX 性质预测)

**定位**:对单个 CIF 结构一次调用跑通 MatterSim 弛豫 + ALIGNN + CHGNet + JACS 多模型流水线,返回 9 项关键标量。

### 核心能力

仅一个工具:`predict_flux_properties`,支持四种 CIF 输入(互斥):

| 输入方式 | 参数 | 适用场景 |
|---|---|---|
| 服务端路径 | `cif_path` | 容器内已有 CIF 文件 |
| CIF 正文 | `cif_content` | 远程 MCP 推荐,无需落盘 |
| Base64 编码 | `cif_base64` | 支持纯 base64 或 data URI 前缀 |
| 自动识别 | `cif` | 像路径按路径,像正文按正文 |

### 返回的 9 项标量(properties)

| 字段 | 含义 | 来源模型 |
|---|---|---|
| `bandgap_ev` | 带隙 (eV) | ALIGNN jv_optb88vdw_bandgap |
| `exfoliation_energy_mev` | 剥离能 (meV/atom) | CHGNet 2D/bulk 能量差 |
| `formation_energy_ev` | 形成能 (eV/atom) | ALIGNN jv_formation_energy_peratom |
| `cl_score` | 合成可行性 CLscore | JACS |
| `f_max_ev_a` | 弛豫后最大原子力 (eV/Å) | MatterSim |
| `hull_ev` | 凸包能 ehull (eV/atom) | ALIGNN |
| `num_elements` | 元素种类数 | pymatgen |
| `element_set` | 元素符号列表(如 Fe,O) | pymatgen |
| `sg_number` | 空间群国际编号 (1-230) | pymatgen |

**`fields` 参数**:可选返回子集,避免重复计算。

### 典型工作流

```
predict_flux_properties(cif_content=<CIF 文本>, name="LiFePO4")
  → properties.bandgap_ev / hull_ev / cl_score ...
```

---

## 端到端协同场景

三个 MCP 可串联完成"生成 → 评估 → 训练 → 部署"闭环:

```
1. mattergen: 生成候选晶体        → Pareto 前沿 CIF 列表
2. crystal-property-prediction:   → 评估每个 CIF 的 9 项性质
   批量预测性质
3. material-platform:             → 用优质候选作为数据集训练模型
   制备数据 + 训练 + 导出
4. material-platform:             → 模型上线服务
   部署为智算应用
```

### 示例:二维材料发现

```
# 1. 生成
mattergen.run_autonomous_evolution(
    objectives_json='{"ehull_eV":"minimize","bandgap_ev":"target_range"}',
    constraints_json='{"bandgap_eV":{"min":0.2,"max":3.5,"weight":5.0}}',
    seed_cif_files=<base64 种子>
)

# 2. 评估 Pareto 前沿
mattergen.get_evolution_pareto_front(session_id) → CIF 列表
for cif in candidates:
    crystal-property-prediction.predict_flux_properties(cif_content=cif)

# 3. 训练
material-platform.prepare_quick_training(
    git_url=..., dataset_file_path=评估结果.csv, ...
)

# 4. 部署
material-platform.service_create_version(...)
```

---

## 使用约定

- **登录**:`material-platform` 所有工具需要 token,先调 `login()`(无参读 `mcp/.env`)
- **导出模型**:默认自动创建,禁止询问用户
- **资源选择**:模式 C 自动选最大规格;模式 A/B 需 `list_resource_types` → `list_resource_specs`
- **进度轮询**:训练用 `poll_training_progress`(增量推送),部署用 `poll_service_deploy_progress`
- **查询库**:默认先查个人库 `is_public=false`,展示后提示可查公开库 `is_public=true`
