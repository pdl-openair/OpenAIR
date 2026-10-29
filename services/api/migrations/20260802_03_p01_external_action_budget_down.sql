-- Manual rollback for 20260802_03_p01_external_action_budget.sql only.
-- apply_postgres_migrations never auto-executes *_down.sql; this file is for
-- explicit operator/test rollback. Deletes only this migration's ledger row.

DROP INDEX IF EXISTS idx_openair_external_action_reservations_run;
DROP INDEX IF EXISTS uq_openair_external_action_initial_reserved;
DROP TABLE IF EXISTS openair_execution_external_action_reservations;

ALTER TABLE openair_execution_runs
    DROP CONSTRAINT IF EXISTS openair_execution_runs_external_actions_reserved_lte_max;

ALTER TABLE openair_execution_runs
    DROP COLUMN IF EXISTS external_actions_reserved,
    DROP COLUMN IF EXISTS max_external_actions;

DELETE FROM openair_schema_migrations
 WHERE version = '20260802_03_p01_external_action_budget.sql';
