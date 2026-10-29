# 证据规则细则 — experiment-report

## 1. 数值溯源

- 报告中每个数值必须满足其一：
  1. 来自 `evidence_manifest.json` 对应 key 的 `extracted`；
  2. Claude 现场 Read 了 manifest 指向的来源文件并抄录原值。
- 表格一律带 `来源` 列（相对研究目录的路径，多来源分号分隔）。
- 正文关键数字用行内指针，例：`DFT 带隙 1.21 eV ‹dft/C001/band_gap/final/band_gap.json›`。
- **manifest 与来源文件都没有的数字不得出现**——不得凭记忆补、不得换算编造
  （单位换算允许，但须注明"由 <来源> 换算"）。

## 2. 缺失标注

- 统一前缀 `⚠️ 缺失`；section 结构永远完整，占位缺数据写 `⚠️ 缺失：<原因>`。
- §9 固定表列：`环节 | 涉及 | 缺失原因 | 影响 | 补救建议`，内容 = EM `missing[]`
  全量 + 人工补充的影响评估。
- 措辞限制（硬规则）：
  - 湿实验全 pending / 无台账 → §8 禁用「实验验证」「实验证实」，只能说「计算预测」；
  - proposal 降级（degraded_proposal.md）→ §3 必须声明"MP 数据库证据缺失"；
  - 进化降级（alt_ledger）→ §4.2 必须声明"替代环，非 NSGA 完整进化"；
  - DFT 部分 skipped/failed → §5 必须逐候选标注状态，不许静默省略。

## 3. 三方对比表（§7 / 对 wetlab comparison 表2 的引入规范）

| candidate_id | formula | 性质 | ML (alignn) | DFT (VASP) | 实验 (n) | Δ(ML,DFT) | Δ(DFT,实验) | 判定 | 来源 |
|---|---|---|---|---|---|---|---|---|---|

- 列结构固定；缺哪方写 `—(<未…>)`，禁止留空格让读者猜。
- `来源` 列写三个文件路径（ml ← pipeline_state.json；dft ← dft/…；exp ← comparison_*.md）。
- 判定词表：`一致 / 偏差>tol / pending_experiment / 无DFT参照 / 记录`。

## 4. 图表

- 落 `report/figures/`，报告内相对路径引用（`figures/xxx.png`）。
- 数据图 → **项目内** `paper-figure` skill（matplotlib）：
  - `pareto_front.png`：leaderboard 目标两两散点（有 evo 数据才画）；
  - `parity_mldft.png`：bandgap ML vs DFT 散点 + y=x 线（≥2 个候选才画）；
  - `candidate_props.png`：候选×性质柱状。
- 管线图 → **项目内** `figure-spec` skill：`pipeline.svg`（§2 用）。
- 项目内缺对应 viz skill → 图位写 `⚠️ 缺失（<skill名> 未安装）`，禁止引用
  `C:\Users\...\.claude\skills\` 用户级路径（自包含铁律）。
- 数据不足（如单候选画不了 parity）→ 不画，§9 记一条缺失原因。

### 4a. 内联位置（铁律：每张图必须就近内联，禁止只给路径）

- `report/figures/` 下**每张**生成的图都必须以 `![alt](figures/xxx)` 或 `<img>` 形式
  内联到其语义相关的小节，紧跟最相关的那张表/那段正文之后——**不得**只在正文写
  「见图 `report/figures/xxx.png`」让读者跳转。路径引用是图文分离的反模式。
- 钉位（与 section-sources.md 占位符一致）：
  | 图 | 钉在哪 | 紧跟 |
  |---|---|---|
  | `pipeline.svg` | §2 方法总览 | 组件表之前 |
  | `pareto_front.png` | §4.3 Pareto 结果 | leaderboard 表之后 |
  | `phase_diagram.png` | §5.3 热力学稳定性 | 凸包相表之后 |
  | `candidate_props.png` | §7 预测 vs 实测 | 三方对比表之后 |
  | `parity_mldft.png` | §5.2 带隙 或 §7 | 带隙/三方对比表之后（≥2 候选才有）|
- alt 文本须描述**图表达的内容**（如「11 个氧化物个体的带隙–ehull 散点」），
  非文件名。

### 4b. 显示尺寸（目标：渲染后图内字号略小于正文字体）

- Markdown `![alt](path)` **无法限宽**，渲染器默认按原图像素铺满，高分辨率 PNG
  （300 DPI × 大 figsize）会爆宽到正文字号 2–3 倍。必须改用 HTML：
  ```html
  <img src="figures/xxx.png" width="<W>" alt="<内容描述>" />
  ```
- 先查源图尺寸（`PIL.Image.open(f).size` 或 `identify`）与 SVG 的 `viewBox`/内嵌
  `font-size`，再按下表选 `width`。目标是图内文字 ~11–14px、正文 ~16px（图略小）：
  | 图类型 | 源特征 | 目标 width(px) |
  |---|---|---|
  | 近正方形单图（~1200×1050，大轴标签） | 1:1 | ~360 |
  | 超宽多子图横排（2:1，双子图） | 横排 panel | ~720 |
  | 矢量管线/架构图（SVG，内嵌 font 10–13px） | 小字号流程 | ~900（放大让标注清晰）|
  | 普通单柱/线图（figsize 5×3.5 @300DPI） | 标准 | ~480–560 |
- 超宽图（宽高比 ≥1.8）宁可放大到 720+ 也要让子图轴标签不挤；近正方形图宁小勿大
  （360 足够，过大则图内字号超正文）。SVG 是矢量、放大不失真，按内嵌字号定宽。
- `width` 只设宽度、不设 `height`（保纵横比）。同一报告内同类型图宽度对齐
  （如 pareto_front 与 phase_diagram 同取 360）。

## 5. 自包含与只读

- 报告生成过程：collect_evidence.py（只读扫描）→ 图表（只读上游数据）→ 撰写。
- 全程不修改 openair/ 下任何上游产物；`report/` 是唯一写入区。
- 报告头部必须链接 `report/evidence_manifest.json` 供审稿人核数。
