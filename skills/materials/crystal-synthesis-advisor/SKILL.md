---
name: crystal-synthesis-advisor
description: 通用晶体可制备性评估与制备参数推荐。覆盖助熔剂法、CVT、固相反应、电弧/感应熔炼、CVD 五类方法，支持任意晶体材料（不限二维半导体）。Use this skill whenever the user provides a CIF file or chemical formula and asks about 合成可行性 / 可制备性 / 怎么合成 / 制备参数 / 单晶生长 / 生长条件 / synthesis recipe / growth parameters, even when no specific growth method is named. 该技能不依赖静态知识库：通过 Materials Project 预检、ALIGNN/FLUX ML 性质预测、Semantic Scholar + WebSearch 文献检索动态抽取知识，实验室约束与用户偏好通过 auto memory 跨会话记忆。
---

# 晶体可制备性评估与制备参数推荐

对目标材料（CIF 或化学式）回答两个问题：**"能不能做出来"**（可制备性评估）和 **"怎么做"**（制备参数推荐）。证据来自三个独立来源的交叉——ML 性质预测、Materials Project 数据库、文献动态检索——单靠任何一项都不足以支撑结论。

## 总体流程

```
Phase 0  读记忆（实验室约束、用户偏好）
Phase 1  材料解析（组成/族系/维度/元素特性）
Phase 2  Materials Project 预检（相是否已知）
Phase 3  ML 性质预测（ehull/形成能/CLscore）
Phase 4  文献动态检索与工艺抽取
Phase 5  可制备性综合评估（证据链 + 等级）
Phase 6  制备参数推荐（方案生成 + 约束校验）
Phase 7  输出与记忆更新
```

分工原则：推理与分析由 Claude 完成；检索走 WebSearch/web_reader/Semantic Scholar API；ML 预测走 ALIGNN 与 FLUX 物性预测 MCP；计算类校验（时长累加、沸点比较）用简短 Python 脚本完成，不要心算。

## Phase 0: 读取记忆

开始前检查 auto memory 目录中是否已有本技能的约束与偏好记忆：

- 查找 MEMORY.md 索引中 `crystal_synth_lab_constraints`（实验室约束：炉子上限、时长上限、气氛设备等）和 `crystal_synth_user_prefs`（方案数量偏好、风险容忍、方法偏好等）。
- **存在** → 直接使用，并在报告开头向用户复述一次当前约束，给用户当场修正的机会（修正后更新记忆文件）。
- **不存在** → 用 AskUserQuestion 一次性问清（炉子最高温度、是否有管式炉/真空封管/电弧炉/CVT 双温区炉、单次实验时长上限、期望方案数、风险偏好），得到答案后**立即写入记忆**（见 Phase 7 的记忆格式），下次会话不再重复问。

这样设计的原因：约束在多次推荐间基本不变，问一遍就记住，用户不用每次重复。

## Phase 1: 材料解析

从输入提取：

1. **化学式**（CIF 用 `_chemical_formula_sum`；文件名常含提示）
2. **组成特征**：元素清单、稀土/过渡金属/主族/阴离子分类、是否含挥发性（Se、Te、As、Sb、P）、毒性（As、Se、Tl、Cd、Pb、Hg）、易氧化（稀土、碱金属）元素
3. **维度与物性初判**：层状/链状/三维（看 CIF 晶胞与配位，或化学常识如石墨烯型、ThCr2Si2 型层状）；金属/半导体初判
4. **同类体系**：写出 3-5 个用于检索的"锚点"化学式或体系（如目标 Tb6Pr3In2 → 锚点 R-In、R3In、TbIn、PrIn；目标 CrSiTe3 → 锚点 CrXTe3、CrSiTe3 本身、磁性范德华材料）

只有化学式没有 CIF 时，跳过结构类判断，在报告中注明"结构未知，评估基于化学类比"。

## Phase 2: Materials Project 预检

用 mp-search 技能（或直接查 Materials Project API）回答：

- 目标化学式是否已有条目（有 → 实验上大概率已被合成过，找 ICSD 实验条目；无 → 新相，文献证据权重上调）
- 同组成不同计量比有哪些竞争相（判断分相风险）
- 近邻化学式的已知相（同族类比材料）

## Phase 3: ML 性质预测

有 CIF 时调用两个 MCP（只有化学式时跳过结构相关项，报告中注明）：

| 工具 | 性质 | 判读 |
|------|------|------|
| ALIGNN MCP | ehull（凸包能） | < 0.05 eV/atom 热力学稳定；0.05–0.15 亚稳可接受；> 0.15 高分相风险 |
| ALIGNN MCP | formation_energy | > +0.5 eV/atom 时警惕（未必不可合成，但需强驱动） |
| FLUX MCP | cl_score（CLscore） | > 3 合成可行性高；< 2 偏低 |
| FLUX MCP | f_max（弛豫后残余力） | 大值提示 CIF 结构可疑，评估降权 |

ML 预测误差在 0.1 eV/atom 量级，只作证据之一，绝不单独立结论。

## Phase 4: 文献动态检索与工艺抽取

这是本技能替代静态知识库的核心步骤。目标：找到 **3-8 篇含可复现工艺参数的文献**（同类化合物或同方法论文），抽取成结构化配方记录。

### 检索顺序

1. **Semantic Scholar API**（`https://api.semanticscholar.org/graph/v1/paper/search?query=<query>&fields=title,abstract,year,externalIds&limit=20`，用 WebFetch）。query 用英文（材料合成文献主体是英文），组合 Phase 1 的锚点与方法词，如：
   - `Tb3In single crystal growth flux`
   - `rare earth indide single crystal synthesis`
   - `<锚点> chemical vapor transport crystal growth`
2. **WebSearch 补充**（中文/综述/学位论文线索）：`<化学式> 单晶 生长 助熔剂`、`<化学式> single crystal synthesis`。
3. 用 **web_reader MCP 或 WebFetch** 抓取最有希望的 2-4 篇全文（优先开放获取；DOI 落到 publisher 页面时常只有摘要，摘要里有温度/方法关键词就够初筛，抽取工艺细节时再找预印本/全文链接）。

找不到任何同类文献时如实报告"无直接文献先例"，评估降级为 ML+MP 交叉，推荐方案改为类比外推并明确标注不确定性——不要编造文献参数。

### 抽取字段（每篇文献一条记录）

```json
{
  "配方ID": "lit_001",
  "来源": {"标题": "...", "DOI或URL": "...", "年份": 2024},
  "材料": "同类材料化学式",
  "方法": "flux | CVT | solid-state | arc-melting | CVD | 其他",
  "与目标的相似点": "同结构原型 / 同阳离子族系 / 同阴离子",
  "原料与配比": "如 Tb:In = 1:4（原文表述）",
  "助熔剂/输运剂": "如 In 自助熔剂 / I2 5mg/cm3",
  "容器与气氛": "如 石英管真空封装 / Ar 手套箱",
  "温度程序": {"升温": "...", "最高温度_摄氏": 950, "保温时间_h": 240, "降温/梯度": "1°C/h 或 梯度 50°C"},
  "分离与后处理": "...",
  "晶体尺寸与质量": "如 mm 级片状单晶",
  "置信度": "高(全文细节) | 中(摘要级) | 低(综述转引)"
}
```

## Phase 5: 可制备性综合评估

把 Phase 2-4 的证据汇总为**证据链表**（每条注明来源与指向），然后给等级：

| 等级 | 判据（满足任一即考虑，综合权衡） |
|------|------|
| **A 高度可行** | 同相已有实验报道；或 ehull<0.05 且同原型化合物已合成 |
| **B 可行** | 同族类比已合成 + ehull<0.15；或文献报道了相近计量比相 |
| **C 存疑** | 仅化学类比成立；或 ehull 0.15–0.3；或存在强竞争相 |
| **D 不建议** | ehull>0.3 且无文献先例；或目标元素组合有不可回避的工艺矛盾（如需温度远超设备上限） |

证据互相矛盾时（如 ML 说稳定但文献从无报道），明确写出矛盾点而不是取平均。D 级也要给出"缺什么证据才能翻案"（如"若 DFT 弛豫后 ehull<0.1 可升为 C"）。

## Phase 6: 制备参数推荐

### 方法选择

按 Phase 1/4/5 的材料特征选 1-3 条候选路线（详见 [references/method-selector.md](references/method-selector.md) 的映射表）：

- 含挥发性阴离子（S/Se/Te/As）+ 想要单晶 → **CVT 或真空封管助熔剂**
- 金属间化合物/金属-rich → **助熔剂（自助熔剂/Sn）或电弧熔炼**
- 氧化物/陶瓷粉体 → **固相反应**（单晶再叠加助熔剂/提拉）
- 薄膜需求 → **CVD**（注明与体单晶参数体系不同）

用户指明方法时优先用户的；评估结论与用户方法冲突时（如 CVT 不适合该体系）明确说明并给替代。

### 方案生成

从 Phase 4 抽取的配方记录聚类出典型路线，生成 **3-5 个方案**（数量按用户偏好记忆，默认 4）：

- 1 个**基线方案**：各参数取文献窗口中值，最接近成功先例
- 1-2 个**筛查方案**：短周期快速验证成相（快冷、小批量）
- 1-2 个**优化方案**：慢冷/大稀释/长保温冲大晶体，或备选助熔剂/输运剂路线

每个方案包含：原料与配比（标准化写法如 `Tb:Pr:In=6:3:2`）、助熔剂/输运剂及比例、容器与气氛、完整温度程序（分段温度/时长/速率）、分离方式、预期晶体尺寸、风险列表、溯源（参考配方ID + 文献 DOI）。

### 约束校验（必做，用 Python 脚本核验）

每个方案过一遍，不通过就调整后再验：

1. **温度上限**：所有段温度 ≤ 实验室炉子上限（记忆中的约束）
2. **元素沸点**：含挥发性元素时，非熔化段温度不超过最低沸点元素−50℃ 余量（Se 684℃、As 615℃ 升华、Te 988℃ 等，全表见 [references/element-properties.md](references/element-properties.md)）
3. **温度层次**：多段程序 Tmax > 次高温 > 低温，相邻差 ≥ 50℃
4. **时长**：各段累加 ≤ 用户时长上限（默认 200h；CVT 长周期实验按用户上限放宽）
5. **安全**：毒性/易燃/易爆组合（如 As+卤素、碱金属+水）在风险列表中显式标注

## Phase 7: 输出与记忆更新

### 输出目录

```
synthesis-advisor/<化学式>_<YYYYMMDD>/
├── assessment_report.md      # 评估报告（主输出）
├── extracted_recipes.jsonl   # Phase 4 抽取的文献配方记录（可追溯）
└── recommend_output.json     # 推荐方案（结构同 Phase 6）
```

### 评估报告结构（固定模板）

```markdown
# <化学式> 可制备性评估与制备参数推荐
## 一、执行摘要（等级 + 一句话结论 + 首选路线）
## 二、材料解析（组成/维度/元素特性）
## 三、证据链
  ### 3.1 Materials Project 预检
  ### 3.2 ML 性质预测
  ### 3.3 文献证据（表：文献→方法→关键参数→与目标关系）
## 四、可制备性等级与理由（矛盾点/缺口明示）
## 五、推荐方案（每方案参数表 + 温度程序）
## 六、风险与建议执行顺序
## 附录：约束核验表、参考文献列表
```

### 记忆更新

- 用户在本次会话中修正过约束/偏好 → 更新对应记忆文件
- 完成一次推荐后，若发现值得长期记住的事实（如"该实验室没有 CVT 双温区炉"），追加进约束记忆

记忆文件格式（写到 auto memory 目录并登记进 MEMORY.md 索引）：

- `crystal_synth_lab_constraints.md`（type: project）——炉子上限、时长上限、可用设备清单、气氛/封管能力
- `crystal_synth_user_prefs.md`（type: user）——方案数量、风险偏好、方法偏好、报告语言/详略

## 参考文件

| 文件 | 何时读 |
|------|--------|
| [references/method-selector.md](references/method-selector.md) | Phase 6 选方法时——五类方法的适用映射与参数要点 |
| [references/element-properties.md](references/element-properties.md) | Phase 1 元素特性判断与 Phase 6 沸点校验时——常用元素熔/沸点、挥发性、毒性速查表 |
| [references/extraction-guide.md](references/extraction-guide.md) | Phase 4 检索卡壳时——检索 query 模板、Semantic Scholar 用法、常见落空场景的降级策略 |

## 边界与诚实性要求

- 检索不到就是检索不到，明确写"未找到"，用类比外推并标注置信度——**绝不虚构文献参数或 DOI**
- ML 预测给出数值时同时给出误差量级与"仅作证据之一"的定位
- 新计算生成相（MatterGen 等产物）默认按"无文献先例"处理，评估上限通常是 B，除非找到同相实验报道
- 推荐方案中任何超出设备约束的参数都必须在生成阶段就调整掉，不要输出后让用户自己发现
