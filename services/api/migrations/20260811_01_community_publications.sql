-- C1 community interface: registry of user-published tasks/artifacts.
-- A row here marks a live task/artifact as publicly forkable/readable across
-- workspaces. Unlike skill contributions (byte-blob snapshots), a community
-- publication points at the live resource row by id; publishing = marking public.

CREATE TABLE IF NOT EXISTS openair_community_publications (
    publication_id        TEXT PRIMARY KEY,
    resource_type         TEXT NOT NULL CHECK (resource_type IN ('task', 'artifact')),
    resource_id           TEXT NOT NULL,
    publisher_owner_id    TEXT NOT NULL,
    publisher_workspace_id TEXT NOT NULL,
    title                 TEXT NOT NULL,
    summary               TEXT,
    published_manifest    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (resource_type, resource_id)
);

CREATE INDEX IF NOT EXISTS idx_community_pub_created
    ON openair_community_publications (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_community_pub_type
    ON openair_community_publications (resource_type);
CREATE INDEX IF NOT EXISTS idx_community_pub_resource
    ON openair_community_publications (resource_type, resource_id);

-- User comments on published community resources (append-only discussion;
-- orthogonal to admin publication review).
CREATE TABLE IF NOT EXISTS openair_community_comments (
    comment_id    TEXT PRIMARY KEY,
    publication_id TEXT NOT NULL REFERENCES openair_community_publications(publication_id) ON DELETE CASCADE,
    author_owner_id TEXT NOT NULL,
    author_workspace_id TEXT NOT NULL,
    body          TEXT NOT NULL,
    parent_comment_id TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_community_comments_pub
    ON openair_community_comments (publication_id, created_at);
