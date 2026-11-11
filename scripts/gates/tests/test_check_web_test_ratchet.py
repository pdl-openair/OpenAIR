"""Unit tests for check_web_test_ratchet: new failures block, known failures do not."""

import json
from pathlib import Path

import check_web_test_ratchet as ratchet


def _report(*entries: tuple[str, str, str]) -> str:
    files: dict[str, list[dict[str, str]]] = {}
    for file_name, full_name, status in entries:
        files.setdefault(file_name, []).append(
            {"fullName": full_name, "status": status}
        )
    return json.dumps(
        {
            "testResults": [
                {"name": file_name, "assertionResults": assertions}
                for file_name, assertions in files.items()
            ]
        }
    )


def _known(*ids: str) -> str:
    return json.dumps(
        {"version": 1, "nodes": [{"id": node_id, "status": "failed"} for node_id in ids]}
    )


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _run(tmp_path: Path, report: Path, known: Path) -> int:
    return ratchet.main(
        [
            "--report",
            str(report),
            "--known-failures",
            str(known),
            "--root",
            str(tmp_path),
        ]
    )


def test_new_failure_is_violation(tmp_path: Path, capsys) -> None:
    report = _write(
        tmp_path,
        "report.json",
        _report(("a.test.ts", "fails", "failed"), ("a.test.ts", "passes", "passed")),
    )
    known = _write(tmp_path, "known.json", _known())
    code = _run(tmp_path, report, known)
    assert code == ratchet.EXIT_VIOLATION
    assert "NEW FAILURE" in capsys.readouterr().err


def test_known_failure_passes(tmp_path: Path) -> None:
    report = _write(tmp_path, "report.json", _report(("a.test.ts", "fails", "failed")))
    known = _write(tmp_path, "known.json", _known("a.test.ts::fails"))
    code = _run(tmp_path, report, known)
    assert code == ratchet.EXIT_PASS


def test_fixed_known_failure_is_non_blocking(tmp_path: Path) -> None:
    report = _write(tmp_path, "report.json", _report(("a.test.ts", "fails", "passed")))
    known = _write(tmp_path, "known.json", _known("a.test.ts::fails"))
    code = _run(tmp_path, report, known)
    assert code == ratchet.EXIT_PASS


def test_missing_or_empty_report_fails_closed(tmp_path: Path) -> None:
    known = _write(tmp_path, "known.json", _known())
    for name, text in (("missing.json", None), ("empty.json", '{"testResults": []}')):
        report = tmp_path / name
        if text is not None:
            report.write_text(text, encoding="utf-8")
        code = ratchet.main(
            ["--report", str(report), "--known-failures", str(known), "--root", str(tmp_path)]
        )
        assert code == ratchet.EXIT_ERROR


def test_absolute_paths_are_normalized(tmp_path: Path) -> None:
    report = _write(
        tmp_path,
        "report.json",
        _report((str(tmp_path / "apps" / "web" / "a.test.ts"), "fails", "failed")),
    )
    known = _write(tmp_path, "known.json", _known("apps/web/a.test.ts::fails"))
    code = _run(tmp_path, report, known)
    assert code == ratchet.EXIT_PASS
