-- D3: pg_trgm GIN index for fast ILIKE search on artifact title/summary.
--
-- ArtifactRepository.list_by_scope searches `title`/`summary` ILIKE '%q%'
-- (plus storage_ref for inline content). Without a trigram index each ILIKE is
-- a sequential scan over openair_artifacts. pg_trgm's GIN index accelerates
-- ILIKE/LIKE with leading wildcards, so the existing query (unchanged) becomes
-- an index scan.
--
-- D3b (a denormalized search_text column with content excerpts so object-stored
-- bodies are searchable — the inline storage_ref ILIKE breaks when content moves
-- to MinIO) is deferred: it needs content extraction at every write path and has
-- narrow benefit (title/summary already cover all storage types).

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_openair_artifacts_title_trgm
    ON openair_artifacts USING GIN (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_openair_artifacts_summary_trgm
    ON openair_artifacts USING GIN (summary gin_trgm_ops);
