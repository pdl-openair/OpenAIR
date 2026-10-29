#!/usr/bin/env bash
# Pilot runbook for 族 1 (rare-earth intermetallics) ablation.
#
# Spawns 5 STATIC_RULES runs in parallel (deterministic, no Claude).
# Prints a checklist of 5 AGENT_NO_MEM and 5 AGENT_WITH_MEM runs that
# require manual Claude orchestration per MANUAL_ORCHESTRATION.md.
# Computes ORACLE post-hoc from AGENT_WITH_MEM trajectories.
set -euo pipefail

SKILL="/home/george/code/openair/.claude/skills/crystal-design-structure-crossover-agent"
BASE="/home/george/code/openair/skills/materials/mattergen-results/mattergen-base/ablation_family1_pilot"
SEEDS_DIR="/home/george/code/openair/skills/materials/mattergen-results/mattergen-base/generated_crystals_cif"
SEEDS=$(ls $SEEDS_DIR/*.cif | tr '\n' ',' | sed 's/,$//')

MEMORY_DIR="$BASE/shared_memory"
mkdir -p "$BASE" "$MEMORY_DIR"

# Scaffold all 20 runs
python "$SKILL/scripts/ablation_runner.py" scaffold \
    --base-run-dir "$BASE" \
    --family rare_earth_intermetallics \
    --fitness-backend mattergen_eval \
    --objectives '{"ehull_eval":"minimize","novel":"maximize","stable":"maximize","rmsd_relax":"minimize"}' \
    --constraints '{"comp_valid_eval":{"min":1.0,"weight":10},"structure_valid":{"min":1.0,"weight":10},"num_elements":{"min":2,"weight":10}}' \
    --seeds "$SEEDS" \
    --modes static_rules,agent_no_mem,agent_with_mem \
    --replicates 5 \
    --pop-size 16 --max-generations 4

# Run STATIC_RULES in parallel (5 subprocesses)
echo "[runbook] Launching 5 STATIC_RULES runs in background..."
for rep in 0 1 2 3 4; do
    bash "$BASE/static_rules_rep$rep/run_static.sh" > "$BASE/static_rules_rep$rep/run.log" 2>&1 &
done
wait
echo "[runbook] STATIC_RULES runs complete."

# AGENT runs require external Claude orchestration
echo "[runbook] For AGENT_NO_MEM and AGENT_WITH_MEM runs, open these directories"
echo "         in separate Claude sessions and follow MANUAL_ORCHESTRATION.md:"
for mode in agent_no_mem agent_with_mem; do
    for rep in 0 1 2 3 4; do
        echo "  $BASE/${mode}_rep$rep"
    done
done

# After all AGENT runs finish, aggregate
echo "[runbook] After manual runs complete, execute:"
echo "  python $SKILL/scripts/pilot_analysis.py --base-dir $BASE --out $BASE/aggregate.json"
