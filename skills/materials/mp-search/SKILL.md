---
name: mp-search
description: Materials Project 数据库检索技能。在 Proposal 阶段用于检索目标材料体系的相关数据（热力学、电子结构、晶体结构、XRD 等），生成结构化的数据库检索报告。当用户提到"查 MP 数据库"、"检索材料数据"、"搜索 Materials Project"、"查一下 xxx 材料的物性"时自动触发。
output_dir: openair/<research_name>/proposal/
output_file: mp_search_report.md
scripts_dir: scripts
---

# Proposal MP Search — Materials Project 数据库检索

在研究 Proposal 阶段，系统性地检索 Materials Project 数据库，为材料体系选择提供数据支撑。

## 触发条件

- 用户说"查 MP 数据库"、"检索材料数据"、"搜索 Materials Project"
- Proposal 阶段的"数据库检索"原子技能被选中
- 用户提到具体材料并需要物性数据（如"MoS2 的带隙是多少"）

## 前置条件

- `.env` 中配置了 `MP_API_KEY`
- 依赖：`pymatgen` + `mp-api`（`uv pip install pymatgen mp-api`）

## 工具

本 skill 的核心脚本：`scripts/mp_query.py`，提供 6 个子命令：

| 子命令 | 用途 | 示例 |
|--------|------|------|
| `search` | 按条件搜索材料 | `python3 scripts/mp_query.py search --formula "MoS2" --stable` |
| `info` | 单个材料完整物性 | `python3 scripts/mp_query.py info --material-id mp-1434` |
| `structure` | 晶体结构 + CIF | `python3 scripts/mp_query.py structure --material-id mp-1434` |
| `xrd` | 模拟 XRD 衍射图谱 | `python3 scripts/mp_query.py xrd --material-id mp-1434` |
| `elements` | 元素基本性质 | `python3 scripts/mp_query.py elements --symbols Mo S` |
| `compare` | 多材料物性对比 | `python3 scripts/mp_query.py compare --material-ids mp-1434 mp-1023939` |

## 工作流

### Step 1: 确定检索范围

用 **AskUserQuestion** 与用户确认：

1. **研究主题名称** `<research_name>`（用于输出路径）
2. **目标材料体系**：具体化学式（如 MoS2）、化学体系（如 Mo-S）、或元素组合
3. **关注的物性维度**（可多选）：
   - 热力学稳定性（energy_above_hull, formation_energy）
   - 电子结构（band_gap, is_metal, efermi）
   - 力学性质（bulk_modulus, shear_modulus）
   - 晶体结构（spacegroup, lattice parameters）
   - XRD 衍射数据
   - 元素性质

### Step 2: 宽范围搜索

根据用户提供的材料体系，先用 `search` 子命令做宽范围搜索，获取所有候选材料：

```bash
# 按化学式搜索
python3 scripts/mp_query.py search --formula "MoS2"

# 按化学体系搜索（包含该体系的所有化学计量比）
python3 scripts/mp_query.py search --chemsys "Mo-S"

# 按元素搜索（含这些元素的所有材料）
python3 scripts/mp_query.py search --elements Mo S

# 带过滤条件
python3 scripts/mp_query.py search --chemsys "Mo-S" --stable
python3 scripts/mp_query.py search --elements Mo S --band-gap-range 1.0 3.0 --stable
```

### Step 3: 筛选关键材料

从搜索结果中筛选出**热力学稳定相**和**近稳定相**（energy_above_hull < 0.05 eV/atom），为每个关键材料调用 `info` 获取详细物性：

```bash
# 对每个关键材料 ID 查询完整物性
python3 scripts/mp_query.py info --material-id mp-1434
```

如果用户关注晶体结构，额外调用 `structure` 和 `xrd`：

```bash
python3 scripts/mp_query.py structure --material-id mp-1434
python3 scripts/mp_query.py xrd --material-id mp-1434
```

### Step 4: 多材料对比

如果搜索到多个相关材料，用 `compare` 做物性对比：

```bash
python3 scripts/mp_query.py compare --material-ids mp-1434 mp-1023939 mp-1042086
```

### Step 5: 元素性质补充

查询目标体系涉及元素的氧化态、离子半径等：

```bash
python3 scripts/mp_query.py elements --symbols Mo S
```

### Step 6: 生成检索报告

将所有检索结果整理为结构化报告，用 **Write** 工具写入：

**输出路径**：`openair/<research_name>/proposal/mp_search_report.md`

**报告结构**：

```markdown
# Materials Project 数据库检索报告

## 检索信息
- **检索日期**: YYYY-MM-DD
- **目标体系**: [化学式/化学体系]
- **检索策略**: [描述搜索条件和过滤方式]
- **命中数量**: [总结果数] 个材料，其中 [N] 个稳定相

## 1. 稳定相材料

### [材料ID] — [化学式]
- **晶体系统**: [晶系] | **空间群**: [空间群]
- **密度**: [X] g/cm³ | **原子数**: [N]
- **形成能**: [X] eV/atom | **凸包能**: [X] eV/atom
- **带隙**: [X] eV（[直接/间接]）
- **体模量**: [X] GPa | **剪切模量**: [X] GPa

> 简要说明该材料的特点和研究价值

## 2. 近稳定相材料（energy_above_hull < 0.05 eV/atom）

[同上格式]

## 3. 亚稳相材料

[表格汇总]

| Material ID | 化学式 | 凸包能 (eV) | 带隙 (eV) | 晶系 | 空间群 |
|-------------|--------|-------------|-----------|------|--------|
| ... | ... | ... | ... | ... | ... |

## 4. XRD 特征峰（仅稳定相）

### [材料ID] — [化学式]
| 2θ (°) | 相对强度 | (hkl) | d-spacing (Å) |
|---------|----------|-------|---------------|
| ... | ... | ... | ... |

## 5. 元素性质

[从 elements 子命令结果整理的表格]

## 6. 数据质量评估

- 数据完整性：[描述 MP 数据覆盖的维度]
- DFT 泛函：PBE/GGA（MP 默认）
- 可靠性说明：MP 数据基于 DFT 计算，与实验值可能存在偏差

## 7. 检索结论

- 推荐关注的材料体系：[列出]
- 数据支撑的研究方向建议：[基于检索结果的简要分析]
- 缺失数据：[哪些物性在 MP 中没有覆盖，建议实验补充]
```

## 输出路径

- **输出目录**：`openair/<research_name>/proposal/`
- **输出文件**：`mp_search_report.md`
- 首次使用时，用 **AskUserQuestion** 询问 `<research_name>`

## 注意事项

- **并行执行**：多个材料的 `info`/`structure`/`xrd` 查询互相独立，应并行调用
- **API 限制**：MP API 有请求频率限制，避免短时间内大量查询；单次 search 已包含足够信息
- **数据来源标注**：报告中所有数据必须标注来自 Materials Project DFT 计算
- **stable 优先**：先关注稳定相（energy_above_hull = 0），再扩展到近稳定相
- **不要伪造数据**：所有数据必须来自 `mp_query.py` 的实际查询结果，禁止编造
- **错误处理**：如果 MP API 不可用，明确告知用户，不要用缓存数据冒充实时查询
