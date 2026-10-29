BEGIN;

LOCK TABLE openair_recovery_directive,
           openair_external_retry_attempt,
           openair_external_reconciliation_evidence,
           openair_external_reconciliation,
           openair_external_evidence_access_audit,
           openair_external_evidence,
           openair_external_execution
    IN ACCESS EXCLUSIVE MODE;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM openair_external_evidence LIMIT 1)
       OR EXISTS (SELECT 1 FROM openair_external_evidence_access_audit LIMIT 1)
       OR EXISTS (SELECT 1 FROM openair_external_reconciliation LIMIT 1)
       OR EXISTS (SELECT 1 FROM openair_external_retry_attempt LIMIT 1)
       OR EXISTS (SELECT 1 FROM openair_recovery_directive LIMIT 1)
       OR EXISTS (SELECT 1 FROM openair_external_execution LIMIT 1) THEN
        RAISE EXCEPTION 'refusing to drop non-empty external execution evidence audit tables';
    END IF;
END $$;

DROP TABLE openair_recovery_directive;
DROP TABLE openair_external_retry_attempt;
DROP TABLE openair_external_reconciliation_evidence;
DROP TABLE openair_external_reconciliation;
DROP TABLE openair_external_evidence_access_audit;
DROP TABLE openair_external_evidence;
DROP TABLE openair_external_execution;

DELETE FROM openair_schema_migrations
 WHERE version = '20260730_04_external_execution_evidence.sql';

COMMIT;
