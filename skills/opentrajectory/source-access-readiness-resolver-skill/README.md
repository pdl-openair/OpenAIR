# source-access-readiness-resolver-skill

OpenTrajectory #7 - 文献访问准备状态判定

## 功能

根据去重后的候选文献元数据，判断其访问准备状态：
- `structured_fulltext_ready`: 有结构化全文（HTML/XML/TEI）
- `pdf_ready`: 有 PDF
- `landing_page_only`: 仅有落地页
- `metadata_only`: 仅有元数据
- `blocked`: 访问受阻
- `unknown`: 未知

## 边界

**做什么：**
- 检查元数据中的标识符（pmcid, arxiv_id, doi, pdf_url 等）
- 分类可用访问类型
- 优先结构化全文 > PDF > 落地页 > 仅元数据
- 继承上游 provenance 并追加本 skill 的记录

**不做什么：**
- 不下载 PDF
- 不抓取全文
- 不解析 PDF/HTML/XML/TEI
- 不做标题摘要筛选
- 不评分、不分层、不排序、不 top-k
- 不抽取 Evidence 或生成 Lead

## 使用

```bash
python resolver.py examples/request.json
```

## 测试

```bash
python test_resolver.py
```

## Schema 验证

```bash
python resolver.py examples/request.json > output.json
python -c "
import json, jsonschema
with open('output.schema.json') as f:
    schema = json.load(f)
with open('output.json') as f:
    data = json.load(f)
jsonschema.validate(instance=data, schema=schema)
print('Valid')
"
```

## 输入

来自 `candidate-dedup-and-provenance` (#6) 的 `DedupedCandidate[]`

## 输出

`CandidateWithAccessStatus[]` + summary + warnings + errors
