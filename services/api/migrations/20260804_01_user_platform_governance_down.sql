DROP TABLE IF EXISTS openair_contributions;
DROP TABLE IF EXISTS openair_password_reset_tokens;
DROP TABLE IF EXISTS openair_audit_logs;
ALTER TABLE openair_users DROP COLUMN IF EXISTS quota_alert_threshold_pct;
