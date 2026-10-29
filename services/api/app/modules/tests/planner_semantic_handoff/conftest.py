"""No business database or live HTTP may be accessed by this Suite."""

import httpx
import psycopg
import pytest
from app.modules.production_definition_source import ProductionDefinitionSource
from app.modules.registry_snapshot_repository import InMemoryRegistrySnapshotRepository

from .evidence import record


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    record(
        "pytest_" + report.when,
        {
            "nodeid": item.nodeid,
            "outcome": report.outcome,
            "exception": str(call.excinfo.value) if call.excinfo else None,
            "traceback": report.longreprtext,
        },
    )


@pytest.fixture(autouse=True)
def isolated_boundaries(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("PV2 forbids live HTTP and business database access")

    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    monkeypatch.setattr(psycopg, "connect", forbidden)
    monkeypatch.setattr(psycopg.Connection, "connect", forbidden)


@pytest.fixture(scope="module")
def source():
    return ProductionDefinitionSource(
        archive=InMemoryRegistrySnapshotRepository(), planning_only=True
    )
