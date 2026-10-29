# 文献调研综述：{{research_topic}}

> 生成时间：{{timestamp}}
> 研究主题：{{research_topic}}
> 调研范围：{{scope}}

---

## 1. 调研目标与范围

{{research_goal}}

---

## 2. 筛选结果

共筛选论文 **{{total_papers}}** 篇，保留 **{{kept_papers}}** 篇，排除 **{{excluded_papers}}** 篇。

### 筛选明细

| 论文标题 | 决策 | 理由 |
|----------|------|------|
{{#decisions}}| {{title}} | {{decision}} | {{reason}} |
{{/decisions}}

---

## 3. 核心文献精读

{{#papers}}
### {{title}}

- **作者**: {{authors}}
- **年份/来源**: {{year}} / {{venue}}
- **研究问题**: {{research_question}}
- **核心方法**: {{method}}
- **主要贡献**: {{contribution}}
- **数据集**: {{datasets}}
- **主要结果**: {{results}}
- **局限性**: {{limitations}}
- **可改进方向**: {{improvements}}

{{/papers}}

---

## 4. 文献矩阵

| 论文 | 作者 | 年份 | 方法 | 数据集 | 主要结果 | 局限 |
|------|------|------|------|--------|----------|------|
{{#matrix}}| {{title}} | {{authors}} | {{year}} | {{method}} | {{datasets}} | {{results}} | {{limitations}} |
{{/matrix}}

---

## 5. 研究空白与后续建议

{{#gaps}}
- {{description}}
{{/gaps}}

### 后续方向

{{follow_up_suggestions}}
