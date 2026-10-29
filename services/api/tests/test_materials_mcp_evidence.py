from __future__ import annotations

import importlib.util
import json
import socket
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.execution.durable_skill_runtime import _has_mcp_transport_evidence


ROOT = Path(__file__).resolve().parents[3]
MCP_CLIENT = ROOT / "skills/materials/crystal-design-structure-crossover-agent/scripts/mcp_client.py"
ADAPTER = ROOT / "skills/materials/crystal-design-structure-crossover-py/adapter/structure_crossover_adapter.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, body: dict) -> None:
        self._body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._body


def _tool_response(value: dict) -> dict:
    return {"result": {"content": [{"type": "text", "text": json.dumps(value)}]}}


def _receipts(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_mcp_receipts_cover_success_async_failures_and_hashes(monkeypatch, tmp_path: Path) -> None:
    client = _load(MCP_CLIENT, "materials_mcp_client_evidence")
    sink = tmp_path / "receipts.jsonl"
    endpoint = "https://mcp.example/alignn/mcp"
    monkeypatch.setenv("MATERIALS_MCP_RECEIPTS_PATH", str(sink))

    monkeypatch.setattr(client.urllib.request, "urlopen", lambda *_a, **_k: _Response(_tool_response({"bandgap": 1.2})))
    assert client.call_tool(endpoint, "predict", {"cif": "x"}) == {"bandgap": 1.2}
    first = _receipts(sink)[0]
    assert first["mcp_name"] == "alignn" and first["tool"] == "predict"
    assert first["transport_outcome"] == "ok" and len(first["request_sha256"]) == 64

    responses = iter([_tool_response({"job_id": "j1"}), _tool_response({"status": "running"}), _tool_response({"status": "done", "result": {"answer": 7}})])
    monkeypatch.setattr(client.urllib.request, "urlopen", lambda *_a, **_k: _Response(next(responses)))
    assert client.call_async_tool(endpoint, "submit", "poll", {"x": 1}, interval=0) == {"answer": 7}
    async_rows = _receipts(sink)[1:]
    assert [(row["tool"], row["transport_outcome"]) for row in async_rows] == [("submit", "ok"), ("poll", "ok"), ("poll", "ok")]

    for error, expected in [
        (socket.timeout("late"), "timeout"),
        (urllib.error.HTTPError(endpoint, 503, "bad", {}, None), "http_error"),
    ]:
        def raise_error(*_a, _error=error, **_k):
            raise _error
        monkeypatch.setattr(client.urllib.request, "urlopen", raise_error)
        with pytest.raises(type(error)):
            client.call_tool(endpoint, "fails", {"x": expected})
        assert _receipts(sink)[-1]["transport_outcome"] == expected

    monkeypatch.setattr(client.urllib.request, "urlopen", lambda *_a, **_k: _Response({"result": {"isError": True}}))
    with pytest.raises(RuntimeError, match="returned isError"):
        client.call_tool(endpoint, "fails", {"x": "tool"})
    assert _receipts(sink)[-1]["transport_outcome"] == "mcp_error"

    monkeypatch.setattr(client.urllib.request, "urlopen", lambda *_a, **_k: _Response(_tool_response({"ok": True})))
    client.call_tool(endpoint, "hash", {"a": 1})
    client.call_tool(endpoint, "hash", {"a": 1})
    client.call_tool(endpoint, "hash", {"a": 2})
    hashes = [row["request_sha256"] for row in _receipts(sink)[-3:]]
    assert hashes[0] == hashes[1] and hashes[0] != hashes[2]


def test_adapter_aggregates_only_current_receipts_and_validator_stays_strict(tmp_path: Path) -> None:
    adapter = _load(ADAPTER, "structure_crossover_adapter_evidence")
    sink = tmp_path / "mcp_transport_receipts.jsonl"
    valid = {"mcp_name": "alignn", "tool": "predict", "request_sha256": "a" * 64, "transport_outcome": "ok"}
    sink.write_text(json.dumps(valid) + "\n", encoding="utf-8")
    assert adapter._read_mcp_receipts(sink) == [valid]
    output = {"state": "succeeded", "mcp_receipts": adapter._read_mcp_receipts(sink)}
    assert _has_mcp_transport_evidence(output)

    for broken in [
        {"mcp_name": "alignn", "tool": "predict", "transport_outcome": "ok"},
        {**valid, "request_sha256": "wrong"},
        {k: v for k, v in valid.items() if k != "transport_outcome"},
        {**valid, "transport_outcome": "invented"},
    ]:
        assert not _has_mcp_transport_evidence({"state": "succeeded", "mcp_receipts": [broken]})


def test_adapter_same_run_dir_keeps_receipts_owned_by_each_invocation(monkeypatch, tmp_path: Path) -> None:
    adapter = _load(ADAPTER, "structure_crossover_adapter_isolation")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    run_sh = tmp_path / "run.sh"
    run_sh.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    monkeypatch.setattr(adapter, "RUN_SH", run_sh)
    receipts_seen: list[Path] = []

    def fake_run(_command, **kwargs):
        receipt_path = Path(kwargs["env"]["MATERIALS_MCP_RECEIPTS_PATH"])
        receipts_seen.append(receipt_path)
        marker = "a" if len(receipts_seen) <= 3 else "b"
        receipt_path.write_text(json.dumps({"mcp_name": "alignn", "tool": "predict", "request_sha256": marker * 64, "transport_outcome": "ok"}) + "\n", encoding="utf-8")
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    output_a = adapter.run_structure_crossover_mvp("receipt isolation A", {"skip_runplan_llm": True, "run_dir": str(run_dir)})
    output_b = adapter.run_structure_crossover_mvp("receipt isolation B", {"skip_runplan_llm": True, "run_dir": str(run_dir)})
    assert len(set(receipts_seen)) == 2
    assert output_a["mcp_receipts"] == [{"mcp_name": "alignn", "tool": "predict", "request_sha256": "a" * 64, "transport_outcome": "ok"}]
    assert output_b["mcp_receipts"] == [{"mcp_name": "alignn", "tool": "predict", "request_sha256": "b" * 64, "transport_outcome": "ok"}]


def test_receipt_sinks_are_unique_concurrent_and_never_truncate_peers(tmp_path: Path) -> None:
    adapter = _load(ADAPTER, "structure_crossover_adapter_concurrent_isolation")
    run_dir = tmp_path / "shared-run"
    run_dir.mkdir()
    with ThreadPoolExecutor(max_workers=2) as executor:
        sink_a, sink_b = list(executor.map(lambda _index: adapter._create_mcp_receipts_path(run_dir), range(2)))
    assert sink_a != sink_b
    sink_a.write_text("A receipt", encoding="utf-8")
    peer_started_after_a = adapter._create_mcp_receipts_path(run_dir)
    assert sink_a.read_text(encoding="utf-8") == "A receipt"
    assert sink_b.read_text(encoding="utf-8") == ""
    assert peer_started_after_a.read_text(encoding="utf-8") == ""

    stale = run_dir / "mcp_transport_receipts.stale.jsonl"
    stale.write_text(json.dumps({"mcp_name": "stale"}) + "\n", encoding="utf-8")
    exact = {"mcp_name": "alignn", "tool": "predict", "request_sha256": "c" * 64, "transport_outcome": "ok"}
    sink_a.write_text(json.dumps(exact) + "\n", encoding="utf-8")
    sink_b.write_text(json.dumps({"mcp_name": "other"}) + "\n", encoding="utf-8")
    assert adapter._read_mcp_receipts(sink_a) == [exact]
