-- T0-1: make project completion requirements and Workflow provenance explicit
-- members of every persisted ResearchDesignVersion.
--
-- This is a one-time contract upgrade. Rows are migrated only when the
-- required historical facts are mechanically provable. Legacy rows that
-- cannot satisfy the current contract without guessing are left unchanged
-- for row-local compatibility handling; they must not block API startup.

UPDATE openair_tasks AS task
   SET payload = jsonb_set(
       task.payload,
       '{researchDesignVersions}',
       (
           SELECT jsonb_agg(
               design.value
               || CASE
                      WHEN design.value ? 'projectCompletionRequirements'
                      THEN '{}'::jsonb
                      ELSE jsonb_build_object(
                          'projectCompletionRequirements',
                          (
                              SELECT jsonb_agg(
                                  jsonb_build_object(
                                      'completionRequirementId',
                                      'migrated_project_completion_'
                                          || (design.value->>'researchDesignId')
                                          || '_'
                                          || (target.ordinality - 1)::TEXT,
                                      'targetOutputRef',
                                      'understanding.targetOutputs['
                                          || (target.ordinality - 1)::TEXT
                                          || ']',
                                      'description', target.value
                                  )
                                  ORDER BY target.ordinality
                              )
                              FROM jsonb_array_elements_text(
                                  design.value->'understanding'->'targetOutputs'
                              ) WITH ORDINALITY AS target(value, ordinality)
                          )
                      )
                  END
               || CASE
                      WHEN design.value ? 'workflowProvenanceRef'
                      THEN '{}'::jsonb
                      ELSE jsonb_build_object(
                          'workflowProvenanceRef',
                          CASE
                              WHEN NULLIF(
                                  BTRIM(task.payload->'workflowAdmission'->>'workflowId'),
                                  ''
                              ) IS NULL
                              THEN 'null'::jsonb
                              ELSE jsonb_build_object(
                                  'workflowId', task.payload->'workflowAdmission'->>'workflowId',
                                  'workflowVersion',
                                      (task.payload->'workflowAdmission'->>'workflowVersion')::INTEGER,
                                  'contentHash',
                                      'sha256:' || REGEXP_REPLACE(
                                          LOWER(task.payload->'workflowAdmission'->>'workflowContentHash'),
                                          '^sha256:',
                                          ''
                                      )
                              )
                          END
                      )
                  END
               ORDER BY design.ordinality
           )
           FROM jsonb_array_elements(task.payload->'researchDesignVersions')
                WITH ORDINALITY AS design(value, ordinality)
       ),
       TRUE
   )
 WHERE jsonb_typeof(task.payload->'researchDesignVersions') = 'array'
   AND jsonb_array_length(task.payload->'researchDesignVersions') > 0
   -- Only migrate rows whose completion targets are already mechanically valid.
   -- Invalid legacy rows remain untouched instead of aborting the whole migration.
   AND NOT EXISTS (
       SELECT 1
         FROM jsonb_array_elements(task.payload->'researchDesignVersions') AS design
        WHERE jsonb_typeof(design->'understanding'->'targetOutputs') IS DISTINCT FROM 'array'
           OR CASE
                  WHEN jsonb_typeof(design->'understanding'->'targetOutputs') = 'array'
                  THEN jsonb_array_length(design->'understanding'->'targetOutputs') = 0
                  ELSE TRUE
              END
           OR EXISTS (
               SELECT 1
                 FROM jsonb_array_elements(
                          CASE
                              WHEN jsonb_typeof(design->'understanding'->'targetOutputs') = 'array'
                              THEN design->'understanding'->'targetOutputs'
                              ELSE '[]'::jsonb
                          END
                      ) AS target(value)
                WHERE jsonb_typeof(target.value) IS DISTINCT FROM 'string'
                   OR NULLIF(BTRIM(target.value #>> '{}'), '') IS NULL
           )
   )
   -- Workflow provenance is backfilled only when the persisted admission is
   -- absent/null, explicitly empty, or a complete mechanically valid identity.
   -- Partial/invalid legacy admissions are preserved for row-local handling.
   AND (
       NOT (task.payload ? 'workflowAdmission')
       OR task.payload->'workflowAdmission' = 'null'::jsonb
       OR (
           jsonb_typeof(task.payload->'workflowAdmission') = 'object'
           AND (
               COALESCE(
                   NULLIF(BTRIM(task.payload->'workflowAdmission'->>'workflowId'), ''),
                   NULLIF(BTRIM(task.payload->'workflowAdmission'->>'workflowVersion'), ''),
                   NULLIF(BTRIM(task.payload->'workflowAdmission'->>'workflowContentHash'), '')
               ) IS NULL
               OR (
                   NULLIF(BTRIM(task.payload->'workflowAdmission'->>'workflowId'), '') IS NOT NULL
                   AND COALESCE(
                       (task.payload->'workflowAdmission'->>'workflowVersion') ~ '^[1-9][0-9]*$',
                       FALSE
                   )
                   AND COALESCE(
                       LOWER(task.payload->'workflowAdmission'->>'workflowContentHash')
                           ~ '^(sha256:)?[0-9a-f]{64}$',
                       FALSE
                   )
               )
           )
       )
   )
   AND EXISTS (
       SELECT 1
         FROM jsonb_array_elements(task.payload->'researchDesignVersions') AS design
        WHERE NOT (design ? 'projectCompletionRequirements')
           OR NOT (design ? 'workflowProvenanceRef')
   );
