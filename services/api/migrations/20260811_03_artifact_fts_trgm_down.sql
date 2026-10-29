-- D3 down: drop the trigram search indexes. pg_trgm extension is left in place
-- (shared, cheap, may be used by future indexes).

DROP INDEX IF EXISTS idx_openair_artifacts_summary_trgm;
DROP INDEX IF EXISTS idx_openair_artifacts_title_trgm;
