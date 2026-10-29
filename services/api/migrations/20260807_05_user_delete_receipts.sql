CREATE TABLE IF NOT EXISTS openair_user_delete_receipts (
    target_type  TEXT NOT NULL,
    target_id    TEXT NOT NULL,
    owner_id     TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    task_id      TEXT NULL,
    payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
    committed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (target_type, target_id, owner_id, workspace_id)
);
CREATE INDEX IF NOT EXISTS idx_openair_user_delete_receipts_task
    ON openair_user_delete_receipts(task_id, owner_id, workspace_id);
