-- Down: drop key_source from token usage ledger.

DROP INDEX IF EXISTS idx_token_ledger_user_key_source;

ALTER TABLE openair_token_usage_ledger
    DROP CONSTRAINT IF EXISTS openair_token_ledger_key_source_check;

ALTER TABLE openair_token_usage_ledger
    DROP COLUMN IF EXISTS key_source;
