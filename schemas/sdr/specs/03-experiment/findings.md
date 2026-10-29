# Experiment — Findings Synthesis

## 用户价值

把已验证分析综合为有边界、可反证、可追溯的研究发现。

## Input
- `result_analysis`

## Output
- `findings_synthesis`

## Acceptance
- `findings_trace_to_accepted_analysis_or_evidence`
- `support_refutation_and_inconclusive_distinguished`
- `confidence_caveats_and_counterevidence_explicit`
- `hypothesis_verdict_only_when_applicable`
- `mechanism_fact_interpretation_and_inference_distinguished`
- `unsupported_mechanism_links_not_fabricated`
- `no_hypothesis_or_mechanism_allowed_as_not_applicable`

## Contract Metadata

```yaml
schemaVersion: definition-contract-metadata.v1
judgmentRequirement: domain_professional
```

## 可选输入

`research_problem_framing`、`evidence_review`、`hypothesis`、`mechanism_evidence` 为可选输入；缺失时保持缺口，不通过猜测补值。

## 失败与不足

证据不足时返回 bounded / inconclusive；无 hypothesis 或 mechanism 时对应内容可为 not-applicable。

## 来源与重入边界

finding→analysis/evidence/hypothesis/mechanism source 保持 lineage；accepted analysis/evidence 或 hypothesis revision 改变时重入。

## 目的

<!-- findings_synthesis: 将验证后的假设和证据综合为结构化发现，建立完整证据链。 -->

## 发现条目

<!-- finding ID (F-ID): 每条发现唯一编号 -->

### F-xxx: <!-- finding statement -->

- **statement:** <!-- 用一句话陈述发现 -->
- **evidence_chain:** <!-- A-xxx → M-xxx → H-xxx → Q-xxx → G-xxx -->
- **confidence_level:** High / Medium / Low
- **conditions:** <!-- 发现成立的条件 -->

<!-- Repeat for each finding -->

## 发现汇总

| F-ID | Statement | Evidence Chain | Confidence |
|------|-----------|---------------|------------|
| F-xxx | <!-- --> | A→M→H→Q→G | High/Medium/Low |

## 证据链完整性

<!-- 每条发现的六层追溯必须完整 -->

| F-ID | Artifact (A) | Method (M) | Hypothesis (H) | Question (Q) | Gap (G) | Complete? |
|------|-------------|------------|----------------|-------------|---------|-----------|
| F-xxx | A-xxx | M-xxx | H-xxx | Q-xxx | G-xxx | yes/no |

## 备注

<!-- -->
