# Goal-Evolving Demo — Mid-search Pivot

This is a **demo** capability for the Nature paper's Fig 7a. It is NOT a
production-grade mid-search pivot — it requires that new objectives' property
keys are already characterized in the current population.

## When it works

After a run with `--objectives '{"bandgap": "maximize"}'` has completed
characterization with the `alignn` backend, the population has `bandgap`,
`ehull`, `dielectric`, etc. all populated. You can pivot to any of those
keys as a new objective.

## When it fails

If you pivot to a key not in the current characterization (e.g. pivot to
`magmom` when the run was characterized with `alignn` default keys excluding
magmom), `pivot` raises `KeyError` listing the missing keys.

## Demo runbook (for Fig 7a)

```bash
# Step 1: Run 3 generations optimizing bandgap
bash <SKILL>/scripts/run.sh init --run-dir /tmp/demo \
    --fitness-backend alignn \
    --objectives '{"bandgap": "maximize", "ehull": "minimize"}' \
    --constraints '{"bandgap": {"min": 0.5, "weight": 10}}' \
    --pop-size 16 --max-generations 3 --noise-level 0.3 \
    --seed-cifs <seeds>
for g in 1 2 3; do
    bash <SKILL>/scripts/run.sh step --run-dir /tmp/demo
done

# Step 2: Pivot to ehull + novel (requires mattergen_eval backend's keys present)
bash <SKILL>/scripts/run.sh pivot --run-dir /tmp/demo \
    --objectives '{"ehull": "minimize"}'

# Step 3: Continue 2 more generations under new objective
for g in 1 2; do
    bash <SKILL>/scripts/run.sh step --run-dir /tmp/demo
done

# Step 4: Plot Fig 7a from pivot_log.jsonl + snapshot_gen*.json
```

The pivot invalidates the old Pareto ranking and re-ranks the existing
population in place using `saes_selection.fast_non_dominated_sorting` with
the new objectives.
