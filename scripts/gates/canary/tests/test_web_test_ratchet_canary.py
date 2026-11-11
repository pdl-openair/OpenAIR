"""Canary: web test ratchet blocks a new failure and stays green for a known one."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import run_gate


def _report(path: Path, full_name: str, status: str) -> None:
    path.write_text(
        json.dumps(
            {
                "testResults": [
                    {
                        "name": "apps/web/src/example.test.ts",
                        "assertionResults": [
                            {"fullName": full_name, "status": status}
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def _known(path: Path, full_name: str | None) -> None:
    nodes = [] if full_name is None else [{"id": f"apps/web/src/example.test.ts::{full_name}"}]
    path.write_text(json.dumps({"version": 1, "nodes": nodes}), encoding="utf-8")


def test_new_web_failure_is_red(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    known = tmp_path / "known.json"
    _report(report, "new failure", "failed")
    _known(known, None)
    proc = run_gate(
        "check_web_test_ratchet.py",
        "--report",
        str(report),
        "--known-failures",
        str(known),
        "--root",
        str(tmp_path),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "NEW FAILURE" in proc.stderr


def test_known_web_failure_is_green(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    known = tmp_path / "known.json"
    _report(report, "known failure", "failed")
    _known(known, "known failure")
    proc = run_gate(
        "check_web_test_ratchet.py",
        "--report",
        str(report),
        "--known-failures",
        str(known),
        "--root",
        str(tmp_path),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "known failure" in proc.stdout
