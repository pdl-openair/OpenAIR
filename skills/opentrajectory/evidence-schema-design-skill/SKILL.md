---
name: evidence-schema-design-skill
description: >-
  Decide whether the formal intent_understanding, constraints_criteria, and
  retrieval_strategy_design Artifacts can jointly support one handoffable
  EvidenceSchema, then design that task-specific schema when the handoff is
  admissible or return the existing blocked outcome when it is not.
---

# Evidence Schema Design

## 1. Goal and responsibility boundary

Define what later research work must **record, preserve, and compare** so that results from different sources can support valid scientific judgment for the current task.

This Skill designs a task-specific `EvidenceSchema`. It does not acquire sources, read papers, extract source-level records, form Findings, or make Research Conclusions.

The frozen downstream object order is:

`evidence_schema_design` → source work and reading → `evidence_extraction` → `ResearchRecord` → scientific judgment-bound `Evidence`

`evidence_extraction` forms each `ResearchRecord` around **research object → conditions → results → source**, while preserving necessary context and source identity. A `ResearchRecord` becomes `Evidence` only when it is later used to support, refute, or limit a specific scientific judgment.

This Skill does **not** create `ResearchRecord` or `Evidence`. It only determines whether the formal inputs can support one consistent minimum record design, then specifies which information later records must retain, which dimensions must remain comparable, and which missing information would limit judgment.

## 2. Formal inputs and authority rule

Consume the three formal upstream Artifacts directly:

- `intent_understanding`: use the stated research problem, research object, target outcomes, boundary conditions, comparison context, true ambiguities, source scope, and output intent.
- `constraints_criteria`: use the interpreted constraints and their `semantic_state`, plus the formal `missing_information` and `research_risks` fields.
- `retrieval_strategy_design`: use the evidence questions, coverage directions, retrieval focus, source scope, priorities, facets, and query strategy that define what the research must cover.

`constraints_criteria` does not contain or delegate through free-text filter rules. Preserve explicit user constraints from their formal semantic fields, and consume must-have, optional, exclusion, and query choices from `retrieval_strategy_design`. Do not reconstruct a removed filter-rule field or treat its absence as missing information.

`task_id` is execution identity only. `design_config` is optional runtime configuration only and must not introduce scientific facts.

Treat all three scientific Artifacts as formal authorities within their own content boundaries. None has default precedence over another. Use them as supplied: do not alias, reconstruct, project, translate, or silently replace one authority with another, and do not redo their upstream reasoning.

Unknown or unconfirmed upstream information must remain unknown or unconfirmed. A value that later research is meant to discover is not a conflict merely because it is currently unknown.

Do not expect or reconstruct model confidence, coverage, `quality_report`, or manual-review declarations. They are not scientific authorities. Workflow routing is determined by system code from the formal upstream state.

## 3. Handoff-first decision sequence

Follow this sequence once, in order. Do not begin task-specific `EvidenceSchema` content until the handoff decision is complete.

### Step 1 — Establish whether the three authorities can describe one handoffable task

Read the three Artifacts together and determine whether they can be interpreted consistently as one current research task without overriding any formal authority. The consistency decision must cover the parts that control downstream record design, especially:

- task identity and research question;
- research object or population being studied;
- the result information that later `ResearchRecord`s must preserve;
- conditions or context needed to interpret those results;
- key comparison dimensions when comparison affects the judgment.

Complementary details may be combined when they do not contradict one another. Ordinary unknowns, candidate values, unresolved values that later research should discover, and non-critical differences remain visible as uncertainty and do not by themselves block handoff.

If a contradiction in task identity, research object, or the minimum record/comparison structure cannot be given one consistent interpretation without ignoring, weakening, or choosing over a formal authority, the handoff is **not admissible**. Do not select the locally most coherent Artifact and continue. Do not downgrade that contradiction to an assumption, warning, or uncertainty rule merely to keep generating a schema.

### Step 2 — Decide whether the minimum design is determinable

After the authority consistency check, determine whether the combined formal inputs define enough structure for later evidence processing. A `ready` decision requires all applicable conditions below:

1. The research object(s) and result information can be identified well enough to state what later `ResearchRecord`s must preserve.
2. The conditions or context needed to interpret those results can be identified well enough to keep materially different records from being treated as equivalent.
3. When the research judgment depends on comparison, the key comparison dimension(s), baseline(s), control(s), or comparability condition(s) can be identified well enough to prevent invalid comparison.
4. The schema can state how later records retain source provenance and how material uncertainty or missing information remains visible.

Sparse input is not automatically blocked. If missing facts are values that later research is supposed to discover, but the Skill can still define **what must be recorded and compared**, the handoff may be `ready` while those values remain unknown.

Return `blocked` when either the authority consistency check fails in a way that prevents one minimum record/comparison structure, or the minimum record information or a required key comparison dimension cannot otherwise be determined. If comparison is genuinely irrelevant to the research question, the absence of comparison dimensions is not itself a blocking condition.

For a conflict-driven `blocked` result, use the existing blocked contract: explain the unresolved conflict in `blocking_reason`, and use the `missing_*` arrays to identify the minimum record information or comparison basis that cannot be uniquely determined because of that conflict. Do not add a third status or a new conflict branch.

Once `blocked` is decided, stop. Do not design or return any `evidence_schema` content.

## 4. Semantic draft output

Only after Step 1 and Step 2 both permit `ready`, return the scientific semantics needed to build the EvidenceSchema. The provider-facing draft has two arrays, `ready` and `blocked`; exactly one contains one item and the other is empty.

For `ready[0]`, provide only:

```text
task_scope
domain_hints
design_assumptions
evidence_targets          # scientific target content, without target_id
gap_taxonomy
uncertainty_rules         # trigger + handling, without rule_id
design_warnings
```

Do not generate `status`, `schema_id`, `schema_version`, `research_question`, `target_id`, or `rule_id`. These are deterministic or already-authoritative fields. The registered finalizer adds them without rewriting the scientific content, and the finalized result is then validated against the Formal Schema in `output.schema.json`. Do not add empty extension containers or other placeholders solely to complete the schema.

For `blocked[0]`, provide only the existing scientific blocked semantics: `blocking_reason`, `missing_minimum_information[]`, `missing_comparison_dimensions[]`, and `design_warnings`. Do not add a third branch or a machine status field.

## 5. Design the task-specific EvidenceSchema

After the handoff is admitted and the complete `ready` envelope is fixed, fill the schema from the decision-bearing content of all three formal Artifacts. Ask:

> What must a later ResearchRecord preserve so that the current research question can be compared and judged without losing the conditions or source context that make the result meaningful?

Build only task-relevant `evidence_targets[]`. A target should represent an independently useful information requirement for later records, such as a research object or candidate, a condition, a result or metric, a comparison baseline, a mechanism-related observation, a characterization result, a calculation, a limitation, or provenance information.

Use the schema fields consistently:

- `description`: identify the concrete information that later records must preserve.
- `condition_dimensions[]`: list conditions or contextual variables that must travel with a result for it to remain interpretable.
- `comparison_requirements[]`: state the dimensions, baselines, controls, or matching conditions needed for fair comparison when comparison affects the research judgment.
- `provenance_requirements[]`: state the source anchors and contextual traceability needed to return from a later record to its source.
- `completion_criteria`: state what would make the target sufficiently recorded for the current task.
- `requiredness`: distinguish information required for valid judgment from information that is preferred or optional.
- `gap_taxonomy[]` and `uncertainty_rules[]`: describe how missing, conflicting, or non-comparable information should remain visible instead of being silently normalized.

Separate information requirements when combining them would hide a difference that can change scientific judgment. Keep them together when they are only different descriptions of the same record requirement. Do not create targets from keywords or from a fixed domain template.

Important upstream requirements, constraints, comparison contexts, risks, and retrieval questions must either be represented in the schema or explicitly disclosed as an assumption or warning. A warning may describe a non-blocking uncertainty; it must not conceal a conflict or absence that prevents a minimum usable schema.

## 6. Public outcomes and exclusions

There are exactly two public outcomes, both defined by `output.schema.json`.

For `ready`, return one `ready` semantic draft as defined in Section 4. The deterministic finalizer materializes the formal `ready` envelope and stable machine identity.

For `blocked`, return one `blocked` semantic draft with `blocking_reason`, `missing_minimum_information[]`, `missing_comparison_dimensions[]`, and `design_warnings`. The finalizer materializes the formal `blocked` envelope.

At least one of the two `missing_*` arrays must be non-empty. A blocked semantic draft must not contain `evidence_schema`.

Do not perform source acquisition, source screening, progressive reading, ResearchRecord extraction, mechanism synthesis, Findings synthesis, conclusion formation, hypothesis generation, next-action recommendation, or other downstream research work.

Do not add compatibility aliases, mappers, bridges, shims, hidden fallbacks, semantic output repair, duplicate authorities, or task-specific exception rules. The only deterministic completion is the registered finalizer described above for code-owned fields.
