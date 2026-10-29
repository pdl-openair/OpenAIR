ALTER TABLE openair_community_publications
    DROP CONSTRAINT IF EXISTS openair_community_publications_granularity_check;

ALTER TABLE openair_community_publications
    DROP COLUMN IF EXISTS recommended_granularity;
