from __future__ import annotations

import os

import pytest


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if os.environ.get("OPENAIR_TESTKIT_REQUIRE_NO_SKIP") != "1":
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    skipped = len(reporter.stats.get("skipped", [])) if reporter is not None else 0
    if skipped:
        if reporter is not None:
            reporter.write_line(
                f"NO_VALID_EVIDENCE: {skipped} test(s) skipped in a fail-closed Harness run",
                red=True,
                bold=True,
            )
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
