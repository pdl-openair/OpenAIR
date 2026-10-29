-- One live requested Recovery per source Run and expected Task revision.
-- retry/replan share the same partial unique index by design.

DO $$
DECLARE
    conflict_count BIGINT;
    conflict_samples TEXT;
BEGIN
    IF to_regclass('public.openair_task_recovery_requests') IS NULL THEN
        RAISE EXCEPTION
            'p01 requested mutex preflight failed: 20260802_04 request table is required';
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname = 'public'
           AND indexname = 'uq_openair_task_recovery_requested_mutex'
    ) THEN
        RAISE EXCEPTION
            'p01 requested mutex preflight failed: target index already exists; refuse modify';
    END IF;

    SELECT COUNT(*)
      INTO conflict_count
      FROM (
          SELECT source_run_id, expected_task_revision
            FROM openair_task_recovery_requests
           WHERE state = 'requested'
           GROUP BY source_run_id, expected_task_revision
          HAVING COUNT(*) > 1
      ) conflicts;

    IF conflict_count > 0 THEN
        SELECT string_agg(sample, ', ' ORDER BY sample)
          INTO conflict_samples
          FROM (
              SELECT left(md5(source_run_id), 12)
                     || ':' || expected_task_revision::TEXT AS sample
                FROM openair_task_recovery_requests
               WHERE state = 'requested'
               GROUP BY source_run_id, expected_task_revision
              HAVING COUNT(*) > 1
               ORDER BY source_run_id, expected_task_revision
               LIMIT 5
          ) samples;
        RAISE EXCEPTION
            'p01 requested mutex preflight failed: % duplicate requested source/revision group(s); samples: %; refuse auto-cancel',
            conflict_count,
            COALESCE(conflict_samples, '');
    END IF;
END
$$;

CREATE UNIQUE INDEX uq_openair_task_recovery_requested_mutex
    ON openair_task_recovery_requests(source_run_id, expected_task_revision)
    WHERE state = 'requested';
