CREATE TABLE openair_resource_saves (
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT pk_openair_resource_saves PRIMARY KEY (owner_id, workspace_id, resource_id)
);

CREATE INDEX idx_openair_resource_saves_scope_created
    ON openair_resource_saves(owner_id, workspace_id, created_at DESC, resource_id ASC);
