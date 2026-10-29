# Steering Playbook (formula scenario)

> Source: design spec §5. Read this each iteration before deciding whether to call `steer`.

## 读 snapshot 时盯的 5 个信号

```
1. failures.diffcsp                  # DiffCSP 失败的公式列表
2. failures.alignn_null              # ALIGNN 拒绝的公式列表
3. failures.mattersim_unconverged    # 弛豫没收敛的公式
4. failures.filtered_out             # 通过硬过滤掉的高分公式
5. top_pool[].formula + values       # 看化学趋势
```

## 触发 → 动作 对照表

| snapshot 观察 | 诊断 | 动作 | steer 命令 |
|---|---|---|---|
| `failures.diffcsp` 占 >50%,失败公式有共性元素 | mp_csp checkpoint 不覆盖此化学空间 | 收紧 hints 到已知 prototype | `steer --crossover-hint prototypes:perovskite,spinel` |
| `failures.diffcsp` 突然变多且配方没变 | DiffCSP sidecar 挂了 | 停止 run,probe,不 steer | (退出) |
| `failures.alignn_null` 集中在某 property | 该化学空间超出 ALIGNN 训练分布 | 改 hint 避开 | `steer --crossover-hint avoid+layered TMD` |
| `failures.filtered_out` 全是同一个 ehull 超标 | 阈值对该化学族太严 | 软化 ehull | `steer --constraint ehull:0.2::10` |
| Pb 反复出现且 reward 低 | 模型 stuck | 硬禁 | `steer --ban Pb` |
| `top_pool` 全是 Ga 族,想要的 In 缺失 | 元素空间没探索够 | 限白名单 | `steer --allow-only Ga,In,Al,N,P,As` |
| reward 连续 2 代不涨 + top_pool 公式重复 | 早收敛 | 加 batch_size + 加 swap_groups | `steer --batch-size 5 --crossover-hint swap_groups+In,Al,Ga` |
| `n_valid < pool_size/2` 持续多代 | 约束过紧 | 软化主要约束 | `steer --weight ehull:5` |
| 某 prototype 反复进 top-K | 该族是富矿 | 强化 hints 指向 | `steer --crossover-hint prototypes:perovskite` |
| top_pool 全 valid + reward 在涨 + 公式多样 | 健康 | 什么都不做 | (直接 step) |
| `mattergen_eval.status == "pending"` 跨多代 | sidecar 慢 | 不 steer,继续 step | (no steer) |

## 三阶段策略

| 阶段 | 占比 | 策略 |
|---|---|---|
| Early (iter 0-30%) | 探索 | 不 ban 元素;不收紧 hint;让模型自由产出,只看 baseline |
| Mid (30-70%) | 收敛 | 看到主导但 unwanted 的元素出现开始 `--ban`;富矿 prototype 浮现时强化 `prototypes:`;failures 多就软化约束 |
| Late (70-100%) | 利用 | 收紧 hints 到 top 表现的 prototype;降 batch_size 做精细搜索;不要 ban 新元素 |

## 不要 steer 的情况

```
healthy:
  - n_valid >= pool_size * 0.6
  - top_pool 公式 unique 率 >= 0.8
  - best_reward 比 iter-1 高(或噪声范围内)
  - failures.diffcsp 比例 < 30%
  → 直接 step

always_skip:
  - iter=1 没有 baseline 就 steer (最大反模式)
  - 单个 alignn 数值异常就 steer (误差 ±0.1-0.3 eV)
  - mattergen-eval status == pending 时改 hints
```

## 反模式

| 反模式 | 为什么坏 |
|---|---|
| iter 1 就 `--ban` 看到的不想要元素 | 没看到模型分布就限空间,starve diversity |
| `--allow-only` 写 4 元素以下 | 公式空间太窄 |
| 反复切换 `--crossover-hint prototypes:` | 每次重新适应,reward 跳跃是噪声 |
| 反复调 `--constraint` 阈值 | filter 抖动让 reward 不可比 |
| 同时多个 `--ban` | 看不清是哪个起作用 |
| 看 alignn `bandgap=2.0` 觉得低就 steer | 单点误差大 |
| mattergen-eval pending 时 steer | 下一代 metrics 还没回 |

## vs structure-agent playbook 差异

| 维度 | structure-agent | formula-agent |
|---|---|---|
| steer 改 hint | 没有(hints 嵌在 MCP 内) | 核心机制(`--crossover-hint`) |
| steer 改 noise | `--noise-level 0.5` | 没有(noise 是 LLM 隐式行为) |
| steer 改 crossover_rate | `--crossover-rate 0.5` | 没有(Claude 自决) |
| MCP 失败处理 | 单纯记错 | steer 改 hint 主动规避 |
