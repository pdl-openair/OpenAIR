from pathlib import Path


API_ROOT = Path(__file__).parents[1]
UP = API_ROOT / "migrations" / "20260917_01_research_package_asset_immutability.sql"
DOWN = API_ROOT / "migrations" / "20260917_01_research_package_asset_immutability_down.sql"


def test_migration_fences_identity_content_and_package_version() -> None:
    sql = UP.read_text(encoding="utf-8")

    assert "uq_openair_research_package_identity" in sql
    assert "researchPackageId" in sql
    assert "researchPackageVersion" in sql
    assert "researchPackageSpecVersion" in sql
    assert "researchPackageContentSha256" in sql
    assert "research_package_version_immutable" in sql
    assert "NEW.artifact_id IS DISTINCT FROM OLD.artifact_id" in sql
    assert "NEW.created_at IS DISTINCT FROM OLD.created_at" in sql
    assert "BEFORE INSERT OR UPDATE ON openair_artifacts" in sql
    assert "NEW.storage_type IS DISTINCT FROM 'object'" in sql


def test_down_migration_removes_only_research_package_fence() -> None:
    sql = DOWN.read_text(encoding="utf-8")

    assert "DROP TRIGGER IF EXISTS trg_openair_enforce_research_package_asset" in sql
    assert "DROP FUNCTION IF EXISTS openair_enforce_research_package_asset" in sql
    assert "DROP INDEX IF EXISTS uq_openair_research_package_identity" in sql
    assert "DELETE FROM openair_schema_migrations" in sql
