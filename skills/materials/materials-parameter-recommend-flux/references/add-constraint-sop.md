# 制备参数推荐系统：添加新约束的标准操作流程

当用户需要为制备参数推荐系统添加新的约束条件（如温度限制、助熔剂规则、工艺参数约束等）时，使用本 SOP 进行标准化修改。

---

## 1. 系统架构速查

推荐系统采用三步 Pipeline 架构，理解每个步骤的功能有助于定位正确的修改位置。

### Pipeline 执行流程

```
用户 Query
  ↓
Step 1: similar_retrieval (相似检索)
  ↓
Step 2: statistic_window (统计窗口)
  ↓
Step 3: recommend_recipe (推荐配方) ← 【主要修改位置】
  ↓
输出推荐结果
```

### 核心文件定位

| 步骤 | 文件路径 | 主要功能 | 修改频率 |
|------|----------|----------|----------|
| **Step 3** | `src/recommend_recipe.py` | 配方方案生成（包含约束验证） | **高** |
| Step 2 | `src/statistic_window.py` | 参数统计与窗口计算 | 中 |
| Step 1 | `src/similar_retrieval.py` | 用户意图解析与相似检索 | 低 |
| Step 1 | `utils/parse_intuition.py` | LLM 意图解析 | 低 |

### Step 3 中的关键函数（`src/recommend_recipe.py`）

| 函数名 | 功能 | 修改场景 |
|--------|------|----------|
| `build_scheme_generation_prompt()` | 构建 LLM Prompt（包含约束指令） | **添加 Prompt 指令时必改** |
| `build_baseline_scheme()` | 生成基线方案（含约束计算逻辑） | **添加约束计算逻辑时必改** |
| `build_feature_extraction_prompt()` | 从相似配方提炼典型方案 | 很少修改 |

---

## 2. 约束类型分类与修改决策

在动手修改前，先确定新约束属于哪种类型，这将决定修改的位置和方式。

### 约束类型速查表

| 约束类型 | 典型示例 | Prompt 修改 | 逻辑修改 |
|----------|----------|-------------|----------|
| **温度约束** | "Tmax ≤ 元素沸点" | ✅ 必须 | ✅ 必须 |
| **次高温约束** | "次高温 ≤ 元素沸点" | ✅ 必须 | ✅ 必须 |
| **助熔剂约束** | "助熔剂安全性要求" | ✅ 必须 | ❌ 不需要 |
| **工艺约束** | "保温时间上限" | ✅ 必须 | ⚠️ 视情况 |
| **意图解析约束** | "新增过滤条件" | ❌ 不需要 | ❌ 需要改其他文件 |

### 修改位置决策树

```
新约束是什么类型？
│
├─ 温度/次高温约束
│   ├─ 在 Prompt 中说明 → 修改 build_scheme_generation_prompt()
│   └─ 在代码中计算验证 → 修改 build_baseline_scheme()
│
├─ 助熔剂/安全性约束
│   └─ 仅在 Prompt 中说明 → 修改 build_scheme_generation_prompt()
│
└─ 统计/窗口约束
    └─ 在统计阶段过滤 → 修改 statistic_window.py
```

---

## 3. 标准修改流程

### 3.1 读取当前代码状态

在修改前，先读取相关文件了解当前实现：

**必读文件**：
```bash
# 读取 recommend_recipe.py 中的相关函数
# 位置 1: build_scheme_generation_prompt() - 约第475行
# 位置 2: build_baseline_scheme() - 约第320行
```

**读取命令**：
```bash
cat src/recommend_recipe.py | head -n 500 | tail -n 200
```

### 3.2 定位修改点

#### 修改点 1：Prompt 指令（`build_scheme_generation_prompt()`）

在函数中找到约束说明部分，添加新的约束指令。

**典型位置**：约第475-485行，在 `lab_constraints_str` 之后

**当前代码模式**：
```python
lab_constraints_str = f"""- 最高允许温度: {lab_constraints.get('最高允许温度_摄氏', 1000)}℃
- 降温速率范围: ...
"""

prompt = f"""...
实验室约束：
{lab_constraints_str}
制备元素沸点温度约束：
- 最高温段保温温度不得超过制备元素中沸点最低的温度
...
"""
```

#### 修改点 2：约束计算逻辑（`build_baseline_scheme()`）

在函数末尾、return 语句之前添加约束检查和修正逻辑。

**典型位置**：约第400-420行，在 Tmax 约束检查之后

**当前代码模式**：
```python
def build_baseline_scheme(param_windows: Dict, target_material: Dict) -> Dict:
    # ... 基线方案构建代码 ...

    # Tmax 约束检查（已存在）
    if tmax_val > allowed_max_temp:
        baseline['工艺参数']['温度程序']['最高温段保温温度_摄氏'] = round(safe_limit, 1)

    # ← 在这里添加新的约束检查

    return baseline
```

### 3.3 修改代码

#### 添加 Prompt 指令

```python
# 在 build_scheme_generation_prompt() 中，找到约束说明部分，添加：

制备元素沸点温度约束：
- 最高温段保温温度不得超过制备元素中沸点最低的温度
- 【新增】次高温段温度同样不得超过制备元素中沸点最低的温度
- 温度层次原则：最高温段 > 次高温段 > 低温段，相邻段温差建议≥50℃
```

#### 添加约束计算逻辑

```python
# 在 build_baseline_scheme() 中，在 return baseline 之前添加：

# 【新增】次高温段温度约束检查
try:
    th = baseline['工艺参数']['温度程序'].get('次高温段温度_摄氏')
    th_val = float(th) if th else None

    if th_val is not None and allowed_max_temp is not None:
        safe_limit = allowed_max_temp - 5.0  # 留5℃安全余量
        if th_val > safe_limit:
            logger.warning(f"次高温段温度 ({th_val}℃) 超过限制。调整至 {safe_limit}℃")
            baseline['工艺参数']['温度程序']['次高温段温度_摄氏'] = round(safe_limit, 1)
except Exception:
    pass
```

---

## 4. 常见约束修改示例

### 4.1 添加温度约束

**需求**：约束最高温度不超过某个值

**修改点**：
1. `build_scheme_generation_prompt()` - 添加 Prompt 指令
2. `build_baseline_scheme()` - 添加约束计算

**示例代码**：
```python
# Prompt 中添加：
温度约束：最高温段保温温度不得超过 1000℃

# 逻辑中添加：
if tmax_val > 1000:
    baseline['工艺参数']['温度程序']['最高温段保温温度_摄氏'] = 1000
```

### 4.2 添加助熔剂安全性约束

**需求**：助熔剂必须安全稳定

**修改点**：
1. `build_scheme_generation_prompt()` - 添加 Prompt 指令
2. 不需要修改逻辑代码

**示例代码**：
```python
# Prompt 中添加：
助熔剂安全要求：
- 所有方案里的助熔剂设计需优先考量安全性与稳定性
- 规避剧毒、强腐蚀性、高挥发性及化学性质活泼的试剂
- 优先选用安全稳定的助熔剂
```

### 4.3 添加工艺参数约束

**需求**：保温时间不超过某个上限

**修改点**：
1. `build_scheme_generation_prompt()` - 添加 Prompt 指令
2. `build_baseline_scheme()` - 添加约束计算

**示例代码**：
```python
# Prompt 中添加：
保温时间约束：最高温段保温时间不得超过 48h

# 逻辑中添加：
soak_time = baseline['工艺参数']['温度程序'].get('最高温段保温时间_h', 0)
if soak_time > 48:
    baseline['工艺参数']['温度程序']['最高温段保温时间_h'] = 48
```

---

## 5. 测试验证流程

### 5.1 本地运行（完整 Pipeline）

**注意**：必须激活虚拟环境后运行完整的 Pipeline

```bash
# 1. 进入项目目录
cd <你的项目目录>

# 2. 激活虚拟环境
source .venv/bin/activate

# 3. 运行完整的三步 Pipeline
python runner/run_pipeline.py \
    --query "用助熔剂法制备AlInSe₃" \
    --top-k 20 \
    --data-dir data \
    --similar-output data/similar_mates/similar_output.jsonl \
    --window-output data/recommand_window/window_output.jsonl \
    --recommend-output data/recommand_recipe/recommend_output.jsonl \
    --feature-output data/recommand_recipe/feature_output.jsonl \
    --debug
```

**Pipeline 参数说明**：
- `--query`：用户查询（目标材料描述）
- `--top-k`：返回的相似材料数量
- `--data-dir`：数据目录路径
- `--similar-output`：相似材料检索输出文件（可选）
- `--window-output`：统计窗口输出文件（可选）
- `--recommend-output`：配方推荐输出文件（可选）
- `--feature-output`：配方特征输出文件（可选）
- `--debug`：启用调试模式（打印详细信息）

### 5.2 本地单步测试（可选）

如需单独测试某个步骤，可以运行：

```bash
# Step 1: 相似检索
python -m src.similar_retrieval \
    --query "用助熔剂法制备AlInSe₃" \
    --top-k 20 \
    --kb-path data/knowledge_base/knowledge_base_processed.jsonl \
    --intuition-template data/similar_mates/intuition_template.jsonl \
    --requirement-template data/similar_mates/how_to_parse_intuition.jsonl \
    --output data/similar_mates/similar_on-base_processed.jsonl

# Step 2: 统计窗口
python -m src.statistic_window \
    --similar-file data/similar_mates/similar_on-base_processed.jsonl \
    --input-file data/recommand_window/input_template.jsonl \
    --output-file data/recommand_window/output_result.jsonl

# Step 3: 推荐配方
python -m src.recommend_recipe \
    --similar-file data/similar_mates/similar_on-base_processed.jsonl \
    --window-file data/recommand_window/output_result.jsonl \
    --input-file data/recommand_recipe/input_real.jsonl \
    --output-file data/recommand_recipe/output_result.jsonl \
    --feature-output data/recommand_recipe/similar_recipes_feat_result.jsonl
```

### 5.3 检查输出要点

验证新约束是否生效：

1. **温度约束** → 检查输出 JSON 中温度字段是否被正确约束
2. **助熔剂约束** → 检查 LLM 生成的方案是否遵循安全要求
3. **工艺约束** → 检查相关参数是否在允许范围内

---

## 6. 快速参考清单

每次添加新约束时，按以下清单检查：

- [ ] 1. 确定约束类型和修改点
- [ ] 2. 在 `build_scheme_generation_prompt()` 中添加 Prompt 指令
- [ ] 3. 在 `build_baseline_scheme()` 中添加约束计算逻辑（如需要）
- [ ] 4. 确保约束计算包含安全余量（如 -5℃）
- [ ] 5. 检查相关参数之间的依赖关系（如 Tmax > 次高温）
- [ ] 6. 更新沸点参考表（如有新增元素）
- [ ] 7. 测试验证

---

## 7. 关键代码位置速查

### `src/recommend_recipe.py` 行号参考

| 功能 | 起始行 | 结束行 |
|------|--------|--------|
| `build_scheme_generation_prompt()` | ~460 | ~560 |
| `build_baseline_scheme()` | ~320 | ~420 |
| 温度约束检查 | ~380 | ~420 |
| 次高温约束检查 | ~400 | ~420 |

### Prompt 中的约束说明位置

约第475行：
```python
lab_constraints_str = f"""- 最高允许温度: ..."""
# 在这之后添加新的约束说明
```

### 沸点参考表（常见元素）

| 元素 | 沸点(℃) | 元素 | 沸点(℃) |
|------|---------|------|---------|
| S | 444 | Mg | 1090 |
| Se | 684 | Ca | 1484 |
| Te | 988 | Sr | 1382 |
| Zn | 907 | Ba | 1870 |
| Cd | 767 | In | 2072 |
| Ga | 2204 | Al | 2470 |

---

## 8. 约束修改的完整示例

### 8.1 场景：添加新的温度约束

**需求**：限制次高温段温度不超过 600℃

**步骤 1：修改 Prompt**

在 `build_scheme_generation_prompt()` 中找到约束说明部分，添加：

```python
次高温温度约束：
- 次高温段保温温度不得超过 600℃
```

**步骤 2：修改约束计算逻辑**

在 `build_baseline_scheme()` 中，添加：

```python
# 次高温温度约束检查
try:
    th = baseline['工艺参数']['温度程序'].get('次高温段温度_摄氏')
    th_val = float(th) if th else None

    if th_val is not None:
        max_allowed_th = 600.0
        safe_limit = max_allowed_th - 5.0  # 留5℃安全余量

        if th_val > safe_limit:
            logger.warning(f"次高温段温度 ({th_val}℃) 超过 600℃ 限制。调整至 {safe_limit}℃")
            baseline['工艺参数']['温度程序']['次高温段温度_摄氏'] = round(safe_limit, 1)
except Exception:
    pass
```

**步骤 3：测试验证**

```bash
python runner/run_pipeline.py --query "用助熔剂法制备xxx" --debug
```

检查输出中次高温段温度是否 ≤ 595℃。

---

## 9. 常见问题与解决方案

### 问题 1：约束不生效

**可能原因**：
- Prompt 中没有添加指令
- 约束计算逻辑位置不对
- 日志被过滤

**解决方案**：
1. 检查 Prompt 中是否有约束指令
2. 确认约束计算在 return 之前
3. 使用 `--debug` 查看详细日志

### 问题 2：约束冲突

**可能原因**：
- 两个约束互相矛盾
- 约束与用户偏好冲突

**解决方案**：
1. 检查约束优先级
2. 调整约束计算顺序
3. 在日志中提示冲突

### 问题 3：新元素沸点未定义

**解决方案**：
1. 在 `boiling_points` 字典中添加新元素
2. 查找可靠的元素沸点数据
3. 建议使用摄氏温标

---

**文档版本**：v1.0
**适用系统**：preparation-recommendation v1.0+
**最后更新**：2026-03-24
**相关文档**：
- [SKILL.md](SKILL.md) - 完整使用指南
- [meta-rule.md](meta-rule.md) - 默认元需求与约束
