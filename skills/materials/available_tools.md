# Materials Skills — 可用工具清单

本文档汇总 `skills/materials/` 各 skill 运行时实际依赖的外部工具,涵盖 MCP 服务、CLI 命令、Python 库与环境变量。L2 编排 skill(`candidate-generation` / `characterization` / `database-search` / `parameter-recommendation` / `stability-verification` / `synthesis-execution`)自身不直接调用工具,仅编排下列 L1 工具。

---

## 一、MCP 服务

### 1. `material-platform`(协同开发平台)

**定位**:代码 / 数据 / 模型资产管理、训练调度、应用部署。需要 `login` 获取 token(无参时读 `mcp/.env`)。

| 能力域 | 关键工具 |
|---|---|
| 认证 | `login` |
| 代码资产 | `code_query` / `code_create` / `code_update` / `code_delete` |
| 数据集 | `dataset_create` / `dataset_create_with_version` / `dataset_query` / `dataset_get_version_list` / `dataset_download_*` |
| 模型 | `model_create` / `model_create_version` / `model_query` / `model_get_detail` |
| 元数据推断 | `infer_dataset_metadata` / `infer_model_metadata` |
| 资源查询 | `list_resource_types` → `list_resource_specs` → `list_training_images` |
| 训练(模式 A 从零) | `validate_training_params` → `run_training_pipeline` |
| 训练(模式 B 复用流水线) | `list_workflows` → `get_workflow_detail` → `create_and_run_experiment` |
| 训练(模式 C 快速) | `prepare_quick_training` / `provision_training_assets` |
| 进度轮询 | `poll_training_progress` / `watch_experiment` / `wait_experiment` |
| 导出模型 | `ensure_export_model`(默认自动创建,禁止询问) |
| 应用部署 | `service_create` → `service_prepare_version` → `service_create_version` → `poll_service_deploy_progress` |

**承载 skill**:`mmp-resources` / `mmp-training-workflow` / `mmp-deploy-app`。
**注入方式**:通过 `OPENAIR_MMP_MCP_URL` 环境变量写入 Claude Agent SDK `mcp_servers`。

---

### 2. `crystal-property-prediction`(cppred / FLUX)

**定位**:对单个 CIF 一次调用跑通 MatterSim 弛豫 + ALIGNN + CHGNet + JACS 多模型流水线。

| 工具 | 输入 | 输出 |
|---|---|---|
| `predict_flux_properties` | 四选一:`cif_path` / `cif_content` / `cif_base64` / `cif`(自动识别);可选 `name`、`fields` 子集 | 9 项标量(见下) |

**返回 9 项标量**:`bandgap_ev`、`exfoliation_energy_mev`、`formation_energy_ev`、`cl_score`、`f_max_ev_a`、`hull_ev`、`num_elements`、`element_set`、`sg_number`。

**承载 skill**:
- `materials-new-crystal-generation` — 进化循环内逐候选 CIF 评估
- `materials-mp-constraint-search` — 可选 `--verify-cppred` 后过滤 `cl_score / f_max / exfoliation_energy`

---

### 3. `mattergen`(DEMO 进化引擎)

**定位**:晶体材料生成 + 多目标进化优化,产出 Pareto 前沿候选结构。

| 能力域 | 关键工具 |
|---|---|
| 会话管理 | `create_evolution_session` / `list_evolution_sessions` / `delete_evolution_session` |
| 一键自动进化(推荐) | `run_autonomous_evolution` → `get_autonomous_evolution_result`(异步提交 + 轮询) |
| 交互式进化 | `start_evolution` / `step_evolution` / `continue_evolution` |
| 进度查询 | `get_evolution_status` / `get_evolution_population` / `get_evolution_pareto_front` |
| 约束管理 | `add_evolution_constraint`(动态添加性质约束,如 bandgap 0.2~3.5 eV) |
| 适应度干预 | `modify_crystal_fitness`(乘 / 加系数) |
| 领域知识注入 | `inject_evolution_knowledge` |

**关键参数**:`objectives_json`、`constraints_json`、`initial_pool_mode`(`random` / `cif_dir` / `hybrid`)、`seed_cif_files`(base64 单个或 JSON 数组批量)。

**承载 skill**:`materials-new-crystal-generation`。

> ⚠️ **MCP 版本说明**:当前 IDE 注册的 MCP 为更细粒度的 `alignn_prediction` / `diffcsp-generate` / `mattergen-generate` / `mattersim-relax`,与本节描述的 `mattergen`(evolution 封装)和 `crystal-property-prediction`(flux 流水线封装)是**不同世代**的封装。权威清单参见 `materials-new-crystal-generation/references/mcp_servers.md`。

---

## 二、CLI / 可执行命令

| 工具 | 用途 | 关键约定 | 使用 skill |
|---|---|---|---|
| **`jcc` CLI** | 远端 HPC Slurm 集群任务提交 / 查询 / 下载 | 用户自安装,需在 `PATH`;`submit_all.sh` 自动以 `--task-name <Formula>_<Nat>at_relax`;`run_hpc.sh --local-path --output-dir --task-name --nodes --ntasks` | `vasp-thermodynamic-stability` |
| **`python -m src.similar_retrieval`** | 相似材料检索 | 项目根目录运行 | `materials-parameter-recommend-flux` |
| **`python -m src.statistic_window`** | 参数统计窗口 | 项目根目录运行 | `materials-parameter-recommend-flux` |
| **`python install.py --project-root <root>`** | Flux skill 一次性安装 | — | `materials-parameter-recommend-flux` |
| **`jq`** | 解析 jcc 任务 JSON | 与 `jcc` 配套 | `vasp-thermodynamic-stability` |

### `jcc` 资源规格约定

| 单元格规模 | 推荐 `--nodes` × `--ntasks` | INCAR 配套 |
|---|---|---|
| 小 / 中(≤30 atoms) | `--nodes 2 --ntasks 8` | `KPAR=2 NCORE=4` |
| 大(≥60 atoms) | `--nodes 1 --ntasks 4` | 避免 GLEX OOM |

> `jcc --help` 中 "ntasks per-node" 文案错误,实测为总 MPI 进程数(`nodes=2 ntasks=8 → 8 total cores`)。

---

## 三、Python 库

| 库 | 用途 | 使用 skill |
|---|---|---|
| **`pymatgen`** | CIF 解析、`StructureMatcher` 去重、`Kpoints.automatic_density()` 生成 KPOINTS、POTCAR 装配、`analysis.phase_diagram.PhaseDiagram` 凸包分析、对称性(sg_number) | `vasp-thermodynamic-stability`、`materials-mp-constraint-search`、`materials-new-crystal-generation`(emo_engine) |
| **`mp-api`** | Materials Project 数据库查询(`MPRester`) | `vasp-thermodynamic-stability`、`materials-mp-constraint-search` |
| **`numpy`** | 数值计算 | `vasp-thermodynamic-stability` |

---

## 四、环境变量

| 变量 | 用途 | 必填 |
|---|---|---|
| `MP_API_KEY` | Materials Project API 密钥 | 是(涉及 MP 查询时) |
| `JCC_USERNAME` / `JCC_PASSWORD` | jcc CLI HPC 登录凭据 | 是(涉及 VASP 提交时) |
| `VASP_PSP_DIR` | VASP pseudopotential 目录(POTCAR 生成) | 是(涉及 VASP 输入生成) |
| `JCC_NODES` | jcc 默认节点数(默认 `1`,CLI `--nodes` 覆盖) | 否 |
| `JCC_NTASKS` | jcc 默认 MPI 进程数(默认 `4`,CLI `--ntasks` 覆盖) | 否 |
| `MMP_USERNAME` / `MMP_PASSWORD` | material-platform 平台登录凭据(写入 `mcp/.env`) | 是(平台 skill) |
| `OPENAIR_MMP_MCP_URL` | material-platform MCP 端点,注入 Agent SDK | 是(平台 skill) |

---

## 五、典型闭环

```
MP 查询
  materials-mp-constraint-search (mp_api + 可选 cppred 验证)
    ↓ seeds
进化生成
  mattergen.run_autonomous_evolution (objectives + constraints)
    ↓ Pareto 前沿 CIF 列表
性质评估
  crystal-property-prediction.predict_flux_properties (9 项标量)
    ↓ 优质候选
VASP 精修稳定性
  vasp-thermodynamic-stability (jcc CLI + pymatgen PhaseDiagram)
    ↓ 训练数据
平台训练 / 部署
  material-platform (prepare_quick_training → ensure_export_model → service_create_version)
```
