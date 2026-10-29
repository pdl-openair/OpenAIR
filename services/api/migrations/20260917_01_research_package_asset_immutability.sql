-- Research Package v0.2 is a published, immutable Artifact content identity.
-- Reuse the canonical Artifact/object-store tables; add only the deterministic
-- package-version fence needed by the Research Package contract.

CREATE UNIQUE INDEX IF NOT EXISTS uq_openair_research_package_identity
    ON openair_artifacts (
        owner_id,
        workspace_id,
        (metadata->>'researchPackageId'),
        (metadata->>'researchPackageVersion')
    )
    WHERE kind = 'research_package';

CREATE OR REPLACE FUNCTION openair_enforce_research_package_asset()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' AND NEW.kind = 'research_package' THEN
        IF NEW.storage_type IS DISTINCT FROM 'object'
           OR COALESCE(NEW.content_sha256, '') !~ '^[0-9a-f]{64}$'
           OR NEW.storage_ref IS DISTINCT FROM NEW.content_sha256
           OR NEW.content_size IS NULL OR NEW.content_size < 0
           OR BTRIM(COALESCE(NEW.metadata->>'researchPackageId', '')) = ''
           OR BTRIM(COALESCE(NEW.metadata->>'researchPackageVersion', '')) = ''
           OR NEW.metadata->>'researchPackageSpecVersion' IS DISTINCT FROM '2'
           OR NEW.metadata->>'researchPackageContentSha256' IS DISTINCT FROM NEW.content_sha256
        THEN
            RAISE EXCEPTION 'research_package_asset_contract_invalid' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF OLD.kind = 'research_package' THEN
            IF NEW.artifact_id IS DISTINCT FROM OLD.artifact_id
               OR NEW.kind IS DISTINCT FROM OLD.kind
               OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
               OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
               OR NEW.created_by IS DISTINCT FROM OLD.created_by
               OR NEW.created_at IS DISTINCT FROM OLD.created_at
               OR NEW.origin IS DISTINCT FROM OLD.origin
               OR NEW.title IS DISTINCT FROM OLD.title
               OR NEW.summary IS DISTINCT FROM OLD.summary
               OR NEW.format IS DISTINCT FROM OLD.format
               OR NEW.mime_type IS DISTINCT FROM OLD.mime_type
               OR NEW.storage_type IS DISTINCT FROM OLD.storage_type
               OR NEW.storage_scope IS DISTINCT FROM OLD.storage_scope
               OR NEW.storage_ref IS DISTINCT FROM OLD.storage_ref
               OR NEW.content_size IS DISTINCT FROM OLD.content_size
               OR NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256
               OR NEW.source_task_id IS DISTINCT FROM OLD.source_task_id
               OR NEW.metadata->>'researchPackageId' IS DISTINCT FROM OLD.metadata->>'researchPackageId'
               OR NEW.metadata->>'researchPackageVersion' IS DISTINCT FROM OLD.metadata->>'researchPackageVersion'
               OR NEW.metadata->>'researchPackageSpecVersion' IS DISTINCT FROM OLD.metadata->>'researchPackageSpecVersion'
               OR NEW.metadata->>'researchPackageContentSha256' IS DISTINCT FROM OLD.metadata->>'researchPackageContentSha256'
            THEN
                RAISE EXCEPTION 'research_package_version_immutable' USING ERRCODE = '23514';
            END IF;
        ELSIF NEW.kind = 'research_package' THEN
            RAISE EXCEPTION 'research_package_creation_requires_insert' USING ERRCODE = '23514';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_openair_enforce_research_package_asset ON openair_artifacts;
CREATE TRIGGER trg_openair_enforce_research_package_asset
BEFORE INSERT OR UPDATE ON openair_artifacts
FOR EACH ROW EXECUTE FUNCTION openair_enforce_research_package_asset();
