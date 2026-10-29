from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace

import pytest

from testkit import postgres_harness, pytest_no_skip


def _identity() -> postgres_harness.PostgresIdentity:
    return postgres_harness.PostgresIdentity(
        run_id="run-a",
        container_name="openair-test-postgres-runa",
        container_id="container-a",
        image="postgres:17-alpine",
        image_id="sha256:real-image",
        database="openair_test_runa",
        user="openair_test_runa",
        port=15432,
        server_version="17.10",
    )


def test_child_environment_uses_shared_contract_and_removes_incident_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(postgres_harness.LEGACY_DSN_ENV, "postgresql://manual/incident")
    environment = postgres_harness.child_environment(
        _identity(),
        "postgresql://openair_test:secret@127.0.0.1:15432/openair_test_runa",
    )

    assert postgres_harness.LEGACY_DSN_ENV not in environment
    assert environment[postgres_harness.DSN_ENV].endswith("/openair_test_runa")
    assert environment["OPENAIR_TEST_POSTGRES_RUN_ID"] == "run-a"
    assert environment["OPENAIR_TEST_POSTGRES_CONTAINER_ID"] == "container-a"
    assert environment["OPENAIR_TEST_POSTGRES_IMAGE_ID"] == "sha256:real-image"
    assert environment["OPENAIR_TESTKIT_REQUIRE_NO_SKIP"] == "1"
    assert "testkit.pytest_no_skip" in environment["PYTEST_PLUGINS"]


def test_missing_container_runtime_is_no_valid_evidence_and_non_successful(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    arguments = Namespace(
        docker="openair-deliberately-missing-docker-command",
        image=postgres_harness.DEFAULT_IMAGE,
        evidence_dir=str(tmp_path),
        command=["--", "python", "-c", "raise SystemExit(0)"],
    )

    exit_code = postgres_harness.run_harness(arguments)

    assert exit_code != 0
    assert "NO_VALID_EVIDENCE" in capsys.readouterr().err
    evidence_files = list(tmp_path.glob("postgres-*.json"))
    assert len(evidence_files) == 1
    evidence = json.loads(evidence_files[0].read_text(encoding="utf-8"))
    assert evidence["status"] == "NO_VALID_EVIDENCE"
    assert evidence["child_exit_code"] is None
    assert evidence["legacy_dsn_accepted"] is False
    assert "postgres" not in evidence


def test_partial_provision_failure_removes_the_started_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docker_calls: list[tuple[str, ...]] = []

    def fake_docker(docker_command: str, *arguments: str, check: bool = True):
        docker_calls.append(arguments)
        if arguments[0] == "run":
            return SimpleNamespace(returncode=0, stdout="container-partial\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="28.3.2\n", stderr="")

    def fail_port(*args, **kwargs):
        raise postgres_harness.InfrastructureUnavailable("port mapping unavailable")

    monkeypatch.setattr(postgres_harness, "_docker", fake_docker)
    monkeypatch.setattr(postgres_harness, "_host_port", fail_port)

    with pytest.raises(postgres_harness.InfrastructureUnavailable, match="port mapping"):
        postgres_harness.provision_postgres(
            docker_command="docker", image=postgres_harness.DEFAULT_IMAGE, run_id="partial-run"
        )

    assert ("rm", "-f", "openair-test-postgres-partialrun") in docker_calls


def test_required_suite_skip_forces_failed_pytest_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAIR_TESTKIT_REQUIRE_NO_SKIP", "1")

    class Reporter:
        stats = {"skipped": [object()]}

        def write_line(self, *args, **kwargs):
            return None

    reporter = Reporter()
    plugin_manager = SimpleNamespace(get_plugin=lambda name: reporter if name == "terminalreporter" else None)
    session = SimpleNamespace(
        config=SimpleNamespace(pluginmanager=plugin_manager),
        exitstatus=pytest.ExitCode.OK,
    )

    pytest_no_skip.pytest_sessionfinish(session, int(pytest.ExitCode.OK))

    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED


def test_postgres_profile_is_pinned_to_postgresql_17() -> None:
    assert postgres_harness.DEFAULT_IMAGE == "postgres:17-alpine"
