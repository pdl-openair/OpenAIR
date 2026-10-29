-- Token ledger: distinguish platform-provided vs user BYOK usage (visibility projection).
-- Existing rows default to platform. Append-only table: ADD COLUMN only.

ALTER TABLE openair_token_usage_ledger
    ADD COLUMN IF NOT EXISTS key_source TEXT NOT NULL DEFAULT 'platform';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'openair_token_ledger_key_source_check'
    ) THEN
        ALTER TABLE openair_token_usage_ledger
            ADD CONSTRAINT openair_token_ledger_key_source_check
            CHECK (key_source IN ('platform', 'user'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_token_ledger_user_key_source
    ON openair_token_usage_ledger(user_id, key_source, created_at DESC);
