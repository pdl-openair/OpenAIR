-- S04-01 user platform governance: audit / password reset / contributions / quota prefs

ALTER TABLE openair_users
    ADD COLUMN IF NOT EXISTS quota_alert_threshold_pct INTEGER DEFAULT 80;

CREATE TABLE IF NOT EXISTS openair_audit_logs (
    audit_id     TEXT PRIMARY KEY,
    action       TEXT NOT NULL,
    actor_id     TEXT,
    target_id    TEXT,
    outcome      TEXT NOT NULL DEFAULT 'success',
    detail       JSONB NOT NULL DEFAULT '{}'::jsonb,
    ip           TEXT,
    user_agent   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_logs_action_created
    ON openair_audit_logs (action, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_actor_created
    ON openair_audit_logs (actor_id, created_at DESC);

CREATE TABLE IF NOT EXISTS openair_password_reset_tokens (
    token_id     TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES openair_users(user_id) ON DELETE CASCADE,
    token_hash   TEXT NOT NULL UNIQUE,
    expires_at   TIMESTAMPTZ NOT NULL,
    used_at      TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_password_reset_user
    ON openair_password_reset_tokens (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS openair_contributions (
    contribution_id   TEXT PRIMARY KEY,
    owner_id          TEXT NOT NULL REFERENCES openair_users(user_id),
    workspace_id      TEXT,
    resource_type     TEXT NOT NULL DEFAULT 'skill',
    resource_ref      TEXT NOT NULL,
    title             TEXT NOT NULL,
    description       TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'private_ready',
    review_notes      TEXT,
    reviewer_id       TEXT,
    reviewed_at       TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_contributions_owner_created
    ON openair_contributions (owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_contributions_status_created
    ON openair_contributions (status, created_at DESC);
