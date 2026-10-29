-- Manual rollback for 20260909_01_research_design_authority.sql.
-- Removes only fields introduced by T0-1; it does not attempt to recreate any
-- historical implicit authority.

UPDATE openair_tasks AS task
   SET payload = jsonb_set(
       task.payload,
       '{researchDesignVersions}',
       (
           SELECT jsonb_agg(
               (design.value - 'projectCompletionRequirements' - 'workflowProvenanceRef')
               ORDER BY design.ordinality
           )
           FROM jsonb_array_elements(task.payload->'researchDesignVersions')
                WITH ORDINALITY AS design(value, ordinality)
       ),
       TRUE
   )
 WHERE jsonb_typeof(task.payload->'researchDesignVersions') = 'array'
   AND jsonb_array_length(task.payload->'researchDesignVersions') > 0;

DELETE FROM openair_schema_migrations
 WHERE version = '20260909_01_research_design_authority.sql';
