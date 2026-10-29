# Memory Module

Natural-language memory store for cross-run experience reuse.

## Schema (`memory_store.MemoryEntry`)

| Field | Type | Description |
|---|---|---|
| `entry_id` | str | 12-char hex (uuid4 truncated) |
| `run_id` | str | Run directory name |
| `family` | str | Material family tag (e.g. `rare_earth_intermetallics`) |
| `gen` | int | Generation at which the decision was made |
| `context` | str | Natural-language snapshot summary (1–3 sentences) |
| `action` | str | Steer action taken (e.g. `ban element Au`, `noise 0.2`) |
| `outcome` | dict[str, float] | `delta_sun`, `delta_ehull_median`, `delta_stable_count` |
| `confidence` | float | [0, 1]; starts 0.5, immediately ±0.05 by outcome (Δsun>0 or Δehull<0 → upvote, else downvote), decayed ×0.98/step |
| `created_at` | str | ISO 8601 UTC |

## Retrieval scoring

`MemoryStore.query(context, family, top_k=5)` ranks entries by:

```
combined_score = Jaccard(tokenize(context), tokenize(stored_context)) × confidence
```

Tokenization: lowercase + `[a-z0-9_]+` regex, no stopword removal. Jaccard
chosen over TF-IDF because contexts are short (~10–30 tokens).

## File layout

```
<memory-dir>/
└── store.json    # Flat JSON array of MemoryEntry dicts
```

Per-run-dir artifacts (written by `step`):

```
<RUN_DIR>/
├── snapshot_latest.json
├── memory_query_result.json     # top-K retrieved, written after step
├── steer_log.jsonl              # all steer actions, append-only
└── population.json              # rank updated each step
```

## Confidence semantics (outcome-driven, ExpeL-style)

Confidence reflects whether the recorded action actually helped, never how
often the entry was retrieved — retrieval-frequency boosts would upvote
failures. Per step, the driver: (1) decays all prior entries ×0.98,
(2) appends the new entry at 0.5, (3) applies the outcome verdict ±0.05
(improvement = `delta_sun > 0`, or flat SUN with `delta_ehull_median < 0`).

## Why natural language (not embeddings)

Auditable. A审稿人 can `cat store.json` and read every memory line by line.
The agent (Claude) reasons about the text directly; no opaque vector DB.
