-- S04-04: new registrations default to inactive until an admin activates them.
-- Existing rows are left unchanged.

ALTER TABLE openair_users
    ALTER COLUMN is_active SET DEFAULT FALSE;
