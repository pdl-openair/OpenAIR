# 四个 MCP 服务的集成冒烟测试

本目录对仓库已上线的 **4 个 MCP 服务**做端到端冒烟测试：MCP 握手 → `tools/list` →
实际调用工具（异步任务含 submit + 轮询）→ 断言返回结构。**不是单元测试**——这些服务
都是 GPU 重推理，脚本必须对着**运行中的实例**跑。

> 仓库本身没有 pytest / linter 依赖，这里也保持一致：**仅用 Python 标准库**（`urllib`
> 手搓 MCP JSON-RPC over streamable-http），`python tests/test_xxx.py` 直接跑，退出码
> `0`=通过 / `1`=失败。

## 文件

| 文件 | 作用 |
|---|---|
| `mcp_client.py` | 共享极简 MCP 客户端（initialize / list_tools / call_tool / 异步 submit+poll） |
| `test_alignn.py` | ALIGNN 性质预测：默认 CrSiTe3 → bandgap/formation_energy/magmom |
| `test_mattergen.py` | MatterGen 结构生成：`generate_crystals_async` + 轮询 |
| `test_diffcsp.py` | DiffCSP CSP 预测：`generate_structures_async(mp_csp, GaTe)` + 轮询 |
| `test_mattersim.py` | MatterSim 弛豫：`relax_structure_async(Si 原胞)` + 轮询 |

## 指向哪个实例

每个脚本读对应环境变量（取不到则用**公网默认**），可随时切到本地实例：

| 脚本 | 环境变量 | 默认（公网） | 本地 |
|---|---|---|---|
| `test_alignn.py` | `ALIGNN_MCP_URL` | `<MATERIALS_MCP_BASE_URL>/alignn/mcp` | `http://localhost:9125/mcp` |
| `test_mattergen.py` | `MATTERGEN_MCP_URL` | `<MATERIALS_MCP_BASE_URL>/mattergen-generate/mcp` | `http://localhost:9126/mcp` |
| `test_diffcsp.py` | `DIFFCSP_MCP_URL` | `<MATERIALS_MCP_BASE_URL>/diffcsp-generate/mcp` | `http://localhost:9127/mcp` |
| `test_mattersim.py` | `MATTERSIM_MCP_URL` | `<MATERIALS_MCP_BASE_URL>/mattersim-relax/mcp` | `http://localhost:9129/mcp` |

> ⚠️ **NAT hairpin**：从集群内部 curl 公网 IP 会返回空，这是 NAT
> 假象不是 bug——测公网路径要从**集群外**跑；集群内请用本地端口或内网 IP。
> 公网/内网地址通过 `MATERIALS_MCP_BASE_URL` 注入，源代码不再硬编码 IP。

## 自定义 CIF（alignn / mattersim）

`test_alignn.py` 和 `test_mattersim.py` 接受 CIF 文件路径覆盖默认结构：

```bash
ALIGNN_CIF_PATH=/path/to/your.cif python test_alignn.py
MATTERSIM_CIF_PATH=/path/to/your.cif python test_mattersim.py
```

未设置时：alignn 走服务端默认 CrSiTe3；mattersim 用脚本内置 Si 原胞。
MatterGen / DiffCSP 是 formula → CIF 生成，不需要 CIF 输入。

## 跑法

```bash
# 单测（任选其一），默认即公网
python test_alignn.py
DIFFCSP_MCP_URL=http://localhost:9127/mcp python test_diffcsp.py

# 一键全跑（公网默认）
for t in alignn mattergen diffcsp mattersim; do python test_$t.py || echo "!! $t 失败"; done
```

## 预期耗时与成本

| 脚本 | 预期耗时 | 备注 |
|---|---|---|
| `test_alignn` | ~10–60s | 首次会从 figshare 下模型 zip（~45MB/个），后续命中缓存 |
| `test_mattergen` | **~2–3 min** | 单次生成固定 ~2 分钟 |
| `test_diffcsp` | ~数十秒–数分钟 | 1000 步 CSP 采样 |
| `test_mattersim` | ~数十秒 | BatchRelaxer，依赖 `/opt/mattersim/*.pth` 已挂载 |

> 公网端点**无鉴权**，跑一次即消耗一次 GPU 推理；无需频繁回归。

## 轮询超时调整

异步脚本的超时可经环境变量放宽（默认值已含余量）：

```bash
MATTERGEN_POLL_TIMEOUT=600 DIFFCSP_POLL_TIMEOUT=900 MATTERSIM_POLL_TIMEOUT=300 ...
```

## 失败语义

服务端统一用 `{value: null, error: "..."}` 表达可恢复失败（非 999/-1 哨兵）。各脚本
遇到这种软失败会把 `error` 打印出来并以退出码 `1` 结束；遇到协议层问题（连不上、
JSON-RPC error、`isError`、轮询超时）同样退出 `1` 并打印原因。
