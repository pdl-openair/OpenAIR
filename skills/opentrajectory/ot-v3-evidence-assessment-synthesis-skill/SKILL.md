---
name: ot-v3-evidence-assessment-synthesis-skill
description: Assess supplied OpenTrajectory ResearchRecords for one current judgment target. Produce a traceable evidence assessment that distinguishes support, refutation, limitations, conflicts, and evidence gaps.
---

# v3 Evidence Assessment and Research Synthesis

Use only the supplied `research_records` as evidence. Do not browse, retrieve, or invent sources, measurements, conditions, or identifiers.

Assess the current question in `research_context` directly. It may contain one context or all supplied upstream contexts; keep their boundaries. `research_records` includes records from all supplied upstream contributions. A record may support or refute a statement only within its reported conditions and information identity. Do not treat author interpretation as an established observation, merge incomparable conditions, or turn a missing field into a result.

For every evidence judgment, cite only the supplied `record_id` values that support that exact statement, state its scope boundary, and preserve uncertainty. Use `conflicts` only for materially incompatible records; use `limitations` and `evidence_gaps` for information that weakens or prevents a stronger answer. When evidence is incomplete, return a bounded assessment rather than filling gaps with background knowledge.

Return one JSON object. The final message does not include a draft schema. Runtime code, not this Skill, owns audit fields and final validation. Use an empty array when a section has no qualifying evidence. Do not invent records, measurements, or quotes.

- `direct_assessment`: one bounded string answering the current question. Incomplete evidence remains a bounded assessment.
- `evidence_judgments`: objects with `statement`, `disposition` (`supported` / `refuted` / `limited` / `uncertain`), `record_ids` limited to supplied `rr:` ids, `scope_boundary`, `rationale`, and `confidence` from 0 to 1.
- `conflicts`: objects with `statement`, at least two `record_ids`, and `implication`. Empty when records are not materially incompatible.
- `limitations`: strings.
- `evidence_gaps`: objects with `gap`, `related_record_ids`, and `impact`.
- `synthesis_warnings`: strings for judgment limits that are not evidence gaps.
