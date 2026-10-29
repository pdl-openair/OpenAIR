-- A dispatch is stable research work identity; each retry is a distinct
-- capability call with its own checkpoint and attempt evidence.
ALTER TABLE openair_external_execution_checkpoints
    DROP CONSTRAINT IF EXISTS openair_external_execution_checkpoints_dispatch_id_key;

CREATE INDEX IF NOT EXISTS idx_openair_external_execution_checkpoints_dispatch
    ON openair_external_execution_checkpoints(dispatch_id);
