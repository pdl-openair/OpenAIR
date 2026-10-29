-- S04-02: OAuth identity bindings (GitHub first; GitLink later uses same table).

ALTER TABLE openair_users
  ALTER COLUMN hashed_password DROP NOT NULL;

CREATE TABLE IF NOT EXISTS openair_oauth_identities (
    id                 TEXT PRIMARY KEY,
    user_id            TEXT NOT NULL REFERENCES openair_users(user_id) ON DELETE CASCADE,
    provider           TEXT NOT NULL,
    provider_user_id   TEXT NOT NULL,
    provider_login     TEXT,
    provider_email     TEXT,
    linked_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT openair_oauth_identities_provider_check
      CHECK (provider = ANY (ARRAY['github'::text, 'gitlink'::text])),
    CONSTRAINT uq_oauth_provider_user UNIQUE (provider, provider_user_id),
    CONSTRAINT uq_oauth_user_provider UNIQUE (user_id, provider)
);

CREATE INDEX IF NOT EXISTS idx_oauth_identities_user
  ON openair_oauth_identities (user_id);
