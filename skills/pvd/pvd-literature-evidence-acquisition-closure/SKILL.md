---
name: pvd-literature-evidence-acquisition-closure
description: Executes bounded PVD literature acquisition and closes traceable EvidencePacket, ResearchRecords, provenance, coverage/gaps, and pending-parameter evidence linkage.
---

# PVD Literature Evidence Acquisition & Closure

This is one material-preparation capability. Internally it reuses registered OpenAIR retrieval, scientific screening, progressive reading, Evidence extraction, and ResearchRecord structuring. Internal calls and batches never become fixed PVD Workflow nodes.

Inputs are the exact T03 retrieval design, EvidenceSchema, coverage target, pending parameter identities, user query, and task id. Do not reinterpret or promote pending parameters.

Execution contract:
- retrieve once from the T03 query variants;
- attempt scientific screening; if unavailable, preserve the real retrieval order;
- read an initial wave of at most 8 papers;
- extract Evidence per paper through the shared bounded parallel Evidence runtime;
- continue with up to 4 additional papers only when formal required Evidence targets remain uncovered;
- isolate single-paper / single-Evidence failures;
- structure ResearchRecords when possible, otherwise preserve every valid Evidence item as deterministic singleton records;
- emit explicit unresolved gaps and pending-parameter Evidence linkage.

Only finalized Evidence with real source anchors may enter ResearchRecords. Do not judge comparability, direction, feasibility, or recommended parameter values.
