---
name: vasp-stability-durable
description: >
  对候选材料执行完整的 VASP 热力学稳定性计算：查询 Materials Project 竞争相、
  生成 VASP 输入文件、提交 jcc HPC 计算、等待结果、解析 OUTCAR 构建凸包相图，
  输出能量凸包距离（ehull）和形成能。适用于需要通过 DFT 计算验证材料热力学稳定性的场景。
---

# vasp-stability-durable

确定性 skill_script：读取 S3 Pareto CIF（取第一名），运行完整 DFT 稳定性计算流水线。

## 工作流

1. 从 `pareto_cif_paths[0]` 读取 CIF，用 pymatgen 解析化学式和原子数
2. 调用 `vasp-thermodynamic-stability/scripts/setup_competing_phases.py` 生成 VASP 输入
3. 若 `VASP_PSP_DIR` 未配置，用 `jcc hpc vaspkit` 生成 POTCAR
4. `jcc hpc create` 提交弛豫计算，task_name = `{formula}_{natoms}at_relax`
5. 每 30s 轮询 `jcc hpc status`，最多 18 次（9 分钟），内部超时 550s
6. `jcc hpc result download` 下载结果 zip
7. 从 OUTCAR 提取 `energy(sigma->0)`
8. 调用 `analyze_stability.py` 构建相图，计算 ehull 和形成能
9. 写入 result.json，含 `handoff_summary` 自然语言摘要供 S9 消费

## 必需环境变量

| 变量 | 用途 |
|---|---|
| `JCC_BASE_URL` | jcc 服务地址 |
| `JCC_USERNAME` | 账号 |
| `JCC_PASSWORD` | 密码 |
| `JCC_CLUSTER_NAME` | 目标集群（默认 `HPCSlurmArm`） |
| `MP_API_KEY` | Materials Project API（竞争相查询） |
| `VASP_PSP_DIR` | PAW 赝势目录（可选，缺失时用 vaspkit 服务） |

凭证模板见 `.env`（不要提交到 git）。

## 超时策略

- 内部超时：550s（留 50s 供 worker 清理）
- 超时时输出 `state: partial_failure`，`failure_reason: jcc_timeout`
- jcc GLEX OOM 时自动降半 ntasks 重试一次

## 输出

`result.json` 包含：
- `state`: succeeded / partial_failure
- `target_formula`: 化学式
- `ehull_eV`: 能量凸包距离 (eV/atom)
- `formation_energy_eV_per_atom`: 形成能
- `stability_verdict`: stable / metastable / unstable / calculation_incomplete
- `handoff_summary`: 自然语言摘要，供 S9 直接注入 system prompt
