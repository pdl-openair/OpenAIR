from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import os
from pathlib import Path
import secrets
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
import pytest

from app.modules.postgres_migrations import apply_postgres_migrations
from app.modules.registry_snapshot_repository import RegistrySnapshotRepository, snapshot_from_envelope
from app.modules.research_orchestration_resolver import build_registry_snapshot


ADMIN_DSN_ENV = "OPENAIR_T03_POSTGRES_ADMIN_DSN"
MARKER_PREFIX = "OpenAIR T03 R1 owned QA database"
MIGRATIONS = Path(__file__).resolve().parents[4] / "migrations"
UP = MIGRATIONS / "20260728_02_registry_snapshots.sql"
DOWN = MIGRATIONS / "20260728_02_registry_snapshots_down.sql"


@dataclass(frozen=True)
class PostgresEnvironment:
    database: str
    marker: str
    owner_role: str
    app_role: str
    owner_dsn: str
    app_dsn: str
    admin_dsn: str


def _description(admin: psycopg.Connection, database: str) -> str | None:
    row = admin.execute(
        "SELECT pg_catalog.shobj_description(oid, 'pg_database') AS description "
        "FROM pg_catalog.pg_database WHERE datname=%s",
        (database,),
    ).fetchone()
    return None if row is None else row["description"]


def _drop_owned_environment(admin: psycopg.Connection, environment: PostgresEnvironment) -> None:
    description = _description(admin, environment.database)
    if description is not None:
        if description != environment.marker:
            pytest.fail("refusing to drop unowned T03 R1 database")
        admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()",
            (environment.database,),
        )
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(environment.database)))
    admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(environment.app_role)))
    admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(environment.owner_role)))


@pytest.fixture
def postgres_environment() -> PostgresEnvironment:
    admin_dsn = os.environ.get(ADMIN_DSN_ENV, "").strip()
    if not admin_dsn:
        pytest.fail(f"{ADMIN_DSN_ENV} is required")
    suffix = uuid.uuid4().hex[:12]
    database = f"openair_t03_r1_{suffix}"
    owner_role = f"t03_owner_{suffix}"
    app_role = f"t03_app_{suffix}"
    marker = f"{MARKER_PREFIX} {suffix}"
    owner_password = secrets.token_urlsafe(18)
    app_password = secrets.token_urlsafe(18)
    environment = PostgresEnvironment(
        database=database,
        marker=marker,
        owner_role=owner_role,
        app_role=app_role,
        owner_dsn=make_conninfo(admin_dsn, dbname=database, user=owner_role, password=owner_password),
        app_dsn=make_conninfo(admin_dsn, dbname=database, user=app_role, password=app_password),
        admin_dsn=admin_dsn,
    )
    with psycopg.connect(admin_dsn, autocommit=True, row_factory=dict_row) as admin:
        try:
            admin.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(owner_role), sql.Literal(owner_password)
                )
            )
            admin.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(app_role), sql.Literal(app_password)
                )
            )
            admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(database), sql.Identifier(owner_role)))
            admin.execute(sql.SQL("COMMENT ON DATABASE {} IS {}").format(sql.Identifier(database), sql.Literal(marker)))
            with psycopg.connect(environment.owner_dsn, row_factory=dict_row) as owner:
                from tests.integration._production_postgres_bootstrap import bootstrap_production_postgres_schema

                with owner.cursor() as cursor:
                    bootstrap_production_postgres_schema(cursor)
                    cursor.execute(
                        sql.SQL("GRANT SELECT, INSERT ON openair_registry_snapshots TO {}").format(
                            sql.Identifier(app_role)
                        )
                    )
            yield environment
        finally:
            _drop_owned_environment(admin, environment)


def _snapshots(marker: str):
    old = build_registry_snapshot()
    changed = old.envelope
    changed["definitions"]["agents"][0]["capability"].append(marker)
    return old, snapshot_from_envelope(changed)


def test_production_bootstrap_insert_reload_collision_and_history(postgres_environment: PostgresEnvironment) -> None:
    repository = RegistrySnapshotRepository(
        lambda: psycopg.connect(postgres_environment.app_dsn, row_factory=dict_row)
    )
    old, new = _snapshots("current-registry")
    repository.put(old)
    repository.put(old)
    repository.put(new)
    assert repository.get(old.content_hash).canonical_payload == old.canonical_payload
    assert repository.get(new.content_hash).canonical_payload == new.canonical_payload

    with psycopg.connect(postgres_environment.owner_dsn, row_factory=dict_row) as owner:
        constraints = {
            row["contype"]
            for row in owner.execute(
                """SELECT constraint_row.contype::text AS contype
                     FROM pg_catalog.pg_constraint AS constraint_row
                     JOIN pg_catalog.pg_class AS relation ON relation.oid=constraint_row.conrelid
                    WHERE relation.relname='openair_registry_snapshots'"""
            ).fetchall()
        }
        assert {"p", "c"} <= constraints
        with pytest.raises(psycopg.errors.UniqueViolation):
            owner.execute(
                "INSERT INTO openair_registry_snapshots(schema_version,content_hash,payload) VALUES (%s,%s,%s::jsonb)",
                (1, old.content_hash, '{"different":true}'),
            )


@pytest.mark.parametrize("actor", ["application", "owner"])
@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE openair_registry_snapshots SET payload='{}'::jsonb",
        "DELETE FROM openair_registry_snapshots",
        "TRUNCATE TABLE openair_registry_snapshots",
    ],
    ids=["update", "delete", "truncate"],
)
def test_database_rejects_committed_archive_mutation(
    postgres_environment: PostgresEnvironment,
    actor: str,
    statement: str,
) -> None:
    repository = RegistrySnapshotRepository(
        lambda: psycopg.connect(postgres_environment.app_dsn, row_factory=dict_row)
    )
    snapshot, _ = _snapshots(f"immutable-{actor}")
    repository.put(snapshot)
    with psycopg.connect(postgres_environment.owner_dsn, row_factory=dict_row) as owner:
        owner.execute(
            sql.SQL("GRANT UPDATE, DELETE, TRUNCATE ON openair_registry_snapshots TO {}").format(
                sql.Identifier(postgres_environment.app_role)
            )
        )
        role_facts = owner.execute(
            """SELECT tableowner, has_table_privilege(%s, 'openair_registry_snapshots', 'SELECT,INSERT') AS expected,
                      has_table_privilege(%s, 'openair_registry_snapshots', 'UPDATE,DELETE,TRUNCATE') AS dangerous
                 FROM pg_catalog.pg_tables WHERE schemaname='public' AND tablename='openair_registry_snapshots'""",
            (postgres_environment.app_role, postgres_environment.app_role),
        ).fetchone()
        assert role_facts == {
            "tableowner": postgres_environment.owner_role,
            "expected": True,
            "dangerous": True,
        }

    dsn = postgres_environment.app_dsn if actor == "application" else postgres_environment.owner_dsn
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as connection:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="append-only"):
            connection.execute(statement)
    assert repository.get(snapshot.content_hash).canonical_payload == snapshot.canonical_payload


def test_snapshot_insert_rolls_back_without_archive_residue(postgres_environment: PostgresEnvironment) -> None:
    snapshot, _ = _snapshots("rollback")
    with psycopg.connect(postgres_environment.app_dsn, row_factory=dict_row) as connection:
        with pytest.raises(RuntimeError, match="bounded rollback proof"):
            with connection.transaction():
                connection.execute(
                    "INSERT INTO openair_registry_snapshots(schema_version,content_hash,payload) VALUES (%s,%s,%s::jsonb)",
                    (snapshot.schema_version, snapshot.content_hash, snapshot.canonical_payload.decode("utf-8")),
                )
                raise RuntimeError("bounded rollback proof")
        assert connection.execute(
            "SELECT count(*) AS n FROM openair_registry_snapshots WHERE content_hash=%s", (snapshot.content_hash,)
        ).fetchone()["n"] == 0


def test_down_lock_nonempty_refusal_safe_down_and_reupgrade(postgres_environment: PostgresEnvironment) -> None:
    with psycopg.connect(postgres_environment.owner_dsn, autocommit=True, row_factory=dict_row) as owner:
        tables_before = {
            row["tablename"]
            for row in owner.execute("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public'")
        }
        ledger_before = {
            row["version"] for row in owner.execute("SELECT version FROM openair_schema_migrations")
        }
        owner.execute(DOWN.read_text(encoding="utf-8"))
        assert owner.execute("SELECT to_regclass('openair_registry_snapshots') AS name").fetchone()["name"] is None
        assert owner.execute("SELECT to_regprocedure('openair_registry_snapshots_reject_mutation()') AS name").fetchone()["name"] is None
        tables_after = {
            row["tablename"]
            for row in owner.execute("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public'")
        }
        assert tables_before - {"openair_registry_snapshots"} == tables_after
        assert ledger_before - {UP.name} == {
            row["version"] for row in owner.execute("SELECT version FROM openair_schema_migrations")
        }
        with owner.transaction():
            with owner.cursor() as cursor:
                apply_postgres_migrations(cursor)
        owner.execute(
            sql.SQL("GRANT SELECT, INSERT ON openair_registry_snapshots TO {}").format(
                sql.Identifier(postgres_environment.app_role)
            )
        )

    repository = RegistrySnapshotRepository(
        lambda: psycopg.connect(postgres_environment.app_dsn, row_factory=dict_row)
    )
    snapshot, _ = _snapshots("down-refusal")
    repository.put(snapshot)
    with psycopg.connect(postgres_environment.owner_dsn, autocommit=True, row_factory=dict_row) as owner:
        with pytest.raises(psycopg.errors.RaiseException, match="non-empty"):
            owner.execute(DOWN.read_text(encoding="utf-8"))
        owner.execute("ROLLBACK")
        assert owner.execute("SELECT to_regclass('openair_registry_snapshots') AS name").fetchone()["name"] == "openair_registry_snapshots"
