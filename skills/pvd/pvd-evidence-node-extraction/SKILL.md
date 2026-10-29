---
name: pvd-evidence-node-extraction
description: Extracts source-grounded PVD Evidence Nodes from bounded paper packets for downstream ResearchRecord structuring.
---

# PVD Evidence Node Extraction

## Purpose

Extract traceable Evidence Nodes from already acquired and read source material for the PVD workflow.

The output unit is an **Evidence Node**, not a Research Record. Research-record grouping belongs to the downstream `pvd_research_record_structuring` Skill.

Return one JSON object with `evidence_items` only. The final message does not include a provider schema. Each item has `source_anchor_id`, `evidence_type`, `assertion_mode`, `claim`, `evidence_quote`, `conditions`, `metric`, `mechanism`, `comparison`, `qualifiers`, `confidence`, and `matched_target_ids`. Use an empty array when the paper adds no decision-bearing evidence. The registered finalizer restores source identity, provenance, and `evidence_id`.

## Input

- `task_id`: stable task/run identifier.
- `paper_packets`: canonical progressive-reading packets. The Provider sees only the registered bounded `anchored_sections` projection; the deterministic finalizer retains the full canonical sections and source anchors.
- `evidence_schema`: the upstream PVD evidence target schema.
- `item_budget`: optional runtime-visible hard maximum for a single-paper call.

## Model-owned reasoning

Select a **minimal sufficient, non-redundant evidence set** for the current evidence targets. The goal is target coverage and downstream decision value, not sentence-by-sentence enumeration of the paper.

Within the runtime item budget:

1. cover decision-bearing `required` Evidence Targets first;
2. add non-duplicate evidence that materially improves `preferred` target coverage;
3. preserve explicit conflicts, limitations, negative findings, and uncertainty when they can change a downstream judgment;
4. prioritize direct quantitative results and experimental conditions, then explicitly supported mechanisms, baselines, and comparability limits;
5. stop when the paper's useful target coverage is represented; do not fill capacity merely because it remains.

One Evidence Node may cover multiple target IDs when the same quoted statement genuinely supports them.

For each selected source statement in `paper_packets[].anchored_sections`:

1. select exactly one supplied `source_anchor_id`;
2. classify `evidence_type` and `assertion_mode`;
3. preserve a concise scientific claim;
4. copy a verbatim or whitespace-equivalent `evidence_quote` from the anchored section;
5. preserve reported conditions, metrics, mechanisms and comparisons without inventing missing values;
6. assign only target IDs actually supported by the quoted statement;
7. set `target_match_status` only from the scientific judgment; an empty `matched_target_ids` remains empty;
8. express uncertainty via qualifiers/confidence rather than adding unsupported facts.

Do not emit extra Evidence Nodes merely to restate the same scientific fact or to exhaustively catalogue statements that do not improve target coverage.

## Structured-output boundary

The public Structured Output Runtime validates the returned `evidence_items` object, performs the single bounded structure recovery when needed, runs deterministic assembly, and then validates the unchanged Formal output schema. The model does not receive that schema.

The deterministic finalizer may restore only source identity and provenance fields from the selected `source_anchor_id`, verify quote grounding, create stable `evidence_id`, and assemble summary/audit fields. It must not delete or deduplicate target IDs, infer target links from parameter names, create `target_match_status`, rewrite quotes, or turn structural failure into scientific `unknown`.

A model-selected item with an illegal target ID, unknown source anchor, ungrounded quote, or another independently checkable item-level violation is dropped with an explicit `evidence_item_unavailable` warning while valid sibling Evidence from the same paper is retained. Whole-paper technical failure remains isolated by the shared per-paper runtime. Deterministic isolation never repairs scientific content or invents target links.

## Hard boundaries

Do not:

- group multiple Evidence Nodes into samples/runs/studies;
- emit `research_records`;
- infer unreported PVD values;
- judge cross-study comparability;
- recommend parameter directions or values;
- rewrite source identity fields;
- invent, remove, deduplicate, or backfill target assignments to satisfy structure.
