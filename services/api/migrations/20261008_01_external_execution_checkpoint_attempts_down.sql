-- Manual rollback. The old one-checkpoint-per-dispatch model cannot represent
-- retry history, so refuse rollback rather than discard execution evidence.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM openair_external_execution_checkpoints
         GROUP BY dispatch_id
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION 'cannot rollback checkpoint attempts with retry evidence present';
    END IF;
END $$;

DROP INDEX IF EXISTS idx_openair_external_execution_checkpoints_dispatch;

ALTER TABLE openair_external_execution_checkpoints
    ADD CONSTRAINT openair_external_execution_checkpoints_dispatch_id_key
        UNIQUE (dispatch_id);

DELETE FROM openair_schema_migrations
 WHERE version = '20261008_01_external_execution_checkpoint_attempts.sql';
