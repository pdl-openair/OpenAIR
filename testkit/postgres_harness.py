from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
from uuid import uuid4


DEFAULT_IMAGE = "postgres:17-alpine"
DSN_ENV = "OPENAIR_TEST_POSTGRES_DSN"
LEGACY_DSN_ENV = "OPENAIR_LINE_C_ROUND5_POSTGRES_DSN"


class InfrastructureUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class PostgresIdentity:
    run_id: str
    container_name: str
    container_id: str
    image: str
    image_id: str
    database: str
    user: str
    port: int
    server_version: str


def _run(
    command: Sequence[str],
    *,
    check: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            check=check,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise InfrastructureUnavailable(detail.strip()) from exc


def _docker(
    docker_command: str,
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = _run((docker_command, *arguments))
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise InfrastructureUnavailable(f"docker {' '.join(arguments[:2])} failed: {detail}")
    return result


def _host_port(docker_command: str, container_name: str) -> int:
    output = _docker(docker_command, "port", container_name, "5432/tcp").stdout.strip()
    if not output:
        raise InfrastructureUnavailable("Docker did not publish the PostgreSQL port")
    try:
        return int(output.splitlines()[0].rsplit(":", 1)[1])
    except (IndexError, ValueError) as exc:
        raise InfrastructureUnavailable(f"Unrecognized Docker port mapping: {output}") from exc


def _wait_until_ready(
    docker_command: str,
    container_name: str,
    user: str,
    database: str,
    *,
    attempts: int = 60,
) -> str:
    for _ in range(attempts):
        ready = _docker(
            docker_command,
            "exec",
            container_name,
            "pg_isready",
            "-U",
            user,
            "-d",
            database,
            check=False,
        )
        if ready.returncode == 0:
            query = _docker(
                docker_command,
                "exec",
                container_name,
                "psql",
                "-U",
                user,
                "-d",
                database,
                "-Atqc",
                "SELECT current_setting('server_version')",
                check=False,
            )
            if query.returncode == 0 and query.stdout.strip():
                return query.stdout.strip()
        time.sleep(0.5)
    logs = _docker(docker_command, "logs", container_name, check=False)
    raise InfrastructureUnavailable(
        "PostgreSQL did not become ready: " + (logs.stderr.strip() or logs.stdout.strip())
    )


def provision_postgres(
    *,
    docker_command: str,
    image: str,
    run_id: str,
) -> tuple[PostgresIdentity, str]:
    suffix = run_id.replace("-", "")[:12]
    container_name = f"openair-test-postgres-{suffix}"
    database = f"openair_test_{suffix}"
    user = f"openair_test_{suffix}"
    password = secrets.token_hex(24)

    _docker(docker_command, "version", "--format", "{{.Server.Version}}")
    started = _docker(
        docker_command,
        "run",
        "-d",
        "--name",
        container_name,
        "--label",
        f"openair.test.run_id={run_id}",
        "-e",
        f"POSTGRES_USER={user}",
        "-e",
        f"POSTGRES_PASSWORD={password}",
        "-e",
        f"POSTGRES_DB={database}",
        "-p",
        "127.0.0.1::5432",
        image,
    )
    container_id = started.stdout.strip()
    try:
        if not container_id:
            raise InfrastructureUnavailable("Docker returned no PostgreSQL container identity")
        port = _host_port(docker_command, container_name)
        server_version = _wait_until_ready(docker_command, container_name, user, database)
        image_id = _docker(
            docker_command,
            "inspect",
            "--format",
            "{{.Image}}",
            container_name,
        ).stdout.strip()
        identity = PostgresIdentity(
            run_id=run_id,
            container_name=container_name,
            container_id=container_id,
            image=image,
            image_id=image_id,
            database=database,
            user=user,
            port=port,
            server_version=server_version,
        )
        dsn = f"postgresql://{user}:{password}@127.0.0.1:{port}/{database}"
        return identity, dsn
    except BaseException:
        try:
            _docker(docker_command, "rm", "-f", container_name, check=False)
        except InfrastructureUnavailable:
            pass
        raise


def child_environment(identity: PostgresIdentity, dsn: str) -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop(LEGACY_DSN_ENV, None)
    environment[DSN_ENV] = dsn
    environment["OPENAIR_TEST_POSTGRES_RUN_ID"] = identity.run_id
    environment["OPENAIR_TEST_POSTGRES_CONTAINER_ID"] = identity.container_id
    environment["OPENAIR_TEST_POSTGRES_IMAGE_ID"] = identity.image_id
    environment["OPENAIR_TEST_POSTGRES_DATABASE"] = identity.database
    environment["OPENAIR_TESTKIT_REQUIRE_NO_SKIP"] = "1"
    plugins = environment.get("PYTEST_PLUGINS", "").strip()
    plugin = "testkit.pytest_no_skip"
    environment["PYTEST_PLUGINS"] = f"{plugins},{plugin}" if plugins else plugin
    return environment


def _write_evidence(directory: Path, run_id: str, payload: dict[str, object]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"postgres-{run_id}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def run_harness(args: argparse.Namespace) -> int:
    run_id = uuid4().hex
    started_at = datetime.now(timezone.utc).isoformat()
    identity: PostgresIdentity | None = None
    child_exit_code: int | None = None
    failure: str | None = None
    cleanup_failure: str | None = None
    evidence_path: Path | None = None
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("NO_VALID_EVIDENCE: no Suite command was provided", file=sys.stderr)
        return 2

    try:
        identity, dsn = provision_postgres(
            docker_command=args.docker,
            image=args.image,
            run_id=run_id,
        )
        print(
            "POSTGRES_HARNESS_READY "
            f"run_id={run_id} database={identity.database} "
            f"server_version={identity.server_version} image_id={identity.image_id}"
        )
        child = subprocess.run(command, env=child_environment(identity, dsn), check=False)
        child_exit_code = child.returncode
        if child_exit_code != 0:
            failure = f"Suite command exited with {child_exit_code}"
    except (InfrastructureUnavailable, OSError) as exc:
        failure = str(exc)
    finally:
        if identity is not None:
            try:
                removed = _docker(args.docker, "rm", "-f", identity.container_name, check=False)
                if removed.returncode != 0:
                    cleanup_failure = removed.stderr.strip() or removed.stdout.strip()
            except InfrastructureUnavailable as exc:
                cleanup_failure = str(exc)
            if cleanup_failure:
                failure = failure or f"PostgreSQL cleanup failed: {cleanup_failure}"

        status = "PASS" if failure is None and child_exit_code == 0 else "NO_VALID_EVIDENCE"
        payload: dict[str, object] = {
            "schema_version": 1,
            "run_id": run_id,
            "profile": "service-integration-postgres",
            "status": status,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "command": command,
            "child_exit_code": child_exit_code,
            "failure": failure,
            "cleanup_failure": cleanup_failure,
            "legacy_dsn_accepted": False,
        }
        if identity is not None:
            payload["postgres"] = {
                "container_id": identity.container_id,
                "image": identity.image,
                "image_id": identity.image_id,
                "database": identity.database,
                "server_version": identity.server_version,
                "isolated_container": True,
            }
        evidence_path = _write_evidence(Path(args.evidence_dir), run_id, payload)

    if failure is not None:
        print(f"NO_VALID_EVIDENCE: {failure}", file=sys.stderr)
        print(f"HARNESS_EVIDENCE={evidence_path}", file=sys.stderr)
        return child_exit_code or 2
    print(f"HARNESS_EVIDENCE={evidence_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a formal Suite with an isolated disposable PostgreSQL dependency."
    )
    parser.add_argument("--docker", default="docker", help="Docker CLI executable")
    parser.add_argument("--image", default=DEFAULT_IMAGE, help="PostgreSQL container image")
    parser.add_argument("--evidence-dir", default="artifacts/harness")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    return run_harness(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
