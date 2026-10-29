-- Rollback S04-04 default: new users would again be created active.

ALTER TABLE openair_users
    ALTER COLUMN is_active SET DEFAULT TRUE;
