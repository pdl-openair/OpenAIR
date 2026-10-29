-- Revert OpenI provider allowance (keeps existing openi rows only if none exist).

ALTER TABLE openair_oauth_identities
  DROP CONSTRAINT IF EXISTS openair_oauth_identities_provider_check;

ALTER TABLE openair_oauth_identities
  ADD CONSTRAINT openair_oauth_identities_provider_check
  CHECK (provider = ANY (ARRAY['github'::text, 'gitlink'::text]));
