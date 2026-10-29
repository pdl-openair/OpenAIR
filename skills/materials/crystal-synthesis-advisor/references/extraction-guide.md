# 文献检索与工艺抽取指南

Phase 4 检索不顺利或结果太少时读取本文件。

## 检索 query 模板

英文检索命中率远高于中文（合成文献主体是英文）。按目标材料类型组合锚点与方法词：

| 材料类型 | query 模板（`<>` 处替换） |
|---------|--------------------------|
| 已知化合物 | `<化学式> single crystal growth` / `<化学式> synthesis` |
| 新相（无先例） | `<族系> single crystal flux growth`（族系=近邻化学式，如 R3In → `rare earth In3 single crystal`） |
| 硫族/层状 | `<锚点> chemical vapor transport` / `<锚点> vapor transport crystal` |
| 金属间化合物 | `<锚点> flux growth intermetallic` |
| 氧化物 | `<锚点> crystal growth flux` / `<锚点> solid state reaction synthesis` |
| 方法专项 | `self flux growth rare earth` / `iodine vapor transport chalcogenide` |

多锚点各查一轮（Semantic Scholar 一次 20 条），汇总去重后再挑全文。

## Semantic Scholar API 用法

```
https://api.semanticscholar.org/graph/v1/paper/search?query=<URL编码query>&fields=title,abstract,year,externalIds,citationCount&limit=20
```

- 用 WebFetch 调用；返回 JSON 中 `data[].title/abstract/externalIds.DOI/year`
- 429 限流时等几秒重试，或转用 WebSearch
- 摘要中含 "single crystal / flux / CVT / transport agent / sealed quartz" 等词的优先
- `citationCount` 高的综述可作方法学参考但工艺细节要找原文

## 全文获取策略

1. DOI → 优先找 arXiv/预印本/作者主页版本（WebSearch: `<DOI> pdf` 或 `<标题> filetype:pdf`）
2. 开放获取期刊（J. Crystal Growth 部分开放、Sci. Rep.、Nature 系、IOP 开放档）
3. 只拿到摘要时：抽取方法/温度关键词，`置信度: 中`，参数窗口标注较宽
4. 中文文献（知网/学位论文）用 WebSearch 搜 `<化学式> 单晶 助熔剂 制备`，常含详细工艺

## 抽取时的注意点

- 温度程序常写在实验节一句话里（如 "heated to 950 °C for 48 h, then cooled at 2 K/h to 500 °C"），逐段拆开记录
- 配比写法多样：原子比、重量比、mol%——照抄原文到"原料与配比"，标准化换算另写
- 输运剂用量、封管真空度、梯度方向是 CVT 复现关键，缺了就标"文献未明确"
- 同一文献多材料共用一个工艺时，每材料各记一条（配方ID 加后缀）
- 晶体尺寸描述（"mm-sized plates"）直接引用原文短语

## 检索落空的降级策略

| 场景 | 策略 |
|------|------|
| 目标化合物零结果 | 放宽到同族系（同阳离子不同计量比、同结构原型不同元素） |
| 只有材料学性能论文无工艺 | 从其引用的合成文献追（Semantic Scholar API: `references` 端点） |
| 全部落空 | 如实报告"无直接文献先例"，Phase 6 用方法学通用参数（method-selector.md 各方法要点）生成外推方案，全部标注"类比外推，置信度低"，评估等级不高于 C |
