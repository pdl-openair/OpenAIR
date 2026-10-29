-- Community publication publish-side granularity hint (debug.md #6).
-- Publishers mark a task publication as 'full' (whole project, default) or
-- 'template' (workflow-template-only). Forkers still choose at fork time; this
-- is a recommendation that the browse UI labels and the fork UI defaults from.
ALTER TABLE openair_community_publications
    ADD COLUMN IF NOT EXISTS recommended_granularity TEXT NOT NULL DEFAULT 'full';

ALTER TABLE openair_community_publications
    DROP CONSTRAINT IF EXISTS openair_community_publications_granularity_check;

ALTER TABLE openair_community_publications
    ADD CONSTRAINT openair_community_publications_granularity_check
    CHECK (recommended_granularity IN ('full', 'template'));
