-- Manual, reviewed rollback for 20260731_08_user_token_quota.sql
-- Do not run automatically. Review first: dropping the ledger loses billing history.

DROP TRIGGER IF EXISTS openair_quota_adjustments_reject_update_delete ON openair_quota_adjustments;
DROP FUNCTION IF EXISTS openair_quota_adjustments_reject_mutation();
DROP TRIGGER IF EXISTS openair_token_ledger_reject_update_delete ON openair_token_usage_ledger;
DROP FUNCTION IF EXISTS openair_token_ledger_reject_mutation();

DROP TABLE IF EXISTS openair_quota_adjustments;
DROP TABLE IF EXISTS openair_token_usage_ledger;

ALTER TABLE openair_users DROP CONSTRAINT IF EXISTS openair_users_quota_tier_check;
ALTER TABLE openair_users DROP CONSTRAINT IF EXISTS openair_users_role_check;
ALTER TABLE openair_users DROP COLUMN IF EXISTS period_start;
ALTER TABLE openair_users DROP COLUMN IF EXISTS period_used;
ALTER TABLE openair_users DROP COLUMN IF EXISTS bonus_quota;
ALTER TABLE openair_users DROP COLUMN IF EXISTS quota_tier;
ALTER TABLE openair_users DROP COLUMN IF EXISTS role;

DELETE FROM openair_schema_migrations WHERE version = '20260731_08_user_token_quota.sql';
