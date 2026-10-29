-- Rolling back would discard plaintext external evidence. Refuse until every
-- raw body has been explicitly purged.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM openair_external_evidence WHERE raw_body IS NOT NULL
    ) THEN
        RAISE EXCEPTION
            'cannot restore encrypted evidence schema while plaintext evidence exists';
    END IF;
END $$;

DROP INDEX idx_openair_external_evidence_expiry;

ALTER TABLE openair_external_evidence
    DROP CONSTRAINT openair_external_evidence_raw_body_check,
    DROP COLUMN raw_body,
    ADD COLUMN raw_availability TEXT NOT NULL DEFAULT 'purged',
    ADD COLUMN encryption_algorithm TEXT NULL,
    ADD COLUMN encryption_version INTEGER NULL,
    ADD COLUMN key_id TEXT NULL,
    ADD COLUMN nonce BYTEA NULL,
    ADD COLUMN ciphertext BYTEA NULL,
    ADD COLUMN auth_tag BYTEA NULL;

ALTER TABLE openair_external_evidence
    ALTER COLUMN raw_availability DROP DEFAULT,
    ADD CONSTRAINT openair_external_evidence_raw_availability_check
        CHECK (raw_availability IN ('encrypted', 'redacted_only', 'purged')),
    ADD CONSTRAINT openair_external_evidence_check1
        CHECK (
            (raw_availability = 'encrypted' AND encryption_algorithm = 'AES-256-GCM'
             AND encryption_version = 1 AND key_id IS NOT NULL AND octet_length(nonce) = 12
             AND ciphertext IS NOT NULL AND octet_length(auth_tag) = 16 AND purged_at IS NULL)
            OR
            (raw_availability IN ('redacted_only', 'purged') AND encryption_algorithm IS NULL
             AND encryption_version IS NULL AND key_id IS NULL AND nonce IS NULL
             AND ciphertext IS NULL AND auth_tag IS NULL)
        ),
    ADD CONSTRAINT openair_external_evidence_check2
        CHECK ((raw_availability = 'purged') = (purged_at IS NOT NULL)),
    ADD CONSTRAINT openair_external_evidence_key_id_nonce_key UNIQUE (key_id, nonce);

CREATE INDEX idx_openair_external_evidence_expiry
    ON openair_external_evidence(expires_at, evidence_id)
    WHERE raw_availability = 'encrypted';

DELETE FROM openair_schema_migrations
 WHERE version = '20261008_03_plaintext_external_evidence.sql';
