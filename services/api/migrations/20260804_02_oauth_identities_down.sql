DROP TABLE IF EXISTS openair_oauth_identities;

-- Re-apply NOT NULL only when every row has a password (safe rollback for OAuth-only users).
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM openair_users WHERE hashed_password IS NULL) THEN
    RAISE NOTICE 'openair_users.hashed_password left nullable: OAuth-only users exist';
  ELSE
    ALTER TABLE openair_users ALTER COLUMN hashed_password SET NOT NULL;
  END IF;
END $$;
