-- Store external execution evidence directly. Encryption/keyring configuration
-- is intentionally removed; a response must not disappear when an optional
-- deployment secret is absent.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM openair_external_evidence
         WHERE raw_availability = 'encrypted'
    ) THEN
        RAISE EXCEPTION
            'cannot remove external evidence encryption while encrypted rows exist';
    END IF;
END $$;

DROP INDEX idx_openair_external_evidence_expiry;

ALTER TABLE openair_external_evidence
    DROP CONSTRAINT openair_external_evidence_key_id_nonce_key,
    DROP CONSTRAINT openair_external_evidence_raw_availability_check,
    DROP CONSTRAINT openair_external_evidence_check1,
    DROP CONSTRAINT openair_external_evidence_check2,
    ADD COLUMN raw_body BYTEA NULL;

-- Historical redacted-only rows never contained recoverable bytes. Preserve
-- their metadata and mark the absent body honestly instead of fabricating it.
UPDATE openair_external_evidence
   SET purged_at = COALESCE(purged_at, clock_timestamp())
 WHERE raw_availability = 'redacted_only';

ALTER TABLE openair_external_evidence
    DROP COLUMN raw_availability,
    DROP COLUMN encryption_algorithm,
    DROP COLUMN encryption_version,
    DROP COLUMN key_id,
    DROP COLUMN nonce,
    DROP COLUMN ciphertext,
    DROP COLUMN auth_tag,
    ADD CONSTRAINT openair_external_evidence_raw_body_check
        CHECK ((raw_body IS NULL) = (purged_at IS NOT NULL));

CREATE INDEX idx_openair_external_evidence_expiry
    ON openair_external_evidence(expires_at, evidence_id)
    WHERE raw_body IS NOT NULL;
