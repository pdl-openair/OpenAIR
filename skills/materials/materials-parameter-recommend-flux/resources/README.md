# 制备参数推荐系统 - 使用指南

这是一个独立的 Skill，可在任何项目中快速部署制备参数推荐功能。

## 📦 Skill 内容

```
preparation-recommendation/
├── SKILL.md                    # Skill 说明文档
└── resources/
    ├── install.py              # 自动安装脚本
    ├── run.sh                 # 快速运行脚本
    ├── requirements.txt       # Python 依赖
    ├── pyproject.toml         # 项目配置
    ├── .env.example           # 环境变量示例
    ├── src/                   # 核心源代码
    │   ├── similar_retrieval.py
    │   ├── statistic_window.py
    │   ├── recommend_recipe.py
    │   └── reference_api.py
    ├── utils/                 # 工具函数
    │   ├── parse_intuition.py
    │   ├── response2json.py
    │   ├── matai_api.py
    │   └── ...
    ├── runner/                # 运行脚本
    │   ├── run_pipeline.py
    │   └── api_server.py
    └── data/                  # 数据模板
        ├── knowledge_base/
        ├── similar_mates/
        ├── recommand_window/
        └── recommand_recipe/
```

## 🚀 快速开始

### 1. 安装

在目标项目目录下运行：

```bash
python resources/install.py
```

这会自动完成：
- ✅ 创建目录结构
- ✅ 复制源代码
- ✅ 设置数据模板
- ✅ 创建虚拟环境
- ✅ 生成配置文件

### 2. 配置

编辑 `.env` 文件，添加你的 API 密钥：

```bash
nano .env
```

```bash
LLM_API_KEY=sk_xxx  # 替换为你的密钥
```

### 3. 运行

```bash
# 激活虚拟环境
source .venv/bin/activate

# 方式1：使用快速脚本
./run.sh "用助熔剂法制备AlInSe₃" 20

# 方式2：直接运行
python runner/run_pipeline.py --query "用助熔剂法制备AlInSe₃" --top-k 20
```

### 4. 查看结果

结果会自动保存到 `data/` 目录：

```
data/
├── recommand_recipe/
│   ├── output_result-FromContainer.jsonl          # 推荐配方
│   └── similar_recipes_feat_result-FromContainer.jsonl  # 配方特征
└── ...
```

## 🔧 自定义配置

### 修改查询参数

编辑 `data/similar_mates/intuition_template.jsonl`：

```json
{
  "目标材料": {
    "化学式": "你的材料",
    "结构原型": "结构类型"
  }
}
```

### 修改实验室约束

编辑 `data/recommand_window/input_template.jsonl`：

```json
{
  "实验室约束": {
    "最高允许温度_摄氏": 1000,
    "最长单次实验时长_h": 200
  }
}
```

## 📚 详细文档

完整的文档请查看 [SKILL.md](SKILL.md)。

## 🐛 常见问题

### Q: 安装脚本失败怎么办？

手动安装：

```bash
# 1. 创建虚拟环境
python -m venv .venv
source .venv/bin/activate

# 2. 复制文件
cp -r resources/src src/
cp -r resources/utils utils/
cp -r resources/runner runner/
cp -r resources/data data/

# 3. 安装依赖
pip install -r resources/requirements.txt
```

### Q: 如何使用 MatAI API？

在 `.env` 中设置：

```bash
USE_MATAI_API=1
MATAI_BASE_URL=http://your-server:port
```

### Q: 如何调试？

```bash
export LOG_LEVEL=DEBUG
python runner/run_pipeline.py --query "xxx" --debug
```

## 📄 许可证

本项目仅供研究使用。

## 🤝 贡献

如有问题或建议，请联系维护团队。

---

**版本**：v1.0
**更新日期**：2026-03-24
