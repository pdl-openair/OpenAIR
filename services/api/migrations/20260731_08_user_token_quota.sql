-- Sprint-03 统一用户额度（S03-14-01 规则母 spec）
-- 1) openair_users 扩展：role（权限）/ quota_tier（额度等级）/ bonus_quota / period_used / period_start
-- 2) openair_token_usage_ledger：token 使用账本（append-only）
-- 3) openair_quota_adjustments：人工调整审计（append-only；S03-14-04 消费）
--
-- 前置：openair_users 必须先存在（main.py lifespan 已将 user_repo.bootstrap()
-- 前移到 DurableExecutionRepository.bootstrap() 之前）。

ALTER TABLE openair_users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'user';
ALTER TABLE openair_users ADD COLUMN IF NOT EXISTS quota_tier TEXT NOT NULL DEFAULT 'trial';
ALTER TABLE openair_users ADD COLUMN IF NOT EXISTS bonus_quota BIGINT NOT NULL DEFAULT 0;
ALTER TABLE openair_users ADD COLUMN IF NOT EXISTS period_used BIGINT NOT NULL DEFAULT 0;
ALTER TABLE openair_users ADD COLUMN IF NOT EXISTS period_start TIMESTAMPTZ NULL;

ALTER TABLE openair_users
    ADD CONSTRAINT openair_users_role_check CHECK (role IN ('user', 'admin'));
ALTER TABLE openair_users
    ADD CONSTRAINT openair_users_quota_tier_check CHECK (quota_tier IN ('trial', 'free', 'pro', 'internal', 'admin'));

CREATE TABLE IF NOT EXISTS openair_token_usage_ledger (
    ledger_id     TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL REFERENCES openair_users(user_id),
    workspace_id  TEXT NULL,
    task_id       TEXT NULL,
    call_id       TEXT NULL,
    source        TEXT NOT NULL,
    provider      TEXT NULL,
    model         TEXT NULL,
    status        TEXT NOT NULL CHECK (status IN ('success', 'failed', 'retried')),
    attempt       INTEGER NOT NULL DEFAULT 1,
    input_tokens  BIGINT NOT NULL DEFAULT 0,
    output_tokens BIGINT NOT NULL DEFAULT 0,
    total_tokens  BIGINT NOT NULL DEFAULT 0,
    estimated     BOOLEAN NOT NULL DEFAULT FALSE,
    usage_detail  JSONB NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_token_ledger_user_time ON openair_token_usage_ledger(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_token_ledger_task ON openair_token_usage_ledger(task_id);
CREATE INDEX IF NOT EXISTS idx_token_ledger_call ON openair_token_usage_ledger(call_id);

CREATE TABLE IF NOT EXISTS openair_quota_adjustments (
    adjustment_id TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL REFERENCES openair_users(user_id),
    adjust_type   TEXT NOT NULL CHECK (adjust_type IN ('bonus_quota', 'tier_change')),
    delta         BIGINT NULL,
    old_tier      TEXT NULL,
    new_tier      TEXT NULL,
    reason        TEXT NOT NULL,
    operator_id   TEXT NOT NULL REFERENCES openair_users(user_id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_quota_adjustments_user ON openair_quota_adjustments(user_id, created_at DESC);

-- Append-only hardening（仿 20260728_02_registry_snapshots.sql）：
-- 账本与审计都是「事实记录」，不允许改/删；额度修正通过新增调整行完成。

CREATE OR REPLACE FUNCTION openair_token_ledger_reject_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'openair_token_usage_ledger is append-only'
        USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS openair_token_ledger_reject_update_delete ON openair_token_usage_ledger;
CREATE TRIGGER openair_token_ledger_reject_update_delete
BEFORE UPDATE OR DELETE ON openair_token_usage_ledger
FOR EACH STATEMENT
EXECUTE FUNCTION openair_token_ledger_reject_mutation();

CREATE OR REPLACE FUNCTION openair_quota_adjustments_reject_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'openair_quota_adjustments is append-only'
        USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS openair_quota_adjustments_reject_update_delete ON openair_quota_adjustments;
CREATE TRIGGER openair_quota_adjustments_reject_update_delete
BEFORE UPDATE OR DELETE ON openair_quota_adjustments
FOR EACH STATEMENT
EXECUTE FUNCTION openair_quota_adjustments_reject_mutation();

COMMENT ON TABLE openair_token_usage_ledger IS
    'Per-call token usage ledger (S03-14-02). Append-only; provider-neutral (provider/model are first-class dimensions).';
COMMENT ON TABLE openair_quota_adjustments IS
    'Admin quota adjustment audit trail (S03-14-04). Append-only.';
