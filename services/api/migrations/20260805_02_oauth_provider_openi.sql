-- Allow OpenI as an OAuth identity provider alongside github/gitlink.

ALTER TABLE openair_oauth_identities
  DROP CONSTRAINT IF EXISTS openair_oauth_identities_provider_check;

ALTER TABLE openair_oauth_identities
  ADD CONSTRAINT openair_oauth_identities_provider_check
  CHECK (provider = ANY (ARRAY['github'::text, 'gitlink'::text, 'openi'::text]));
