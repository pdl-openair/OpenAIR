from __future__ import annotations

import inspect
from pathlib import Path

from app.execution.python_adapter_runner import _run_registered_adapter_entrypoint


def test_registered_adapter_worker_forces_utf8_stdio(monkeypatch, tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill"
    adapter_dir = skill_dir / "adapter"
    adapter_dir.mkdir(parents=True)
    (adapter_dir / "__init__.py").write_text("", encoding="utf-8")
    adapter_source = "\n".join([
        "def run(payload):",
        "    return {\"text\": \"薄膜厚度 5 µm\", \"echo\": payload[\"text\"]}",
        "",
    ])
    (adapter_dir / "unicode_adapter.py").write_text(adapter_source, encoding="utf-8")

    monkeypatch.setenv("PYTHONIOENCODING", "ascii")
    monkeypatch.setenv("PYTHONUTF8", "0")

    kwargs = {
        "repo_root": Path(__file__).resolve().parents[2],
        "skill_dir": skill_dir,
        "entrypoint": ("adapter.unicode_adapter", "run", "payload_v1"),
        "input_payload": {"text": "压电 µ 测试"},
    }
    parameters = inspect.signature(_run_registered_adapter_entrypoint).parameters
    if "progress_callback" in parameters:
        kwargs["progress_callback"] = None

    result = _run_registered_adapter_entrypoint(**kwargs)

    assert result == {
        "text": "薄膜厚度 5 µm",
        "echo": "压电 µ 测试",
    }
