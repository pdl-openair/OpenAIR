---
name: materials-parameter-recommend-flux
description: "Flux（助熔剂）法制备参数推荐系统。基于相似材料检索 + 参数统计窗口 + Claude 推理的三步 Pipeline，为给定目标材料（如 AlInSe3、CrSiTe3 等新晶体候选）推荐完整 Flux 生长配方（原料配比、助熔剂选择、温度曲线、分离方式）。触发说法：「帮我制备参数推荐」、「用助熔剂法制备 XXX」、「推荐实验方案」、「生成配方」。包含基线方案 + 多个变体方案，所有方案满足实验室约束（最高温度、降温速率、实验时长）。LLM 推理由 Claude 直接完成，计算步骤（相似度评分、统计）由 Python 脚本完成，无需外部 API。不要用于：(a) 非 Flux 法（CVD/水热/固相烧结）→ 用 materials-parameter-recommend-cvd；(b) 查询已知材料属性 → 用 crystal-property-prediction MCP 或 materials-crystal-property-evaluation。"
compatibility: 任何项目（需要 Python 3.12+）
---

# 制备参数推荐系统

基于相似材料检索、参数统计分析和 Claude 推理的制备参数推荐系统。LLM 推理步骤（意图解析、特征提取、配方生成）由 Claude 直接完成，计算步骤（相似度评分、统计分析）由 Python 脚本完成。

## Pipeline 架构

```
用户 Query
  |
  v
Step 1: Claude 意图解析 → 生成结构化 intent JSON
  |
  v
Step 2: Python 相似检索 (similar_retrieval.py) → 相似材料列表
  |
  v
Step 3: Python 统计窗口 (statistic_window.py) → 参数统计窗口
  |
  v
Step 4: Claude 特征提取 → 典型生长方案
  |
  v
Step 5: Claude 配方生成 → 推荐实验方案
  |
  v
输出推荐结果
```

**分工原则**：Claude 做推理（理解、分析、生成），Python 做计算（评分、统计）。

## 快速开始

### 目录说明

- **Skill 根目录** (`<skill_root>`)：包含 `SKILL.md`、`references/`、`resources/` 的目录
- **项目根目录** (`<project_root>`)：用户希望安装代码的目标目录

### 步骤 1：安装

```bash
cd <skill_root>/resources
python install.py --project-root <project_root>
```

安装脚本自动完成：
- 创建 `recommend_parameter/` 子目录及目录结构
- 复制源代码和数据模板
- 复制知识库数据
- 创建虚拟环境并安装依赖（仅 numpy）

无需配置 `.env` 或外部 API 密钥。

### 步骤 2：确认推荐元需求

**重要**：在运行推荐流程之前，读取 `<skill_root>/references/meta-rule.md` 向用户说明默认的推荐元需求和约束，并确认是否需要修改。

**如果需要修改**：
- 读取 `<skill_root>/references/add-constraint-sop.md`
- 按指引修改 `<project_root>/recommend_parameter/data/` 下的相关文件

**如果不需要修改**：直接运行推荐流程。

### 步骤 3：运行推荐流程

按照下方"执行 Pipeline"章节的步骤，依次执行五步 Pipeline。

---

## 执行 Pipeline

以下是你（Claude）执行推荐流程时的完整步骤。用户只需提供 query（如"用助熔剂法制备AlInSe₃"）和 top-k 值。

### 前置：确定路径

```
工作目录 = <project_root>/recommend_parameter/
知识库 = data/knowledge_base/knowledge_base_processed.jsonl
意图模板 = data/similar_mates/intuition_template.jsonl
字段说明 = data/similar_mates/how_to_parse_intuition.jsonl
实验室约束 = data/recommand_window/input_template.jsonl
用户偏好 = data/recommand_recipe/input_real.jsonl
```

### Step 1: Claude 意图解析

**你直接完成此步骤，不调用 Python。**

1. 读取 `data/similar_mates/how_to_parse_intuition.jsonl`（字段定义和提取规则）
2. 读取 `data/similar_mates/intuition_template.jsonl`（输出格式模板）
3. 根据用户 query，按字段规则提取结构化意图信息
4. 输出格式：严格遵循模板的 JSON，去掉所有 `//` 注释

将解析结果写入文件：
```
data/similar_mates/intention_parsed.jsonl
```

输出 JSON 必须包含以下顶层字段：
- `目标材料`：化学式、结构原型、是否二维、是否半导体等
- `实验目标`：生长方法等
- `检索过滤条件`：只看助熔剂法、只看二维材料等
- `相似度设置`：使用模板中的默认权重
- `返回设置`：top_k 值

### Step 2: Python 相似检索

运行 Python 脚本进行相似度计算和检索：

```bash
cd <project_root>/recommend_parameter
source .venv/bin/activate

python -m src.similar_retrieval \
    --query "<用户query>" \
    --query_use_path \
    --query_path data/similar_mates/intention_parsed.jsonl \
    --top-k <top_k> \
    --kb-path data/knowledge_base/knowledge_base_processed.jsonl \
    --intuition-template data/similar_mates/intuition_template.jsonl \
    --output data/similar_mates/similar_output_<材料名>.jsonl
```

### Step 3: Python 统计窗口

运行 Python 脚本进行参数统计分析：

```bash
python -m src.statistic_window \
    --similar-file data/similar_mates/similar_output_<材料名>.jsonl \
    --input-file data/recommand_window/input_template.jsonl \
    --output-file data/recommand_window/window_output_<材料名>.jsonl
```

### Step 4: Claude 特征提取

**你直接完成此步骤，不调用 Python。**

1. 读取 Step 2 的相似材料输出文件
2. 从相似材料的配方列表中，按 5 个维度分析典型生长方案：
   - 助熔剂比例：高稀释(>10) / 中等(2-10) / 低(<2) / 自助熔剂
   - 温度程序：Tmax范围、保温时长、降温速率
   - 生长策略：慢冷大晶体 / 快冷小晶体 / 高温长保温 / 低温保守
   - 分离方式：离心 / 酸洗 / 倾析 / 机械分离
   - 材料适配性：适用的结构类型、族系、维度特征
3. 识别 5-7 种典型生长方案，每种方案包含：
   - 方案ID、方案名称、核心特征、适用场景
   - 代表配方ID列表和对应材料ID-化学式列表

将特征提取结果写入文件：
```
data/recommand_recipe/feature_output_<材料名>.jsonl
```

输出格式：
```json
{
  "配方特征": [
    {
      "配方ID": "rec_001",
      "对应材料ID-化学式": "mat_001-Material",
      "助熔剂比例": "高稀释",
      "温度程序": {"最高温度_摄氏": 1000, "降温速率__℃每小时": 10, "保温时间_h": 24},
      "生长策略": "缓慢降温",
      "分离方式": "酸洗",
      "材料适配性": "适用于XX类材料"
    }
  ],
  "典型生长方案": [
    {
      "方案ID": "scheme_001",
      "方案名称": "高助熔剂稀释方案",
      "核心特征": "...",
      "适用": "...",
      "代表配方ID列表": ["rec_001", "rec_002"],
      "对应材料ID-化学式列表": ["mat_001-X", "mat_002-Y"]
    }
  ]
}
```

### Step 5: Claude 配方生成

**你直接完成此步骤，不调用 Python。**

1. 读取以下文件：
   - Step 3 的参数统计窗口输出
   - Step 4 的特征提取输出
   - `data/recommand_recipe/input_real.jsonl`（用户偏好）
   - Step 2 的相似材料输出（用于溯源）
2. 读取 `<skill_root>/references/meta-rule.md` 中的约束条件
3. 生成推荐实验方案，要求：
   - 包含基线方案（取各参数窗口中值）和变体方案
   - 所有方案满足实验室约束（温度上限、降温速率范围、实验时长）
   - 温度参数考虑元素沸点约束（最高温可超沸点用于熔化，次高温不得超过最低沸点）
   - 助熔剂设计优先安全稳定
   - 每个方案包含完整工艺参数、预期结果标签、溯源信息

将最终结果写入文件：
```
data/recommand_recipe/recommend_output_<材料名>.jsonl
```

输出格式：
```json
{
  "目标材料": {
    "化学式": "...",
    "结构原型": "...",
    "是否二维": false,
    "是否半导体": true,
    "材料族系": []
  },
  "推荐实验方案列表": [
    {
      "方案ID": "方案_A",
      "方案类型": "baseline",
      "方案给人的一句话说明": "...",
      "工艺参数": {
        "原料信息": "...",
        "原料标准化": "形如：Al:In:Se=1:1:3",
        "助熔剂信息": "...",
        "助熔剂标准化": "形如：Sn:AlInS3=10:1",
        "容器": "...",
        "籽晶": "...",
        "温度程序": {
          "是否存在次高温预反应段": "...",
          "升温到次高温时间_h": 10.0,
          "次高温段温度_摄氏": 600.0,
          "次高温段保温时间_h": 12.0,
          "升温到最高温时间_h": 20.0,
          "最高温段保温温度_摄氏": 950.0,
          "最高温段保温时间_h": 24.0,
          "降温速率_主降温_℃每小时": 2.0,
          "降温时间_主降温_h": 100.0,
          "低温段保温温度_摄氏": 700.0,
          "低温段保温时间_h": 24.0,
          "冷却速率_至室温_标签": "炉冷"
        },
        "分离与后处理": {
          "分离方式": "...",
          "分离温度_摄氏": 600.0,
          "晶体的进一步处理": "..."
        }
      },
      "预期结果标签": {
        "预期晶体尺寸": "...",
        "预期风险水平": "...",
        "风险来源简述": [...]
      },
      "溯源信息": {
        "主要参考配方ID": [...],
        "参考材料ID-化学式": [...],
        "参考方案类型ID-名称": [...]
      }
    }
  ],
  "整体备注": [...]
}
```

---

## 自定义配置

### 修改检索参数

编辑 `data/similar_mates/intuition_template.jsonl` 中的权重和过滤条件。

### 修改实验室约束

编辑 `data/recommand_window/input_template.jsonl`：

```json
{
  "实验室约束": {
    "最高允许温度_摄氏": 1000,
    "最小降温速率_℃每小时": 0.5,
    "最大降温速率_℃每小时": 300,
    "最长单次实验时长_h": 200
  }
}
```

### 修改推荐偏好

编辑 `data/recommand_recipe/input_real.jsonl`：

```json
{
  "用户方案设计偏好": {
    "期望方案数量": 5,
    "策略偏好": "多样覆盖",
    "优先大尺寸晶体": true,
    "优先缩短实验时长": false
  }
}
```

### 添加约束规则

读取 `<skill_root>/references/add-constraint-sop.md` 学习如何添加新的约束条件。

---

## 数据目录结构

```
<project_root>/recommend_parameter/
    ├── .venv/
    ├── data/
    │   ├── knowledge_base/
    │   │   └── knowledge_base_processed.jsonl    # 知识库（必需输入）
    │   ├── similar_mates/
    │   │   ├── intuition_template.jsonl          # 意图模板
    │   │   ├── how_to_parse_intuition.jsonl      # 字段定义
    │   │   ├── intention_parsed.jsonl            # Step 1 输出（Claude 生成）
    │   │   └── similar_output_<材料>.jsonl       # Step 2 输出
    │   ├── recommand_window/
    │   │   ├── input_template.jsonl              # 实验室约束
    │   │   └── window_output_<材料>.jsonl        # Step 3 输出
    │   └── recommand_recipe/
    │       ├── input_real.jsonl                  # 用户偏好
    │       ├── feature_output_<材料>.jsonl       # Step 4 输出（Claude 生成）
    │       └── recommend_output_<材料>.jsonl     # Step 5 输出（Claude 生成）
    ├── src/
    │   ├── similar_retrieval.py                  # 相似检索
    │   └── statistic_window.py                  # 统计窗口
    ├── utils/
    └── runner/
```

## 更新知识库

将新材料数据追加到 `data/knowledge_base/knowledge_base_processed.jsonl`：

```json
{"材料ID": "mat_xxx", "化学式": "新材料", "结构原型": "...", "是否二维": false, "是否半导体": true, "配方列表": [...]}
```

---

**版本**：v2.0
**依赖**：Python 3.12+, numpy
**最后更新**：2026-05-07
